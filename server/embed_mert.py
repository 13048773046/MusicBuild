# -*- coding: utf-8 -*-
"""流萤音乐 · MERT 语义音频表征（2026-10-24 P1-5 音频特征升级）。

用微软 MERT-v1-95M（预训练 96T 音乐语料）把每首歌编码成 768 维语义 embedding，
替换/升级原 20 维手工 librosa 特征（音色/乐器/情绪/场景语义更丰富），
供「音频内容相似（audio 路）」「i2i 关联（item_sims）」使用。

写入 audio_features（version=2）：
  raw_vec = 768 维 embedding（均值池化，原始值）
  vec     = z-score 归一化 [0,1]（stats 缺失时 L2 归一化兜底，供余弦相似）

用法：
  python3 embed_mert.py --limit 10               # 小批量测试
  python3 embed_mert.py --offset 0 --limit 1600  # 分片 1（后台并行跑）
  python3 embed_mert.py --offset 1600            # 分片 2
  python3 embed_mert.py --refresh-stats          # 全量完成后重算 z-score
  分片完成后：python3 feature_extract.py --build-i2i  # 重建 i2i（基于新 vec）
"""
import os
import sys
import json
import time
import glob
import subprocess
import argparse

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ.setdefault("OMP_NUM_THREADS", "4")

ROOT = "/www/wwwroot/music"
sys.path.insert(0, ROOT)
from server.feature_extract import db, all_track_ids, local_audio_path

try:
    import numpy as np
    import torch
    import soundfile as sf
    from transformers import AutoModel, Wav2Vec2FeatureExtractor
except ImportError as e:
    sys.stderr.write("依赖缺失: %s（pip3 install torch transformers soundfile）\n" % e)
    sys.exit(1)

MERT_SR = 24000
ANALYZE_SECONDS = 30
EMBED_VERSION = 2
MODEL_NAME = "m-a-p/MERT-v1-95M"

_extractor = None
_model = None


def _get_model():
    global _extractor, _model
    if _model is None:
        t0 = time.time()
        _extractor = Wav2Vec2FeatureExtractor.from_pretrained(MODEL_NAME)
        _model = AutoModel.from_pretrained(MODEL_NAME, trust_remote_code=True)
        _model.eval()
        print("MERT model loaded %.1fs" % (time.time() - t0), flush=True)
    return _extractor, _model


def embed_audio(path):
    """ffmpeg 预转 24kHz wav → MERT 均值池化 → 768 维 numpy"""
    tmp = "/tmp/mert_%d_%s.wav" % (os.getpid(), abs(hash(path)) % 100000)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-t", str(ANALYZE_SECONDS),
             "-i", path, "-ar", str(MERT_SR), "-ac", "1", "-f", "wav", tmp],
            check=True, timeout=120)
        y, sr = sf.read(tmp, dtype="float32")
        if sr != MERT_SR or len(y) == 0:
            raise ValueError("bad wav sr=%d len=%d" % (sr, len(y)))
        ex, model = _get_model()
        inputs = ex(y, sampling_rate=sr, return_tensors="pt")
        with torch.no_grad():
            out = model(**inputs)
        return out.last_hidden_state.mean(dim=1).squeeze(0).cpu().numpy()
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def _mert_stats(c):
    """只统计 MERT(version>=2) mert_raw（768 维原始）的每维 mean/std。
    （2026-11-01 起 MERT 原始值单独存 mert_raw，raw_vec 归 librosa 20 维——不复用）"""
    rows = c.execute(
        "SELECT mert_raw FROM audio_features WHERE mert_raw IS NOT NULL AND mert_raw!='' AND version>=?",
        (EMBED_VERSION,)).fetchall()
    if len(rows) < 5:
        return None
    arr = np.array([json.loads(r[0]) for r in rows], dtype=float)
    mean = arr.mean(axis=0)
    std = arr.std(axis=0)
    std[std < 1e-6] = 1.0
    return (mean, std)


def _norm_vec(raw, stats):
    """MERT embedding 归一化：有 MERT 全库统计→z-score[0,1]；无统计→L2 归一化兜底"""
    if stats is not None:
        mean, std = stats
        z = (raw - mean) / std
        return (np.clip(z, -3, 3) / 3.0 * 0.5 + 0.5).tolist()
    n = np.linalg.norm(raw)
    return (raw / n if n > 1e-9 else raw).tolist()


