# -*- coding: utf-8 -*-
"""P2-7：content 域路由（mixin，由 server/handler.py 的 H 类继承）。
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


# 每日推荐缓存：(uid, "YYYY-MM-DD") → [track_id, ...]；当天复用，隔天自然失效
_DAILY_CACHE = {}


def _rec_payload(c, ids, viewer_id, reason_map=None, route=""):
    """把歌曲 id 列表组装成与 /api/tracks 同构的响应项（推荐/相似/每日共用）。

    合并 tracks（上传）与 collected_tracks（采集）两张表，补歌手名/评论数/是否点赞，
    顺序按传入 ids。
    """
    ids = [i for i in (ids or []) if i]
    if not ids:
        return []
    reason_map = reason_map or {}
    ph = ",".join("?" * len(ids))
    out = {}
    try:
        trows = c.execute("""SELECT t.id,t.title,t.artist,t.album,t.cover,t.fname,t.size,t.dur,
                                    t.created,t.play_count,t.like_count,t.share_count,t.user_id,t.lrc,
                                    COALESCE(u.name,''),
                                    (SELECT COUNT(*) FROM comments cm WHERE cm.track_id=t.id),
                                    MAX(CASE WHEN l.user_id IS NOT NULL THEN 1 ELSE 0 END),
                                    COALESCE(t.genre,'')
                             FROM tracks t
                             LEFT JOIN users u ON u.id=t.user_id
                             LEFT JOIN likes l ON l.track_id=t.id AND l.user_id=?
                             WHERE t.id IN (%s) GROUP BY t.id""" % ph,
                           tuple([viewer_id or ""] + ids)).fetchall()
        for r in trows:
            out[r[0]] = {"id": r[0], "title": r[1], "artist": r[2], "album": r[3],
                         "cover": r[4], "fname": r[5] or "",
                         "size": int(r[6] or 0), "dur": r[7],
                         "durText": _fmt_dur(r[7]), "created": r[8],
                         "playCount": r[9] or 0, "likeCount": r[10] or 0,
                         "shareCount": r[11] or 0,
                         "userId": r[12] or "", "userName": r[14] or "",
                         "hasLyric": bool(r[13]), "genre": r[17] or "",
                         "reason": reason_map.get(r[0], route or "推荐"),
                         "recRoute": route,
                         "commentCount": r[15] or 0, "isLiked": bool(r[16])}
    except Exception:
        pass
    try:
        crows = c.execute("""SELECT id,title,artist,album,cover,duration,size,created,
                                    play_count,like_count,share_count,user_id,lrc,COALESCE(genre,'')
                             FROM collected_tracks WHERE id IN (%s)""" % ph, ids).fetchall()
    except Exception:
        crows = []
    uname_map = {}
    uids = {r[11] for r in crows if r[11]}
    if uids:
        try:
            uph = ",".join("?" * len(uids))
            for urow in c.execute("SELECT id, name FROM users WHERE id IN (%s)" % uph,
                                  tuple(uids)):
                uname_map[urow[0]] = urow[1] or ""
        except Exception:
            pass
    liked = set()
    if viewer_id:
        try:
            for r in c.execute(
                    "SELECT track_id FROM likes WHERE user_id=? AND track_id IN (%s)" % ph,
                    tuple([viewer_id] + ids)):
                liked.add(r[0])
        except Exception:
            pass
    for r in crows:
        out[r[0]] = {"id": r[0], "title": r[1], "artist": r[2], "album": r[3],
                     "cover": "1", "fname": "",
                     "size": int(r[6] or 0), "dur": r[5],
                     "durText": _fmt_dur(r[5]), "created": r[7],
                     "playCount": r[8] or 0, "likeCount": r[9] or 0,
                     "shareCount": r[10] or 0,
                     "userId": r[11] or "", "userName": uname_map.get(r[11] or "", ""),
                     "hasLyric": bool(r[12]), "genre": r[13] or "",
                     "reason": reason_map.get(r[0], route or "推荐"),
                     "recRoute": route, "commentCount": 0,
                     "isLiked": r[0] in liked}
    return [out[i] for i in ids if i in out]


class ContentMixin:

    def _share_or_auth(self, c, u, tid):
        """资源接口守卫：分享页（携带匹配该曲的 share token）或已登录用户才放行。

        防护目标：避免匿名爬虫拿到 track_id 后绕过分享页直接抓取歌词/签发音频流。
        - 分享页 JS 带 ?share={token}（share_tokens 表 token→track_id 校验）
        - App 端带 Authorization: Bearer（登录态）
        - 其余请求一律 403
        """
        q = urllib.parse.parse_qs(u.query)
        share = (q.get("share") or [""])[0]
        if share:
            try:
                row = c.execute("SELECT track_id FROM share_tokens WHERE token=?", (share,)).fetchone()
                if row and row[0] == tid:
                    return True
            except Exception:
                pass
        if resolve_token(c, self.headers.get("Authorization", "")):
            return True
        return False

    def _pay_notify_handle(self):
        """易支付异步回调（GET/POST 通用）：验签通过且 trade_status=TRADE_SUCCESS 才开通会员（source=self 模板）。"""
        cfg = _read_site_config()
        pkey = (cfg.get("pay_merchant_key") or "").strip()
        params = {}
        if self.path and "?" in self.path:
            qs_part = self.path.split("?", 1)[1]
            for k, v in urllib.parse.parse_qs(qs_part).items():
                params[k] = v[0]
        else:
            try:
                raw = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 16 * 1024))
                data = json.loads(raw.decode("utf-8") or "{}")
                if isinstance(data, dict):
                    params = {str(k): str(v) for k, v in data.items()}
            except Exception:
                params = {}
        sign = params.get("sign", "")
        # 大小写归一比较：不同易支付网关 sign 大小写不一（彩虹/异次元多为大写），
        # 统一小写比对避免「MD5 校验不全/失败」误报
        if not pkey or not sign or _pay_sign(params, pkey).lower() != sign.lower():
            self._json(403, {"err": "bad sign"}); return True
        if params.get("trade_status") != "TRADE_SUCCESS":
            body = b"fail"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return True
        order_no = (params.get("out_trade_no") or "").strip()
        if not order_no:
            body = b"fail"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return True
        c = db()
        row = c.execute("SELECT id, user_id, plan, days, amount FROM payment_orders WHERE order_no=? AND status='pending'",
                        (order_no,)).fetchone()
        if not row:
            c.close()
            body = b"fail"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return True
        oid, uid, plan, days, _amount = row[0], row[1], row[2], row[3], row[4]
        # #14：验签只证明消息来自易支付网关，金额一致性需显式校验（防网关配置/篡改导致少付多放）
        try:
            _cb_money = float(params.get("money") or 0)
        except (TypeError, ValueError):
            _cb_money = 0.0
        if abs(_cb_money - float(_amount or 0)) > 0.01:
            log.warning("pay notify amount mismatch order=%s expected=%s got=%s", order_no, _amount, _cb_money)
            c.close()
            body = b"fail"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return True
        now = time.time()
        me = c.execute("SELECT member_expire, svip_expire FROM users WHERE id=?", (uid,)).fetchone()
        base = max((me[0] or 0) if plan == "vip" else (me[1] or 0), now)
        new_expire = base + (days or 30) * 86400
        if plan == "vip":
            c.execute("UPDATE users SET member_expire=?, member='vip' WHERE id=?", (new_expire, uid))
        else:
            c.execute("UPDATE users SET svip_expire=?, member='svip' WHERE id=?", (new_expire, uid))
        c.execute("UPDATE payment_orders SET status='paid', paid_at=? WHERE id=?", (now, oid))
        # 前端自行开通 → source="self" 模板
        _om_content = _member_notif_text(plan, days or 30, new_expire, source="self")
        try:
            nid = uuid.uuid4().hex
            _official_send(c, uid, _om_content)
            c.execute("""INSERT INTO notifications(id, user_id, type, title, content, from_user_id, from_user_name, track_id, track_title, is_read, created)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                      (nid, uid, "official", "会员开通成功",
                       _om_content, "", "流萤音乐官方", "", "", 0, now))
            ws_push(uid, {"kind": "notification",
                          "notification": {"id": nid, "type": "official", "title": "会员开通成功",
                                           "content": _om_content,
                                           "fromUserId": "", "fromUser": "流萤音乐官方",
                                           "trackId": "", "trackTitle": "", "isRead": 0, "created": now},
                          "unread": _notif_unread(c, uid)})
        except Exception as _e:
            log.info("pay notify notif err: %s", _e)
        c.commit()
        c.close()
        # 易支付回调要求响应纯文本 success
        body = b"success"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)
        return True

    def _h_get_content(self, u):
        # ===== 全景音效：分离音轨查询 =====
        if u.path.startswith("/api/stems/"):
            from server import stems
            tid = u.path[len("/api/stems/"):]
            if not re.fullmatch(r"[A-Za-z0-9_-]+", tid or ""):
                self._err(400, "bad id"); return True
            qs = urllib.parse.parse_qs(u.query)
            trigger = (qs.get("trigger", [""])[0]) == "1"
            st = stems.get_status(tid)
            if st is None:
                if trigger:
                    stems.request_separate(tid)
                self._json(200, {"status": "none"}); return True
            if trigger and st["status"] not in ("done", "processing"):
                stems.request_separate(tid)
            resp = {"status": st["status"]}
            if st["status"] == "done":
                resp["vocalsUrl"] = PUBLIC_BASE + "/api/stem-file/" + st["vocals"]
                resp["accUrl"] = PUBLIC_BASE + "/api/stem-file/" + st["accompaniment"]
            self._json(200, resp); return True

        # ===== 全景音效：opus 音轨文件服务（支持 Range） =====
        if u.path.startswith("/api/stem-file/"):
            name = u.path[len("/api/stem-file/"):]
            if (name != os.path.basename(name) or not name.endswith(".opus")
                    or not re.fullmatch(r"[A-Za-z0-9_.-]+", name)):
                self._err(400, "bad file"); return True
            fp = os.path.join(STEMS, name)
            if not os.path.exists(fp):
                self._err(404, "not found"); return True
            size = os.path.getsize(fp)
            ctype = "audio/ogg"
            rng = self._parse_range(size)
            if rng is True:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers(); return True
            if rng:
                start, end = rng
                length = end - start + 1
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                try:
                    with open(fp, "rb") as f:
                        f.seek(start); remain = length
                        while remain > 0:
                            chunk = f.read(min(65536, remain))
                            if not chunk: break
                            self.wfile.write(chunk); remain -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return True
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                with open(fp, "rb") as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk: break
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return True

        if u.path == "/api/pay/notify":
            # 易支付 GET 回调
            return self._pay_notify_handle()
        if u.path == "/api/search/history":
            c = db()
            uid = resolve_token(c, self.headers.get("Authorization", ""))
            if not uid:
                c.close()
                self._json(200, {"list": []})
                return True
            rows = c.execute("SELECT id, keyword, created FROM search_history WHERE user_id=? ORDER BY created DESC LIMIT 20", (uid,)).fetchall()
            c.close()
            self._json(200, {"list": [{"id": r[0], "keyword": r[1], "created": r[2]} for r in rows]})
            return True
        if u.path.startswith("/api/playlists/") and u.path.endswith("/tracks"):
            pid = u.path.split("/")[3]
            c = db()
            uid = resolve_token(c, self.headers.get("Authorization", ""))
            if not uid:
                c.close()
                self._err(401, "unauthorized"); return True
            # 验证歌单属于当前用户
            pl = c.execute("SELECT id, name, cover, description, track_count, created FROM playlists WHERE id=? AND user_id=?", (pid, uid)).fetchone()
            if not pl:
                c.close()
                self._err(404, "playlist not found"); return True
            rows = c.execute("SELECT track_id, track_title, track_artist, track_cover, source, created FROM playlist_tracks WHERE playlist_id=? ORDER BY sort DESC, created DESC", (pid,)).fetchall()
            c.close()
            lst = [{"id": r[0], "trackId": r[0], "title": r[1], "artist": r[2], "cover": r[3], "source": r[4], "created": r[5]} for r in rows]
            self._json(200, {"playlist": {"id": pl[0], "name": pl[1], "cover": pl[2] or "", "description": pl[3] or "", "trackCount": pl[4] or 0, "created": pl[5]}, "list": lst})
            return True
        if u.path == "/api/tracks":
            if not rate_limit(self, "tracks"):
                self._err(429, "请求过于频繁，请稍后再试")
                return True
            c = db()
            qs = urllib.parse.parse_qs(u.query)
            # local=1：仅返回本地可播的用户上传歌（fname 非空）随机 3 首——官网试听播放器专用，
            # 保证拿到的一定能签名播放（采集歌 play_url 可能为空/第三方直链，不适合官网试听）。
            if (qs.get("local", [""])[0] or "").strip() == "1":
                try:
                    rows = c.execute(
                        "SELECT t.id,t.title,t.artist,t.album,t.cover,t.fname,t.size,t.dur,t.created,"
                        " t.play_count,t.like_count,t.share_count,t.user_id,t.lrc,COALESCE(u.name,''),"
                        " (SELECT COUNT(*) FROM comments cm WHERE cm.track_id=t.id),0"
                        " FROM tracks t LEFT JOIN users u ON u.id=t.user_id"
                        " WHERE t.offline=0 AND t.fname!=''"
                        " GROUP BY t.id ORDER BY RANDOM() LIMIT 3").fetchall()
                    out = [{"id": r[0], "title": r[1], "artist": r[2], "album": r[3],
                            "cover": r[4], "fname": r[5], "size": r[6], "dur": r[7],
                            "durText": _fmt_dur(r[7]), "created": r[8],
                            "playCount": r[9] or 0, "likeCount": r[10] or 0,
                            "shareCount": r[11] or 0,
                            "userId": r[12] or "", "userName": r[14] or "",
                            "hasLyric": bool(r[13]),
                            "commentCount": r[15] or 0, "isLiked": False} for r in rows]
                except Exception:
                    c.close()
                    self._err(500, "local tracks failed")
                    return True
                c.close()
                self._json(200, out)
                return True
            viewer_id = (qs.get("userId", [""])[0] or "").strip()
            # token 优先识别查看者（新客户端）；无 token 回退 userId 参数（旧客户端）
            tok_uid = resolve_token(c, self.headers.get("Authorization", ""))
            if tok_uid:
                viewer_id = tok_uid
            # 可选 q 参数：构建「相关歌曲流」——按歌名/歌手/专辑/上传者昵称匹配，
            # 命中歌曲随机取 10 首；不足 10 首时用全量随机补足（无相关即全随机）。
            # 不传 q 时保持原有全量随机 10 首行为。
            keyword = (qs.get("q", [""])[0] or "").strip()
            base_sql = """SELECT t.id,t.title,t.artist,t.album,t.cover,t.fname,t.size,t.dur,t.created,
                          t.play_count,t.like_count,t.share_count,t.user_id,t.lrc,
                          COALESCE(u.name,''),
                          (SELECT COUNT(*) FROM comments cm WHERE cm.track_id=t.id),
                          MAX(CASE WHEN l.user_id IS NOT NULL THEN 1 ELSE 0 END)
                          FROM tracks t
                          LEFT JOIN users u ON u.id=t.user_id
                          LEFT JOIN likes l ON l.track_id=t.id AND l.user_id=?"""
            if keyword:
                like = "%" + keyword + "%"
                rows = c.execute(
                    base_sql + """ WHERE t.offline=0 AND (t.title LIKE ? OR t.artist LIKE ? OR t.album LIKE ?
                                   OR COALESCE(u.name,'') LIKE ?)
                                   GROUP BY t.id ORDER BY RANDOM() LIMIT 10""",
                    (viewer_id, like, like, like, like)).fetchall()
                if len(rows) < 10:
                    ids = [r[0] for r in rows]
                    need = 10 - len(rows)
                    if ids:
                        ph = ",".join("?" * len(ids))
                        extra = c.execute(
                            base_sql + (" WHERE t.offline=0 AND t.id NOT IN (%s)"
                                        " GROUP BY t.id ORDER BY RANDOM() LIMIT ?" % ph),
                            tuple([viewer_id] + ids + [need])).fetchall()
                    else:
                        extra = c.execute(
                            base_sql + " WHERE t.offline=0 GROUP BY t.id ORDER BY RANDOM() LIMIT ?",
                            (viewer_id, need)).fetchall()
                    rows = rows + extra
            else:
                # P1 优化：两段式随机采样——先取 id 集合（单列、无排序），
                # Python random.sample 抽样后按主键查详情，避免 ORDER BY RANDOM()
                # 对全表建临时排序（曲库增大时发现页每请求全表扫+排序）。
                # #7：id 集合走 60s 缓存（_id_pool），上传新歌后失效，避免每请求全表扫
                all_ids = _id_pool(c, "tracks")
                pick = random.sample(all_ids, min(10, len(all_ids))) if all_ids else []
                if pick:
                    ph = ",".join("?" * len(pick))
                    rows = c.execute(
                        base_sql + (" WHERE t.offline=0 AND t.id IN (%s)"
                                    " GROUP BY t.id" % ph),
                        tuple([viewer_id] + pick)).fetchall()
                else:
                    rows = []
            # ---- 合并采集歌曲（collected_tracks）----
            # 前端零改动：采集歌曲与用户上传歌曲通过同一个 /api/tracks 下发，
            # 字段结构与 tracks 完全一致；cover 返回占位 "1" 让前端拼出
            # /api/cover/{id}，再由后端 302 到第三方封面，不暴露第三方直链。
            collect_cols = """id,title,artist,album,cover,duration,size,created,
                              play_count,like_count,share_count,user_id,lrc"""
            if keyword:
                like = "%" + keyword + "%"
                crows = c.execute(
                    "SELECT " + collect_cols + """ FROM collected_tracks
                        WHERE offline=0 AND (title LIKE ? OR artist LIKE ? OR album LIKE ?)
                        ORDER BY RANDOM() LIMIT 10""",
                    (like, like, like)).fetchall()
                if len(crows) < 10:
                    cids = [r[0] for r in crows]
                    need = 10 - len(crows)
                    if cids:
                        ph = ",".join("?" * len(cids))
                        crows = crows + c.execute(
                            "SELECT " + collect_cols + (" FROM collected_tracks"
                                " WHERE offline=0 AND id NOT IN (%s) ORDER BY RANDOM() LIMIT ?" % ph),
                            tuple(cids + [need])).fetchall()
                    else:
                        crows = crows + c.execute(
                            "SELECT " + collect_cols + " FROM collected_tracks WHERE offline=0"
                            " ORDER BY RANDOM() LIMIT ?", (need,)).fetchall()
            else:
                # P1 优化：同 tracks——采集曲 id 抽样后按主键查询，避免全表随机排序
                all_cids = _id_pool(c, "collected_tracks")
                pick_c = random.sample(all_cids, min(10, len(all_cids))) if all_cids else []
                if pick_c:
                    ph = ",".join("?" * len(pick_c))
                    crows = c.execute(
                        "SELECT " + collect_cols + (" FROM collected_tracks"
                            " WHERE offline=0 AND id IN (%s)" % ph),
                        tuple(pick_c)).fetchall()
                else:
                    crows = []
            # 采集歌曲归属账号昵称（每首歌按自己的 user_id 关联，避免把第一条账号名塞给所有歌）
            uname_map = {}
            uids = {r[11] for r in crows if r[11]}
            if uids:
                uph = ",".join("?" * len(uids))
                for urow in c.execute("SELECT id, name FROM users WHERE id IN (%s)" % uph,
                                      tuple(uids)):
                    uname_map[urow[0]] = urow[1] or ""
            merged = [{"id": r[0], "title": r[1], "artist": r[2], "album": r[3],
                       "cover": "1", "fname": "",
                       "size": int(r[6]) if r[6] is not None else 0,   # App 端 size 为 Int?，浮点会导致整个列表解码失败
                       "dur": r[5],
                       "durText": _fmt_dur(r[5]), "created": r[7],
                       "playCount": r[8] or 0, "likeCount": r[9] or 0,
                       "shareCount": r[10] or 0,
                       "userId": r[11] or "", "userName": uname_map.get(r[11] or "", ""),
                       "hasLyric": bool(r[12]),
                       "commentCount": 0, "isLiked": False} for r in crows]
            # 用户上传歌曲输出
            merged += [{"id": r[0], "title": r[1], "artist": r[2], "album": r[3],
                        "cover": r[4], "fname": r[5], "size": r[6], "dur": r[7],
                        "durText": _fmt_dur(r[7]), "created": r[8],
                        "playCount": r[9] or 0, "likeCount": r[10] or 0,
                        "shareCount": r[11] or 0,
                        "userId": r[12] or "", "userName": r[14] or "",
                        "hasLyric": bool(r[13]),
                        "commentCount": r[15] or 0,
                        "isLiked": bool(r[16])} for r in rows]
            random.shuffle(merged)
            c.close()
            self._json(200, merged)
            return True
        if u.path.startswith("/api/track/"):
            if not rate_limit(self, "tracks"):
                self._err(429, "请求过于频繁，请稍后再试")
                return True
            tid = u.path.rsplit("/", 1)[-1]
            c = db()
            tok_uid = resolve_token(c, self.headers.get("Authorization", ""))
            viewer_id = tok_uid or ""
            row = c.execute(
                """SELECT t.id,t.title,t.artist,t.album,t.cover,t.fname,t.size,t.dur,t.created,
                          t.play_count,t.like_count,t.share_count,t.user_id,t.lrc,
                          COALESCE(u.name,''),
                          (SELECT COUNT(*) FROM comments cm WHERE cm.track_id=t.id),
                          MAX(CASE WHEN l.user_id IS NOT NULL THEN 1 ELSE 0 END)
                          FROM tracks t LEFT JOIN users u ON u.id=t.user_id
                          LEFT JOIN likes l ON l.track_id=t.id AND l.user_id=?
                          WHERE t.id=? AND t.offline=0 GROUP BY t.id""",
                (viewer_id, tid)).fetchone()
            if row:
                out = {"id": row[0], "title": row[1], "artist": row[2], "album": row[3],
                       "cover": row[4], "fname": row[5], "size": row[6], "dur": row[7],
                       "durText": _fmt_dur(row[7]), "created": row[8],
                       "playCount": row[9] or 0, "likeCount": row[10] or 0,
                       "shareCount": row[11] or 0, "userId": row[12] or "",
                       "userName": row[14] or "", "hasLyric": bool(row[13]),
                       "commentCount": row[15] or 0, "isLiked": bool(row[16])}
            else:
                crow = c.execute(
                    """SELECT id,title,artist,album,cover,duration,size,created,
                              play_count,like_count,share_count,user_id,lrc
                       FROM collected_tracks WHERE id=? AND offline=0""",
                    (tid,)).fetchone()
                if not crow:
                    c.close()
                    self._err(404, "not found")
                    return True
                uname = ""
                urow = c.execute("SELECT name FROM users WHERE id=?", (crow[11] or "",)).fetchone()
                if urow:
                    uname = urow[0]
                out = {"id": crow[0], "title": crow[1], "artist": crow[2], "album": crow[3],
                       "cover": "1", "fname": "",
                       "size": int(crow[6]) if crow[6] is not None else 0,
                       "dur": crow[5], "durText": _fmt_dur(crow[5]), "created": crow[7],
                       "playCount": crow[8] or 0, "likeCount": crow[9] or 0,
                       "shareCount": crow[10] or 0, "userId": crow[11] or "",
                       "userName": uname, "hasLyric": bool(crow[12]),
                       "commentCount": 0, "isLiked": False}
            c.close()
            self._json(200, out)
            return True
        if u.path == "/api/likes":
            if not rate_limit(self, "tracks"):
                self._err(429, "请求过于频繁，请稍后再试")
                return True
            qs = urllib.parse.parse_qs(u.query)
            viewer_id = (qs.get("userId", [""])[0] or "").strip()
            c = db()
            tok_uid = resolve_token(c, self.headers.get("Authorization", ""))
            if tok_uid:
                viewer_id = tok_uid
            if not viewer_id:
                c.close()
                self._json(200, [])
                return True
            like_rows = c.execute(
                "SELECT track_id FROM likes WHERE user_id=? ORDER BY created DESC LIMIT 300",
                (viewer_id,)).fetchall()
            if not like_rows:
                c.close()
                self._json(200, [])
                return True
            ids = [r[0] for r in like_rows]
            ph = ",".join("?" * len(ids))
            # 用户上传歌曲
            rows = c.execute(
                """SELECT t.id,t.title,t.artist,t.album,t.cover,t.fname,t.size,t.dur,t.created,
                   t.play_count,t.like_count,t.share_count,t.user_id,t.lrc,
                   COALESCE(u.name,''),
                   (SELECT COUNT(*) FROM comments cm WHERE cm.track_id=t.id), 1
                   FROM tracks t
                   LEFT JOIN users u ON u.id=t.user_id
                   WHERE t.offline=0 AND t.id IN (%s) GROUP BY t.id""" % ph, ids).fetchall()
            # 采集歌曲（字段与上传一致：cover 占位、size 强转 int）
            crows = c.execute(
                "SELECT id,title,artist,album,cover,duration,size,created,"
                " play_count,like_count,share_count,user_id,lrc"
                " FROM collected_tracks WHERE offline=0 AND id IN (%s)" % ph, ids).fetchall()
            uname_map = {}
            uids = {r[11] for r in crows if r[11]}
            if uids:
                uph = ",".join("?" * len(uids))
                for urow in c.execute("SELECT id, name FROM users WHERE id IN (%s)" % uph,
                                      tuple(uids)):
                    uname_map[urow[0]] = urow[1] or ""
            merged = [{"id": r[0], "title": r[1], "artist": r[2], "album": r[3],
                       "cover": "1", "fname": "",
                       "size": int(r[6]) if r[6] is not None else 0,
                       "dur": r[5], "durText": _fmt_dur(r[5]), "created": r[7],
                       "playCount": r[8] or 0, "likeCount": r[9] or 0,
                       "shareCount": r[10] or 0,
                       "userId": r[11] or "", "userName": uname_map.get(r[11] or "", ""),
                       "hasLyric": bool(r[12]), "commentCount": 0, "isLiked": True} for r in crows]
            merged += [{"id": r[0], "title": r[1], "artist": r[2], "album": r[3],
                        "cover": r[4], "fname": r[5], "size": r[6], "dur": r[7],
                        "durText": _fmt_dur(r[7]), "created": r[8],
                        "playCount": r[9] or 0, "likeCount": r[10] or 0,
                        "shareCount": r[11] or 0,
                        "userId": r[12] or "", "userName": r[14] or "",
                        "hasLyric": bool(r[13]), "commentCount": r[15] or 0,
                        "isLiked": True} for r in rows]
            # 保持点赞时间倒序（新点赞的在前）
            order = {rid: i for i, rid in enumerate(ids)}
            merged.sort(key=lambda x: order.get(x["id"], 999))
            c.close()
            self._json(200, merged)
            return True
        if u.path == "/api/search":
            qs = urllib.parse.parse_qs(u.query)
            keyword = (qs.get("q", [""])[0] or "").strip()
            if not keyword:
                self._json(200, [])
                return True
            viewer_id = (qs.get("userId", [""])[0] or "").strip()
            c = db()
            tok_uid = resolve_token(c, self.headers.get("Authorization", ""))
            if tok_uid:
                viewer_id = tok_uid
            # #6：搜索改 FTS5 trigram（中文子串可命中）+ LIKE 兜底，不再全表 LIKE 扫
            ids = fts_ids(c, "tracks", keyword, 50)
            cids = fts_ids(c, "collected_tracks", keyword, 50)
            rows = []
            if ids:
                ph = ",".join("?" * len(ids))
                rows = c.execute("""SELECT t.id,t.title,t.artist,t.album,t.cover,t.fname,t.size,t.dur,t.created,
                                    t.play_count,t.like_count,t.share_count,t.user_id,t.lrc,
                                    COALESCE(u.name,''),
                                    (SELECT COUNT(*) FROM comments cm WHERE cm.track_id=t.id),
                                    MAX(CASE WHEN l.user_id IS NOT NULL THEN 1 ELSE 0 END)
                                    FROM tracks t
                                    LEFT JOIN users u ON u.id=t.user_id
                                    LEFT JOIN likes l ON l.track_id=t.id AND l.user_id=?
                                    WHERE t.offline=0 AND t.id IN (%s)
                                    GROUP BY t.id
                                    ORDER BY t.play_count DESC""" % ph,
                                  tuple([viewer_id] + ids)).fetchall()
            crows = []
            if cids:
                ph = ",".join("?" * len(cids))
                # 采集歌曲同样参与搜索（字段结构与上传歌曲完全一致）
                crows = c.execute("""SELECT id,title,artist,album,cover,duration,size,created,
                                     play_count,like_count,share_count,user_id,lrc
                                     FROM collected_tracks
                                     WHERE offline=0 AND id IN (%s)
                                     ORDER BY play_count DESC""" % ph,
                                  tuple(cids)).fetchall()
            uname_map = {}
            uids = {r[11] for r in crows if r[11]}
            if uids:
                uph = ",".join("?" * len(uids))
                for urow in c.execute("SELECT id, name FROM users WHERE id IN (%s)" % uph,
                                      tuple(uids)):
                    uname_map[urow[0]] = urow[1] or ""
            c.close()
            out = [{"id": r[0], "title": r[1], "artist": r[2], "album": r[3],
                    "cover": "1", "fname": "",
                    "size": int(r[6]) if r[6] is not None else 0,
                    "dur": r[5], "durText": _fmt_dur(r[5]),
                    "created": r[7],
                    "playCount": r[8] or 0, "likeCount": r[9] or 0,
                    "shareCount": r[10] or 0,
                    "userId": r[11] or "", "userName": uname_map.get(r[11] or "", ""),
                    "hasLyric": bool(r[12]),
                    "commentCount": 0, "isLiked": False} for r in crows]
            out += [{"id": r[0], "title": r[1], "artist": r[2], "album": r[3],
                     "cover": r[4], "fname": r[5], "size": r[6], "dur": r[7],
                     "durText": _fmt_dur(r[7]), "created": r[8],
                     "playCount": r[9] or 0, "likeCount": r[10] or 0,
                     "shareCount": r[11] or 0,
                     "userId": r[12] or "", "userName": r[14] or "",
                     "hasLyric": bool(r[13]),
                     "commentCount": r[15] or 0,
                     "isLiked": bool(r[16])} for r in rows]
            out.sort(key=lambda x: -(x["playCount"] or 0))
            # 歌词搜歌（2026-11-05）：标题/歌手/专辑都没命中时，用一句歌词找歌。
            # 库里 1,524 首带歌词，是最能"想起歌但想不起名字"的场景。
            if len(out) < 8 and len(keyword) >= 2:
                try:
                    seen = {x["id"] for x in out}
                    c2 = db()
                    try:
                        # 歌词库里很多是"逐字带空格"的 LRC（"故 事 的 小 黄 花"），
                        # 整串 LIKE 匹配不到 → 把关键字拆成单字用 % 连接：
                        # %故%事%的%小%黄%花% 既能命中带空格的，也能命中正常的。
                        chars = [ch for ch in keyword if not ch.isspace()][:10]
                        pat = "%" + "%".join(chars) + "%" if len(chars) >= 2 else \
                            "%" + keyword.replace("%", "").replace("_", "") + "%"
                        lrows = c2.execute(
                            "SELECT id FROM collected_tracks WHERE offline=0 AND lrc LIKE ?"
                            " ORDER BY play_count DESC LIMIT 30", (pat,)).fetchall()
                        lrows += c2.execute(
                            "SELECT id FROM tracks WHERE offline=0 AND lrc LIKE ?"
                            " ORDER BY play_count DESC LIMIT 30", (pat,)).fetchall()
                        lids = [r[0] for r in lrows if r[0] not in seen][:20]
                        if lids:
                            extra = _rec_payload(c2, lids, viewer_id, reason_map=None, route="lyric")
                            for x in extra:
                                x["matchedBy"] = "lyric"
                            out += extra
                    finally:
                        c2.close()
                except Exception:
                    pass
            self._json(200, out[:50])
            return True
        if u.path == "/api/official-messages":
            c = db()
            # 定向消息支持：全广播（receiver_user_id=''）或发给我的定向消息（2026-10-19）
            tok_uid = resolve_token(c, self.headers.get("Authorization", ""))
            _uid = tok_uid or ""
            rows = c.execute("""SELECT id, content, created, sender_name FROM official_messages
                                WHERE receiver_user_id='' OR receiver_user_id=?
                                ORDER BY created DESC LIMIT 100""", (_uid,)).fetchall()
            unread_count = 0
            read_at = 0.0
            if tok_uid:
                r = c.execute("SELECT official_read_at FROM users WHERE id=?", (tok_uid,)).fetchone()
                read_at = (r[0] or 0) if r else 0
                unread_count = c.execute("""SELECT COUNT(*) FROM official_messages
                                            WHERE (receiver_user_id='' OR receiver_user_id=?) AND created > ?""",
                                         (_uid, read_at)).fetchone()[0]
            c.close()
            out = [{"id": r[0], "content": r[1], "created": r[2], "sender": r[3],
                    "isNew": bool(tok_uid and r[2] > read_at)} for r in rows]
            self._json(200, {"list": out, "unreadCount": unread_count if tok_uid else 0})
            return True
        if u.path == "/api/notifications":
            c = db()
            tok_uid = resolve_token(c, self.headers.get("Authorization", ""))
            if not tok_uid:
                c.close()
                self._json(200, {"list": [], "unread": 0, "hasMore": False})
                return True
            # 分页支持：page（从1开始）、size（默认20，最大50）
            qs = urllib.parse.parse_qs(u.query)
            page = max(1, int(qs.get("page", ["1"])[0] or "1"))
            size = min(50, max(1, int(qs.get("size", ["20"])[0] or "20")))
            offset = (page - 1) * size
            rows = c.execute("SELECT id, type, title, content, from_user_id, from_user_name, track_id, track_title, comment_id, is_read, created FROM notifications WHERE user_id=? AND is_deleted=0 ORDER BY created DESC LIMIT ? OFFSET ?",
                            (tok_uid, size, offset)).fetchall()
            # 官方消息单独由 /api/official-messages 的 unreadCount 计数（红点=互动+官方 各算一次），
            # 这里必须排除 official 类型，否则官方消息会被重复计入红点
            unread = c.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0 AND is_deleted=0 AND type != 'official'", (tok_uid,)).fetchone()[0]
            total = c.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_deleted=0", (tok_uid,)).fetchone()[0]
            has_more = (offset + len(rows)) < total
            c.close()
            self._json(200, {"list": [{"id": r[0], "type": r[1], "title": r[2], "content": r[3], "fromUserId": r[4], "fromUser": r[5], "trackId": r[6], "trackTitle": r[7], "commentId": r[8], "isRead": r[9], "created": r[10]} for r in rows], "unread": unread, "hasMore": has_more, "total": total})
            return True
        if u.path == "/api/weather":
            if not rate_limit(self, "weather"):
                self._err(429, "请求过于频繁，请稍后再试")
                return True
            qs = urllib.parse.parse_qs(u.query)
            try:
                _lat_s = (qs.get("lat", [""])[0] or "").strip()
                _lng_s = (qs.get("lng", [""])[0] or "").strip()
                if not _lat_s or not _lng_s:
                    self._json(200, {"ok": False, "error": "missing lat/lng"})
                    return True
                _lat, _lng = float(_lat_s), float(_lng_s)
                _hour_s = (qs.get("hour", [""])[0] or "").strip()
                _hour = int(_hour_s) if _hour_s.isdigit() and 0 <= int(_hour_s) <= 23 else None
                _wx = _fetch_weather(_lat, _lng)
                if not _wx:
                    self._json(200, {"ok": False, "error": "weather unavailable"})
                    return True
                _scene = _scene_for(_wx.get("code"), _wx.get("temp"), _hour)
                self._json(200, {"ok": True,
                                 "temp": _wx.get("temp"), "code": _wx.get("code"),
                                 "hour": _hour if _hour is not None else int(time.localtime().tm_hour),
                                 "scene": _scene.get("key") if _scene else None,
                                 "sceneLabel": _scene.get("label") if _scene else None})
            except Exception:
                self._json(200, {"ok": False, "error": "invalid params"})
            return True
        if u.path == "/api/recommend":
            if not rate_limit(self, "recommend"):
                self._err(429, "请求过于频繁，请稍后再试")
                return True
            qs = urllib.parse.parse_qs(u.query)
            viewer_id = (qs.get("userId", [""])[0] or "").strip()
            c = db()
            tok_uid = resolve_token(c, self.headers.get("Authorization", ""))
            if tok_uid:
                viewer_id = tok_uid
            # exclude：已推荐过的歌曲 id（逗号分隔）——推荐流无限续推防重复
            _excl = set()
            _ex_s = (qs.get("exclude", [""])[0] or "").strip()
            if _ex_s:
                _excl = {x for x in _ex_s.split(",") if x}
            # fresh：长时间重开 App 的新鲜会话——加大随机扰动/探索权重，重开不固定重复
            _fresh = (qs.get("fresh", ["0"])[0] or "").strip() in ("1", "true")
            # source：来源标记——"discover"（发现页猜你喜欢）不参与 3 小时推送去重，
            # 不排除已推歌也不记录本次推送（去重只对播放页首页推荐流生效）
            _source = (qs.get("source", [""])[0] or "").strip()
            _now = time.time()
            # M2 场景上下文：前端传 lat/lng（可选 hour）→ 取天气（TTL 缓存）+ 时段 → 心情场景。
            # 任何异常/缺参 → scene=None（静默降级，推荐照常，不影响主流程）
            _scene = None
            try:
                _lat_s = (qs.get("lat", [""])[0] or "").strip()
                _lng_s = (qs.get("lng", [""])[0] or "").strip()
                _hour_s = (qs.get("hour", [""])[0] or "").strip()
                if _lat_s and _lng_s:
                    _lat, _lng = float(_lat_s), float(_lng_s)
                    _hour = int(_hour_s) if _hour_s.isdigit() and 0 <= int(_hour_s) <= 23 else None
                    _wx = _fetch_weather(_lat, _lng)
                    _scene = _scene_for(_wx.get("code") if _wx else None,
                                        _wx.get("temp") if _wx else None, _hour)
            except Exception:
                _scene = None
            # 3 小时推送去重：取该用户 3 小时内已推送过的歌，本次不再推送
            # （仅播放页首页推荐流；发现页猜你喜欢不受限）
            _pushed = set()
            if _source != "discover":
                _pushed = _rec_recent_pushed(viewer_id, _now)
            # 批次轮换：时间桶（同桶主批稳定可复现）+ 会话内序号递增（每次获取不同批）
            _bucket = int(_now // REC_BUCKET_SECONDS)
            _seq, _rnd = _rec_next_seq(viewer_id, _bucket, _now, fresh=_fresh)
            log.info("recommend uid=%s bucket=%d seq=%d fresh=%s pushed=%d",
                     viewer_id or "-", _bucket, _seq, _fresh, len(_pushed))
            # 会员算法分流（2026-10-19）：VIP/SVIP 走精准算法（行为权重↑、随机↓），普通用户走粗略算法
            _precise = False
            try:
                if viewer_id:
                    _mr = c.execute("SELECT member_expire, svip_expire FROM users WHERE id=?",
                                    (viewer_id,)).fetchone()
                    if _mr and member_level(_mr[0], _mr[1]):
                        _precise = True
            except Exception:
                pass
            # 多路召回 + 加权融合 + MMR 重排（算法见 build_recommendations）
            # P1 归因：返回 (tid, reason, route)，route=来源路代码（audio/behavior/collab...），
            # 透出给前端展示与 listen 上报，后端按路统计完听率自调权。
            rec_pairs = build_recommendations(c, viewer_id, exclude=_excl, fresh=_fresh, rnd=_rnd,
                                              pushed=_pushed, scene=_scene, precise=_precise)
            chosen = [r[0] for r in rec_pairs]
            reason_map = {r[0]: r[1] for r in rec_pairs}
            route_map = {r[0]: r[2] for r in rec_pairs}
            # 评论意图推荐：用户最近 3 小时「想听 XX」命中曲库的歌混入推荐流
            # （不打扰式通知，刷到即见；2026-10-24 升级：保送第 1~3 位，连续提及 2 次以上插第 1 位）
            try:
                if viewer_id:
                    wrows = c.execute(
                        "SELECT track_id, cnt FROM wish_recommends WHERE uid=? AND created > ? "
                        "ORDER BY cnt DESC, created DESC LIMIT 5",
                        (viewer_id, _now - _COMMENT_WISH_WINDOW)).fetchall()
                    for (wtid, wcnt) in wrows:
                        if wtid in _excl or wtid in chosen:
                            continue
                        if _source != "discover" and wtid in _pushed:
                            continue
                        insert_at = 0 if (wcnt or 1) >= 2 else min(2, len(chosen))
                        chosen.insert(insert_at, wtid)
                        reason_map[wtid] = "根据你的评论推荐"
            except Exception:
                pass
            if not chosen:
                c.close()
                self._json(200, [])
                return True
            # 5) 组装输出（与 /api/tracks 同一格式，采集/上传无差别）
            ph = ",".join("?" * len(chosen))
            trows = c.execute("""SELECT t.id,t.title,t.artist,t.album,t.cover,t.fname,t.size,t.dur,t.created,
                                 t.play_count,t.like_count,t.share_count,t.user_id,t.lrc,
                                 COALESCE(u.name,''),
                                 (SELECT COUNT(*) FROM comments cm WHERE cm.track_id=t.id),
                                 MAX(CASE WHEN l.user_id IS NOT NULL THEN 1 ELSE 0 END)
                                 FROM tracks t
                                 LEFT JOIN users u ON u.id=t.user_id
                                 LEFT JOIN likes l ON l.track_id=t.id AND l.user_id=?
                                 WHERE t.id IN (%s) GROUP BY t.id""" % ph,
                               tuple([viewer_id] + chosen)).fetchall()
            crows = c.execute("""SELECT id,title,artist,album,cover,duration,size,created,
                                 play_count,like_count,share_count,user_id,lrc
                                 FROM collected_tracks WHERE id IN (%s)""" % ph, chosen).fetchall()
            uname_map = {}
            uids = {r[11] for r in crows if r[11]}
            if uids:
                uph = ",".join("?" * len(uids))
                for urow in c.execute("SELECT id, name FROM users WHERE id IN (%s)" % uph,
                                      tuple(uids)):
                    uname_map[urow[0]] = urow[1] or ""
            c.close()
            merged = [{"id": r[0], "title": r[1], "artist": r[2], "album": r[3],
                       "cover": "1", "fname": "",
                       "size": int(r[6]) if r[6] is not None else 0,
                       "dur": r[5], "durText": _fmt_dur(r[5]), "created": r[7],
                       "playCount": r[8] or 0, "likeCount": r[9] or 0,
                       "shareCount": r[10] or 0,
                       "userId": r[11] or "", "userName": uname_map.get(r[11] or "", ""),
                       "hasLyric": bool(r[12]),
                       "reason": reason_map.get(r[0], "推荐"),
                       "recRoute": route_map.get(r[0], ""),
                       "commentCount": 0, "isLiked": False} for r in crows]
            merged += [{"id": r[0], "title": r[1], "artist": r[2], "album": r[3],
                        "cover": r[4], "fname": r[5], "size": r[6], "dur": r[7],
                        "durText": _fmt_dur(r[7]), "created": r[8],
                        "playCount": r[9] or 0, "likeCount": r[10] or 0,
                        "shareCount": r[11] or 0,
                        "userId": r[12] or "", "userName": r[14] or "",
                        "hasLyric": bool(r[13]),
                        "reason": reason_map.get(r[0], "推荐"),
                        "recRoute": route_map.get(r[0], ""),
                        "commentCount": r[15] or 0,
                        "isLiked": bool(r[16])} for r in trows]
            order = {tid: i for i, tid in enumerate(chosen)}
            merged.sort(key=lambda x: order.get(x["id"], 999))
            # 记录本次推送的歌曲：3 小时内不再重复推送（滑动窗口自然过期）
            # 仅播放页首页推荐流记录；发现页猜你喜欢不占用去重窗口
            if _source != "discover":
                _rec_remember_pushed(viewer_id, chosen, _now)
            # 曝光日志（2026-11-05）：记下"推了哪些、哪一路、第几位"，供 CTR 归因与 LTR 训练。
            # best-effort：任何异常都不影响推荐响应（用户已经在等歌）。
            # 注意：上面组装响应时 c 已关闭，这里单开一条连接（写少读多，代价可忽略）。
            try:
                from server import impressions as _imp
                c_imp = db()
                try:
                    _imp.record(c_imp, viewer_id, _imp.new_request_id(),
                                [(t, route_map.get(t, "")) for t in chosen],
                                source=_source or "", now=_now)
                finally:
                    c_imp.close()
            except Exception:
                pass
            self._json(200, merged)
            return True
        if u.path.startswith("/api/similar/"):
            # 相似歌曲（2026-11-05）：detail 页「跟这首像的还有…」
            # 数据来自 song_vec 建的 track_neighbors（跨歌手"听起来像"），
            # 不是同歌手/同专辑的机械关联。
            if not rate_limit(self, "similar"):
                self._err(429, "请求过于频繁，请稍后再试")
                return True
            tid = urllib.parse.unquote(u.path.rsplit("/", 1)[-1])
            qs = urllib.parse.parse_qs(u.query)
            try:
                n = max(1, min(int((qs.get("n") or ["20"])[0]), 50))
            except ValueError:
                n = 20
            c = db()
            try:
                viewer_id = resolve_token(c, self.headers.get("Authorization", ""))
                sim = []
                try:
                    sim = c.execute(
                        "SELECT sim_track_id, score FROM track_neighbors"
                        " WHERE track_id=? AND src='vec' ORDER BY rank ASC LIMIT ?",
                        (tid, n)).fetchall()
                except Exception:
                    sim = []
                if not sim:
                    # 没有向量时退回同歌手/同风格（老逻辑兜底，别返回空）
                    sim = [(r[0], 0.0) for r in c.execute(
                        "SELECT id FROM ("
                        " SELECT id,artist,genre FROM tracks WHERE offline=0"
                        " UNION ALL SELECT id,artist,genre FROM collected_tracks"
                        "   WHERE offline=0 AND COALESCE(play_url,'')!='')"
                        " WHERE id!=? AND ((artist!='' AND artist=("
                        "   SELECT artist FROM tracks WHERE id=?"
                        "   UNION ALL SELECT artist FROM collected_tracks WHERE id=?)"
                        " ) OR (genre!='' AND genre=("
                        "   SELECT genre FROM tracks WHERE id=?"
                        "   UNION ALL SELECT genre FROM collected_tracks WHERE id=?)))"
                        " LIMIT ?", (tid, tid, tid, tid, tid, n)).fetchall()]
                ids = [r[0] for r in sim]
                score_map = {r[0]: round(float(r[1] or 0), 3) for r in sim}
                items = _rec_payload(c, ids, viewer_id, reason_map=None, route="similar")
                for it in items:
                    it["similarity"] = score_map.get(it["id"], 0.0)
                self._json(200, {"ok": True, "seed": tid, "items": items})
            except Exception as e:
                self._internal_error(e)
            finally:
                c.close()
            return True
        if u.path == "/api/daily":
            # 每日推荐（2026-11-05）：按 (用户, 日期) 确定，当天稳定、隔天换一批。
            # 用同一套多路召回，但批次种子固定为日期 → 同一天多次进入结果一致。
            if not rate_limit(self, "daily"):
                self._err(429, "请求过于频繁，请稍后再试")
                return True
            qs = urllib.parse.parse_qs(u.query)
            c = db()
            try:
                viewer_id = resolve_token(c, self.headers.get("Authorization", ""))
                try:
                    want = max(6, min(int((qs.get("n") or ["30"])[0]), 50))
                except ValueError:
                    want = 30
                day = time.strftime("%Y-%m-%d")
                ck = (viewer_id or "-", day)
                hit = _DAILY_CACHE.get(ck)
                if hit and len(hit) >= want:
                    ids = hit[:want]
                    pairs = []
                else:
                    # 注意：不要在这里写 `from server.recommend import build_recommendations`——
                    # 函数内 import 会让 build_recommendations 变成整个 _h_get_content 的局部变量，
                    # 上面 /api/recommend 分支（依赖模块级 wildcard 导入）会直接 UnboundLocalError。
                    pairs = build_recommendations(
                        c, viewer_id, exclude=set(), fresh=False,
                        rnd=random.Random("%s|daily|%s" % (viewer_id or "-", day)),
                        pushed=set())
                    ids = [p[0] for p in pairs]
                    if len(ids) < want:
                        more = build_recommendations(
                            c, viewer_id, exclude=set(ids), fresh=False,
                            rnd=random.Random("%s|daily|%s|2" % (viewer_id or "-", day)),
                            pushed=set())
                        pairs += more
                        ids += [p[0] for p in more]
                    ids = ids[:want]
                    if len(_DAILY_CACHE) > 2000:
                        _DAILY_CACHE.clear()
                    _DAILY_CACHE[ck] = ids
                reason_map = {p[0]: p[1] for p in pairs}
                items = _rec_payload(c, ids, viewer_id, reason_map=reason_map, route="daily")
                self._json(200, {"ok": True, "date": day, "items": items})
            except Exception as e:
                self._internal_error(e)
            finally:
                c.close()
            return True
        if u.path.startswith("/api/radio/"):
            # 场景音乐 → 个性化分类歌单（2026-11-01）
            # 类型筛选（genre标签+音频特征+MERT场景种子）→ 个性化排序（用户音频偏好余弦），
            # 返回与 /api/tracks 同构列表，前端点击场景卡片直接播放。
            if not rate_limit(self, "radio"):
                self._err(429, "请求过于频繁，请稍后再试")
                return True
            tag = urllib.parse.unquote(u.path.rsplit("/", 1)[-1])
            qs = urllib.parse.parse_qs(u.query)
            viewer_id = (qs.get("userId", [""])[0] or "").strip()
            # 场景随机种子（2026-11-05）：客户端每次点场景带新 seed，
            # 后端按其做「分档窗口洗牌」→ 每次都换顺序；同一 seed 结果可复现
            seed = (qs.get("seed", [""])[0] or "").strip() or None
            c = db()
            tok_uid = resolve_token(c, self.headers.get("Authorization", ""))
            if tok_uid:
                viewer_id = tok_uid
            try:
                from server.radio import radio_tracks
                chosen = radio_tracks(c, tag, viewer_id=viewer_id, limit=60, seed=seed)
            except Exception:
                log.exception("radio %s failed", tag)
                chosen = []
            c.close()
            if not chosen:
                self._json(200, [])
                return True
            c = db()
            ph = ",".join("?" * len(chosen))
            trows = c.execute("""SELECT t.id,t.title,t.artist,t.album,t.cover,t.fname,t.size,t.dur,t.created,
                                 t.play_count,t.like_count,t.share_count,t.user_id,t.lrc,
                                 COALESCE(u.name,''),
                                 (SELECT COUNT(*) FROM comments cm WHERE cm.track_id=t.id),
                                 MAX(CASE WHEN l.user_id IS NOT NULL THEN 1 ELSE 0 END)
                                 FROM tracks t
                                 LEFT JOIN users u ON u.id=t.user_id
                                 LEFT JOIN likes l ON l.track_id=t.id AND l.user_id=?
                                 WHERE t.id IN (%s) GROUP BY t.id""" % ph,
                               tuple([viewer_id] + chosen)).fetchall()
            crows = c.execute("""SELECT id,title,artist,album,cover,duration,size,created,
                                 play_count,like_count,share_count,user_id,lrc
                                 FROM collected_tracks WHERE id IN (%s)""" % ph, chosen).fetchall()
            uname_map = {}
            uids = {r[11] for r in crows if r[11]}
            if uids:
                uph = ",".join("?" * len(uids))
                for urow in c.execute("SELECT id, name FROM users WHERE id IN (%s)" % uph,
                                      tuple(uids)):
                    uname_map[urow[0]] = urow[1] or ""
            c.close()
            merged = [{"id": r[0], "title": r[1], "artist": r[2], "album": r[3],
                       "cover": "1", "fname": "",
                       "size": int(r[6]) if r[6] is not None else 0,
                       "dur": r[5], "durText": _fmt_dur(r[5]), "created": r[7],
                       "playCount": r[8] or 0, "likeCount": r[9] or 0,
                       "shareCount": r[10] or 0,
                       "userId": r[11] or "", "userName": uname_map.get(r[11] or "", ""),
                       "hasLyric": bool(r[12]),
                       "reason": tag, "recRoute": "radio",
                       "commentCount": 0, "isLiked": False} for r in crows]
            merged += [{"id": r[0], "title": r[1], "artist": r[2], "album": r[3],
                        "cover": r[4], "fname": r[5], "size": r[6], "dur": r[7],
                        "durText": _fmt_dur(r[7]), "created": r[8],
                        "playCount": r[9] or 0, "likeCount": r[10] or 0,
                        "shareCount": r[11] or 0,
                        "userId": r[12] or "", "userName": r[14] or "",
                        "hasLyric": bool(r[13]),
                        "reason": tag, "recRoute": "radio",
                        "commentCount": r[15] or 0,
                        "isLiked": bool(r[16])} for r in trows]
            order = {tid: i for i, tid in enumerate(chosen)}
            merged.sort(key=lambda x: order.get(x["id"], 999))
            self._json(200, merged)
            return True
        if u.path.startswith("/api/cover/"):
            tid = u.path.rsplit("/", 1)[-1]
            if tid.endswith(".jpg"):
                tid = tid[:-4]      # og:image 稳定 URL：/api/cover/{tid}.jpg
            c = db()
            row = c.execute("SELECT cover FROM tracks WHERE id=?", (tid,)).fetchone()
            crow = c.execute("SELECT cover FROM collected_tracks WHERE id=?", (tid,)).fetchone()
            c.close()
            fp = safe_file_path(row[0] if row else "")
            if not fp and crow:
                fp = safe_file_path(crow[0] or "")
            if not fp:
                # 采集歌曲封面：优先服务本地下载的 files/{id}_cover.jpg（手机端可直接加载）
                local = safe_file_path(tid + "_cover.jpg")
                if local and os.path.exists(local):
                    self._serve_file(cover_small(local, tid))
                    return True
                # 本地封面缺失：保留第三方直链 302 兜底（仅当 cover 为 http 地址）
                if crow and crow[0] and str(crow[0]).startswith("http"):
                    self.send_response(302)
                    self.send_header("Content-Length", "0")
                    self.send_header("Location", crow[0])
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    return True
                self._err(404, "no cover")
                return True
            self._serve_file(cover_small(fp, tid))
            return True
        if u.path.startswith("/api/lyric/"):
            tid = u.path.rsplit("/", 1)[-1]
            c = db()
            if not self._share_or_auth(c, u, tid):
                c.close()
                self._err(403, "forbidden")
                return True
            row = c.execute("SELECT lrc FROM tracks WHERE id=?", (tid,)).fetchone()
            # 采集歌曲歌词：库内 lrc 文本直接返回（非文件路径）
            crow = c.execute("SELECT lrc FROM collected_tracks WHERE id=?", (tid,)).fetchone()
            c.close()
            fp = safe_file_path(row[0] if row else "")
            if not fp:
                if crow and crow[0]:
                    data = crow[0].encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return True
                self._err(404, "no lyric")
                return True
            with open(fp, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return True
        if u.path.startswith("/api/avatar/"):
            uid_ = u.path.rsplit("/", 1)[-1]
            c = db()
            row = c.execute("SELECT avatar FROM users WHERE id=?", (uid_,)).fetchone()
            c.close()
            fp = safe_file_path(row[0] if row else "")
            if not fp:
                self._err(404, "no avatar")
                return True
            self._serve_file(fp)
            return True
        if u.path.startswith("/api/background/"):
            uid_ = u.path.rsplit("/", 1)[-1]
            c = db()
            row = c.execute("SELECT background_image FROM users WHERE id=?", (uid_,)).fetchone()
            c.close()
            fp = safe_file_path(row[0] if row else "")
            if not fp:
                self._err(404, "no background")
                return True
            self._serve_file(fp)
            return True
        if u.path.startswith("/api/comment-image/"):
            name = u.path.rsplit("/", 1)[-1]
            # 白名单：只允许本服务评论图片上传接口生成的文件名（防目录穿越/任意文件读取）
            if not re.fullmatch(r"cmt_[0-9a-f]{32}\.(?:jpg|jpeg|png|webp)", name):
                self._err(404, "no image")
                return True
            fp = safe_file_path(name)
            if not fp or not os.path.exists(fp):
                self._err(404, "no image")
                return True
            self._serve_file(fp)
            return True
        if u.path.startswith("/api/banner/"):
            name = u.path.rsplit("/", 1)[-1]
            # 白名单：只允许本服务轮播图上传接口生成的文件名（防目录穿越/任意文件读取）
            if not re.fullmatch(r"banner_[0-9a-f]{32}\.(?:jpg|jpeg|png|webp)", name):
                self._err(404, "no image")
                return True
            fp = safe_file_path(name)
            if not fp or not os.path.exists(fp):
                self._err(404, "no image")
                return True
            self._serve_file(fp)
            return True
        if u.path.startswith("/api/splash/"):
            name = u.path.rsplit("/", 1)[-1]
            if not re.fullmatch(r"splash_[0-9a-f]{32}\.(?:jpg|jpeg|png|webp)", name):
                self._err(404, "no image")
                return True
            fp = safe_file_path(name)
            if not fp or not os.path.exists(fp):
                self._err(404, "no image")
                return True
            self._serve_file(fp)
            return True
        if u.path == "/api/splash-ad":
            # SVIP 特权：无广告（2026-10-19 会员体系）——SVIP 用户直接返回无广告
            _svip = False
            try:
                c2 = db()
                _su = resolve_token(c2, self.headers.get("Authorization", ""))
                if _su:
                    _r = c2.execute("SELECT member_expire, svip_expire FROM users WHERE id=?", (_su,)).fetchone()
                    _svip = bool(_r and _r[1] and _r[1] > time.time())
                c2.close()
            except Exception:
                pass
            if _svip:
                self._json(200, {"ad": None, "reason": "svip_no_ads"})
                return True
            c = db()
            try:
                rows = c.execute("SELECT name,title,link FROM splash_ads WHERE active=1 ORDER BY sort,id").fetchall()
            finally:
                c.close()
            if not rows:
                self._json(200, {"ad": None})
                return True
            r = rows[random.randrange(len(rows))]
            self._json(200, {"ad": {
                "name": r[0], "title": r[1] or "", "link": r[2] or "",
                "img": "/api/splash/" + r[0]
            }})
            return True
        if u.path.startswith("/api/package/") or u.path.startswith("/music/api/package/"):
            name = u.path.rsplit("/", 1)[-1]
            if not re.fullmatch(r"package_[0-9a-f]{32}\.(?:apk|ipa)", name):
                self._err(404, "no package")
                return True
            fp = safe_file_path(name)
            if not fp or not os.path.exists(fp):
                self._err(404, "no package")
                return True
            self._serve_file(fp, cacheable=False)
            return True
        if u.path.startswith("/api/comments/"):
            tid = u.path.rsplit("/", 1)[-1]
            c = db()
            rows = c.execute("""SELECT id,user_id,user_name,content,images,created
                                FROM comments WHERE track_id=? ORDER BY created DESC LIMIT 200""",
                             (tid,)).fetchall()
            # AI 标识：评论者是否为 AI 乐评账号（透明标识）
            ai_ids = set()
            user_ids = {r[1] for r in rows if r[1]}
            # 评论者头衔/认证/会员（实时取 users，后台修改后即时生效）
            user_meta = {}
            if user_ids:
                for (i,) in c.execute(
                        "SELECT id FROM users WHERE id IN (%s) AND is_ai=1" %
                        ",".join("?" * len(user_ids)), tuple(user_ids)).fetchall():
                    ai_ids.add(i)
                for rid, rtitle, rv, rlabel, rvip, rsvip in c.execute(
                        "SELECT id,title,verified,verified_label,member_expire,svip_expire"
                        " FROM users WHERE id IN (%s)" % ",".join("?" * len(user_ids)),
                        tuple(user_ids)).fetchall():
                    user_meta[rid] = (rtitle or "", rv or "", rlabel or "",
                                      member_level(rvip, rsvip))
            # 评论者在线状态（WebSocket 活跃连接快照，批量判断）
            with WS_LOCK:
                online_uids = set(WS_CLIENTS.keys())
            c.close()
            def _parse_images(raw):
                if not raw:
                    return []
                try:
                    v = json.loads(raw)
                    return v if isinstance(v, list) else []
                except Exception:
                    return []
            self._json(200, [{"id": r[0], "userId": r[1], "userName": r[2],
                              "content": r[3], "images": _parse_images(r[4]),
                              "created": r[5], "isAi": r[1] in ai_ids,
                              "userOnline": r[1] in online_uids,
                              "title": (user_meta.get(r[1]) or ("", "", "", ""))[0],
                              "verified": (user_meta.get(r[1]) or ("", "", "", ""))[1],
                              "verifiedLabel": (user_meta.get(r[1]) or ("", "", "", ""))[2],
                              "member": (user_meta.get(r[1]) or ("", "", "", ""))[3]} for r in rows])
            return True
        if u.path.startswith("/api/stream-prepare/"):
            q = urllib.parse.parse_qs(u.query)
            exp_s = (q.get("exp") or [""])[0]
            sig = (q.get("sig") or [""])[0]
            if exp_s or sig:
                if not verify_stream(u.path.rsplit("/", 1)[-1], exp_s, sig):
                    self._err(403, "stream link expired or invalid")
                    return True
            elif SIGN_STRICT:
                self._err(403, "stream link expired or invalid")
                return True
            if not rate_limit(self, "stream"):
                self._err(429, "请求过于频繁，请稍后再试")
                return True
            tid = u.path.rsplit("/", 1)[-1]
            c = db()
            row = c.execute("SELECT fname FROM tracks WHERE id=?", (tid,)).fetchone()
            crow = c.execute("SELECT play_url FROM collected_tracks WHERE id=?", (tid,)).fetchone()
            c.close()
            fp = safe_file_path(row[0] if row else "")
            if not fp and crow and crow[0] and not str(crow[0]).startswith("http"):
                local = safe_file_path(str(crow[0]))
                if local and os.path.exists(local):
                    fp = local
            if not fp:
                self._err(404, "not found")
                return True
            # 低码率已停用（2026-10-24 用户要求）：不再后台转码生成 files_low 缓存
            self._json(200, {"status": "ok"})
            return True
        if u.path.startswith("/api/stream-url/"):
            if not rate_limit(self, "stream"):
                self._err(429, "请求过于频繁，请稍后再试")
                return True
            tid = u.path.rsplit("/", 1)[-1]
            c = db()
            if not self._share_or_auth(c, u, tid):
                c.close()
                self._err(403, "forbidden")
                return True
            exists = c.execute("SELECT 1 FROM tracks WHERE id=?", (tid,)).fetchone()
            if not exists:
                # 采集歌曲同样签发签名（播放链路见 /api/stream 分支）
                exists = c.execute("SELECT 1 FROM collected_tracks WHERE id=?", (tid,)).fetchone()
            c.close()
            if not exists:
                self._err(404, "not found")
                return True
            exp = int(time.time()) + STREAM_SIGN_TTL
            self._json(200, {"sig": sign_stream(tid, exp), "exp": exp})
            return True
        if u.path.startswith("/api/share-url/"):
            if not rate_limit(self, "share_url"):
                self._err(429, "请求过于频繁，请稍后再试")
                return True
            tid = u.path.rsplit("/", 1)[-1]
            c = db()
            row = c.execute("SELECT token, short_code FROM share_tokens WHERE track_id=?", (tid,)).fetchone()
            if not row:
                # 先查上传歌曲，再查采集歌曲（两类歌曲都支持分享短链）
                row = c.execute("SELECT 1 FROM tracks WHERE id=?", (tid,)).fetchone()
                if not row:
                    row = c.execute("SELECT 1 FROM collected_tracks WHERE id=?", (tid,)).fetchone()
                if not row:
                    c.close()
                    self._err(404, "not found")
                    return True
                for _ in range(5):
                    tok = secrets.token_hex(6)          # 12 位随机，不可反推歌曲 ID
                    code = gen_short_code()             # 8 位短码（汽水风格短链）
                    try:
                        c.execute("INSERT INTO share_tokens(token, track_id, created, short_code) VALUES(?,?,?,?)",
                                  (tok, tid, time.time(), code))
                        c.commit()
                        row = (tok, code)
                        break
                    except sqlite3.IntegrityError:
                        continue
            elif not row[1]:
                code = gen_short_code()
                c.execute("UPDATE share_tokens SET short_code=? WHERE token=?", (code, row[0]))
                c.commit()
                row = (row[0], code)
            token, code = row
            c.close()
            self._json(200, {"token": token, "code": code})
            return True
        if u.path.startswith("/s/"):
            if not rate_limit(self, "share_url"):
                self._err(429, "请求过于频繁，请稍后再试")
                return True
            code = u.path.strip("/").rsplit("/", 1)[-1]
            c = db()
            row = c.execute("SELECT track_id, token FROM share_tokens WHERE short_code=?", (code,)).fetchone()
            if not row:
                c.close()
                self._err(404, "not found")
                return True
            tid, share_tok = row[0], row[1]
            tr = c.execute("""SELECT id, title, artist, cover, lrc, user_id,
                              COALESCE((SELECT name FROM users WHERE id=tracks.user_id), '')
                              FROM tracks WHERE id=?""", (tid,)).fetchone()
            if not tr:
                tr = c.execute("SELECT id, title, artist, cover, lrc, user_id, '' FROM collected_tracks WHERE id=?", (tid,)).fetchone()
            c.close()
            if not tr:
                self._err(404, "not found")
                return True
            _, title, artist, cover, _lrc, _uid, uname = tr
            author = uname or artist or "未知歌手"
            # 社交卡片抓取器（QQ/微信/Telegram）通常不跟随 302：/s/ 直接 200 渲染
            # 分享页（含 OG 标签），保证一次性拿到完整卡片数据
            page = self._share_page(title, author, tid, cover, code, share_token=share_tok)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(page)
            return True
        if u.path.startswith("/api/stream/"):
            # 签名校验：带签名参数必须通过；无签名在 SIGN_STRICT=1 时拒绝（默认兼容旧客户端）
            q = urllib.parse.parse_qs(u.query)
            exp_s = (q.get("exp") or [""])[0]
            sig = (q.get("sig") or [""])[0]
            if exp_s or sig:
                if not verify_stream(u.path.rsplit("/", 1)[-1], exp_s, sig):
                    self._err(403, "stream link expired or invalid")
                    return True
            elif SIGN_STRICT:
                self._err(403, "stream link expired or invalid")
                return True
            if not rate_limit(self, "stream"):
                self._err(429, "请求过于频繁，请稍后再试")
                return True
            tid = u.path.rsplit("/", 1)[-1]
            c = db()
            row = c.execute("SELECT fname FROM tracks WHERE id=?", (tid,)).fetchone()
            crow = c.execute("SELECT play_url FROM collected_tracks WHERE id=?", (tid,)).fetchone()
            c.close()
            fp = safe_file_path(row[0] if row else "")
            # 采集歌曲：已下载到本地时 play_url 存本地文件名，走本地流式播放（同 tracks）
            if not fp and crow and crow[0] and not str(crow[0]).startswith("http"):
                local = safe_file_path(str(crow[0]))
                if local and os.path.exists(local):
                    fp = local
            if not fp:
                # 未下载的采集歌曲：保留第三方直链 302 兜底
                if crow and crow[0] and str(crow[0]).startswith("http"):
                    self.send_response(302)
                    self.send_header("Content-Length", "0")
                    self.send_header("Location", crow[0])
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    return True
                self._err(404, "not found")
                return True
            # 低码率已停用（2026-10-24 用户要求）：?br=96/low 请求一律回退原码率，不再转码
            low_fallback = False
            br = (q.get("br") or [""])[0]
            if br in ("96", "low"):
                low_fallback = True            # 本次回退原码率，不阻塞播放
            size = os.path.getsize(fp)
            ctype = mimetypes.guess_type(fp)[0] or "audio/mpeg"
            rng = self._parse_range(size)
            if rng is True:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                return True
            if rng:
                start, end = rng
                length = end - start + 1
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                if low_fallback:
                    self.send_header("X-Bitrate-Fallback", "1")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                try:
                    with open(fp, "rb") as f:
                        f.seek(start)
                        remain = length
                        while remain > 0:
                            chunk = f.read(min(65536, remain))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remain -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return True
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            if low_fallback:
                self.send_header("X-Bitrate-Fallback", "1")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                with open(fp, "rb") as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return True
        # ===== 会员/认证（2026-10-19 会员体系）=====
        if u.path == "/api/me/membership":
            c = db()
            uid = resolve_token(c, self.headers.get("Authorization", ""))
            if not uid:
                c.close()
                self._err(401, "unauthorized"); return True
            row = c.execute("SELECT member_expire, svip_expire FROM users WHERE id=?", (uid,)).fetchone()
            c.close()
            if not row:
                self._err(404, "user not found"); return True
            vip, svip = row[0] or 0, row[1] or 0
            self._json(200, {"member": member_level(vip, svip),
                             "memberExpire": vip, "svipExpire": svip})
            return True
        if u.path == "/api/verify/categories":
            c = db()
            rows = c.execute("SELECT id, level1, level2 FROM verification_categories ORDER BY created").fetchall()
            c.close()
            self._json(200, {"list": [{"id": r[0], "level1": r[1], "level2": r[2] or ""} for r in rows]})
            return True
        if u.path == "/api/verify/my":
            c = db()
            uid = resolve_token(c, self.headers.get("Authorization", ""))
            if not uid:
                c.close()
                self._err(401, "unauthorized"); return True
            rows = c.execute("SELECT id, level1, level2, label, reason, status, created, reviewed_at, review_note"
                             " FROM verification_applications WHERE user_id=? ORDER BY created DESC LIMIT 20",
                             (uid,)).fetchall()
            c.close()
            self._json(200, {"list": [{"id": r[0], "level1": r[1], "level2": r[2] or "",
                                       "label": r[3] or "", "reason": r[4] or "",
                                       "status": r[5], "created": r[6], "reviewedAt": r[7] or 0,
                                       "reviewNote": r[8] or ""} for r in rows]})
            return True
        self._err(404, "unknown")
        return False

    def _h_post_content(self, u):
        length = int(self.headers.get("Content-Length", 0))
        # ===== 显式负反馈：不感兴趣（2026-11-05）=====
        # body: {"trackId":"<id>","kind":"track|artist|genre|mood","value":"<可选>","reason":"<可选>"}
        # 不传 value 时按 kind 自动从歌曲信息里推导（歌手/风格/情绪），前端可以只发一个按钮事件。
        if u.path == "/api/dislike":
            c = db()
            try:
                uid = resolve_token(c, self.headers.get("Authorization", ""))
                if not uid:
                    self._err(401, "unauthorized")
                    return True
                try:
                    raw = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 8 * 1024))
                    data = json.loads(raw.decode("utf-8") or "{}")
                except Exception:
                    self._err(400, "bad json")
                    return True
                tid = str(data.get("trackId") or data.get("id") or "").strip()
                kind = str(data.get("kind") or "track").strip().lower()
                if kind not in ("track", "artist", "genre", "mood", "scene"):
                    kind = "track"
                value = str(data.get("value") or "").strip()
                reason = str(data.get("reason") or "").strip()[:60]
                if not value:
                    if not tid:
                        self._err(400, "trackId required")
                        return True
                    if kind == "track":
                        value = tid
                    else:
                        row = c.execute(
                            "SELECT COALESCE(artist,''),COALESCE(genre,'') FROM collected_tracks WHERE id=?"
                            " UNION ALL SELECT COALESCE(artist,''),COALESCE(genre,'') FROM tracks WHERE id=?",
                            (tid, tid)).fetchone()
                        if not row:
                            self._err(404, "track not found")
                            return True
                        if kind == "artist":
                            value = (row[0] or "").split("/")[0].split("&")[0].strip()
                        elif kind == "genre":
                            value = (row[1] or "").split(",")[0].strip()
                        else:
                            tr = c.execute(
                                "SELECT tag FROM track_tags WHERE track_id=?"
                                " AND kind IN ('mood','scene') LIMIT 1", (tid,)).fetchone()
                            value = tr[0] if tr else ""
                if not value:
                    self._json(200, {"ok": True, "skipped": True})
                    return True
                c.execute(
                    "INSERT OR REPLACE INTO rec_dislikes(uid,kind,value,reason,created)"
                    " VALUES(?,?,?,?,?)", (uid, kind, value, reason, time.time()))
                # 拉黑整首歌时顺手清掉点赞（避免"拉黑了还显示红心"）
                if kind == "track":
                    try:
                        c.execute("DELETE FROM likes WHERE user_id=? AND track_id=?", (uid, tid))
                    except Exception:
                        pass
                c.commit()
                # 让推荐侧缓存立刻失效，下一次请求就生效
                try:
                    from server.recommend import _REC_DISLIKE_CACHE
                    _REC_DISLIKE_CACHE.pop(uid, None)
                except Exception:
                    pass
                # 每日推荐是当天缓存的，拉黑后必须把该用户的缓存踢掉，否则还会返回它
                try:
                    for _k in [k for k in _DAILY_CACHE if k[0] == (uid or "-")]:
                        _DAILY_CACHE.pop(_k, None)
                except Exception:
                    pass
                self._json(200, {"ok": True, "kind": kind, "value": value})
                return True
            except Exception as e:
                self._internal_error(e)
                return True
            finally:
                try:
                    c.close()
                except Exception:
                    pass
        # ===== 认证申请（2026-10-19 会员体系）=====
        if u.path == "/api/verify/apply":
            c = db()
            uid = resolve_token(c, self.headers.get("Authorization", ""))
            if not uid:
                c.close()
                self._err(401, "unauthorized"); return True
            try:
                raw = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 32 * 1024))
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._err(400, "bad json"); return True
            level1 = (data.get("level1") or "").strip()
            level2 = (data.get("level2") or "").strip()
            label = (data.get("label") or "").strip()[:40]
            reason = (data.get("reason") or "").strip()[:200]
            if not level1 or not reason:
                self._err(400, "level1 and reason required"); return True
            # 已有待审核申请不允许重复提交
            pend = c.execute("SELECT id FROM verification_applications WHERE user_id=? AND status='pending'",
                             (uid,)).fetchone()
            if pend:
                c.close()
                self._err(400, "已有待审核申请"); return True
            app_id = uuid.uuid4().hex
            c.execute("""INSERT INTO verification_applications(id,user_id,level1,level2,label,reason,status,created)
                         VALUES(?,?,?,?,?,?,?,?)""",
                      (app_id, uid, level1, level2, label, reason, "pending", time.time()))
            # 提交成功确认通知（官方通知聊天页面定向显示）
            try:
                _om_content = "我们已收到你的「%s」认证申请，将在 24 小时内完成审核，请留意官方通知。" % (level1 or "")
                nid = uuid.uuid4().hex
                _official_send(c, uid, _om_content)
                c.execute("""INSERT INTO notifications(id, user_id, type, title, content, from_user_id, from_user_name, track_id, track_title, is_read, created)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                          (nid, uid, "official", "认证申请已提交",
                           _om_content,
                           "", "流萤音乐官方", "", "", 0, time.time()))
                ws_push(uid, {"kind": "notification",
                             "notification": {"id": nid, "type": "official", "title": "认证申请已提交",
                                              "content": _om_content,
                                              "fromUserId": "", "fromUser": "流萤音乐官方",
                                              "trackId": "", "trackTitle": "", "isRead": 0,
                                              "created": time.time()},
                             "unread": _notif_unread(c, uid)})
            except Exception as _e:
                log.info("verify apply notif err: %s", _e)
            c.commit()
            c.close()
            self._json(200, {"ok": True, "id": app_id})
            return True
        # ===== 支付预留（易支付，2026-10-19）=====
        if u.path == "/api/pay/create":
            # App 内开通会员下单：后台 H5「网站管理→支付设置」配置易支付商户ID/密钥/网关后启用
            c = db()
            uid = resolve_token(c, self.headers.get("Authorization", ""))
            if not uid:
                c.close()
                self._err(401, "unauthorized"); return True
            try:
                raw = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 16 * 1024))
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._err(400, "bad json"); return True
            plan = (data.get("plan") or "").strip()
            if plan not in ("vip", "svip"):
                self._err(400, "plan must be vip or svip"); return True
            term = (data.get("term") or "month").strip()
            if term not in ("month", "quarter", "year"):
                self._err(400, "term must be month/quarter/year"); return True
            oid = uuid.uuid4().hex
            order_no = "LY" + uuid.uuid4().hex.upper()[:16]
            # 档位价格与时长：以后台「网站管理→支付设置」配置为准（vip/svip × month/quarter/year 价格与天数），前端不写死
            cfg0 = _read_site_config()
            amount = float(cfg0.get("%s_%s_price" % (plan, term)) or 0)
            days = int(cfg0.get("%s_%s_days" % (plan, term)) or 0)
            if amount <= 0 or days <= 0:
                self._json(200, {"ok": True, "orderNo": order_no, "amount": amount,
                                 "plan": plan, "term": term, "payUrl": "", "note": "该档位价格未配置，请在后台设置后重试"})
                return True
            c.execute("""INSERT INTO payment_orders(id,order_no,user_id,plan,amount,days,status,created)
                         VALUES(?,?,?,?,?,?,?,?)""",
                      (oid, order_no, uid, plan, amount, days, "pending", time.time()))
            c.commit()
            c.close()
            cfg = cfg0
            pid = (cfg.get("pay_merchant_id") or "").strip()
            pkey = (cfg.get("pay_merchant_key") or "").strip()
            gw = (cfg.get("pay_gateway") or "").strip()
            if not cfg.get("pay_enabled") or not pid or not pkey or not gw:
                self._json(200, {"ok": True, "orderNo": order_no, "amount": amount,
                                 "plan": plan, "term": term, "days": days,
                                 "payUrl": "", "note": "支付未启用，请在后台配置易支付"})
                return True
            # 回调地址：后台未填时自动使用当前站点 /api/pay/notify
            host = self.headers.get("Host", "")
            scheme = "https" if self.headers.get("X-Forwarded-Proto", "http") == "https" else "http"
            notify_url = (cfg.get("pay_notify_url") or "").strip() or ("%s://%s/api/pay/notify" % (scheme, host))
            return_url = (cfg.get("pay_return_url") or "").strip() or ("%s://%s/api/pay/return" % (scheme, host))
            params = {
                "pid": pid,
                "type": "alipay",
                "out_trade_no": order_no,
                "notify_url": notify_url,
                "return_url": return_url,
                "name": "流萤音乐%s会员%d天" % ("SVIP" if plan == "svip" else "VIP", days),
                "money": str(amount),
            }
            sign = _pay_sign(params, pkey)
            q = urllib.parse.urlencode(dict(params, sign=sign, sign_type="MD5"))
            pay_gw = gw.rstrip("/")
            if not pay_gw.endswith(".php"):
                pay_gw += "/submit.php"
            self._json(200, {"ok": True, "orderNo": order_no, "amount": amount,
                             "plan": plan, "term": term, "days": days,
                             "payUrl": pay_gw + "?" + q, "note": ""})
            return True
        if u.path == "/api/pay/notify":
            # 易支付异步回调（GET/POST 通用），逻辑在 _pay_notify_handle
            return self._pay_notify_handle()
        if u.path != "/api/upload":
            self._err(404, "unknown")
            return True
        if not rate_limit(self, "upload"):
            self._err(429, "上传过于频繁，请稍后再试")
            return True
        try:
            if length > MAX_UPLOAD + 1024 * 1024:
                self._err(413, "too large")
                return True
            if length <= 0:
                self._err(400, "empty body")
                return True
            fields, files = self._parse_multipart(length)
            if fields is None:
                self._err(400, "need multipart")
                return True
            c = db()
            uid, via_token = self.auth_user(c, fields)
            if not uid:
                c.close()
                self._err(401, "未登录或登录已过期")
                return True
            audio = files.get("file")
            if not audio or not audio[0]:
                c.close()
                self._err(400, "no file")
                return True
            filename, filedata = audio
            if len(filedata) > MAX_UPLOAD:
                c.close()
                self._err(413, "too large")
                return True
            ext = os.path.splitext(filename)[1].lower()
            if ext not in (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"):
                c.close()
                self._err(400, "unsupported audio type")
                return True
            if not audio_magic_ok(filedata, ext):
                c.close()
                self._err(400, "音频文件内容校验失败")
                return True
            tid = uuid.uuid4().hex
            stored = tid + ext
            fp = os.path.join(FILES, stored)
            with open(fp, "wb") as f:
                f.write(filedata)
            dur = audio_duration(fp)
            title = (fields.get("title") or os.path.splitext(filename)[0]).strip()[:MAX_NAME] or "未命名"
            cover_fname = ""
            cover = files.get("cover")
            if cover and cover[0]:
                cfn, cdata = cover
                cext = os.path.splitext(cfn)[1].lower()
                if cext not in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
                    cext = ".jpg"
                if not image_magic_ok(cdata, cext):
                    c.close()
                    self._err(400, "封面文件格式校验失败")
                    return True
                cover_fname = tid + "_cover" + cext
                cpath = os.path.join(FILES, cover_fname)
                with open(cpath, "wb") as f:
                    f.write(cdata)
            lrc_fname = ""
            lrc_file = files.get("lrc")
            if lrc_file and lrc_file[0]:
                _, ldata = lrc_file
                if len(ldata) > 512 * 1024:
                    ldata = ldata[:512 * 1024]
                lrc_fname = tid + ".lrc"
                lpath = os.path.join(FILES, lrc_fname)
                with open(lpath, "wb") as f:
                    f.write(ldata)
            c.execute("""INSERT INTO tracks(id,title,artist,album,cover,lrc,fname,size,dur,created,user_id)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                      (tid, title, (fields.get("artist") or "").strip()[:MAX_NAME],
                       (fields.get("album") or "").strip()[:MAX_NAME],
                       cover_fname, lrc_fname, stored, len(filedata), dur, time.time(), uid))
            c.commit()
            # 实时数据事件：我的作品列表更新 + 全局歌曲列表更新（广播在线用户）
            ws_push(uid, {"kind": "event", "event": "my_tracks", "action": "upload"})
            ws_broadcast({"kind": "event", "event": "track_list", "reason": "upload"})
            invalidate_id_pool()   # #7：新歌立即可被随机池选中
            c.close()
            self._json(200, {"id": tid, "title": title, "cover": cover_fname,
                             "dur": dur, "durText": _fmt_dur(dur)})
        except Exception as e:
            self._internal_error(e)
        return False
