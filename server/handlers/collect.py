# -*- coding: utf-8 -*-
"""P2-7：collect 域路由（mixin，由 server/handler.py 的 H 类继承）。
拆分自 handler.py 的 do_GET 顶层分支，逻辑与路由条件保持原样，
仅把分支内"裸 return"改为 return True（表示已处理，返回给分发调用链）。"""
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
from server.ws import *
from server.recommend import *
from server.qishui import *
from server.fanqie import *
from server.media import *
from server.ai import *
from server._priv import *
from server.config import *
from server.database import db
from server.common import *
from server.ws import *
from server._priv import *
from server.recommend import *
from server.qishui import *
from server.media import *
from server.ai import *


# —— 汽水/番茄解析缺失补偿：经音源桥(gd)按歌名+歌手搜同名 ——
_QS_BRIDGE = "http://127.0.0.1:8789"


def _qs_norm_name(s):
    """归一化歌名/歌手，用于跨源同名匹配（与 collect_v3 同规则）"""
    return re.sub(r"[\s\-–—·.()（）【】\[\]&]|feat.*|ft\.?.*", "", str(s or "")).lower()


def _bridge_gd_search(name, singer):
    """gd 音源桥搜索同名歌曲。
    返回 (歌手双向包含的命中, 仅歌名相同的兜底) 或 (None, None)；
    音频补偿必须用命中（避免错误音频入库），封面可用兜底（同名即可）。"""
    nn, ns = _qs_norm_name(name), _qs_norm_name(singer)
    hit, fb = None, None
    for page in (1, 2):
        try:
            u = _QS_BRIDGE + "/search?source=gd&page=%d&limit=15&kw=%s" % (
                page, urllib.parse.quote("%s %s" % (name, singer)))
            with urllib.request.urlopen(u, timeout=25) as r:
                data = json.loads(r.read().decode())
        except Exception:
            time.sleep(0.5)
            continue
        if not data.get("ok"):
            time.sleep(0.5)
            continue
        for s in data.get("list") or []:
            if _qs_norm_name(s.get("name") or "") != nn:
                continue
            item = {"songmid": str(s.get("songmid") or ""), "singer": str(s.get("singer") or ""),
                    "album": str(s.get("albumName") or ""), "img": str(s.get("img") or "")}
            sg = _qs_norm_name(item["singer"])
            if ns and (ns in sg or sg in ns):
                hit = item
                break
            if fb is None and item["img"].startswith("http"):
                fb = item
        if hit:
            break
        time.sleep(0.2)
    return hit, fb


def _bridge_song_url(source, songmid, name, singer, album):
    """经音源桥取音频直链（/song），失败返回空串"""
    try:
        u = _QS_BRIDGE + "/song?source=%s&songmid=%s&name=%s&singer=%s&album=%s&interval=0&img=" % (
            urllib.parse.quote(source), urllib.parse.quote(songmid),
            urllib.parse.quote(name), urllib.parse.quote(singer), urllib.parse.quote(album or ""))
        with urllib.request.urlopen(u, timeout=60) as r:
            info = json.loads(r.read().decode())
        uu = str(info.get("url") or "")
        if uu.startswith("http"):
            return uu
    except Exception:
        pass
    return ""


