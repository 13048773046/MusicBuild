# -*- coding: utf-8 -*-
"""P2-7：front 域路由（mixin，由 server/handler.py 的 H 类继承）。
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


class FrontMixin:

    def _h_get_front(self, u):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/ws":
            _ws_handshake(self, u)
            return True
        if u.path in ("/", "/api/health"):
            self._json(200, {"ok": True, "service": "music",
                             "time": time.strftime("%Y-%m-%d %H:%M:%S")})
            return True
        if u.path == "/api/site-config":
            cfg = _read_site_config()
            # 敏感字段不外泄：商户密钥只给掩码状态（后台表单留空=保持不变）
            if cfg.get("pay_merchant_key"):
                cfg["pay_merchant_key"] = "已配置（留空保持不变）"
            self._json(200, cfg)
            return True
        if u.path == "/api/announcement":
            c = db()
            row = c.execute("SELECT id, title, content, version, show_once FROM announcements"
                            " WHERE active=1 AND type='announcement' ORDER BY created DESC LIMIT 1").fetchone()
            c.close()
            if not row:
                self._json(200, {"id": "", "title": "", "content": "", "version": "", "showOnce": 0})
                return True
            self._json(200, {"id": row[0], "title": row[1], "content": row[2],
                             "version": row[3] or "", "showOnce": row[4] or 0})
            return True
        if u.path == "/api/check-update":
            qs = urllib.parse.parse_qs(u.query)
            client_v = (qs.get("v") or [""])[0].strip()
            platform = (qs.get("platform") or [""])[0].strip().lower()
            device_id = (qs.get("device_id") or [""])[0].strip()
            # 顺带记录活跃设备（App 每次检查更新都会带 device_id + platform）
            if device_id:
                try:
                    import time as _t
                    c2 = db()
                    c2.execute(
                        "INSERT INTO devices(device_id, platform, app_version, first_seen, last_seen) VALUES(?,?,?,?,?) "
                        "ON CONFLICT(device_id) DO UPDATE SET platform=excluded.platform, "
                        "app_version=excluded.app_version, last_seen=excluded.last_seen",
                        (device_id, platform, client_v, _t.time(), _t.time()))
                    c2.commit()
                    c2.close()
                except Exception:
                    pass
            c = db()
            row = c.execute("SELECT id, title, content, version, force_update, download_url,"
                            " download_url_ios, download_url_android, version_ios, version_android"
                            " FROM announcements"
                            " WHERE active=1 AND type='update' ORDER BY created DESC LIMIT 1").fetchone()
            c.close()
            if not row:
                self._json(200, {"hasUpdate": False})
                return True
            latest_v = row[3] or ""
            # 双端版本号独立：iOS 与 Android 迭代节奏不同（如 iOS 1.1.44 / Android 1.1.24），
            # 若共用单一 version，低版本平台会陷入"永远提示更新、下完还是同版本"的循环。
            v_ios = (row[8] or "").strip() or latest_v
            v_android = (row[9] or "").strip() or latest_v
            target_v = v_android if platform == "android" else v_ios
            if client_v and target_v and not _version_lt(client_v, target_v):
                self._json(200, {"hasUpdate": False})
                return True
            ios_url = row[6] or row[5] or ""
            android_url = row[7] or row[5] or ""
            # 按设备平台分发下载链接：安卓拿安卓包、苹果拿 iOS 包，杜绝混淆
            dl = android_url if platform == "android" else ios_url
            self._json(200, {"hasUpdate": True, "id": row[0], "title": row[1],
                             "content": row[2], "version": target_v,
                             # sqlite 返回 str，必须转 int：iOS/Android 客户端按 Int 解析，字符串会导致强制更新失效
                             "forceUpdate": int(row[4] or 0),
                             "downloadUrl": dl,
                             "downloadUrlIos": ios_url,
                             "downloadUrlAndroid": android_url})
            return True
        if u.path == "/api/history":
            c = db()
            uid = resolve_token(c, self.headers.get("Authorization", ""))
            if not uid:
                c.close()
                self._json(200, {"list": [], "total": 0, "hasMore": False})
                return True
            qs = urllib.parse.parse_qs(u.query)
            page, page_size = parse_page(qs)
            total = c.execute("SELECT COUNT(*) FROM play_history WHERE user_id=?", (uid,)).fetchone()[0]
            if page is None:
                rows = c.execute("SELECT id, track_id, track_title, track_artist, track_cover, source, created FROM play_history WHERE user_id=? ORDER BY created DESC LIMIT 100", (uid,)).fetchall()
            else:
                rows = c.execute("SELECT id, track_id, track_title, track_artist, track_cover, source, created FROM play_history WHERE user_id=? ORDER BY created DESC LIMIT ? OFFSET ?",
                                (uid, page_size, (page - 1) * page_size)).fetchall()
            c.close()
            lst = [{"id": r[0], "trackId": r[1], "title": r[2], "artist": r[3], "cover": r[4], "source": r[5], "created": r[6]} for r in rows]
            self._json(200, {"list": lst, "total": total, "hasMore": bool(page is not None and page * page_size < total)})
            return True
        if u.path == "/api/playlists":
            c = db()
            uid = resolve_token(c, self.headers.get("Authorization", ""))
            if not uid:
                c.close()
                self._json(200, {"list": []})
                return True
            rows = c.execute("SELECT id, name, cover, description, track_count, created FROM playlists WHERE user_id=? ORDER BY created DESC", (uid,)).fetchall()
            c.close()
            self._json(200, {"list": [{"id": r[0], "name": r[1], "cover": r[2] or "", "description": r[3] or "", "trackCount": r[4] or 0, "created": r[5]} for r in rows]})
            return True
        if u.path == "/share.html":
            share_html = _render_template("share_collect.html")
            self._send_html(share_html)
            return True
        if u.path.startswith("/api/share/"):
            if not rate_limit(self, "share_url"):
                self._err(429, "请求过于频繁，请稍后再试")
                return True
            token = u.path.rsplit("/", 1)[-1]
            c = db()
            row = c.execute("SELECT track_id, short_code FROM share_tokens WHERE token=?", (token,)).fetchone()
            if not row:
                c.close()
                self._err(404, "not found")
                return True
            tid, short_code = row
            tr = c.execute("""SELECT id, title, artist, cover, lrc, user_id,
                              COALESCE((SELECT name FROM users WHERE id=tracks.user_id), '')
                              FROM tracks WHERE id=?""", (tid,)).fetchone()
            if not tr:
                # 采集歌曲：从 collected_tracks 取标题/歌手/封面
                tr = c.execute("SELECT id, title, artist, cover, lrc, user_id, '' FROM collected_tracks WHERE id=?", (tid,)).fetchone()
            c.close()
            if not tr:
                self._err(404, "not found")
                return True
            _, title, artist, cover, _lrc, _uid, uname = tr
            author = uname or artist or "未知歌手"
            page = self._share_page(title, author, tid, cover, short_code, share_token=token)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(page)
            return True
        return False
