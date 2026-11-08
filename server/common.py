# -*- coding: utf-8 -*-
"""流萤音乐 · 后端模块化包（由线上 server.py 机械重放，函数体零改写）。"""
import os
import re
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
from server.config import ADMIN_PWD_FILE, SITE_CONFIG_FILE, _SITE_CONFIG_DEFAULTS, COLLECT_KEY
from server.database import db

__all__ = ['RATE', 'RATE_LIMITS', 'RateLimiter', '_B62', '_admin_token', '_fmt_dur', '_ingest_behavior', '_notif_unread', '_official_send', '_member_notif_text', '_pay_sign', '_read_site_config', '_ssl_ctx', '_track_info', '_track_owner', '_write_site_config', 'client_ip', 'gen_short_code', 'hash_password', 'is_legacy_hash', 'issue_token', 'member_level', 'parse_page', 'public_user', 'rate_key', 'rate_limit', 'resolve_token', 'safe_file_path', 'sign_stream', 'verify_password', 'verify_stream',
           'fts_ids', '_id_pool', 'invalidate_id_pool', 'cleanup_expired_data']

def client_ip(handler):
    """经 nginx 反代，优先取 X-Real-IP（nginx 写入，客户端不可伪造）；
    其次 X-Forwarded-For **最后一段**（$proxy_add_x_forwarded_for 追加的真实 IP 在末尾）。
    原实现取第一段：客户端可伪造 XFF 头换 IP，绕过全部按 IP 的限流（审查 #2 修复）。"""
    xri = handler.headers.get("X-Real-IP", "")
    if xri:
        return xri.strip()
    xff = handler.headers.get("X-Forwarded-For", "")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return handler.client_address[0] if handler.client_address else ""


def public_user(row, token=None):
    """users 行 → 对外资料 JSON（不含密码哈希，可附带新签发 token）。
    row 顺序: id,account,password_hash,name,avatar,created,
              gender,age,background_image,signature,ip_address,city
    （会员/认证/头衔新列 12-17：title,verified,verified_label,member,member_expire,svip_expire）"""
    out = {
        "id": row[0],
        "account": row[1],
        "name": row[3] or "",
        "avatar": row[4] or "",
        "created": row[5] or 0,
        "gender": row[6] or "",
        "age": row[7] or 0,
        "background": row[8] or "",
        "signature": row[9] or "",
        "ip": row[10] or "",
        "city": row[11] or "" if len(row) > 11 else "",
        "title": row[12] or "" if len(row) > 12 else "",
        "verified": row[13] or "" if len(row) > 13 else "",
        "verifiedLabel": row[14] or "" if len(row) > 14 else "",
        # 会员等级按到期时间实时判定（不用 member 冗余快照，避免过期残留错显）
        "member": member_level(row[16], row[17]) if len(row) > 17 else "",
        "memberExpire": row[16] or 0 if len(row) > 16 else 0,
        "svipExpire": row[17] or 0 if len(row) > 17 else 0,
    }
    if token:
        out["token"] = token
    return out


def member_level(vip_expire, svip_expire, now=None):
    """会员等级判定：SVIP > VIP > 无会员。任一过期时间在未来即生效。"""
    now = now or time.time()
    if svip_expire and svip_expire > now:
        return "svip"
    if vip_expire and vip_expire > now:
        return "vip"
    return ""


def hash_password(pw):
    salt = os.urandom(16).hex()
    h = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"),
                            bytes.fromhex(salt), PBKDF2_ITER).hex()
    return f"{salt}${PBKDF2_ITER}${h}"


def verify_password(pw, stored):
    """兼容两种存储格式：
    v2: salt$iter$hash    v1(旧): salt$hash（120000 轮）"""
    try:
        parts = stored.split("$")
        if len(parts) == 3:
            salt, iters_s, h = parts
            iters = int(iters_s)
        elif len(parts) == 2:
            salt, h = parts
            iters = LEGACY_ITER
        else:
            return False
        if iters <= 0 or iters > 10_000_000:
            return False
        test = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"),
                                   bytes.fromhex(salt), iters).hex()
        return hmac.compare_digest(h, test)
    except Exception:
        return False


