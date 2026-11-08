# -*- coding: utf-8 -*-
"""P2-7：auth 域路由（mixin，由 server/handler.py 的 H 类继承）。
拆分自 handler.py 的 do_POST 顶层分支，逻辑与路由条件保持原样，
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


class AuthMixin:

    def _h_post_auth(self, u):
        length = int(self.headers.get("Content-Length", 0))
        u = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        if u.path == "/api/user/push-token":
            tok = self.headers.get("Authorization", "").replace("Bearer ", "").strip()
            c = db()
            uid = resolve_token(c, tok)
            if not uid:
                c.close()
                self._err(401, "unauthorized"); return True
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}
            except Exception:
                body = {}
            token = (body.get("token") or "").strip()
            platform = (body.get("platform") or "android").strip()
            if token:
                from server.jpush import save_token
                save_token(uid, token, platform)
            c.close()
            self._json(200, {"ok": True, "saved": bool(token)}); return True
        if u.path == "/api/device":
            try:
                raw = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 4 * 1024))
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._err(400, "bad json"); return True
            dev_id = (data.get("device_id") or "").strip()
            if not dev_id:
                self._err(400, "device_id required"); return True
            plat = (data.get("platform") or "").strip()[:16]
            ver = (data.get("version") or "").strip()[:32]
            uid = (data.get("user_id") or "").strip()[:64]
            # 优先用登录态解析（客户端带 Authorization 时自动关联用户，不需要明文传 user_id）
            if not uid:
                try:
                    _tok = resolve_token(db(), self.headers.get("Authorization", ""))
                    if _tok:
                        uid = _tok
                except Exception:
                    pass
            try:
                import time as _t
                c2 = db()
                c2.execute(
                    "INSERT INTO devices(device_id, platform, app_version, user_id, first_seen, last_seen) VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(device_id) DO UPDATE SET platform=excluded.platform, "
                    "app_version=excluded.app_version, "
                    "user_id=CASE WHEN excluded.user_id!='' THEN excluded.user_id ELSE devices.user_id END, "
                    "last_seen=excluded.last_seen",
                    (dev_id, plat, ver, uid, _t.time(), _t.time()))
                c2.commit()
                c2.close()
            except Exception as e:
                log.info("device upsert err: %s", e)
                self._err(500, "server error"); return True
            self._json(200, {"ok": True})
            return True
        if u.path == "/api/register":
            if not rate_limit(self, "register"):
                self._err(429, "注册过于频繁，请稍后再试")
                return True
            try:
                if length <= 0 or length > MAX_PROFILE:
                    self._err(413, "too large")
                    return True
                fields, files = self._parse_multipart(length)
                if fields is None:
                    self._err(400, "need multipart")
                    return True
                # 账号由后端自动生成 6 位数字（不再接受用户自定义账号）
                password = fields.get("password") or ""
                name = (fields.get("name") or "").strip()[:MAX_NAME]
                gender = (fields.get("gender") or "").strip()[:8]
                signature = (fields.get("signature") or "").strip()[:100]
                try:
                    age = int(fields.get("age") or 0)
                except Exception:
                    age = 0
                age = max(0, min(age, 150))
                if not password:
                    self._err(400, "密码不能为空")
                    return True
                if len(password) < 6 or len(password) > 64:
                    self._err(400, "密码长度需在 6-64 位之间")
                    return True
                c = db()
                # 生成 6 位数字账号（100000-999999），冲突则重试
                account = ""
                for _ in range(50):
                    _cand = str(random.randint(100000, 999999))
                    if not c.execute("SELECT 1 FROM users WHERE account=?", (_cand,)).fetchone():
                        account = _cand
                        break
                if not account:
                    c.close()
                    self._err(500, "账号生成失败，请重试")
                    return True
                name = name or ("用户" + account)
                uid = uuid.uuid4().hex
                avatar_fname = ""
                avatar = files.get("avatar")
                if avatar and avatar[1]:
                    aext = os.path.splitext(avatar[0])[1].lower()
                    if aext not in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
                        aext = ".jpg"
                    if not image_magic_ok(avatar[1], aext):
                        c.close()
                        self._err(400, "头像文件格式校验失败")
                        return True
                    avatar_fname = uid + "_avatar" + aext
                    with open(os.path.join(FILES, avatar_fname), "wb") as f:
                        f.write(avatar[1])
                bg_fname = ""
                bg = files.get("background")
                if bg and bg[1]:
                    bext = os.path.splitext(bg[0])[1].lower()
                    if bext not in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
                        bext = ".jpg"
                    if not image_magic_ok(bg[1], bext):
                        c.close()
                        self._err(400, "背景图文件格式校验失败")
                        return True
                    bg_fname = uid + "_bg" + bext
                    with open(os.path.join(FILES, bg_fname), "wb") as f:
                        f.write(bg[1])
                ip = client_ip(self)
                c.execute("""INSERT INTO users(id,account,password_hash,name,avatar,created,
                              gender,age,background_image,signature,ip_address)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                          (uid, account, hash_password(password), name, avatar_fname, time.time(),
                           gender, age, bg_fname, signature, ip))
                token = issue_token(c, uid)
                c.commit()
                row = c.execute("""SELECT id,account,password_hash,name,avatar,created,
                                  gender,age,background_image,signature,ip_address,city,
                                  title,verified,verified_label,member,member_expire,svip_expire
                                  FROM users WHERE id=?""", (uid,)).fetchone()
                c.close()
                self._json(200, public_user(row, token))
            except Exception as e:
                self._internal_error(e)
            return True
        if u.path == "/api/login":
            if not rate_limit(self, "login"):
                self._err(429, "登录尝试过于频繁，请稍后再试")
                return True
            try:
                if length <= 0 or length > 64 * 1024:
                    self._err(413, "too large")
                    return True
                fields, files = self._parse_multipart(length)
                if fields is None:
                    self._err(400, "need multipart")
                    return True
                account = (fields.get("account") or "").strip()
                password = fields.get("password") or ""
                c = db()
                row = c.execute("""SELECT id,account,password_hash,name,avatar,created,
                                 gender,age,background_image,signature,ip_address,city,banned,ban_reason
                                 FROM users WHERE account=?""",
                                (account,)).fetchone()
                if not row or not verify_password(password, row[2]):
                    c.close()
                    self._err(401, "账号或密码错误")
                    return True
                if row[12]:
                    c.close()
                    self._err(403, "账号已被封禁：" + (row[13] or "联系管理员"))
                    return True
                # 旧版哈希（v1，120000 轮）自动迁移升级
                if is_legacy_hash(row[2]):
                    c.execute("UPDATE users SET password_hash=? WHERE id=?",
                              (hash_password(password), row[0]))
                # 每次登录自动更新当前 IP
                ip = client_ip(self)
                c.execute("UPDATE users SET ip_address=? WHERE id=?", (ip, row[0]))
                token = issue_token(c, row[0])
                c.commit()
                row = c.execute("""SELECT id,account,password_hash,name,avatar,created,
                                 gender,age,background_image,signature,ip_address,city,
                                 title,verified,verified_label,member,member_expire,svip_expire
                                 FROM users WHERE id=?""", (row[0],)).fetchone()
                c.close()
                self._json(200, public_user(row, token))
            except Exception as e:
                self._internal_error(e)
            return True
        if u.path == "/api/change-password":
            try:
                if length <= 0 or length > MAX_PROFILE:
                    self._err(413, "too large")
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
                old_pw = fields.get("old_password") or ""
                new_pw = fields.get("new_password") or ""
                if len(new_pw) < 6 or len(new_pw) > 64:
                    c.close()
                    self._err(400, "新密码长度需 6-64 位")
                    return True
                row = c.execute("SELECT password_hash FROM users WHERE id=?",
                                (uid,)).fetchone()
                if not row:
                    c.close()
                    self._err(404, "用户不存在")
                    return True
                if not verify_password(old_pw, row[0]):
                    c.close()
                    self._err(403, "旧密码不正确")
                    return True
                c.execute("UPDATE users SET password_hash=? WHERE id=?",
                          (hash_password(new_pw), uid))
                c.close()
                self._json(200, {"ok": True})
                return True
            except Exception as e:
                self._internal_error(e)
            return True
        if u.path == "/api/profile/update":
            try:
                if length <= 0 or length > MAX_PROFILE:
                    self._err(413, "too large")
                    return True
                fields, files = self._parse_multipart(length)
                if fields is None:
                    self._err(400, "need multipart")
                    return True
                c = db()
                uid, via_token = self.auth_user(c, fields, allow_account=True)
                if not uid:
                    c.close()
                    self._err(401, "未登录或登录已过期")
                    return True
                if not via_token:
                    log.warning("legacy auth profile/update uid=%s ip=%s", uid, client_ip(self))
                name = (fields.get("name") or "").strip()[:MAX_NAME]
                gender = (fields.get("gender") or "").strip()[:8]
                signature = (fields.get("signature") or "").strip()[:100]
                city = (fields.get("city") or "").strip()[:30]
                try:
                    age = int(fields.get("age") or 0)
                except Exception:
                    age = 0
                age = max(0, min(age, 150))
                avatar_fname = None
                av = files.get("avatar")
                if av and av[1]:
                    aext = os.path.splitext(av[0])[1].lower()
                    if aext not in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
                        aext = ".jpg"
                    if not image_magic_ok(av[1], aext):
                        c.close()
                        self._err(400, "头像文件格式校验失败")
                        return True
                    avatar_fname = uid + "_avatar" + aext
                    with open(os.path.join(FILES, avatar_fname), "wb") as f:
                        f.write(av[1])
                bg_fname = None
                bg = files.get("background")
                if bg and bg[1]:
                    bext = os.path.splitext(bg[0])[1].lower()
                    if bext not in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
                        bext = ".jpg"
                    if not image_magic_ok(bg[1], bext):
                        c.close()
                        self._err(400, "背景图文件格式校验失败")
                        return True
                    bg_fname = uid + "_bg" + bext
                    with open(os.path.join(FILES, bg_fname), "wb") as f:
                        f.write(bg[1])
                ip = client_ip(self)
                if avatar_fname and bg_fname:
                    c.execute("""UPDATE users SET name=?,gender=?,age=?,signature=?,
                                 avatar=?,background_image=?,city=?,ip_address=? WHERE id=?""",
                              (name, gender, age, signature, avatar_fname, bg_fname, city, ip, uid))
                elif avatar_fname:
                    c.execute("""UPDATE users SET name=?,gender=?,age=?,signature=?,
                                 avatar=?,city=?,ip_address=? WHERE id=?""",
                              (name, gender, age, signature, avatar_fname, city, ip, uid))
                elif bg_fname:
                    c.execute("""UPDATE users SET name=?,gender=?,age=?,signature=?,
                                 background_image=?,city=?,ip_address=? WHERE id=?""",
                              (name, gender, age, signature, bg_fname, city, ip, uid))
                else:
                    c.execute("""UPDATE users SET name=?,gender=?,age=?,signature=?,
                                 city=?,ip_address=? WHERE id=?""",
                              (name, gender, age, signature, city, ip, uid))
                c.commit()
                row = c.execute("""SELECT id,account,password_hash,name,avatar,created,
                                  gender,age,background_image,signature,ip_address,city
                                  FROM users WHERE id=?""", (uid,)).fetchone()
                _pub = public_user(row)
                c.close()
                # 实时数据事件：资料（头像/签名/昵称/背景）更新后推送，多端同步
                ws_push(uid, {"kind": "event", "event": "profile", "user": _pub})
                self._json(200, _pub)
            except Exception as e:
                self._internal_error(e)
            return True

        # ===== 邮箱验证码：发送验证码 =====
        if u.path == "/api/send-email-code":
            _ec_start = time.time()
            try:
                if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
                    logging.info(f"send-email-code SMTP未配置 耗时{time.time()-_ec_start:.1f}s")
                    self._err(503, "邮箱服务未配置")
                    return True
                if length <= 0 or length > 64 * 1024:
                    logging.info(f"send-email-code 请求体过大 耗时{time.time()-_ec_start:.1f}s")
                    self._err(413, "too large")
                    return True
                # 审查 #4：按 IP 限流（10 次/分），防对任意邮箱批量轰炸
                if not rate_limit(self, "send_email"):
                    logging.info(f"send-email-code IP限流 耗时{time.time()-_ec_start:.1f}s")
                    self._err(429, "发送过于频繁，请稍后再试")
                    return True
                fields, _ = self._parse_multipart(length)
                if fields is None:
                    # 兼容 JSON 请求
                    try:
                        body = self.rfile.read(length).decode("utf-8")
                        fields = json.loads(body) if body else {}
                    except Exception:
                        fields = {}
                email = (fields.get("email") or "").strip().lower()
                # 校验邮箱格式
                if not re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", email):
                    logging.info(f"send-email-code 邮箱格式错误 email={email[:40]} 耗时{time.time()-_ec_start:.1f}s")
                    self._err(400, "邮箱格式不正确")
                    return True
                c = db()
                now = time.time()
                # 检查重发间隔
                row = c.execute("SELECT last_sent, day_count, day_date FROM email_codes WHERE email=?", (email,)).fetchone()
                if row and (now - row[0]) < EMAIL_CODE_RESEND_INTERVAL:
                    wait = int(EMAIL_CODE_RESEND_INTERVAL - (now - row[0]))
                    c.close()
                    logging.info(f"send-email-code 重发间隔 email={email[:40]} wait={wait} 耗时{time.time()-_ec_start:.1f}s")
                    self._err(429, f"请 {wait} 秒后再试")
                    return True
                # 审查 #4：单个邮箱每日发送上限（跨日自动重置）
                _today = time.strftime("%Y-%m-%d")
                _day_cnt = (row[1] or 0) if row and row[2] == _today else 0
                if _day_cnt >= EMAIL_CODE_DAILY_LIMIT:
                    c.close()
                    logging.info(f"send-email-code 日限流 email={email[:40]} 耗时{time.time()-_ec_start:.1f}s")
                    self._err(429, "该邮箱今日发送次数已达上限，请明天再试")
                    return True
                # 生成 6 位数字验证码
                code = f"{random.randint(100000, 999999)}"
                # 发送邮件
                import smtplib
                from email.mime.text import MIMEText
                from email.header import Header
                from email.utils import formataddr
                msg = MIMEText(
                    f"您的验证码是：{code}\n\n验证码 {EMAIL_CODE_EXPIRE // 60} 分钟内有效，请勿泄露给他人。",
                    "plain", "utf-8"
                )
                # 用 formataddr 正确格式化 From 头，避免 QQ 邮箱报 RFC 错误
                msg["From"] = formataddr((str(Header(SMTP_FROM_NAME, "utf-8")), SMTP_USER))
                msg["To"] = email
                msg["Subject"] = Header("流萤音乐 · 邮箱验证码", "utf-8")
                try:
                    if SMTP_PORT == 465:
                        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10)
                    else:
                        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10)
                        server.starttls()
                    server.login(SMTP_USER, SMTP_PASS)
                    server.sendmail(SMTP_USER, [email], msg.as_string())
                    server.quit()
                except Exception as e:
                    logging.error(f"SMTP send failed: {e}")
                    logging.info(f"send-email-code SMTP失败 email={email[:40]} 耗时{time.time()-_ec_start:.1f}s err={str(e)[:120]}")
                    c.close()
                    self._err(500, "邮件发送失败，请稍后再试")
                    return True
                # 保存验证码（重置失败计数；累计当日发送次数，跨日归零）
                c.execute("""INSERT OR REPLACE INTO email_codes(email, code, created, last_sent, fail_count, day_count, day_date)
                             VALUES(?,?,?,?,?,?,?)""",
                          (email, code, now, now, 0, _day_cnt + 1, _today))
                c.commit()
                c.close()
                logging.info(f"send-email-code 成功 email={email[:40]} 耗时{time.time()-_ec_start:.1f}s")
                self._json(200, {"ok": True})
            except Exception as e:
                self._internal_error(e)
            return True

        # ===== 邮箱登录/注册 =====
        if u.path == "/api/login-email":
            if not rate_limit(self, "login_email"):
                self._err(429, "尝试过于频繁，请稍后再试")
                return True
            try:
                if length <= 0 or length > 64 * 1024:
                    self._err(413, "too large")
                    return True
                fields, _ = self._parse_multipart(length)
                if fields is None:
                    try:
                        body = self.rfile.read(length).decode("utf-8")
                        fields = json.loads(body) if body else {}
                    except Exception:
                        fields = {}
                email = (fields.get("email") or "").strip().lower()
                code = (fields.get("code") or "").strip()
                if not email or not code:
                    self._err(400, "请输入邮箱和验证码")
                    return True
                c = db()
                # 校验验证码（审查 #3：连续失败 ≥5 次作废，防 6 位数字爆破）
                row = c.execute("SELECT code, created, fail_count FROM email_codes WHERE email=?", (email,)).fetchone()
                if not row:
                    c.close()
                    self._err(400, "验证码错误")
                    return True
                if row[0] != code:
                    _fc = (row[2] or 0) + 1
                    if _fc >= EMAIL_CODE_MAX_FAIL:
                        c.execute("DELETE FROM email_codes WHERE email=?", (email,))
                    else:
                        c.execute("UPDATE email_codes SET fail_count=? WHERE email=?", (_fc, email))
                    c.commit()
                    c.close()
                    self._err(400, "验证码错误")
                    return True
                if (time.time() - row[1]) > EMAIL_CODE_EXPIRE:
                    c.close()
                    self._err(400, "验证码已过期，请重新获取")
                    return True
                # 查找用户（字段顺序必须与 public_user 期望一致：id,account,password_hash,name,avatar,created,gender,age,background_image,signature,ip_address,city,title,verified,verified_label,member,member_expire,svip_expire）
                user = c.execute("""SELECT id,account,password_hash,name,avatar,created,
                                    gender,age,background_image,signature,ip_address,city,
                                    title,verified,verified_label,member,member_expire,svip_expire
                                    FROM users WHERE email=?""", (email,)).fetchone()
                if not user:
                    # 新用户自动注册：账号生成 6 位数字（100000-999999，冲突重试），
                    # 与 /api/register 一致——App「我的」页展示的 ID 保持短账号风格。
                    # （原实现 account 写入 uid 32 位 hex，导致邮箱用户 ID 显示成长串）
                    uid = uuid.uuid4().hex
                    account = ""
                    for _ in range(50):
                        _cand = str(random.randint(100000, 999999))
                        if not c.execute("SELECT 1 FROM users WHERE account=?", (_cand,)).fetchone():
                            account = _cand
                            break
                    if not account:
                        c.close()
                        self._err(500, "账号生成失败，请重试")
                        return True
                    name = "用户" + account
                    ip = client_ip(self)
                    c.execute("""INSERT INTO users(id,account,password_hash,name,avatar,created,
                                 email,email_verified,ip_address)
                                 VALUES(?,?,?,?,?,?,?,?,?)""",
                              (uid, account, "", name, "", time.time(), email, 1, ip))
                    # 重新查询
                    user = c.execute("""SELECT id,account,password_hash,name,avatar,created,
                                        gender,age,background_image,signature,ip_address,city,
                                        title,verified,verified_label,member,member_expire,svip_expire
                                        FROM users WHERE id=?""", (uid,)).fetchone()
                else:
                    # 检查是否被封禁（banned 字段不在 public_user 顺序里，单独查）
                    banned_row = c.execute("SELECT banned, ban_reason FROM users WHERE id=?", (user[0],)).fetchone()
                    if banned_row and banned_row[0]:
                        c.close()
                        self._err(403, "账号已被封禁：" + (banned_row[1] or "联系管理员"))
                        return True
                token = issue_token(c, user[0])
                c.commit()
                # 删除已使用的验证码
                c.execute("DELETE FROM email_codes WHERE email=?", (email,))
                c.commit()
                c.close()
                self._json(200, public_user(user, token))
            except Exception as e:
                self._internal_error(e)
            return True

        # ===== 绑定邮箱（已登录用户）=====
        if u.path == "/api/bind-email":
            try:
                if length <= 0 or length > 64 * 1024:
                    self._err(413, "too large")
                    return True
                fields, files = self._parse_multipart(length)
                if fields is None:
                    self._err(400, "need multipart")
                    return True
                c = db()
                uid, via_token = self.auth_user(c, fields)
                email = (fields.get("email") or "").strip().lower()
                code = (fields.get("code") or "").strip()
                if not email or not code:
                    c.close()
                    self._err(400, "请输入邮箱和验证码")
                    return True
                # 校验验证码（审查 #3：连续失败 ≥5 次作废，防 6 位数字爆破）
                row = c.execute("SELECT code, created, fail_count FROM email_codes WHERE email=?", (email,)).fetchone()
                if not row:
                    c.close()
                    self._err(400, "验证码错误")
                    return True
                if row[0] != code:
                    _fc = (row[2] or 0) + 1
                    if _fc >= EMAIL_CODE_MAX_FAIL:
                        c.execute("DELETE FROM email_codes WHERE email=?", (email,))
                    else:
                        c.execute("UPDATE email_codes SET fail_count=? WHERE email=?", (_fc, email))
                    c.commit()
                    c.close()
                    self._err(400, "验证码错误")
                    return True
                if (time.time() - row[1]) > EMAIL_CODE_EXPIRE:
                    c.close()
                    self._err(400, "验证码已过期，请重新获取")
                    return True
                # 检查邮箱是否已被其他账号绑定
                exists = c.execute("SELECT id FROM users WHERE email=? AND id!=?", (email, uid)).fetchone()
                if exists:
                    c.close()
                    self._err(400, "该邮箱已被其他账号绑定")
                    return True
                # 绑定邮箱
                c.execute("UPDATE users SET email=?, email_verified=1 WHERE id=?", (email, uid))
                c.execute("DELETE FROM email_codes WHERE email=?", (email,))
                c.commit()
                # 返回更新后的用户信息（字段顺序与 public_user 一致）
                user = c.execute("""SELECT id,account,password_hash,name,avatar,created,
                                    gender,age,background_image,signature,ip_address,city,
                                    title,verified,verified_label,member,member_expire,svip_expire
                                    FROM users WHERE id=?""", (uid,)).fetchone()
                _pub = public_user(user)
                c.close()
                ws_push(uid, {"kind": "event", "event": "profile", "user": _pub})
                self._json(200, _pub)
            except Exception as e:
                self._internal_error(e)
            return True

        return False
