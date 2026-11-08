# -*- coding: utf-8 -*-
"""流萤音乐 · 全景音效分离【独立进程】。

主服务（server.stems）通过 subprocess 调用本脚本，让 torch/Demucs 在独立进程运行：
处理结束后进程退出、内存完全释放，且模型崩溃不影响长驻的 Web 服务。

流程：定位源音频 → Demucs(htdemucs) 分段分离 4 轨 →
     人声单独成轨、鼓+贝斯+其他混合成伴奏 → 两条 64k opus 落 STEMS 目录 → 更新 track_stems。
用法：python3 -m server.stem_worker --track <track_id>
"""
import os
import sys
import time
import argparse
import subprocess
import traceback

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("MKL_NUM_THREADS", "2")

# server/ 的上一级即部署根，保证可 import server.*
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from server.config import (STEMS, FILES, STEM_MODEL, STEM_SEGMENT,
                           STEM_BITRATE, STEM_SR)
from server.database import db


def _source_path(tid):
    """定位曲库某首歌的本地源音频（用户上传 tracks / 采集 collected）。"""
    c = db()
    try:
        row = c.execute("SELECT fname FROM tracks WHERE id=?", (tid,)).fetchone()
        if row and row[0]:
            p = os.path.join(FILES, row[0])
            if os.path.exists(p):
                return p
        cr = c.execute("SELECT play_url FROM collected_tracks WHERE id=?", (tid,)).fetchone()
        if cr and cr[0] and not str(cr[0]).startswith("http"):
            p = os.path.join(FILES, str(cr[0]))
            if os.path.exists(p):
                return p
    finally:
        c.close()
    return None


def _set_status(tid, status, **fields):
    c = db()
    try:
        cols = "status=?, updated=?"
        vals = [status, time.time()]
        for k, v in fields.items():
            cols += ", %s=?" % k
            vals.append(v)
        c.execute("UPDATE track_stems SET %s WHERE track_id=?" % cols, vals + [tid])
        c.commit()
    finally:
        c.close()


def _encode_opus(wav, dest_path, sr, bitrate):
    """用 ffmpeg 把 float32 tensor（shape: channels×samples）编码为 opus。

    Demucs 4.1 的 save_audio 只支持 mp3/wav/flac，故走 ffmpeg：
    stdin 喂 raw float32 PCM（交错），libopus 编码；先写临时文件再原子替换。
    """
    import numpy as np
    x = wav.detach().cpu().numpy()
    channels = int(x.shape[0])
    raw = np.ascontiguousarray(
        np.transpose(x, (1, 0)).astype(np.float32)).tobytes()
    tmp = dest_path + ".part.opus"
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "f32le", "-ar", str(sr), "-ac", str(channels),
           "-i", "pipe:0",
           # libopus 仅支持 8/12/16/24/48k（44100 会报 Invalid argument），
           # 统一重采样到 48k 再编码
           "-c:a", "libopus", "-b:a", "%dk" % bitrate, "-vbr", "on",
           "-ar", "48000", tmp]
    proc = subprocess.run(cmd, input=raw, capture_output=True)
    if proc.returncode != 0 or not os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise RuntimeError("opus encode failed: " +
                           proc.stderr.decode(errors="ignore")[-200:])
    os.replace(tmp, dest_path)


def separate(tid):
    src = _source_path(tid)
    if not src:
        raise RuntimeError("no-source-audio")

    import torch
    from demucs.pretrained import get_model
    from demucs.apply import apply_model
    from demucs.audio import AudioFile

    model = get_model(STEM_MODEL)
    model.cpu()

    # Demucs 标准前处理：读入 → 去均值/归一化
    wav = AudioFile(src).read(streams=0, samplerate=model.samplerate,
                              channels=model.audio_channels)
    ref = wav.mean(0)
    wav = (wav - ref.mean()) / (ref.std() + 1e-8)

    with torch.no_grad():
        out = apply_model(model, wav[None], split=True, segment=STEM_SEGMENT,
                          overlap=0.25, progress=False)[0]

    idx = {s: i for i, s in enumerate(model.sources)}
    vocals = out[idx["vocals"]]
    # 伴奏 = 鼓 + 贝斯 + 其他（直接相加，与原曲对齐；人声+伴奏可还原原曲）
    accompaniment = out[idx["drums"]] + out[idx["bass"]] + out[idx["other"]]

    os.makedirs(STEMS, exist_ok=True)
    v_name = tid + "_vocals.opus"
    a_name = tid + "_acc.opus"
    v_path = os.path.join(STEMS, v_name)
    a_path = os.path.join(STEMS, a_name)
    _encode_opus(vocals, v_path, model.samplerate, STEM_BITRATE)
    _encode_opus(accompaniment, a_path, model.samplerate, STEM_BITRATE)
    return v_name, a_name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--track", required=True)
    args = ap.parse_args()
    tid = args.track
    t0 = time.time()
    try:
        _set_status(tid, "processing")
        v_name, a_name = separate(tid)
        _set_status(tid, "done", vocals=v_name, accompaniment=a_name, error="")
        print("OK %s %.0fs %s %s" % (tid, time.time() - t0, v_name, a_name), flush=True)
    except Exception as e:
        traceback.print_exc()
        try:
            _set_status(tid, "failed", error=str(e)[:200])
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
