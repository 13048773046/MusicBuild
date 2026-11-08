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
from concurrent.futures import ThreadPoolExecutor

WS_LOCK = threading.Lock()
WS_CLIENTS = {}
# #12：sock→uid 反查表（ws_register/unregister/_drop_dead_sock 维护），死连接清理 O(1)
_WS_SOCK_OWNER = {}
# #9：离线推送线程池（原每通知开一个 daemon 线程，高并发通知风暴下线程无限膨胀）
_OFFLINE_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="offline_push")

__all__ = ['WS_CLIENTS', 'WS_GUID', 'WS_LOCK', 'WS_STATS_THROTTLE', '_ws_frame', '_ws_handshake', '_ws_recv_exact', '_ws_refresh_recommend', '_ws_serve', 'ws_broadcast', 'ws_push', 'ws_register', 'ws_unregister']

WS_LOCK = threading.Lock()


WS_CLIENTS = {}          # user_id -> set(sock)


WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


WS_STATS_THROTTLE = {}   # uid -> 上次推送 stats/recommend 时间（行为上报节流，防止高频推送）


def _ws_frame(payload, opcode=1):
    """构造服务端→客户端 WebSocket 数据帧（不掩码）。"""
    n = len(payload)
    header = bytearray([0x80 | opcode])
    if n < 126:
        header.append(n)
    elif n < 65536:
        header.append(126)
        header += struct.pack(">H", n)
    else:
        header.append(127)
        header += struct.pack(">Q", n)
    return bytes(header) + payload


def ws_register(uid, sock):
    with WS_LOCK:
        WS_CLIENTS.setdefault(uid, set()).add(sock)
        _WS_SOCK_OWNER[sock] = uid   # #12：sock→uid 反查，死连接清理 O(1)


def ws_unregister(uid, sock):
    with WS_LOCK:
        s = WS_CLIENTS.get(uid)
        if s:
            s.discard(sock)
            if not s:
                WS_CLIENTS.pop(uid, None)
        _WS_SOCK_OWNER.pop(sock, None)


# #12：死连接清理——原来遍历全表找 uid（O(n²)），现按反查表直接定位
def _drop_dead_sock(dead):
    with WS_LOCK:
        for d in dead:
            uid2 = _WS_SOCK_OWNER.pop(d, None)
            if uid2 is None:
                continue
            st = WS_CLIENTS.get(uid2)
            if st:
                st.discard(d)
                if not st:
                    WS_CLIENTS.pop(uid2, None)


def _ws_send(sock, frame, timeout=1.0):
    """带超时发送：死连接/阻塞连接 1s 内判定失败并清理，不卡死调用方。"""
    try:
        sock.settimeout(timeout)
        sock.sendall(frame)
        return True
    except Exception:
        return False
    finally:
        try:
            sock.settimeout(None)
        except Exception:
            pass