def is_legacy_hash(stored):
    return isinstance(stored, str) and len(stored.split("$")) == 2


def issue_token(c, uid):
    tok = secrets.token_urlsafe(32)
    now = time.time()
    c.execute("INSERT INTO tokens(token,user_id,created,expires) VALUES(?,?,?,?)",
              (tok, uid, now, now + TOKEN_TTL))
    return tok


# P2：token→uid 短 TTL 内存缓存（减少高频接口每请求一次 DB 查询）。
# 30s 窗口内登出/踢人最多延迟 30s 生效（可接受）；缓存带锁，线程安全。
_TOKEN_CACHE = {}
_TOKEN_CACHE_LOCK = threading.Lock()
_TOKEN_CACHE_TTL = 30.0


def _invalidate_token(tok):
    """登出/换 token 时调用，立即逐出缓存，避免旧 token 在 TTL 内继续有效。"""
    if not tok:
        return
    with _TOKEN_CACHE_LOCK:
        _TOKEN_CACHE.pop(tok, None)


def resolve_token(c, auth_header):
    """Authorization: Bearer <token> → user_id 或 None（30s TTL 内存缓存）"""
    if not auth_header:
        return None
    scheme, _, tok = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not tok.strip():
        return None
    tok = tok.strip()
    now = time.time()
    with _TOKEN_CACHE_LOCK:
        hit = _TOKEN_CACHE.get(tok)
        if hit and hit[1] > now:
            return hit[0]
    row = c.execute(
        "SELECT user_id FROM tokens WHERE token=? AND expires>?",
        (tok, now)).fetchone()
    uid = row[0] if row else None
    with _TOKEN_CACHE_LOCK:
        if uid is not None:
            _TOKEN_CACHE[tok] = (uid, now + _TOKEN_CACHE_TTL)
        else:
            _TOKEN_CACHE.pop(tok, None)
    return uid


class RateLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._hits = {}

    def allow(self, key, limit, window):
        now = time.time()
        with self._lock:
            dq = self._hits.setdefault(key, deque())
            while dq and now - dq[0] > window:
                dq.popleft()
            if len(dq) >= limit:
                return False
            dq.append(now)
            return True


RATE = RateLimiter()


RATE_LIMITS = {
    "tracks":    (60, 60),    # 列表 60 次/分
    "play":      (120, 60),   # 播放上报 120 次/分（切歌频繁）
    "like":      (60, 60),
    "comment":   (30, 60),
    "share":     (30, 60),
    "login":     (10, 60),
    "register":  (5, 60),
    "upload":    (30, 3600),  # 上传 30 次/小时
    "comment_image": (30, 3600),  # 评论图片上传 30 次/小时
    "stream":    (300, 60),   # 流请求（含签名签发）300 次/分
    "share_url": (60, 60),    # 分享 token 签发/分享页 60 次/分
    "collect":   (60, 60),    # 采集歌曲列表 60 次/分
    "collect_add": (20, 60),  # 采集入库 20 次/分
    "behavior":  (120, 60),   # 用户行为上报 120 次/分
    "log_report": (120, 60),  # App 日志上报 120 次/分（崩溃补报可能批量）
    "recommend": (30, 60),    # 个性化推荐 30 次/分
    "radio":     (30, 60),    # 场景音乐个性化歌单 30 次/分
    "similar":   (60, 60),    # 相似歌曲（详情页）60 次/分
    "daily":     (20, 60),    # 每日推荐 20 次/分（当天缓存，不需要高频）
    "weather":   (60, 60),    # 天气快照 60 次/分（M2）
    "ai_comment": (5, 60),    # AI 乐评生成 5 次/分
    "login_email": (10, 60),  # 邮箱登录 10 次/分
    "send_email": (10, 60),   # 发送邮箱验证码 10 次/分/IP（防轰炸，审查 #4）
}


def _notif_unread(c, uid):
    """该用户当前未读通知数（调用时需持有数据库连接 c）。
    排除 official 类型：官方消息由 /api/official-messages 的 unreadCount 单独计数，
    否则红点（=互动未读+官方未读）会把官方消息重复计入。"""
    row = c.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0 AND type != 'official'",
                    (uid,)).fetchone()
    return row[0] if row else 0


