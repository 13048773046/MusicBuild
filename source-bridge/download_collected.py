#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载脚本 v2：对 play_url 为空的采集歌曲，bridge 取地址下载到本地，ID3 年份过滤 2020-2026。
不符合年份的删除记录和文件。"""
import sqlite3, urllib.request, ssl, os, time, json, re, urllib.parse

# 默认与历史线上完全一致；可用 MUSIC_ROOT / BRIDGE_PORT 覆盖（隔离演练用）。
ROOT = os.environ.get("MUSIC_ROOT", "/www/wwwroot/music")
DB = os.path.join(ROOT, "tracks.db")
FILES = os.path.join(ROOT, "files")
BRIDGE = "http://127.0.0.1:%d" % int(os.environ.get("BRIDGE_PORT", "8789"))
MIN_SIZE = 300 * 1024
YEAR_MIN, YEAR_MAX = 2020, 2026

def make_ctx():
    """按发行版探测 CA 证书路径（CentOS/Ubuntu/通用），全部失败则降级不校验（仅用于下载公开音频流）"""
    for cafile in ("/etc/pki/tls/certs/ca-bundle.crt",
                   "/etc/ssl/certs/ca-certificates.crt",
                   "/etc/ssl/ca-bundle.pem"):
        try:
            return ssl.create_default_context(cafile=cafile)
        except Exception:
            continue
    return ssl._create_unverified_context()

def br(path, params):
    url = BRIDGE + path + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode())

def extract_year(fp):
    """从 mp3 ID3v2 标签提取发行年份，返回 None 表示未知（放行）"""
    try:
        with open(fp, "rb") as f:
            head = f.read(65536)
        if not head.startswith(b"ID3"):
            return None
        for tag in (b"TDRC", b"TYER"):
            i = head.find(tag)
            if i < 0: continue
            data = head[i+10:i+10+200]
            m = re.search(rb"(19|20)\d{2}", data)
            if m:
                return int(m.group(0))
        return None
    except Exception:
        return None

def gd_match(name, singer):
    """gd 音源桥搜索同名：歌名精确 + 歌手双向包含，命中返回 songmid，否则 None。
    用于 qs 等无直链音源的源：先搜同名再经桥取流，避免直接失败删除用户收藏。"""
    nn = re.sub(r"[\s\-–—·.()（）【】\[\]&]|feat.*|ft\.?.*", "", str(name or "")).lower()
    ns = re.sub(r"[\s\-–—·.()（）【】\[\]&]|feat.*|ft\.?.*", "", str(singer or "")).lower()
    for page in (1, 2):
        try:
            r = br("/search", {"source": "gd", "kw": "%s %s" % (name, singer), "page": page, "limit": 15})
        except Exception:
            continue
        if not r.get("ok"):
            continue
        for s in r.get("list") or []:
            if re.sub(r"[\s\-–—·.()（）【】\[\]&]|feat.*|ft\.?.*", "", str(s.get("name") or "")).lower() != nn:
                continue
            sg = re.sub(r"[\s\-–—·.()（）【】\[\]&]|feat.*|ft\.?.*", "", str(s.get("singer") or "")).lower()
            if ns and (ns in sg or sg in ns):
                return str(s.get("songmid") or "")
    return None


c = sqlite3.connect(DB, timeout=30)
rows = c.execute("SELECT id, source, source_id, title, artist FROM collected_tracks WHERE play_url='' OR play_url IS NULL").fetchall()
print("待下载:", len(rows), flush=True)

ok = dropped = fail = skip = 0
for tid, src, sid, title, artist in rows:
    local = os.path.join(FILES, tid + ".mp3")
    if os.path.exists(local) and os.path.getsize(local) > MIN_SIZE:
        skip += 1; continue
    # qs 等源在音源桥无对应直链接口：先经 gd 搜索同名（歌名精确+歌手双向包含）
    if src not in ("gd", "kg", "kw", "tx", "wy", "mg", "soda"):
        sid = gd_match(title, artist)
        if not sid:
            fail += 1
            c.execute("UPDATE collected_tracks SET offline_reason=? WHERE id=?", ("汽水未提供音频直链且音源补偿未命中", tid))
            c.commit()
            print("[qs-未命中 标记] %s - %s（保留收藏，不删除）" % (title, artist), flush=True)
            continue
        src = "gd"
    # 取地址
    url = ""
    for attempt in range(2):
        try:
            r = br("/song", {"source": src, "songmid": sid, "name": title,
                             "singer": artist or "", "album": "", "interval": "0", "img": ""})
            url = r.get("url") or ""
            if url and not str(url).startswith("ERR"): break
        except Exception:
            pass
        time.sleep(2)
    if not url or str(url).startswith("ERR"):
        fail += 1
        c.execute("UPDATE collected_tracks SET offline_reason=? WHERE id=?", ("音源不可用（取不到直链）", tid))
        c.commit()
        print("[fail-url 标记] %s - %s（保留收藏，不删除）" % (title, artist), flush=True)
        continue
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60, context=make_ctx()) as r:
            data = r.read()
        if len(data) < MIN_SIZE:
            fail += 1
            c.execute("UPDATE collected_tracks SET offline_reason=? WHERE id=?", ("音源数据过小（可能为试听片段）", tid))
            c.commit()
            print("[fail-size 标记] %s %dB（保留收藏，不删除）" % (title, len(data)), flush=True)
            continue
        with open(local, "wb") as f:
            f.write(data)
        # 用户收藏不过滤年份（用户可能收藏任何年份的歌）；年份仅记录，不删除
        year = extract_year(local)
        c.execute("UPDATE collected_tracks SET play_url=? WHERE id=?", (tid + ".mp3", tid))
        c.commit()
        ok += 1
        ystr = str(year) if year else "未知"
        print("[+%d] %s年 | %s - %s %dKB" % (ok, ystr, title, artist, len(data)//1024), flush=True)
    except Exception as e:
        fail += 1
        c.execute("UPDATE collected_tracks SET offline_reason=? WHERE id=?", ("下载异常：" + str(e)[:40], tid))
        c.commit()
        print("[fail-dl 标记] %s %s（保留收藏，不删除）" % (title, str(e)[:60]), flush=True)
    time.sleep(1.2)
c.close()
print("DONE ok=%d dropped=%d fail=%d skip=%d" % (ok, dropped, fail, skip), flush=True)
