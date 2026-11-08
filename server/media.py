# -*- coding: utf-8 -*-
"""流萤音乐 · 后端模块化包（由线上 server.py 机械重放，函数体零改写）。"""
import os
import json
import ssl
import sqlite3
import time
import random
import uuid
import hashlib
import hmac
import mimetypes
import re
import secrets
import logging
import socket
import struct
import base64
import threading
import difflib
import subprocess
import urllib.parse
import urllib.request
from html import escape as html_escape
from collections import deque
from http.server import BaseHTTPRequestHandler

from server.config import *
from server.database import db
from server.common import *

__all__ = ['_cover_small_lock', '_dur_mp3', '_dur_wav', '_head', 'audio_duration', 'audio_magic_ok', 'cover_small', 'detect_image_ext', 'image_magic_ok', 'low_ready', 'low_path', 'transcode_low', 'transcode_low_async']

_cover_small_lock = threading.Lock()  # 并发请求同一大图时只生成一次


def cover_small(fp, tid):
    """返回适合手机端加载的封面路径：大图压缩为 max_side JPEG 落盘缓存；失败/小图回退原图。

    采集歌曲封面多为数 MB 原图（见过 12MB），手机端直接加载要数秒；
    这里首次请求生成 600px/82 品质缩略图（约 20-60KB），之后命中缓存。
    Pillow 不可用时自动降级为原图，不影响功能。
    """
    try:
        from PIL import Image
    except Exception:
        return fp
    try:
        size = os.path.getsize(fp)
        if size <= COVER_SMALL_MIN_SIZE:
            return fp
        small = os.path.join(FILES, tid + "_cover_small.jpg")
        with _cover_small_lock:
            if os.path.exists(small) and os.path.getsize(small) > 0:
                if os.path.getmtime(small) + 2 >= os.path.getmtime(fp):
                    return small
            with Image.open(fp) as im:
                im = im.convert("RGB")
                w, h = im.size
                longest = max(w, h)
                if longest <= COVER_SMALL_MAX_SIDE:
                    return fp
                scale = COVER_SMALL_MAX_SIDE / float(longest)
                nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
                resample = getattr(Image, "Resampling", Image).LANCZOS
                im = im.resize((nw, nh), resample)
                im.save(small, "JPEG", quality=COVER_SMALL_QUALITY,
                        optimize=True, progressive=True)
        if os.path.exists(small) and os.path.getsize(small) > 0:
            return small
    except Exception:
        pass
    return fp


def _head(data, n):
    return data[:n]


def audio_magic_ok(data, ext):
    """音频文件头校验（不支持只信扩展名）。ext 已小写。"""
    if ext == ".mp3":
        return _head(data, 3) == b"ID3" or (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0)
    if ext == ".flac":
        return _head(data, 4) == b"fLaC"
    if ext == ".wav":
        return _head(data, 4) == b"RIFF"
    if ext in (".m4a", ".mp4"):
        return len(data) >= 12 and _head(data, 4) == b"ftyp"
    if ext == ".ogg":
        return _head(data, 4) == b"OggS"
    if ext == ".aac":
        return len(data) > 1 and data[0] == 0xFF and (data[1] & 0xF0) == 0xF0
    return False


def image_magic_ok(data, ext):
    if ext in (".jpg", ".jpeg"):
        return _head(data, 3) == b"\xFF\xD8\xFF"
    if ext == ".png":
        return _head(data, 8) == b"\x89PNG\r\n\x1a\n"
    if ext == ".webp":
        return _head(data, 4) == b"RIFF" and data[8:12] == b"WEBP"
    if ext == ".heic":
        # ISO BMFF box 结构：[size(4)] + [type(4)]，type="ftyp" 位于偏移 4-8
        return len(data) >= 12 and data[4:8] == b"ftyp"
    return False


def detect_image_ext(data):
    """按魔数自动识别图片真实格式，返回 (".jpg"/".png"/".webp"/".heic") 或 None。

    用于文件名扩展名与内容不一致的场景（如 iPhone HEIC 改名/伪装成 .jpg，
    或浏览器把 HEIC 报成 image/jpeg），避免误报“格式校验失败”。
    """
    for ext in (".jpg", ".png", ".webp", ".heic"):
        if image_magic_ok(data, ext):
            return ext
    return None


def _dur_wav(path):
    try:
        with open(path, "rb") as f:
            hdr = f.read(12)
            if hdr[0:4] != b"RIFF" or hdr[8:12] != b"WAVE":
                return 0.0
            byte_rate = None
            data_size = 0
            while True:
                chunk = f.read(8)
                if len(chunk) < 8:
                    break
                cid, csize = chunk[0:4], struct.unpack("<I", chunk[4:8])[0]
                if cid == b"fmt ":
                    fmt = f.read(csize)
                    if len(fmt) >= 12:
                        byte_rate = struct.unpack("<I", fmt[8:12])[0]
                elif cid == b"data":
                    data_size = csize
                    f.seek(csize, 1)
                else:
                    f.seek(csize + (csize & 1), 1)
                if byte_rate and data_size:
                    break
        if byte_rate and byte_rate > 0 and data_size > 0:
            return round(data_size / byte_rate, 2)
    except Exception:
        pass
    return 0.0


