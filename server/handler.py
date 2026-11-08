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
from server.ws import *
from server.recommend import *
from server.qishui import *
from server.media import *
from server.ai import *

__all__ = ['H']

from server.handlers.front import FrontMixin
from server.handlers.social import SocialMixin
from server.handlers.collect import CollectMixin
from server.handlers.admin import AdminMixin
from server.handlers.content import ContentMixin
from server.handlers.auth import AuthMixin
from server.handlers.logs import LogsMixin

from server._priv import *
class H(BaseHTTPRequestHandler, FrontMixin, SocialMixin, CollectMixin, AdminMixin, ContentMixin, AuthMixin, LogsMixin):
    timeout = SOCKET_TIMEOUT
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # BaseHTTPRequestHandler 默认往 stderr 打，这里统一走 logging（结构化）
        try:
            log.info("req %s %s %s", self.client_address[0], self.command,
                     self.path.split("?", 1)[0])
        except Exception:
            pass

    # ---- 工具 ----

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        body, enc = self._maybe_gzip(body)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if enc:
            self.send_header("Content-Encoding", enc)
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _maybe_gzip(self, body):
        """P2：JSON/HTML 文本响应按 Accept-Encoding 压缩（省 70-80% 流量）。
        小 body 不压（gzip 头开销不划算）；音频/图片流不走这里。"""
        if len(body) < 512:
            return body, ""
        ae = (self.headers.get("Accept-Encoding") or "").lower()
        if "gzip" in ae:
            import gzip as _gzip
            return _gzip.compress(body, 6), "gzip"
        return body, ""

    def _err(self, code, msg):
        self._json(code, {"err": msg})

    def _send_html(self, html):
        body = html.encode("utf-8")
        body, enc = self._maybe_gzip(body)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if enc:
            self.send_header("Content-Encoding", enc)
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _internal_error(self, e):
        # P1：始终记录完整堆栈（生产也保留），不再依赖 DEBUG 开关
        log.error("internal error: %s %s", self.path, e, exc_info=True)
        self._err(500, "服务器内部错误" if not DEBUG else "internal error: %s" % e)

    def _share_page(self, title, author, tid, cover, short_code="", share_token=""):
        """分享 H5 页面：深黑灰极简单曲页（封面/歌名/作者 + 播放暂停/进度条）。
        播放走服务端签名流 URL（隐藏真实流地址），页面不暴露明文音频链接。
        含社交卡片预览（OG 标签，微信/QQ/Telegram 读取）与短链跳转入口。"""
        tid_js = json.dumps(tid, ensure_ascii=False)
        cover_js = json.dumps(cover or "", ensure_ascii=False)
        # API 基址与 share token 由后端注入，避免前端从 URL 解析时短链 /s/{code} 场景失败
        api_base_js = json.dumps(PUBLIC_BASE.rstrip("/") + "/api", ensure_ascii=False)
        share_token_js = json.dumps(share_token or "", ensure_ascii=False)
        og_title = "《%s》@流萤音乐" % (title or "")
        # og:image 用带 .jpg 扩展名的稳定 URL（QQ/微信抓取器对无扩展名的动态接口常拒绝出图）
        og_image = ("%s/api/cover/%s.jpg" % (PUBLIC_BASE, tid)) if cover else ""
        og_url = ("https://%s/s/%s/" % (urllib.parse.urlsplit(PUBLIC_BASE).netloc, short_code)) if short_code else ""
        meta_og = (
            '<meta property="og:title" content="%s">\n'
            '<meta property="og:type" content="website">\n'
            '<meta property="og:site_name" content="流萤音乐">\n'
            '<meta property="og:description" content="%s - 流萤音乐">\n'
            '%s'
            '<meta name="twitter:card" content="summary_large_image">'
        ) % (html_escape(og_title), html_escape(author or ""),
             '<meta property="og:image" content="%s">\n' % og_image if og_image else "")
        if og_url:
            meta_og += '\n<meta property="og:url" content="%s">' % og_url
        html = _render_template("share_player.html").replace("__META_OG__", meta_og)
        return (html.replace("__TID__", tid_js)
                    .replace("__COVER__", cover_js)
                    .replace("__TITLE__", json.dumps(title or "", ensure_ascii=False))
                    .replace("__AUTHOR__", json.dumps(author or "", ensure_ascii=False))
                    .replace("__API_BASE__", api_base_js)
                    .replace("__SHARE_TOKEN__", share_token_js)).encode()

    def _serve_file(self, fp, cacheable=True):
        size = os.path.getsize(fp)
        ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        if cacheable:
            self.send_header("Cache-Control", "public, max-age=86400")
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

    # ---- 身份解析：token 优先，兼容旧客户端 userId/account ----

    def auth_user(self, c, fields=None, allow_account=False):
        """返回 (uid, authed_via_token)。uid 为 None 表示未识别身份。"""
        uid = resolve_token(c, self.headers.get("Authorization", ""))
        if uid:
            return uid, True
        if AUTH_STRICT:
            return None, False
        if fields:
            legacy = (fields.get("userId") or "").strip()
            if legacy:
                return legacy, False
            if allow_account:
                acc = (fields.get("account") or "").strip()
                if acc:
                    row = c.execute("SELECT id FROM users WHERE account=?", (acc,)).fetchone()
                    if row:
                        return row[0], False
        return None, False

    # ---- Range 解析：返回 None=全量 / False=忽略Range / True=416 / (start,end)=206 ----

    def _parse_range(self, size):
        rng = self.headers.get("Range")
        if not rng:
            return None
        if not rng.startswith("bytes="):
            return False
        spec = rng[6:].split(",", 1)[0].strip()
        if "-" not in spec:
            return False
        a, b = spec.split("-", 1)
        try:
            start = int(a) if a else None
            end = int(b) if b else None
        except ValueError:
            return False
        if start is None:
            # 后缀范围：最后 N 字节
            n = end
            if n is None or n <= 0:
                return False
            start = max(size - n, 0)
            end = size - 1
        else:
            if start >= size:
                return True            # 起始越界 → 416
            end = size - 1 if end is None else min(end, size - 1)
            if end < start:
                return False
        return (start, end)

    # ---- CORS ----

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_HEAD(self):
        try:
            u = urllib.parse.urlparse(self.path)
            if u.path.startswith("/api/stream/"):
                # 与 GET 一致的签名校验
                q = urllib.parse.parse_qs(u.query)
                exp_s = (q.get("exp") or [""])[0]
                sig = (q.get("sig") or [""])[0]
                if exp_s or sig:
                    if not verify_stream(u.path.rsplit("/", 1)[-1], exp_s, sig):
                        self._err(403, "stream link expired or invalid")
                        return
                elif SIGN_STRICT:
                    self._err(403, "stream link expired or invalid")
                    return
                tid = u.path.rsplit("/", 1)[-1]
                c = db()
                row = c.execute("SELECT fname FROM tracks WHERE id=?", (tid,)).fetchone()
                # 采集歌曲：302 到第三方播放地址（HEAD 与 GET 一致）
                crow = c.execute("SELECT play_url FROM collected_tracks WHERE id=?", (tid,)).fetchone()
                c.close()
                fp = safe_file_path(row[0] if row else "")
                if not fp:
                    if crow and crow[0]:
                        self.send_response(302)
                        self.send_header("Content-Length", "0")
                        self.send_header("Location", crow[0])
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        return
                    self._err(404, "not found")
                    return
                size = os.path.getsize(fp)
                ctype = mimetypes.guess_type(fp)[0] or "audio/mpeg"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(size))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                return
            self._err(404, "unknown")
        except Exception as e:
            self._internal_error(e)

    # ---- GET ----

    def do_GET(self):
        try:
            u = urllib.parse.urlparse(self.path)
            # WebSocket 实时通知推送：GET /api/ws?token=xxx（需 Upgrade 头）
            if u.path == "/api/ws":
                _ws_handshake(self, u)
                return

            if u.path in ("/", "/api/health"):
                self._json(200, {"ok": True, "service": "music",
                                 "time": time.strftime("%Y-%m-%d %H:%M:%S")})
                return

            # 其余路由按域分发（P2-7 拆分：server/handlers/*.py mixin）
            if self._h_get_logs(u):
                return
            if self._h_get_front(u):
                return
            if self._h_get_social(u):
                return
            if self._h_get_collect(u):
                return
            if self._h_get_admin(u):
                return
            if self._h_get_content(u):
                return

            self._err(404, "unknown")
        except (socket.timeout, TimeoutError):
            self._err(408, "request timeout")
        except Exception as e:
            self._internal_error(e)

    # ---- multipart 解析（带旧客户端兼容修复） ----

    def _parse_multipart(self, length):
        ctype = self.headers.get("Content-Type", "")
        if not ctype.startswith("multipart/form-data"):
            return None, None
        boundary = ctype.split("boundary=")[-1].encode()
        data = self.rfile.read(length)
        parts = data.split(b"--" + boundary)
        fields, files = {}, {}
        for p in parts:
            if b"Content-Disposition" not in p:
                continue
            head, _, body = p.partition(b"\r\n\r\n")
            head = head.decode("utf-8", "ignore")
            name = None
            if 'name="' in head:
                name = head.split('name="')[1].split('"')[0]
            fn = None
            if 'filename="' in head:
                fn = head.split('filename="')[1].split('"')[0]
            body = body.rstrip(b"\r\n")
            # 兼容：旧版客户端 Content-Disposition 后多写了 CRLF，
            # 导致 Content-Type 头混进文件二进制；剥掉残留头。
            if fn and body.startswith(b"Content-Type:"):
                idx = body.find(b"\r\n\r\n")
                if idx != -1:
                    body = body[idx + 4:]
            if fn:
                files[name] = (fn, body)
            elif name:
                fields[name] = body.decode("utf-8", "ignore")
        return fields, files

    # ---- POST ----

    def do_POST(self):
        try:
            u = urllib.parse.urlparse(self.path)

            # 路由按域分发（P2-7 拆分：server/handlers/*.py mixin）
            # 顺序说明：content（歌曲上传，含"非 upload 即 404"兜底）必须最后，
            # 否则会把 social 的点赞/评论/分享/播放等请求误吞为 404。
            if self._h_post_logs(u):
                return
            if self._h_post_auth(u):
                return
            if self._h_post_admin(u):
                return
            if self._h_post_collect(u):
                return
            if self._h_post_social(u):
                return
            if self._h_post_content(u):
                return

            self._err(404, "unknown")
        except (socket.timeout, TimeoutError):
            self._err(408, "request timeout")
        except Exception as e:
            self._internal_error(e)

    # ---- DELETE（管理端删除类接口） ----

    def do_DELETE(self):
        try:
            u = urllib.parse.urlparse(self.path)

            # 路由按域分发（P2-7 拆分：server/handlers/*.py mixin）
            if self._h_delete_admin(u):
                return
            if self._h_delete_social(u):
                return

            self._err(404, "unknown")
        except (socket.timeout, TimeoutError):
            self._err(408, "request timeout")
        except Exception as e:
            self._internal_error(e)
