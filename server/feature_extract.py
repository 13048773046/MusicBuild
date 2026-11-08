# -*- coding: utf-8 -*-
"""流萤音乐 · 音频特征提取（2026-10-24 个性化推送精准化）。

从曲库本地音频文件提取「内容级」特征（节奏/能量/音色/调性/情绪），
为个性化推荐提供「这首歌内容像不像你常听的」的匹配信号——
替代/补充原先纯文本关键词（歌名/歌手/标签 LIKE）匹配。

覆盖：
  - tracks（用户上传）：本地文件 fname（files/{fname}）
  - collected_tracks（采集）：play_url 为本地相对文件名（files/{play_url}）

特征（每曲 ~20 维 + 可解释标签）：
  tempo / onset_rate / energy / dynamic_complexity /
  spectral_centroid / spectral_bandwidth / spectral_rolloff / zero_crossing_rate /
  mfcc_mean[0:6] / mfcc_std[0:6]
  标签：bpm_band（慢/中/快）、mood（欢快/舒缓/激昂/低沉）、key（调性）、
        danceability（0-1 近似）、valence（0-1 近似）

归一化：vec 为全曲库 z-score 裁剪到 [-3,3] 再映射 [0,1] 的 20 维向量
（供余弦相似度直接使用）。

用法：
  python3 feature_extract.py --limit 5        # 小批量测试
  python3 feature_extract.py                   # 全量（已分析自动跳过，可断点续跑）
  python3 feature_extract.py --track <tid>     # 单曲（上传/采集新歌后懒调用）
  python3 feature_extract.py --refresh-stats   # 只用已有特征重算归一化统计
"""
import os
import sys
import json
import time
import glob
import subprocess
import sqlite3
import argparse
import traceback

ROOT = "/www/wwwroot/music"
FILES_DIR = os.path.join(ROOT, "files")
DB_PATH = os.path.join(ROOT, "tracks.db")

try:
    import numpy as np
    import librosa
except ImportError:
    sys.stderr.write("需要安装 librosa（pip3 install librosa）\n")
    sys.exit(1)

FEAT_VERSION = 1
SR = 22050
ANALYZE_SECONDS = 30          # 只分析前 30 秒（速度/资源可控，特征代表性足够）
VEC_DIM = 20


def _load_audio(path):
    """librosa 默认 soundfile 后端不支持 m4a/aac（曲库大量 m4a）→ 用 ffmpeg 预转 wav 再读。
    只读前 ANALYZE_SECONDS 秒、降采样 22050、单声道，临时文件用完即删。"""
    tmp = "/tmp/feat_%d_%s.wav" % (os.getpid(), abs(hash(path)) % 100000)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-t", str(ANALYZE_SECONDS),
             "-i", path, "-ar", str(SR), "-ac", "1", "-f", "wav", tmp],
            check=True, timeout=90)
        y, sr = librosa.load(tmp, sr=SR, mono=True)
        return y, sr
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def db():
    c = sqlite3.connect(DB_PATH)
    c.execute("PRAGMA busy_timeout=5000")
    c.execute("""CREATE TABLE IF NOT EXISTS audio_features(
        track_id TEXT PRIMARY KEY,
        features TEXT,
        raw_vec TEXT,
        vec TEXT,
        analyzed_at REAL,
        version INTEGER DEFAULT 1)""")
    # 兼容旧表（首次迁移时补 raw_vec 列；并发多进程可能同时检查→竞态，包 try 防御）
    cols = [r[1] for r in c.execute("PRAGMA table_info(audio_features)")]
    if "raw_vec" not in cols:
        try:
            c.execute("ALTER TABLE audio_features ADD COLUMN raw_vec TEXT DEFAULT ''")
        except Exception:
            pass
    # 2026-11-01：新增 mert_raw 列——MERT 768 维原始值单独存放，
    # 与 librosa 的 features/raw_vec 分开共存（MERT 升级曾整行覆盖 librosa 数据）
    if "mert_raw" not in cols:
        try:
            c.execute("ALTER TABLE audio_features ADD COLUMN mert_raw TEXT DEFAULT ''")
        except Exception:
            pass
    return c