def ws_push(uid, payload):
    """向指定用户的全部在线连接推送 JSON 对象（新通知实时送达）。
    用户不在线时（APP 被杀），通过极光推送离线通知。"""
    with WS_LOCK:
        socks = list(WS_CLIENTS.get(uid, ()))
    if socks:
        frame = _ws_frame(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        dead = []
        for s in socks:
            if not _ws_send(s, frame):
                dead.append(s)
        if dead:
            _drop_dead_sock(dead)
    else:
        # 用户不在线：极光离线推送（需后台开关开启）。
        # 整体异步 daemon 线程执行：_read_site_config() 会开新的 DB 连接执行
        # PRAGMA journal_mode=WAL 与建表 DDL，与请求线程的未提交写事务冲突时会
        # 等待 busy_timeout（实测可达 5s×2）；极光推送又是外部 HTTP 调用。
        # 两者都不应阻塞互动接口（点赞/评论/分享/发布）的响应线程，故整个离线分支后台化。
        try:
            notif = payload.get("notification")
            if notif and payload.get("kind") == "notification":
                def _offline_job(uid2, n):
                    try:
                        from server.common import _read_site_config
                        cfg = _read_site_config()
                        if cfg.get("jpush_enabled", False):
                            from server.jpush import push_to_alias
                            title = n.get("title", "流萤音乐")
                            alert = n.get("content", "")
                            extras = {"type": n.get("type", ""),
                                      "trackId": n.get("trackId", ""),
                                      "fromUserId": n.get("fromUserId", "")}
                            push_to_alias(uid2, title, alert, extras)
                    except Exception as e:
                        log.info("offline push fail uid=%s err=%s", uid2, e)
                # #9：提交线程池，避免每通知一个线程（高峰通知风暴线程无限膨胀）
                _OFFLINE_POOL.submit(_offline_job, uid, dict(notif))
        except Exception as e:
            log.info("offline push err uid=%s err=%s", uid, e)


def ws_broadcast(payload):
    """向所有在线连接广播（官方通知发布用）。"""
    with WS_LOCK:
        socks = set()
        for st in WS_CLIENTS.values():
            socks |= set(st)
    if not socks:
        return
    frame = _ws_frame(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    dead = []
    for s in socks:
        if not _ws_send(s, frame):
            dead.append(s)
    if dead:
        _drop_dead_sock(dead)


def _ws_refresh_recommend(c, uid, has_listen):
    """行为变化后节流推送：15s 内合并多次上报，避免高频推送；
    推送 recommend 事件驱动前端刷新「猜你喜欢」，收听时附带听歌时长统计。"""
    _now = time.time()
    _last = WS_STATS_THROTTLE.get(uid, 0)
    if _now - _last < 15:
        return
    WS_STATS_THROTTLE[uid] = _now
    if has_listen:
        _r1 = c.execute("SELECT COALESCE(SUM(value),0) FROM behavior_events"
                        " WHERE user_id=? AND event='listen'", (uid,)).fetchone()
        _r2 = c.execute("SELECT COUNT(DISTINCT track_id) FROM behavior_events"
                        " WHERE user_id=? AND event='listen'", (uid,)).fetchone()
        ws_push(uid, {"kind": "event", "event": "stats",
                      "totalSeconds": int(_r1[0]) if _r1 else 0,
                      "trackCount": int(_r2[0]) if _r2 else 0})
    ws_push(uid, {"kind": "event", "event": "recommend"})


def _ws_recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except socket.timeout:
            # #8：90s 无任何数据视为半开/静默断连，断开释放线程
            return None
        except OSError:
            return None
        if not chunk:
            return None
        buf += chunk
    return buf


def _ws_serve(handler, uid):
    """握手完成后进入帧循环：响应 ping/pong/close，连接断开自动清理。"""
    sock = handler.connection
    ws_register(uid, sock)
    try:
        # #8：读超时 90s——断网/半开连接不再无限 hang 住请求线程。
        # 客户端心跳间隔小于该值（双端心跳 ~30s），正常连接不受影响。
        sock.settimeout(90)
        while True:
            head = _ws_recv_exact(sock, 2)
            if head is None:
                break
            b0, b1 = head[0], head[1]
            opcode = b0 & 0x0F
            masked = b1 & 0x80
            length = b1 & 0x7F
            if length == 126:
                ext = _ws_recv_exact(sock, 2)
                if ext is None:
                    break
                length = struct.unpack(">H", ext)[0]
            elif length == 127:
                ext = _ws_recv_exact(sock, 8)
                if ext is None:
                    break
                length = struct.unpack(">Q", ext)[0]
            if length > 16 * 1024 * 1024:
                break
            # WebSocket 协议：mask key（4 字节）位于 payload 之前；
            # 若先读 payload 再读 mask，会把 mask key 误读进 payload 导致解密乱码
            if masked:
                mask = _ws_recv_exact(sock, 4)
                if mask is None:
                    break
            payload = b""
            while len(payload) < length:
                chunk = sock.recv(min(length - len(payload), 65536))
                if not chunk:
                    payload = None
                    break
                payload += chunk
            if payload is None:
                break
            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:      # close → 回 close 帧后断开
                try:
                    sock.sendall(_ws_frame(b"", opcode=8))
                except Exception:
                    pass
                break
            elif opcode == 0x9:    # ping → pong（保活）
                try:
                    sock.sendall(_ws_frame(payload, opcode=0xA))
                except Exception:
                    break
            elif opcode == 0xA:    # pong
                continue
            elif opcode in (0x1, 0x2):  # text/binary：客户端业务消息
                # 实时行为上报（即时通讯通道）：{"type":"behavior","events":[...]}
                # 与 HTTP /api/behavior 共用落库逻辑；落库后节流推送 recommend 事件，
                # 前端收到即刷新「猜你喜欢」，实现行为一变、推荐实时更新
                try:
                    _msg = json.loads(payload.decode("utf-8") or "{}")
                except Exception as _e:
                    log.info("ws behavior json err %s", _e)
                    _msg = None
                if isinstance(_msg, dict) and _msg.get("type") == "behavior":
                    _evs = _msg.get("events")
                    if isinstance(_evs, list) and _evs and len(_evs) <= 30:
                        try:
                            _c = db()
                            _ins, _hl = _ingest_behavior(_c, uid, _evs, time.time())
                            if _ins:
                                _c.commit()
                                _ws_refresh_recommend(_c, uid, _hl)
                            log.info("ws behavior uid=%s ins=%d", uid, _ins)
                            _c.close()
                        except Exception as _e:
                            log.info("ws behavior ingest err %s", _e)
                continue
    except Exception:
        pass
    finally:
        ws_unregister(uid, sock)
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass


def _ws_handshake(handler, u):
    """GET /api/ws?token=xxx → 101 升级为 WebSocket；未鉴权返回 401。"""
    key = handler.headers.get("Sec-WebSocket-Key", "")
    if not key:
        handler._err(400, "missing websocket key")
        return
    qs = urllib.parse.parse_qs(u.query)
    tok = (qs.get("token", [""])[0] or "").strip()
    c = db()
    uid = None
    if tok:
        row = c.execute("SELECT user_id FROM tokens WHERE token=? AND expires>?",
                        (tok, time.time())).fetchone()
        uid = row[0] if row else None
    c.close()
    if not uid:
        try:
            handler.send_response(401)
            handler.send_header("Content-Type", "text/plain; charset=utf-8")
            handler.send_header("Content-Length", "13")
            handler.end_headers()
            handler.wfile.write(b"unauthorized")
        except Exception:
            pass
        return
    accept = base64.b64encode(
        hashlib.sha1((key + WS_GUID).encode()).digest()).decode("ascii")
    try:
        handler.wfile.write(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: " + accept.encode("ascii") + b"\r\n\r\n")
        handler.wfile.flush()
    except Exception:
        return
    _ws_serve(handler, uid)