def _official_send(c, uid, content):
    """向官方通知聊天页面发送一条定向官方消息（official_messages 表，receiver_user_id=uid）。
    官方消息聊天页（/api/official-messages）读取该表；红点由 official_read_at 计数。
    调用时需持有数据库连接 c。"""
    omid = uuid.uuid4().hex
    c.execute("INSERT INTO official_messages(id, content, created, sender_name, receiver_user_id)"
              " VALUES(?,?,?,?,?)",
              (omid, content, time.time(), "流萤音乐官方", uid))
    return omid


def _member_notif_text(plan, days, expire_ts, source="gift"):
    """会员开通通知文字模板。
    source="gift"（后台管理员赠送）：官方赠送了你 X 天会员，到期时间：...
    source="self"（前端用户自行开通）：你已开通 X 天会员，到期时间：...
    返回纯文本模板，供 official_messages / notifications / ws_push 共用。"""
    name = "SVIP 会员" if plan == "svip" else "VIP 会员"
    expire = time.strftime("%Y-%m-%d", time.localtime(expire_ts))
    if source == "self":
        return "你已开通 %s 天 %s，到期时间：%s" % (days, name, expire)
    return "官方赠送了你 %s 天 %s，到期时间：%s" % (days, name, expire)


def _pay_sign(params, key):
    """易支付 MD5 签名：除 sign/sign_type 外的参数按参数名字典序排序，
    拼接为 k=v&k=v... 后直接追加商户密钥，md5 小写。
    与彩虹/异次元系易支付支付网关的签名规则一致。"""
    items = []
    for k in sorted(params):
        if k in ("sign", "sign_type") or params[k] is None:
            continue
        items.append("%s=%s" % (k, str(params[k])))
    sign_str = "&".join(items) + str(key or "")
    return hashlib.md5(sign_str.encode("utf-8")).hexdigest()


def _ingest_behavior(c, uid, evs, now):
    """批量落库行为事件（校验 + 歌曲信息快照），返回 (写入条数, 是否含收听)。
    供 HTTP /api/behavior 与 WebSocket behavior 消息共用。"""
    ALLOW = ("like", "comment_open", "comment", "share", "listen", "search", "skip",
             "expose", "collect")
    ins, has_listen = 0, False
    for it in evs:
        tid = str(it.get("trackId") or "").strip()
        ev = str(it.get("event") or "").strip()
        if not tid and ev != "search":
            continue
        if ev not in ALLOW:
            continue
        if ev == "listen":
            has_listen = True
        try:
            val = float(it.get("value") or 0)
        except Exception:
            val = 0.0
        meta = it.get("meta")
        if isinstance(meta, dict):
            meta_s = json.dumps(meta, ensure_ascii=False)[:500]
        elif isinstance(meta, str):
            meta_s = meta[:500]
        else:
            meta_s = ""
        # 歌曲信息快照：推荐时直接用它找「类似的歌」，即使歌曲日后下架/删除也不影响推荐
        tinfo = ""
        if tid:
            _tr = c.execute("SELECT title,artist,album FROM tracks WHERE id=?", (tid,)).fetchone()
            if not _tr:
                _tr = c.execute("SELECT title,artist,album FROM collected_tracks WHERE id=?", (tid,)).fetchone()
            if _tr:
                tinfo = json.dumps({"title": _tr[0] or "", "artist": _tr[1] or "",
                                    "album": _tr[2] or ""}, ensure_ascii=False)[:500]
        c.execute("""INSERT INTO behavior_events(id,user_id,track_id,event,value,meta,track_info,created)
                     VALUES(?,?,?,?,?,?,?,?)""",
                  (uuid.uuid4().hex, uid, tid, ev, val, meta_s, tinfo, now))
        ins += 1
    return ins, has_listen


def _ssl_ctx():
    """优先使用系统 CA 证书（服务器 Python 环境证书链不完整会导致 https 下载失败），
    找不到 CA 文件时降级为不校验证书（仅用于封面图片下载，非敏感链路）。"""
    for cafile in ("/etc/pki/tls/certs/ca-bundle.crt",
                   "/etc/ssl/certs/ca-certificates.crt"):
        if os.path.exists(cafile):
            try:
                return ssl.create_default_context(cafile=cafile)
            except Exception:
                pass
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def rate_key(handler, name, extra=""):
    return (name, client_ip(handler), extra)