def local_audio_path(tid):
    """返回 (path, source) 或 (None, None)：解析曲库歌曲的本地音频文件路径"""
    c = db()
    try:
        row = c.execute("SELECT fname FROM tracks WHERE id=?", (tid,)).fetchone()
        if row and row[0]:
            p = os.path.join(FILES_DIR, row[0])
            if os.path.exists(p):
                return p, "track"
        crow = c.execute("SELECT play_url FROM collected_tracks WHERE id=?", (tid,)).fetchone()
        if crow and crow[0] and not str(crow[0]).startswith("http"):
            p = os.path.join(FILES_DIR, str(crow[0]))
            if os.path.exists(p):
                return p, "collected"
    finally:
        c.close()
    return None, None


def _safe(fn, default):
    try:
        v = fn()
        return default if v is None or (isinstance(v, float) and (v != v)) else v
    except Exception:
        return default


def extract_features(path):
    """从音频文件提取特征，返回 (features_dict, raw_vec) 或抛异常"""
    y, sr = _load_audio(path)
    if len(y) == 0:
        raise ValueError("empty audio")
    n = len(y)

    # 节奏
    try:
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
        tempo = float(np.atleast_1d(tempo)[0])
    except Exception:
        tempo, beats = 0.0, None
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    try:
        onset_rate = float(np.count_nonzero(librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr)) / max(ANALYZE_SECONDS, 1))
    except Exception:
        onset_rate = 0.0

    # 能量 / 动态
    rms = librosa.feature.rms(y=y)[0]
    energy = float(np.mean(rms))
    dyn = float(np.std(rms))

    # 频谱
    stft = librosa.stft(y)
    mag = np.abs(stft)
    spec_c = float(np.mean(librosa.feature.spectral_centroid(S=mag, sr=sr)))
    spec_b = float(np.mean(librosa.feature.spectral_bandwidth(S=mag, sr=sr)))
    spec_r = float(np.mean(librosa.feature.spectral_rolloff(S=mag, sr=sr)))
    zcr = float(np.mean(librosa.feature.zero_crossing_rate(y)))

    # 音色（MFCC 前 6 维均值/方差）
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
    mfcc_m = [float(np.mean(mfcc[i])) for i in range(6)]
    mfcc_s = [float(np.std(mfcc[i])) for i in range(6)]

    # 调性（chroma 能量最大键 → key 标签）
    try:
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        chroma_mean = np.mean(chroma, axis=1)
        key_idx = int(np.argmax(chroma_mean))
        _NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        key = _NAMES[key_idx % 12]
    except Exception:
        key = ""

    # 归一化参考：全曲库统计（见 refresh_stats / 首跑时积累）
    raw = np.array([tempo, onset_rate, energy, dyn,
                    spec_c, spec_b, spec_r, zcr] + mfcc_m + mfcc_s, dtype=float)
    # 标签（可解释）
    bpm_band = "慢速" if 0 < tempo < 80 else ("中速" if tempo < 120 else ("快速" if tempo > 0 else "未知"))
    # danceability 近似：BPM 落在 90-130 区间 + 节奏规则性（onset 方差小）+ 能量适中
    if beats is not None and len(beats) > 1:
        gaps = np.diff(beats)
        reg = float(np.std(gaps)) if len(gaps) > 1 else 1.0
    else:
        reg = 1.0
    danceability = max(0.0, min(1.0, 1.0 - abs(tempo - 110) / 60.0
                                - min(reg, 1.0) * 0.4 + (0.2 if 80 <= tempo <= 140 else 0)))
    # valence/情绪近似：能量 + 频谱明亮度 + 节奏 → 欢快/舒缓/激昂/低沉
    bright = spec_c
    if tempo >= 120 and energy > 0.05 and bright > 2500:
        mood = "激昂"
    elif tempo >= 100 and energy > 0.02 and bright > 1500:
        mood = "欢快"
    elif tempo < 80 or energy < 0.01:
        mood = "低沉"
    else:
        mood = "舒缓"
    valence = min(1.0, max(0.0, 0.5 + (bright - 2200) / 6000.0 + (energy - 0.03) * 5.0
                            + (0.15 if tempo >= 100 else -0.15)))
    features = {
        "tempo": round(tempo, 2), "onset_rate": round(onset_rate, 3),
        "energy": round(energy, 5), "dynamic": round(dyn, 5),
        "centroid": round(spec_c, 1), "bandwidth": round(spec_b, 1),
        "rolloff": round(spec_r, 1), "zcr": round(zcr, 4),
        "mfcc_mean": [round(x, 4) for x in mfcc_m],
        "mfcc_std": [round(x, 4) for x in mfcc_s],
        "bpm_band": bpm_band, "mood": mood, "key": key,
        "danceability": round(danceability, 3), "valence": round(valence, 3),
    }
    return features, raw