def _dur_mp3(path):
    """MP3 时长（审查 #10 优化）：
    1) 优先 ffprobe（精确、流式读头不整读文件）；
    2) ffprobe 不可用时回退帧解析，但只读文件前 8MB 采样（原实现整读 50MB+ 大文件到内存）。"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True, timeout=10)
        if r.returncode == 0 and r.stdout:
            import json as _json
            info = _json.loads(r.stdout)
            d = info.get("format", {}).get("duration")
            if d:
                return round(float(d), 2)
    except Exception:
        pass
    try:
        with open(path, "rb") as f:
            data = f.read(8 * 1024 * 1024)   # 采样前 8MB（足够定位帧头/估算平均码率）
        off = 0
        if data[0:3] == b"ID3":
            sz = data[6:10]
            id3_size = (sz[0] << 21) | (sz[1] << 14) | (sz[2] << 7) | sz[3]
            off = 10 + id3_size
        bitrates = []
        i = off
        n = len(data)
        while i + 4 <= n:
            b = data[i:i+4]
            if b[0] == 0xFF and (b[1] & 0xE0) == 0xE0:
                ver = (b[1] >> 3) & 0x03
                layer = (b[1] >> 1) & 0x03
                br_idx = (b[2] >> 4) & 0x0F
                sr_idx = (b[2] >> 2) & 0x03
                if br_idx not in (0, 15) and sr_idx != 3 and layer != 0 and ver != 1:
                    tbl = None
                    if ver == 3:
                        if layer == 3:
                            tbl = [32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448]
                        elif layer == 2:
                            tbl = [32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384]
                        else:
                            tbl = [32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448]
                    else:
                        if layer == 3:
                            tbl = [8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]
                        else:
                            tbl = [8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]
                    kbps = tbl[br_idx - 1]
                    if ver != 3:
                        kbps //= 2
                    if layer == 3:
                        frame_len = (144000 * kbps) // (44100 if ver == 3 else 22050) + 1
                    else:
                        frame_len = (72000 * kbps) // (44100 if ver == 3 else 22050) + 1
                    if frame_len > 0:
                        bitrates.append((kbps, frame_len))
                        i += frame_len
                        continue
            i += 1
        if not bitrates:
            return 0.0
        avg_kbps = sum(k for k, _ in bitrates) / len(bitrates)
        dur = (n - off) * 8.0 / (avg_kbps * 1000.0)
        return round(dur, 2)
    except Exception:
        return 0.0


def audio_duration(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".wav":
        return _dur_wav(path)
    if ext == ".mp3":
        return _dur_mp3(path)
    try:
        if ext in (".m4a", ".aac"):
            from mutagen.mp4 import MP4
            return MP4(path).info.length
        if ext == ".flac":
            from mutagen.flac import FLAC
            return FLAC(path).info.length
        if ext == ".ogg":
            from mutagen.oggvorbis import OggVorbis
            return OggVorbis(path).info.length
    except Exception:
        pass
    return 0.0


# ================= 低码率转码（懒转码 96k + 并发控制 + files_low 缓存）=================
# 背景：4 核/3.6G 服务器不支持实时转码，采用懒转码 + 落盘缓存。
# 契约（handler.py stream?br=96 / stream-prepare 调用）：
#   low_ready(tid)        -> bool      低码率文件是否就绪
#   low_path(tid)         -> str       低码率文件路径 files_low/{tid}_96.m4a
#   transcode_low(fp,tid) -> bool      同步转码（幂等：已就绪直接 True）
#   transcode_low_async(fp,tid)         后台异步转码（per-track 锁防重复 + 全局并发 2）

LOW_BITRATE = 96
# FILES_LOW 已从 server.config 导入（_cfg_path 可配置 paths.files_low）
_low_sem = threading.Semaphore(2)      # 全局并发 2（4 核适配）
_low_locks = {}                        # per-track 转码中标记
_low_locks_guard = threading.Lock()


def low_path(tid):
    return os.path.join(FILES_LOW, "%s_96.m4a" % tid)


def low_ready(tid):
    try:
        p = low_path(tid)
        return os.path.exists(p) and os.path.getsize(p) > 0
    except OSError:
        return False


def transcode_low(fp, tid):
    """同步转码 96kbps AAC-LC / 44.1kHz / stereo 到 files_low/{tid}_96.m4a。
    已就绪幂等返回 True；任何失败返回 False（调用方回退原码率流）。"""
    if low_ready(tid):
        return True
    try:
        os.makedirs(FILES_LOW, exist_ok=True)
    except OSError:
        return False
    out = low_path(tid)
    tmp = out + ".tmp"
    cmd = ["ffmpeg", "-y", "-i", fp, "-vn",
           "-ac", "2", "-ar", "44100", "-b:a", "96k",
           "-c:a", "aac", "-movflags", "+faststart", tmp]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=600)
        if r.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) <= 0:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return False
        os.replace(tmp, out)
        return True
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def transcode_low_async(fp, tid):
    """后台异步转码：per-track 锁防同曲重复 + 全局信号量并发 2，失败静默（下次请求重试）。"""
    with _low_locks_guard:
        if tid in _low_locks or low_ready(tid):
            return
        _low_locks[tid] = True

    def _job():
        try:
            with _low_sem:
                transcode_low(fp, tid)
        finally:
            with _low_locks_guard:
                _low_locks.pop(tid, None)

    threading.Thread(target=_job, daemon=True).start()