class CollectMixin:

    @staticmethod
    def _parse_share_url(url):
        """按域名分发分享链接解析：番茄音乐（novelfm）→ fanqie；汽水/抖音 → qishui。
        返回 (元数据 dict, 平台标记 fq/qs)"""
        if "novelfm.com" in url:
            return _fanqie_parse_share(url), "fq"
        return _qishui_parse_share(url), "qs"

    def _h_get_collect(self, u):
        if u.path == "/collect.html":
            html = _render_template("collect.html")
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return True
        if u.path in ("/api/collect/qs-status", "/api/collect/qs-qr", "/api/collect/qs-poll",
                      "/api/collect/qs-sms-send", "/api/collect/qs-sms-login",
                      "/api/collect/qs-cookie-import"):
            import urllib.request as _ur
            if u.path == "/api/collect/qs-poll":
                _tk = (urllib.parse.parse_qs(u.query).get("token") or [""])[0]
                _bp = "/qs/poll?token=" + urllib.parse.quote(_tk)
            elif u.path == "/api/collect/qs-sms-send":
                _mb = (urllib.parse.parse_qs(u.query).get("mobile") or [""])[0]
                _bp = "/qs/sms/send?mobile=" + urllib.parse.quote(_mb)
            elif u.path == "/api/collect/qs-sms-login":
                _ps = urllib.parse.parse_qs(u.query)
                _mb = (_ps.get("mobile") or [""])[0]
                _cd = (_ps.get("code") or [""])[0]
                _bp = "/qs/sms/login?mobile=" + urllib.parse.quote(_mb) + "&code=" + urllib.parse.quote(_cd)
            elif u.path == "/api/collect/qs-cookie-import":
                _ck = (urllib.parse.parse_qs(u.query).get("cookie") or [""])[0]
                _bp = "/qs/cookie/import?cookie=" + urllib.parse.quote(_ck)
            elif u.path.endswith("qs-status"):
                _bp = "/qs/status"
            else:
                _bp = "/qs/qr"
            try:
                with _ur.urlopen("http://127.0.0.1:8789" + _bp, timeout=15) as _r:
                    _qsd = json.loads(_r.read().decode())
                self._json(200, _qsd)
            except Exception as _e:
                self._json(200, {"ok": False, "err": "汽水音乐服务异常:" + str(_e)})
            return True
        if u.path == "/api/collect/status":
            import subprocess
            try:
                out = subprocess.check_output(["pgrep", "-f", "collect_v3.py"])
                is_run = bool(out.strip())
            except Exception:
                is_run = False
            c = db()
            total = c.execute("SELECT COUNT(*) FROM collected_tracks").fetchone()[0]
            recent = c.execute("SELECT title, artist FROM collected_tracks ORDER BY created DESC LIMIT 50").fetchall()
            c.close()
            run_data = {"started": None, "collected": []}
            try:
                with open("/www/wwwroot/music/source-bridge/collect_run.json", "r", encoding="utf-8") as _rf:
                    run_data = json.load(_rf)
            except Exception:
                pass
            if not isinstance(run_data, dict):
                run_data = {"started": None, "collected": []}
            run_songs = run_data.get("collected") or []
            tgt, st_total = None, total
            try:
                with open("/www/wwwroot/music/source-bridge/collect_target.json", "r", encoding="utf-8") as _tf:
                    _tj = json.load(_tf)
                if isinstance(_tj, dict):
                    tgt = int(_tj.get("target") or 0)
                    st_total = int(_tj.get("start_total") or total)
            except Exception:
                pass
            completed = max(0, total - st_total) if tgt is not None else 0
            achieved = (tgt is not None) and (completed >= tgt)
            self._json(200, {"ok": True, "running": is_run, "total": total,
                              "recent": [{"title": r[0], "artist": r[1] or ""} for r in recent],
                              "run_count": len(run_songs),
                              "run_skipped": run_data.get("skipped", 0),
                              "run_songs": [{"title": str(s.get("title", "")), "artist": str(s.get("artist", "") or ""), "audio": bool(s.get("audio"))} for s in run_songs],
                              "started": run_data.get("started"),
                              "target": tgt, "completed": completed,
                              "remaining": max(0, (tgt - completed) if tgt is not None else 0),
                              "achieved": achieved})
            return True
        if u.path == "/api/collect/search":
            q = urllib.parse.parse_qs(u.query)
            src_p = (q.get("source") or ["wy"])[0]
            kw_p = (q.get("kw") or [""])[0].strip()
            page_p = (q.get("page") or ["1"])[0]
            limit_p = (q.get("limit") or ["15"])[0]
            if not kw_p:
                self._json(200, {"ok": False, "err": "kw required"}); return True
            try:
                br_url = "http://127.0.0.1:8789/search?source=" + urllib.parse.quote(src_p) + \
                         "&kw=" + urllib.parse.quote(kw_p) + "&page=" + urllib.parse.quote(page_p) + \
                         "&limit=" + urllib.parse.quote(limit_p)
                with urllib.request.urlopen(br_url, timeout=25) as _r:
                    _data = json.loads(_r.read().decode())
                self._json(200, _data); return True
            except Exception as e:
                self._json(200, {"ok": False, "err": str(e)}); return True
        if u.path == "/api/collect/parse-share":
            q = urllib.parse.parse_qs(u.query)
            share_url = (q.get("url") or [""])[0].strip()
            if not share_url:
                self._json(200, {"ok": False, "err": "url required"}); return True
            if not rate_limit(self, "collect", extra="parse_share"):
                self._err(429, "请求过于频繁，请稍后再试"); return True
            try:
                m, plat = self._parse_share_url(share_url)
                # 明示解析侧缺失：分享页未下发封面/音频直链（匿名抓取被风控降级时常见）
                cover_missing = not bool(m.get("cover") or m.get("cover_original"))
                audio_missing = not str(m.get("audio_url") or "").startswith("http")
                # 注：不在此处补偿封面——gd 音源桥返回的封面 URL 为过期签名（实测 404），
                # 补偿只会给前端死链；分享页未下发封面时保持明示标记，由 H5 提示 + 登录态根治。
                m["cover_missing"] = cover_missing
                m["audio_missing"] = audio_missing
                # 与 from-share 同规则的入库 ID：md5(plat:track_id)，供前端补传封面使用
                m["_tid"] = hashlib.md5((plat + ":" + m["track_id"]).encode()).hexdigest()
                c = db()
                dup = c.execute("SELECT title FROM collected_tracks WHERE source=? AND source_id=?", (plat, m["track_id"])).fetchone()
                c.close()
                self._json(200, {"ok": True, "track": m, "exists": bool(dup), "exists_title": (dup[0] if dup else "")})
                return True
            except Exception as e:
                self._json(200, {"ok": False, "err": "解析失败：" + str(e)}); return True
        if u.path == "/api/collect/list":
            if not rate_limit(self, "collect"):
                self._err(429, "请求过于频繁，请稍后再试")
                return True
            qs = urllib.parse.parse_qs(u.query)
            page = max(1, int(qs.get("page", ["1"])[0] or 1))
            limit = min(50, max(1, int(qs.get("limit", ["20"])[0] or 20)))
            keyword = (qs.get("q", [""])[0] or "").strip()
            c = db()
            where = ""
            args = []
            if keyword:
                like = "%" + keyword + "%"
                where = " WHERE title LIKE ? OR artist LIKE ? OR album LIKE ?"
                args = [like, like, like]
            total = c.execute("SELECT COUNT(*) FROM collected_tracks" + where, args).fetchone()[0]
            rows = c.execute(
                "SELECT id,title,artist,album,cover,duration,source,source_id,play_url,created,user_id,play_count,like_count,share_count,lrc FROM collected_tracks"
                + where + " ORDER BY created DESC LIMIT ? OFFSET ?",
                args + [limit, (page - 1) * limit]).fetchall()
            c.close()
            self._json(200, {"total": total, "page": page, "limit": limit,
                             "list": [{"id": r[0], "title": r[1], "artist": r[2], "album": r[3],
                                       "cover": r[4], "dur": r[5] or 0, "durText": _fmt_dur(r[5] or 0),
                                       "source": r[6], "sourceId": r[7], "playUrl": r[8],
                                       "created": r[9], "userId": r[10] or "",
                                       "playCount": r[11] or 0, "likeCount": r[12] or 0,
                                       "shareCount": r[13] or 0, "hasLyric": bool(r[14])} for r in rows]})
            return True
        return False

    def _h_post_collect(self, u):
        length = int(self.headers.get("Content-Length", 0))
        if u.path == "/api/collect/start":
            import subprocess
            try:
                subprocess.check_output(["pgrep", "-f", "collect_v3.py"])
                self._json(200, {"ok": True, "running": True, "msg": "已在采集中"})
                return True
            except Exception:
                pass
            # 解析目标参数（表单 body 优先，其次 query）
            try:
                raw = self.rfile.read(min(length, 4096)) if length > 0 else b""
                qs = raw.decode("utf-8") or u.query
                _params = urllib.parse.parse_qs(qs)
            except Exception:
                _params = {}
            new_tgt = None
            try:
                if _params.get("target"):
                    _t = int(str(_params["target"][0]).strip())
                    if _t > 0: new_tgt = _t
            except Exception:
                new_tgt = None
            c = db()
            cur = c.execute("SELECT COUNT(*) FROM collected_tracks").fetchone()[0]
            c.close()
            tgt_file = "/www/wwwroot/music/source-bridge/collect_target.json"
            tgt, start_total = None, cur
            try:
                with open(tgt_file, "r", encoding="utf-8") as _tf:
                    _tj = json.load(_tf)
                if isinstance(_tj, dict):
                    tgt = int(_tj.get("target") or 0)
                    start_total = int(_tj.get("start_total") or cur)
            except Exception:
                pass
            # 解析新参数：平台/模式/关键词
            _VALID_PLATS = ("kw","wy","kg","tx","gd","qs","soda","gm_migu","gm_fivesing",
                            "gm_joox","gm_kugou","gm_kuwo","gm_qq","dy","wy_hot","qq_hot")
            _plats = [x for x in str(_params.get("platforms", ["gd,soda"])[0]).split(",") if x in _VALID_PLATS]
            if not _plats: _plats = ["gd","soda"]
            _mode = str(_params.get("mode", ["search"])[0]).strip()
            if _mode not in ("search","hot"): _mode = "search"
            _kws = [x for x in str(_params.get("keywords", [""])[0]).split(",") if x.strip()]
            if new_tgt is not None:
                # 重设目标：上次目标未达成则延续起点（已完成数带入新目标）；已达成/无目标则从当前重新累计
                prev_done = (tgt is not None) and (cur - start_total >= tgt)
                # 清理/删歌后总数可能低于旧基线，此时基线失效，从当前重新累计
                if cur < start_total:
                    base = cur
                else:
                    base = start_total if (not prev_done and tgt is not None) else cur
                tgt, start_total = new_tgt, base
                try:
                    with open(tgt_file, "w", encoding="utf-8") as _tf:
                        json.dump({"target": tgt, "start_total": start_total, "updated": time.time(),
                                   "platforms": _plats, "mode": _mode, "keywords": _kws}, _tf)
                except Exception:
                    pass
            else:
                # 未设置目标：沿用上次目标；无目标/已达成/基线失效则开启新一轮
                done = (tgt is not None) and (cur - start_total >= tgt)
                if tgt is None or done or cur < start_total:
                    tgt, start_total = 500, cur
                # 把当前平台/模式/关键词刷进配置（无论是否重设目标）
                try:
                    with open(tgt_file, "r", encoding="utf-8") as _tf:
                        _old = json.load(_tf)
                    if not isinstance(_old, dict): _old = {}
                except Exception:
                    _old = {}
                _old["target"] = tgt
                _old["start_total"] = start_total
                _old["updated"] = time.time()
                _old["platforms"] = _plats
                _old["mode"] = _mode
                _old["keywords"] = _kws
                try:
                    with open(tgt_file, "w", encoding="utf-8") as _tf:
                        json.dump(_old, _tf)
                except Exception:
                    pass
            remaining = tgt - (cur - start_total)
            if remaining <= 0:
                # 已完成数已覆盖新目标：视为已达成，不启动采集
                try:
                    with open("/www/wwwroot/music/source-bridge/collect_run.json", "w", encoding="utf-8") as _sf:
                        json.dump({"started": None, "collected": []}, _sf)
                except Exception:
                    pass
                self._json(200, {"ok": True, "running": False,
                                 "target": tgt, "start_total": start_total,
                                 "completed": max(0, cur - start_total), "remaining": 0,
                                 "achieved": True, "msg": "目标已完成"})
                return True
            try:
                with open("/www/wwwroot/music/source-bridge/collect_run.json", "w", encoding="utf-8") as _sf:
                    json.dump({"started": None, "collected": []}, _sf)
            except Exception:
                pass
            logf = open("/www/wwwroot/music/source-bridge/collect_v3.log", "ab")
            subprocess.Popen(["python3", "/www/wwwroot/music/source-bridge/collect_v3.py", "--target", str(remaining)],
                             stdout=logf, stderr=subprocess.STDOUT,
                             cwd="/www/wwwroot/music/source-bridge", stdin=subprocess.DEVNULL)
            self._json(200, {"ok": True, "running": True,
                             "target": tgt, "start_total": start_total,
                             "completed": max(0, cur - start_total), "remaining": max(0, remaining)})
            return True
        if u.path == "/api/collect/stop":
            import subprocess
            subprocess.run(["pkill", "-f", "collect_v3.py"])
            time.sleep(0.5)
            self._json(200, {"ok": True, "running": False})
            return True
        if u.path == "/api/collect/one":
            try:
                raw = self.rfile.read(min(length, 65536)) if length > 0 else b""
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._err(400, "bad json"); return True
            _src = (data.get("source") or "wy").strip()
            _mid = (data.get("songmid") or "").strip()
            _name = (data.get("name") or "").strip()
            if not _mid or not _name:
                self._err(400, "songmid/name required"); return True
            _singer = (data.get("singer") or "").strip()
            _album = (data.get("album") or "").strip()
            _interval = data.get("interval") or ""
            _img = data.get("img") or ""
            c = db()
            _dup = c.execute("SELECT title FROM collected_tracks WHERE source=? AND source_id=?", (_src, _mid)).fetchone()
            if _dup:
                c.close(); self._json(200, {"ok": False, "err": "已存在：" + _dup[0]}); return True
            _tid = hashlib.md5((_src + ":" + _mid).encode()).hexdigest()
            try:
                _br = ("http://127.0.0.1:8789/song?source=" + urllib.parse.quote(_src) +
                       "&songmid=" + urllib.parse.quote(_mid) +
                       "&name=" + urllib.parse.quote(_name) +
                       "&singer=" + urllib.parse.quote(_singer) +
                       "&album=" + urllib.parse.quote(_album) +
                       "&interval=" + urllib.parse.quote(str(_interval)) +
                       "&img=" + urllib.parse.quote(_img))
                with urllib.request.urlopen(_br, timeout=60) as _r:
                    _info = json.loads(_r.read().decode())
                if not _info.get("ok"):
                    c.close(); self._json(200, {"ok": False, "err": "音源解析失败"}); return True
                _url = str(_info.get("url") or "")
                _lrc = _info.get("lyric") or ""
                _pic = _info.get("pic") or _img
            except Exception as e:
                c.close(); self._json(200, {"ok": False, "err": "音源错误:" + str(e)}); return True
            # 下封面
            _cover = ""
            if _pic and str(_pic).startswith("http"):
                try:
                    _req = urllib.request.Request(_pic, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(_req, timeout=15, context=ssl._create_unverified_context()) as _r:
                        _imgdata = _r.read()
                    if len(_imgdata) > 500:
                        with open(os.path.join(FILES, _tid + "_cover.jpg"), "wb") as _f:
                            _f.write(_imgdata)
                        _cover = _tid + "_cover.jpg"
                except Exception:
                    pass
            # 下音频
            _audio, _asize, _aok = "", 0, False
            if _url.startswith("http"):
                try:
                    _req = urllib.request.Request(_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(_req, timeout=60, context=ssl._create_unverified_context()) as _r:
                        _adata = _r.read()
                    if len(_adata) >= 300 * 1024:
                        with open(os.path.join(FILES, _tid + ".mp3"), "wb") as _f:
                            _f.write(_adata)
                        _audio = _tid + ".mp3"; _asize = len(_adata); _aok = True
                except Exception:
                    pass
            _m = re.match(r"^(\d+):(\d{2})$", str(_interval or "").strip())
            _dur = int(_m.group(1)) * 60 + int(_m.group(2)) if _m else 0
            c.execute(
                """INSERT OR IGNORE INTO collected_tracks
                   (id,title,artist,album,cover,duration,source,source_id,play_url,size,created,user_id,lrc)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (_tid, _name, _singer, _album, _cover, _dur, _src, _mid,
                 _audio, _asize, time.time(),
                 "a334105a8a2f4401a19001ee4ce66929", _lrc))
            c.commit(); c.close()
            self._json(200, {"ok": True, "title": _name, "artist": _singer, "audio": _aok}); return True
        if u.path == "/api/collect/add":
            if not rate_limit(self, "collect_add"):
                self._err(429, "请求过于频繁，请稍后再试")
                return True
            if self.headers.get("X-Collect-Key", "").strip() != COLLECT_KEY:
                self._err(403, "forbidden")
                return True
            try:
                raw = self.rfile.read(min(length, 2 * 1024 * 1024)) if length > 0 else b""
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._err(400, "bad json")
                return True
            items = data.get("items") or []
            if not isinstance(items, list) or not items or len(items) > 50:
                self._err(400, "items must be 1..50")
                return True
            # 自定义关键词标签（用户在采集H5里添加的关键词，逗号分隔）
            custom_genre = (data.get("keywords") or "").strip()
            c = db()
            # 归属：数据库中的唯一账号（仅当 users 表只有 1 个用户时）
            urows = c.execute("SELECT id FROM users ORDER BY created LIMIT 2").fetchall()
            owner = urows[0][0] if len(urows) == 1 else ""
            now = time.time()
            ins = skip = 0
            for it in items:
                title = (it.get("title") or "").strip()
                src = (it.get("source") or "").strip()
                sid = (it.get("source_id") or "").strip()
                if not title or not src or not sid:
                    continue
                tid = hashlib.md5(("%s:%s" % (src, sid)).encode()).hexdigest()
                # 封面：若为第三方 http 链接则下载到本地 files/{tid}_cover.jpg（手机端可直接加载），
                # 下载失败则存空（App 显示占位封面）；已入库歌曲跳过下载。
                cover_val = (it.get("cover") or "").strip()
                local_cover = ""
                if cover_val.startswith("http"):
                    try:
                        req = urllib.request.Request(cover_val, headers={"User-Agent": "Mozilla/5.0"})
                        with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx()) as r:
                            img = r.read()
                        if img and len(img) >= 500:
                            with open(os.path.join(FILES, tid + "_cover.jpg"), "wb") as f:
                                f.write(img)
                            local_cover = tid + "_cover.jpg"
                    except Exception:
                        local_cover = ""
                elif cover_val:
                    local_cover = cover_val   # 已是本地文件名
                # 播放链接不再采集入库（外部第三方直链时效短且手机端不可用）
                cur = c.execute(
                    """INSERT OR IGNORE INTO collected_tracks
                       (id,title,artist,album,cover,duration,source,source_id,play_url,size,created,user_id,lrc,genre)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (tid, title, (it.get("artist") or "").strip(), (it.get("album") or "").strip(),
                     local_cover, float(it.get("duration") or 0), src, sid,
                     "", float(it.get("size") or 0), now, owner, it.get("lrc") or "",
                     custom_genre))
                if cur.rowcount and cur.rowcount > 0:
                    ins += 1
                else:
                    skip += 1
            c.commit()
            # 实时数据事件：采集入库 → 全局歌曲列表更新（广播在线用户）
            if ins:
                ws_broadcast({"kind": "event", "event": "track_list", "reason": "collect"})
            c.close()
            self._json(200, {"ok": True, "inserted": ins, "skipped": skip, "owner": owner})
            return True
        if u.path == "/api/collect/from-share":
            if not rate_limit(self, "collect_add", extra="from_share"):
                self._err(429, "请求过于频繁，请稍后再试")
                return True
            try:
                raw = self.rfile.read(min(length, 65536)) if length > 0 else b""
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._err(400, "bad json"); return True
            share_url = (data.get("url") or "").strip()
            if not share_url:
                self._err(400, "url required"); return True
            # 归属账号：默认账号 3065751692（夏梓璇）；可传 user_id 覆盖（须为有效用户）
            owner_id = (data.get("user_id") or "").strip() or "a334105a8a2f4401a19001ee4ce66929"
            try:
                m, plat = self._parse_share_url(share_url)
            except Exception as e:
                self._json(200, {"ok": False, "err": "解析失败：" + str(e)}); return True
            if not m.get("track_id"):
                self._json(200, {"ok": False, "err": "未解析到歌曲 ID"}); return True
            c = db()
            if not c.execute("SELECT 1 FROM users WHERE id=?", (owner_id,)).fetchone():
                c.close()
                self._json(200, {"ok": False, "err": "归属账号不存在"}); return True
            tid = hashlib.md5((plat + ":" + m["track_id"]).encode()).hexdigest()
            dup = c.execute("SELECT title FROM collected_tracks WHERE id=?", (tid,)).fetchone()
            if dup:
                acc = c.execute("SELECT account,name FROM users WHERE id=?", (owner_id,)).fetchone()
                c.close()
                self._json(200, {"ok": True, "inserted": 0, "title": m["title"], "artist": m["artist"],
                                 "owner_account": acc[0] if acc else owner_id, "cover": False,
                                 "audio": False, "cover_missing": False, "audio_missing": False,
                                 "lyric_len": 0, "genre": m.get("genre") or ""})
                return True
            # 下载封面：优先原图（~c5.jpg，3000x3000），失败回退缩略图
            local_cover = ""
            for cov in (m.get("cover_original"), m.get("cover")):
                if cov and str(cov).startswith("http"):
                    try:
                        req = urllib.request.Request(cov, headers={"User-Agent": QISHUI_SHARE_UA})
                        with urllib.request.urlopen(req, timeout=20, context=_ssl_ctx()) as r:
                            img = r.read()
                        if img and len(img) >= 500 and image_magic_ok(img, ".jpg"):
                            with open(os.path.join(FILES, tid + "_cover.jpg"), "wb") as f:
                                f.write(img)
                            local_cover = tid + "_cover.jpg"
                            break
                    except Exception:
                        continue
            # 封面缺失：不补偿——gd 音源桥封面为过期签名 URL（实测 404），补偿只会入库死链。
            # 保持 cover_missing 标记，由 H5 明示提示 + 汽水登录态根治（登录后分享页下发完整 coverURL）。
            cover_missing = not bool(local_cover)
            # 下载音频：M4A 直链 → 存 {tid}.m4a（stream 按扩展名返回 audio/x-m4a）
            local_audio, audio_size = "", 0
            if m.get("audio_url") and str(m["audio_url"]).startswith("http"):
                try:
                    req = urllib.request.Request(m["audio_url"], headers={"User-Agent": QISHUI_SHARE_UA})
                    with urllib.request.urlopen(req, timeout=120, context=_ssl_ctx()) as r:
                        adata = r.read()
                    if len(adata) >= 300 * 1024:
                        with open(os.path.join(FILES, tid + ".m4a"), "wb") as f:
                            f.write(adata)
                        local_audio = tid + ".m4a"
                        audio_size = len(adata)
                except Exception:
                    pass
            # 音频缺失/下载失败 → 音源桥严格匹配（歌名精确+歌手双向包含）补偿，避免错误音频入库
            audio_missing = not bool(local_audio)
            if audio_missing and _t and _t != "-":
                hit, fb = _bridge_gd_search(_t, m["artist"] or "")
                if hit and hit.get("songmid"):
                    au = _bridge_song_url("gd", hit["songmid"], m["title"], hit["singer"], hit["album"])
                    if au:
                        try:
                            req = urllib.request.Request(au, headers={"User-Agent": "Mozilla/5.0"})
                            with urllib.request.urlopen(req, timeout=120, context=_ssl_ctx()) as r:
                                adata = r.read()
                            if len(adata) >= 300 * 1024:
                                with open(os.path.join(FILES, tid + ".mp3"), "wb") as f:
                                    f.write(adata)
                                local_audio = tid + ".mp3"
                                audio_size = len(adata)
                        except Exception:
                            pass
            audio_missing = not bool(local_audio)
            genre = m.get("genre") or ""
            c.execute(
                """INSERT OR IGNORE INTO collected_tracks
                   (id,title,artist,album,cover,duration,source,source_id,play_url,size,created,user_id,lrc,genre)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (tid, m["title"], m["artist"], m["album"], local_cover, m["duration"],
                 plat, m["track_id"], local_audio, audio_size, time.time(), owner_id,
                 m["lyric_lrc"], genre))
            c.commit()
            acc = c.execute("SELECT account,name FROM users WHERE id=?", (owner_id,)).fetchone()
            c.close()
            if local_audio:
                ws_broadcast({"kind": "event", "event": "track_list", "reason": "collect"})
            self._json(200, {"ok": True, "inserted": 1, "title": m["title"], "artist": m["artist"],
                             "owner_account": acc[0] if acc else owner_id, "cover": bool(local_cover),
                             "audio": bool(local_audio),
                             "cover_missing": cover_missing, "audio_missing": audio_missing,
                             "lyric_len": len(m["lyric_lrc"].splitlines()) if m["lyric_lrc"] else 0,
                             "genre": genre})
            return True
        if u.path == "/api/collect/upload-cover":
            # 番茄封面 CDN 封数据中心 IP：后端直接下载 403，由用户浏览器
            # （住宅 IP + 番茄 CDN 全开放 CORS）加载封面后 base64 上传补封面。
            if not rate_limit(self, "collect_add", extra="upload_cover"):
                self._err(429, "请求过于频繁，请稍后再试")
                return True
            try:
                raw = self.rfile.read(min(length, 128 * 1024)) if length > 0 else b""
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._err(400, "bad json"); return True
            tid = (data.get("id") or "").strip()
            b64 = (data.get("data") or "").strip()
            url_mode = (data.get("url") or "").strip()
            if not re.fullmatch(r"[0-9a-f]{32}", tid):
                self._json(200, {"ok": False, "err": "id 不合法"}); return True
            img = b""
            if url_mode.startswith("http"):
                # URL 模式：后端 UA 直接下载（douyinpic 无 CORS 头，浏览器 canvas 读不了像素）
                try:
                    req = urllib.request.Request(url_mode, headers={"User-Agent": QISHUI_SHARE_UA})
                    with urllib.request.urlopen(req, timeout=25, context=_ssl_ctx()) as r:
                        img = r.read()
                except Exception:
                    img = b""
            elif b64:
                try:
                    img = base64.b64decode(b64.split(",")[-1])
                except Exception:
                    self._json(200, {"ok": False, "err": "base64 解码失败"}); return True
            else:
                self._json(200, {"ok": False, "err": "data or url required"}); return True
            if not (img and len(img) >= 500 and image_magic_ok(img, ".jpg")):
                self._json(200, {"ok": False, "err": "图片数据无效"}); return True
            c = db()
            if not c.execute("SELECT 1 FROM collected_tracks WHERE id=?", (tid,)).fetchone():
                c.close()
                self._json(200, {"ok": False, "err": "曲目不存在"}); return True
            with open(os.path.join(FILES, tid + "_cover.jpg"), "wb") as f:
                f.write(img)
            c.execute("UPDATE collected_tracks SET cover=? WHERE id=?", (tid + "_cover.jpg", tid))
            c.commit()
            c.close()
            self._json(200, {"ok": True, "cover": True})
            return True
        return False