def rate_limit(handler, name, extra=""):
    limit, window = RATE_LIMITS[name]
    return RATE.allow(rate_key(handler, name, extra), limit, window)


def parse_page(qs, default_size=20, max_size=50):
    """解析列表分页参数：返回 (page, page_size)。

    - page 从 1 开始；page<1 或未传 → 返回 None，表示「不分页、全量返回」（兼容旧客户端）。
    - pageSize 默认 20，超上限 50 时截断。
    """
    try:
        page = int((qs.get("page", [""])[0] or "").strip() or 0)
    except (ValueError, TypeError):
        page = 0
    try:
        page_size = int((qs.get("pageSize", [""])[0] or "").strip() or default_size)
    except (ValueError, TypeError):
        page_size = default_size
    if page_size < 1:
        page_size = default_size
    if page_size > max_size:
        page_size = max_size
    return (page if page >= 1 else None), page_size


def sign_stream(tid, exp):
    """HMAC-SHA256 签名：防链接被篡改/伪造，配合时效防永久盗链。"""
    return hmac.new(SIGN_SECRET.encode(), f"{tid}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]


def verify_stream(tid, exp_s, sig):
    """校验流请求签名。返回 True 放行 / False 拒绝。"""
    if not exp_s.isdigit():
        return False
    exp = int(exp_s)
    if exp < int(time.time()) - 60:      # 允许 60s 时钟偏差，过期即拒绝
        return False
    if not sig or len(sig) != 32:
        return False
    return hmac.compare_digest(sig, sign_stream(tid, exp))


_B62 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def gen_short_code(n=8):
    """生成 n 位 base62 随机短码（汽水风格短链，如 iXbwY55P）。"""
    return "".join(secrets.choice(_B62) for _ in range(n))


def _admin_token():
    """管理后台密码：持久化文件优先，其次环境变量，最后默认值"""
    try:
        with open(ADMIN_PWD_FILE, "r", encoding="utf-8") as _f:
            _t = _f.read().strip()
        if _t:
            return _t
    except Exception:
        pass
    return os.environ.get("ADMIN_TOKEN", "liuying-admin-8f3a")


# 站点配置 TTL 缓存（审查 #11）：官网/前台高频接口不再每请求查 2 次 COUNT + banners。
# 写入（_write_site_config）后立即失效；30s 窗口内直接返回内存快照。
_SITE_CFG_CACHE = {"t": 0.0, "kv": None}
_SITE_CFG_LOCK = threading.Lock()
_SITE_CFG_TTL = 30.0


def _read_site_config_uncached():
    """读官网配置 + 实时歌曲总数；文件不存在时用默认值。轮播图(banners)以数据库为准（持久化）。"""
    cfg = dict(_SITE_CONFIG_DEFAULTS)
    file_banners = []
    try:
        with open(SITE_CONFIG_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            for k, v in saved.items():
                if k in _SITE_CONFIG_DEFAULTS and k != "banners":
                    cfg[k] = v
            if isinstance(saved.get("banners"), list):
                file_banners = saved["banners"]
    except Exception:
        pass
    # 轮播图：数据库优先（重启/重开不丢失）；旧文件有而数据库空时迁移一次
    try:
        c = db()
        try:
            rows = c.execute("SELECT name,title,link FROM banners ORDER BY sort,id").fetchall()
            if rows:
                cfg["banners"] = [{"name": r[0], "title": r[1] or "", "link": r[2] or ""} for r in rows]
            elif file_banners:
                for i, b in enumerate(file_banners[:50]):
                    if not isinstance(b, dict):
                        continue
                    name = str(b.get("name") or "").strip()
                    if not re.fullmatch(r"banner_[0-9a-f]{32}\.(?:jpg|jpeg|png|webp)", name):
                        continue
                    c.execute("INSERT OR IGNORE INTO banners(name,title,link,sort) VALUES(?,?,?,?)",
                              (name, str(b.get("title") or "")[:60], str(b.get("link") or "")[:300], i))
                c.commit()
                cfg["banners"] = [{"name": r[0], "title": r[1] or "", "link": r[2] or ""}
                                  for r in c.execute("SELECT name,title,link FROM banners ORDER BY sort,id").fetchall()]
        finally:
            c.close()
    except Exception:
        cfg["banners"] = []
    # 实时歌曲总数（用户上传 + 采集，排除 offline）
    try:
        c = db()
        n1 = c.execute("SELECT COUNT(*) FROM tracks WHERE offline=0").fetchone()[0]
        n2 = c.execute("SELECT COUNT(*) FROM collected_tracks WHERE offline=0").fetchone()[0]
        c.close()
        cfg["track_total"] = int(n1 or 0) + int(n2 or 0)
    except Exception:
        cfg["track_total"] = 0
    return cfg


def _read_site_config(force=False):
    """读官网配置：TTL 缓存 30s（审查 #11）；force=True 强制刷新（写入后调用）。"""
    now = time.time()
    with _SITE_CFG_LOCK:
        if not force and _SITE_CFG_CACHE["kv"] is not None and now - _SITE_CFG_CACHE["t"] < _SITE_CFG_TTL:
            return _SITE_CFG_CACHE["kv"]
    cfg = _read_site_config_uncached()
    with _SITE_CFG_LOCK:
        _SITE_CFG_CACHE["t"] = time.time()
        _SITE_CFG_CACHE["kv"] = cfg
    return cfg


def _write_site_config(patch):
    cfg = dict(_SITE_CONFIG_DEFAULTS)
    try:
        with open(SITE_CONFIG_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            for k, v in saved.items():
                if k in _SITE_CONFIG_DEFAULTS and k != "banners":
                    cfg[k] = v
    except Exception:
        pass
    for k, v in (patch or {}).items():
        if k not in _SITE_CONFIG_DEFAULTS:
            continue
        if k == "banners":
            # 官网轮播图：校验后全量写入数据库（动态数量、持久化），文件字段不再作为存储源
            banners = []
            if isinstance(v, list):
                for b in v[:50]:
                    if not isinstance(b, dict):
                        continue
                    name = str(b.get("name") or "").strip()
                    if not re.fullmatch(r"banner_[0-9a-f]{32}\.(?:jpg|jpeg|png|webp)", name):
                        continue
                    banners.append({
                        "name": name,
                        "title": str(b.get("title") or "")[:60],
                        "link": str(b.get("link") or "")[:300],
                    })
            try:
                c = db()
                try:
                    c.execute("DELETE FROM banners")
                    for i, b in enumerate(banners):
                        c.execute("INSERT INTO banners(name,title,link,sort) VALUES(?,?,?,?)",
                                  (b["name"], b["title"], b["link"], i))
                    c.commit()
                finally:
                    c.close()
            except Exception:
                pass
            cfg[k] = banners
        elif k == "pay_merchant_key":
            # 商户密钥：留空（含掩码态回传）表示保持不变，避免前台接口把密钥带回覆盖为空
            if isinstance(v, str) and v.strip() and "留空保持不变" not in v:
                cfg[k] = v.strip()
        elif isinstance(v, bool):
            cfg[k] = v
        elif isinstance(v, str):
            cfg[k] = v.strip()
    with open(SITE_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    # 写入后立即失效缓存，前台下次请求读到新配置
    with _SITE_CFG_LOCK:
        _SITE_CFG_CACHE["kv"] = None
    return cfg


def safe_file_path(fname):
    """仅允许 FILES 目录内的文件，防路径穿越（纵深防御）。"""
    if not fname:
        return None
    name = os.path.basename(str(fname))
    real_files = os.path.realpath(FILES)
    p = os.path.realpath(os.path.join(real_files, name))
    if not (p == real_files or p.startswith(real_files + os.sep)):
        return None
    if not os.path.exists(p):
        return None
    return p


def _fmt_dur(d):
    if not d or d <= 0:
        return "--:--"
    m, s = int(d) // 60, int(d) % 60
    return f"{m}:{s:02d}"


def _track_info(c, tid):
    """歌曲信息快照 JSON（title/artist/album）：行为记录用，推荐时提取相似度关键词。
    优先用户上传表，其次采集表；查不到返回空字符串。"""
    if not tid:
        return ""
    try:
        row = c.execute("SELECT title,artist,album FROM tracks WHERE id=?", (tid,)).fetchone()
        if not row:
            row = c.execute("SELECT title,artist,album FROM collected_tracks WHERE id=?", (tid,)).fetchone()
        if not row:
            return ""
        return json.dumps({"title": row[0] or "", "artist": row[1] or "",
                           "album": row[2] or ""}, ensure_ascii=False)[:500]
    except Exception:
        return ""


def _track_owner(c, tid):
    """歌曲上传者 (user_id, title)：优先用户上传表，其次采集表；查不到返回 (None, None)。
    点赞/评论/分享通知与双表计数均依赖此函数，避免采集歌曲的 owner 收不到通知。"""
    if not tid:
        return None, None
    try:
        row = c.execute("SELECT user_id, title FROM tracks WHERE id=?", (tid,)).fetchone()
        if not row:
            row = c.execute("SELECT user_id, title FROM collected_tracks WHERE id=?", (tid,)).fetchone()
        if not row:
            return None, None
        return row[0], row[1]
    except Exception:
        return None, None


# ================= 审查加固（2026-10-23）=================

# 随机 id 池缓存（#7）：发现页随机 10 首不再每请求全表扫 id。
# 缓存 60s；上传新歌后调用 invalidate_id_pool() 立即失效。
_ID_POOL_CACHE = {"t": 0.0, "tracks": None, "collected_tracks": None}
_ID_POOL_LOCK = threading.Lock()
_ID_POOL_TTL = 60.0
_ID_POOL_TABLES = ("tracks", "collected_tracks")


def _id_pool(c, table):
    """全表 offline=0 歌曲 id 列表（60s 缓存）。table 仅限白名单两表。"""
    if table not in _ID_POOL_TABLES:
        return []
    now = time.time()
    with _ID_POOL_LOCK:
        if _ID_POOL_CACHE[table] is not None and now - _ID_POOL_CACHE["t"] < _ID_POOL_TTL:
            return _ID_POOL_CACHE[table]
    rows = c.execute("SELECT id FROM %s WHERE offline=0" % table).fetchall()
    ids = [r[0] for r in rows]
    with _ID_POOL_LOCK:
        _ID_POOL_CACHE["t"] = time.time()
        _ID_POOL_CACHE[table] = ids
    return ids


def invalidate_id_pool():
    """上传/删除歌曲后调用：清空随机池缓存，新歌立即可被随机到。"""
    with _ID_POOL_LOCK:
        _ID_POOL_CACHE["tracks"] = None
        _ID_POOL_CACHE["collected_tracks"] = None


def fts_ids(c, table, keyword, limit):
    """FTS5 trigram 检索（#6）：返回歌曲 id 列表。
    - 关键词 ≥2 字符优先走 FTS（trigram 子串匹配，中文可用）；
    - FTS 不可用（SQLite<3.34）/语法错误/未命中 → LIKE 兜底补足。
    不抛异常，任何情况下返回 <=limit 条 id。"""
    ids = []
    kw = (keyword or "").strip()
    if not kw:
        return []
    if kw:
        try:
            fts = table + "_fts"
            # trigram tokenizer 规则：≥3 字符走普通子串匹配；2 字符必须用 LIKE 模式
            # （'%关键词%'），否则 trigram 对 <3 字符查询返回空。
            if len(kw) >= 3:
                _q = '"%s"' % kw
            else:
                _q = '%%%s%%' % kw
            rows = c.execute('SELECT id FROM %s WHERE %s MATCH ? LIMIT ?' % (fts, fts),
                             (_q, limit)).fetchall()
            ids = [r[0] for r in rows]
        except Exception:
            ids = []
    if len(ids) < limit:
        like = "%" + kw + "%"
        try:
            extra = c.execute(
                "SELECT id FROM %s WHERE offline=0 AND (title LIKE ? OR artist LIKE ? OR album LIKE ?) LIMIT ?"
                % table, (like, like, like, limit)).fetchall()
        except Exception:
            extra = []
        seen = set(ids)
        for r in extra:
            if r[0] not in seen:
                ids.append(r[0])
                seen.add(r[0])
    return ids[:limit]


# 过期/膨胀数据定期清理（#5）：行为/历史/通知表无限增长会拖慢推荐与统计查询。
# 由 main.py 启动 daemon 线程每 6 小时执行一次；时间窗口 + 全局条数上限双保险。
_CLEANUP_WINDOW = {
    "play_history": (180 * 86400, 20000),     # 保留 180 天 + 全局最近 2 万条
    "search_history": (90 * 86400, 2000),
    "behavior_events": (180 * 86400, 100000),
    "notifications": (180 * 86400, 50000),
    "share_tokens": (90 * 86400, 0),          # 仅按时间清理（短链过期即可删）
    # 推荐曝光（2026-11-05）：90 天足够训练与归因，超出即清。
    # 注意 cap 设为 0——该表没有单列 id（PK 是 request_id+track_id），
    # 条数上限走 id NOT IN 子查询会失败，只做时间窗口清理。
    "recommend_impressions": (90 * 86400, 0),
}
_CLEANUP_LOCK = threading.Lock()


def cleanup_expired_data():
    """清理过期验证码/token 与膨胀的行为/历史/通知数据（幂等，可随时手动调用）。"""
    if not _CLEANUP_LOCK.acquire(blocking=False):
        return   # 已有清理在执行（或上次未结束），跳过本轮，避免并发写锁堆积
    try:
        c = db()
        now = time.time()
        # 1) 过期验证码（5 分钟有效期 + 缓冲）
        c.execute("DELETE FROM email_codes WHERE created < ?", (now - 900,))
        # 2) 过期 token（30 天有效期 + 缓冲；登出 token 已在 _invalidate_token 删除）
        c.execute("DELETE FROM tokens WHERE expires < ?", (now - 86400,))
        # 3) 行为/历史/通知/短链：时间窗口 + 全局条数上限
        for _tbl, (_days, _cap) in _CLEANUP_WINDOW.items():
            try:
                c.execute("DELETE FROM %s WHERE created < ?" % _tbl, (now - _days,))
                if _cap:
                    c.execute("DELETE FROM %s WHERE id NOT IN (SELECT id FROM %s ORDER BY created DESC LIMIT ?)"
                              % (_tbl, _tbl), (_cap,))
            except Exception as _e:
                log.info("cleanup skip %s: %s", _tbl, _e)
        c.commit()
        c.close()
        log.info("cleanup_expired_data done")
    except Exception as e:
        try:
            c.close()
        except Exception:
            pass
        log.info("cleanup_expired_data err: %s", e)
    finally:
        _CLEANUP_LOCK.release()


def _route_metrics(c, days=7):
    """P1 归因闭环：近 days 天各推荐来源路(route)的收听/完听/跳过统计。
    listen 事件 meta.route 为逗号分隔来源路（前端 listen 上报携带，来自推荐接口 recRoute）；
    fraction>=0.6 视为完听（与长期行为画像同口径）。
    返回 [{route, listens, finished, finish_rate, skips}] 按完听率降序；无 route 数据返回 []。"""
    cutoff = time.time() - days * 86400
    agg = {}
    rows = c.execute(
        "SELECT event, meta FROM behavior_events "
        "WHERE created > ? AND event IN ('listen','skip')", (cutoff,)).fetchall()
    for ev, meta in rows:
        try:
            _m = json.loads(meta or "{}")
            if not isinstance(_m, dict):
                continue
            route = _m.get("route", "") or ""
        except Exception:
            continue
        if not route:
            continue
        for r in route.split(","):
            if not r:
                continue
            a = agg.setdefault(r, {"listens": 0, "finished": 0, "skips": 0})
            if ev == "listen":
                a["listens"] += 1
                try:
                    frac = float(_m.get("fraction", 0) or 0)
                except Exception:
                    frac = 0.0
                if frac >= 0.6:
                    a["finished"] += 1
            else:
                a["skips"] += 1
    out = []
    for r, a in agg.items():
        a["route"] = r
        a["finish_rate"] = round((a["finished"] / a["listens"]) if a["listens"] else 0.0, 3)
        out.append(a)
    out.sort(key=lambda x: x["finish_rate"], reverse=True)
    return out
