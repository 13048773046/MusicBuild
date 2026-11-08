# -*- coding: utf-8 -*-
"""App 日志上报（崩溃/异常日志收集）。

客户端（iOS/Android）在崩溃或捕获到错误时，POST /api/log-report 把
平台/版本/设备/日志/堆栈 上报到服务器，落库 app_logs 供排查。
无需登录（崩溃时可能无登录态）；按 IP 限流防刷；字段长度截断防爆库。
"""
import json
import time
import uuid

from server.config import *
from server.database import db
from server.common import *


class LogsMixin:
    # 兼容历史路径：早期客户端拼错过 URL（/apilog-report、/api/api/log-report），
    # 新客户端统一走 /api/log-report。三条路径都收，避免旧版上报 404 丢失。
    _LOG_PATHS = ("/api/log-report", "/apilog-report", "/api/api/log-report")

    def _h_post_logs(self, u):
        if u.path not in self._LOG_PATHS:
            return False
        if not rate_limit(self, "log_report"):
            self._err(429, "上报过于频繁，请稍后再试")
            return True
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 128 * 1024:
                self._err(413, "too large")
                return True
            body = self.rfile.read(length).decode("utf-8", "replace")
            fields = json.loads(body) if body else {}
        except Exception:
            fields = {}
        platform = (fields.get("platform") or "").strip().lower()[:16]
        if platform not in ("ios", "android", "web", "other"):
            platform = "other"
        app_version = (fields.get("app_version") or "").strip()[:24]
        device_id = (fields.get("device_id") or "").strip()[:64]
        level = (fields.get("level") or "info").strip().lower()[:8]
        tag = (fields.get("tag") or "").strip()[:64]
        message = (fields.get("message") or "").strip()[:8192]
        stack = (fields.get("stack") or "").strip()[:8192]
        if not message and not stack:
            self._err(400, "empty log")
            return True
        c = db()
        c.execute(
            """INSERT INTO app_logs(id, platform, app_version, device_id, level, tag, message, stack, created)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (uuid.uuid4().hex, platform, app_version, device_id, level, tag,
             message, stack, time.time()))
        c.commit()
        c.close()
        self._json(200, {"ok": True})
        return True

    def _h_get_logs(self, u):
        """管理端查询日志（GET /api/admin/logs?limit=&platform=&level=）。"""
        if not u.path.startswith("/api/admin/logs"):
            return False
        try:
            uid, _via = self.auth_user(self, {}, allow_admin=True)
            if not uid:
                self._err(401, "未登录或登录已过期")
                return True
        except Exception:
            self._err(401, "未登录或登录已过期")
            return True
        qs = urllib.parse.parse_qs(u.query)
        limit = int((qs.get("limit") or ["50"])[0])
        limit = max(1, min(limit, 200))
        platform = (qs.get("platform") or [""])[0].strip().lower()
        level = (qs.get("level") or [""])[0].strip().lower()
        where = []
        args = []
        if platform:
            where.append("platform=?")
            args.append(platform)
        if level:
            where.append("level=?")
            args.append(level)
        sql = ("SELECT id, platform, app_version, device_id, level, tag, message, stack, created "
               "FROM app_logs" + (" WHERE " + " AND ".join(where) if where else "") +
               " ORDER BY created DESC LIMIT ?")
        args.append(limit)
        c = db()
        rows = c.execute(sql, args).fetchall()
        c.close()
        out = [{
            "id": r[0], "platform": r[1], "appVersion": r[2], "deviceId": r[3],
            "level": r[4], "tag": r[5], "message": r[6], "stack": r[7],
            "created": r[8],
        } for r in rows]
        self._json(200, {"logs": out})
        return True