# ---------------- 归一化统计（全曲库 z-score → [0,1]） ----------------

_STATS_CACHE = None


def _load_stats(c):
    """统计表：所有已分析 vec 的每维 mean/std（从 raw_vec 计算；无数据返回 None）"""
    global _STATS_CACHE
    if _STATS_CACHE is not None:
        return _STATS_CACHE
    rows = c.execute("SELECT raw_vec FROM audio_features WHERE raw_vec IS NOT NULL AND raw_vec!=''").fetchall()
    if not rows:
        _STATS_CACHE = None
        return None
    arr = np.array([json.loads(r[0]) for r in rows], dtype=float)
    if arr.shape[0] < 5:
        _STATS_CACHE = None
        return None
    mean = arr.mean(axis=0)
    std = arr.std(axis=0)
    std[std < 1e-6] = 1.0
    _STATS_CACHE = (mean, std)
    return _STATS_CACHE


def normalize_vec(raw, stats):
    if stats is None:
        # 首跑无统计：直接 min-max 到 [0,1]（按特征合理上限粗归一，后续 refresh-stats 修正）
        bounds = np.array([200, 10, 1, 1, 10000, 6000, 12000, 1,
                           1000, 1000, 1000, 1000, 1000, 1000,
                           1000, 1000, 1000, 1000, 1000, 1000], dtype=float)
        return np.clip(raw / bounds, 0, 1).tolist()
    mean, std = stats
    z = (raw - mean) / std
    return (np.clip(z, -3, 3) / 3.0 * 0.5 + 0.5).tolist()   # [-3,3]→[0,1]


# ---------------- 主流程 ----------------

def analyze_track(tid, force=False):
    """分析单曲并写库（2026-11-01：与 MERT 分开共存）。
    本脚本只负责 librosa 字段（features/raw_vec）：
      - 已存在记录 → UPDATE features/raw_vec（保留 MERT 的 vec/version 不动）
      - 新歌 → INSERT（vec 留空，等 embed_mert.py 补 MERT 向量）
    返回 (tid, ok, msg)"""
    c = db()
    try:
        if not force:
            row = c.execute(
                "SELECT 1 FROM audio_features WHERE track_id=? AND features!='' AND raw_vec!=''",
                (tid,)).fetchone()
            if row:
                return (tid, True, "already")
        path, source = local_audio_path(tid)
        if not path:
            return (tid, False, "no-local-audio")
        try:
            features, raw = extract_features(path)
        except Exception as e:
            return (tid, False, "extract-fail:%s" % str(e)[:120])
        raw_json = json.dumps(raw.tolist())
        exists = c.execute("SELECT 1 FROM audio_features WHERE track_id=?", (tid,)).fetchone()
        if exists:
            c.execute("UPDATE audio_features SET features=?, raw_vec=?, analyzed_at=? WHERE track_id=?",
                      (json.dumps(features, ensure_ascii=False), raw_json, time.time(), tid))
        else:
            # 新歌：vec 留空等 embed_mert.py 补 MERT 768 维（vec 只存 MERT，避免 20 维混入矩阵）
            c.execute("""INSERT INTO audio_features(track_id, features, raw_vec, vec, analyzed_at, version)
                         VALUES(?,?,?,?,?,?)""",
                      (tid, json.dumps(features, ensure_ascii=False), raw_json, "",
                       time.time(), FEAT_VERSION))
        c.commit()
        return (tid, True, source)
    finally:
        c.close()


def all_track_ids():
    c = db()
    try:
        rows = c.execute("SELECT id FROM tracks WHERE offline=0").fetchall()
        rows += c.execute("SELECT id FROM collected_tracks WHERE offline=0 AND play_url!=''").fetchall()
        ids = []
        seen = set()
        for (tid,) in rows:
            if tid and tid not in seen:
                seen.add(tid)
                ids.append(tid)
        return ids
    finally:
        c.close()