def analyze_track(tid, force=False):
    c = db()
    try:
        if not force:
            row = c.execute(
                "SELECT 1 FROM audio_features WHERE track_id=? AND version>=?",
                (tid, EMBED_VERSION)).fetchone()
            if row:
                return (tid, True, "already")
        path, source = local_audio_path(tid)
        if not path:
            return (tid, False, "no-local-audio")
        raw = embed_audio(path)
        if raw.shape[0] < 64:
            return (tid, False, "embed-dim-%d" % raw.shape[0])
        stats = _mert_stats(c)
        vec = _norm_vec(raw, stats)
        # 2026-11-01：MERT 只写自己的字段（vec / mert_raw / version），
        # 不覆盖 librosa 的 features/raw_vec——两套数据分开共存。
        exists = c.execute("SELECT 1 FROM audio_features WHERE track_id=?", (tid,)).fetchone()
        if exists:
            c.execute("UPDATE audio_features SET vec=?, mert_raw=?, version=?, analyzed_at=? WHERE track_id=?",
                      (json.dumps(vec), json.dumps(raw.tolist()), EMBED_VERSION, time.time(), tid))
        else:
            c.execute(
                "INSERT INTO audio_features(track_id, features, raw_vec, vec, mert_raw, analyzed_at, version)"
                " VALUES(?,?,?,?,?,?,?)",
                (tid, "", "", json.dumps(vec), json.dumps(raw.tolist()), time.time(), EMBED_VERSION))
        c.commit()
        return (tid, True, source)
    finally:
        c.close()


def refresh_stats():
    """全量 mert_raw（768 维 MERT 原始）重算 z-score 统计并刷新 vec
    （2026-11-01 起 MERT 原始值单独存 mert_raw，raw_vec 归 librosa——不混读）"""
    c = db()
    try:
        rows = c.execute(
            "SELECT track_id, mert_raw FROM audio_features"
            " WHERE mert_raw IS NOT NULL AND mert_raw!='' AND version>=?",
            (EMBED_VERSION,)).fetchall()
        if len(rows) < 5:
            print("refresh-stats: MERT 数据不足（<5 首），跳过")
            return
        arr = np.array([json.loads(r[1]) for r in rows], dtype=float)
        mean = arr.mean(axis=0)
        std = arr.std(axis=0)
        std[std < 1e-6] = 1.0
        updated = 0
        for tid, raw_s in rows:
            try:
                raw = np.array(json.loads(raw_s), dtype=float)
                z = (raw - mean) / std
                vec = (np.clip(z, -3, 3) / 3.0 * 0.5 + 0.5).tolist()
                c.execute("UPDATE audio_features SET vec=? WHERE track_id=?",
                          (json.dumps(vec), tid))
                updated += 1
            except Exception:
                pass
        c.commit()
        print("refresh-stats: %d 首 MERT 向量按最新分布重归一化完成" % updated)
    finally:
        c.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--track", type=str, default="")
    ap.add_argument("--refresh-stats", action="store_true")
    args = ap.parse_args()

    if args.refresh_stats:
        refresh_stats()
        return

    if args.track:
        tid, ok, msg = analyze_track(args.track, force=True)
        print("track %s: %s (%s)" % (tid, ok, msg))
        return

    ids = all_track_ids()
    if args.offset:
        ids = ids[args.offset:]
    if args.limit:
        ids = ids[:args.limit]
    total = len(ids)
    ok_n = fail_n = skip_n = 0
    t0 = time.time()
    for i, tid in enumerate(ids, 1):
        tid2, ok, msg = analyze_track(tid)
        if msg == "already":
            skip_n += 1
        elif ok:
            ok_n += 1
        else:
            fail_n += 1
            print("[%d/%d] %s FAIL %s" % (i, total, tid, msg), flush=True)
        if i % 20 == 0 or i == total:
            el = time.time() - t0
            eta = el / i * (total - i)
            print("[%d/%d] ok=%d skip=%d fail=%d 用时 %.0fs ETA %.0fs"
                  % (i, total, ok_n, skip_n, fail_n, el, eta), flush=True)
    print("完成：总数 %d，成功 %d，跳过 %d，失败 %d" % (total, ok_n, skip_n, fail_n), flush=True)


if __name__ == "__main__":
    main()