def build_i2i(topk=20):
    """生成 i2i 音频相似关联表 item_sims（2026-10-24 优化）：
    全库 vec（行归一化）两两余弦 → 每首歌 topk 相似歌。矩阵乘一次性完成。"""
    c = db()
    try:
        c.execute("""CREATE TABLE IF NOT EXISTS item_sims(
            track_id TEXT, sim_track_id TEXT, rank INTEGER DEFAULT 0,
            score REAL DEFAULT 0, built_at REAL DEFAULT 0,
            PRIMARY KEY(track_id, sim_track_id))""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_item_sims_src ON item_sims(track_id, rank)")
        # 只取当前生效版本（MERT version>=2 / 旧版=1）的 vec，避免混维矩阵相乘报错
        rows = c.execute(
            "SELECT track_id, vec FROM audio_features"
            " WHERE vec IS NOT NULL AND vec!=''"
            " AND version=(SELECT MAX(version) FROM audio_features)").fetchall()
        if len(rows) < 10:
            print("build-i2i: 特征数据不足（%d 首），跳过" % len(rows))
            return
        ids = []
        vecs = []
        for tid, v in rows:
            try:
                arr = json.loads(v)
                if arr and len(arr) >= 16:
                    a = np.array(arr, dtype=float)
                    n = np.linalg.norm(a)
                    if n > 1e-9:
                        vecs.append(a / n)
                        ids.append(tid)
            except Exception:
                pass
        mat = np.array(vecs, dtype=float)          # (n, dim) 行已归一化
        sim = mat.dot(mat.T)                        # (n, n) 余弦矩阵
        n = len(ids)
        now = time.time()
        c.execute("DELETE FROM item_sims")
        batch = []
        for i in range(n):
            row = sim[i]
            order = np.argsort(-row)
            rank = 0
            for j in order:
                if j == i:
                    continue                        # 自身跳过
                batch.append((ids[i], ids[j], rank, float(row[j]), now))
                rank += 1
                if rank >= topk:
                    break
            if len(batch) >= 1000:
                c.executemany(
                    "INSERT OR REPLACE INTO item_sims(track_id, sim_track_id, rank, score, built_at)"
                    " VALUES(?,?,?,?,?)", batch)
                batch = []
                c.commit()
        if batch:
            c.executemany(
                "INSERT OR REPLACE INTO item_sims(track_id, sim_track_id, rank, score, built_at)"
                " VALUES(?,?,?,?,?)", batch)
            c.commit()
        print("build-i2i: %d 首 × top%d 相似关联写入 item_sims" % (n, topk))
    finally:
        c.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--track", type=str, default="")
    ap.add_argument("--force", action="store_true",
                    help="全量强制重算 librosa 特征（忽略已分析，恢复被 MERT 覆盖的 features/raw_vec）")
    ap.add_argument("--refresh-stats", action="store_true")
    ap.add_argument("--build-i2i", action="store_true")
    args = ap.parse_args()

    if args.build_i2i:
        build_i2i()
        return

    if args.refresh_stats:
        c = db()
        try:
            rows = c.execute("SELECT track_id, raw_vec FROM audio_features WHERE raw_vec IS NOT NULL AND raw_vec!=''").fetchall()
            if len(rows) < 5:
                print("refresh-stats: 数据不足（<5 首带 raw_vec），跳过")
                return
            arr = np.array([json.loads(r[1]) for r in rows], dtype=float)
            mean = arr.mean(axis=0)
            std = arr.std(axis=0)
            std[std < 1e-6] = 1.0
            _STATS_CACHE = (mean, std)   # 让 normalize_vec 用新统计
            updated = 0
            for tid, raw_s in rows:
                try:
                    raw = np.array(json.loads(raw_s), dtype=float)
                    vec = normalize_vec(raw, _STATS_CACHE)
                    c.execute("UPDATE audio_features SET vec=? WHERE track_id=?",
                              (json.dumps(vec), tid))
                    updated += 1
                except Exception:
                    pass
            c.commit()
            print("refresh-stats: %d 首全部按最新分布重归一化完成" % updated)
        finally:
            c.close()
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
    c = db()
    try:
        for i, tid in enumerate(ids, 1):
            tid2, ok, msg = analyze_track(tid, force=args.force)
            if msg == "already":
                skip_n += 1
            elif ok:
                ok_n += 1
            else:
                fail_n += 1
                print("[%d/%d] %s FAIL %s" % (i, total, tid, msg))
            if i % 25 == 0 or i == total:
                el = time.time() - t0
                print("[%d/%d] ok=%d skip=%d fail=%d 用时 %.1fs" % (i, total, ok_n, skip_n, fail_n, el))
    finally:
        c.close()
    print("完成：总数 %d，成功 %d，跳过 %d，失败 %d" % (total, ok_n, skip_n, fail_n))


if __name__ == "__main__":
    main()
