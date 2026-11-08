# -*- coding: utf-8 -*-
"""P2-7：admin 域路由（mixin，由 server/handler.py 的 H 类继承）。
拆分自 handler.py 的 do_DELETE 顶层分支，逻辑与路由条件保持原样，
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


class AdminMixin:

    def _h_delete_admin(self, u):
        length = int(self.headers.get("Content-Length", 0))
        u = urllib.parse.urlparse(self.path)
        if u.path.startswith("/api/admin/ai-comment/"):
            if self.headers.get("X-Admin-Token", "").strip() != _admin_token():
                self._err(401, "unauthorized"); return True
            cid = u.path[len("/api/admin/ai-comment/"):]
            if cid:
                c = db()
                try:
                    c.execute("DELETE FROM comments WHERE id=? AND user_id=?", (cid, _ai_uid()))
                    c.commit()
                finally:
                    c.close()
            self._json(200, {"ok": True}); return True
        if u.path.startswith("/api/admin/splash-ads/"):
            if self.headers.get("X-Admin-Token", "").strip() != _admin_token():
                self._err(401, "unauthorized"); return True
            sid = u.path[len("/api/admin/splash-ads/"):]
            c = db()
            try:
                row = c.execute("SELECT name FROM splash_ads WHERE id=?", (sid,)).fetchone()
                if row:
                    c.execute("DELETE FROM splash_ads WHERE id=?", (sid,))
                    c.commit()
            finally:
                c.close()
            if row:
                try:
                    os.remove(os.path.join(FILES, row[0]))
                except OSError:
                    pass
            self._json(200, {"ok": True}); return True
        if u.path.startswith("/api/admin/official-message/"):
            if self.headers.get("X-Admin-Token", "").strip() != _admin_token():
                self._err(401, "unauthorized"); return True
            mid = u.path.rsplit("/", 1)[-1]
            c = db()
            row = c.execute("SELECT content FROM official_messages WHERE id=?", (mid,)).fetchone()
            if not row:
                c.close()
                self._err(404, "not found"); return True
            content = row[0]
            c.execute("DELETE FROM official_messages WHERE id=?", (mid,))
            # 官方通知发布时按内容为每个用户生成一条 notifications 记录；
            # 无外键关联，按 type+content 匹配删除（官方通知内容一般唯一）。
            c.execute("DELETE FROM notifications WHERE type='official' AND content=?", (content,))
            c.commit()
            c.close()
            self._json(200, {"ok": True}); return True
        self._err(404, "unknown")
        return False

    def _admin_auth(self):
        """后台鉴权：X-Admin-Token 比对（类方法级，GET/POST 共用）"""
        return self.headers.get("X-Admin-Token", "").strip() == _admin_token()

    def _h_get_admin(self, u):
        if u.path.startswith("/api/admin/song-insight"):
            # 单曲"系统理解"核对（2026-11-05）：画像 + 标签 + 语义邻居 + 向量信息
            # 用法：/api/admin/song-insight?id=<track_id>  或 ?q=<歌名关键词>
            if not self._admin_auth():
                self._err(401, "unauthorized")
                return True
            qs = urllib.parse.parse_qs(u.query)

            def _jl(s, default=None):
                """安全 JSON 解析（画像里的 JSON 数组/对象字段）。"""
                if not s:
                    return default if default is not None else []
                try:
                    return json.loads(s)
                except Exception:
                    return default if default is not None else []

            tid = (qs.get("id") or [""])[0].strip()
            kw = (qs.get("q") or [""])[0].strip()
            try:
                c2 = db()
                try:
                    if not tid and kw:
                        r = c2.execute(
                            "SELECT id FROM collected_tracks WHERE title LIKE ?"
                            " ORDER BY play_count DESC LIMIT 1", ("%" + kw + "%",)).fetchone()
                        if not r:
                            r = c2.execute(
                                "SELECT id FROM tracks WHERE title LIKE ?"
                                " ORDER BY play_count DESC LIMIT 1", ("%" + kw + "%",)).fetchone()
                        tid = r[0] if r else ""
                    if not tid:
                        self._json(200, {"ok": False, "err": "no_track"})
                        return True
                    row = c2.execute(
                        "SELECT id,title,COALESCE(artist,''),COALESCE(album,''),COALESCE(genre,''),"
                        " COALESCE(play_count,0),COALESCE(like_count,0),COALESCE(lrc,'')"
                        " FROM collected_tracks WHERE id=?"
                        " UNION ALL SELECT id,title,COALESCE(artist,''),COALESCE(album,''),"
                        " COALESCE(genre,''),COALESCE(play_count,0),COALESCE(like_count,0),"
                        " COALESCE(lrc,'') FROM tracks WHERE id=?", (tid, tid)).fetchone()
                    if not row:
                        self._json(200, {"ok": False, "err": "not_found", "id": tid})
                        return True
                    prof = None
                    try:
                        pr = c2.execute(
                            "SELECT sub,genres,moods,scenes,instr,vocal,theme,arc,profile,"
                            " valence,energy,warmth,lang,era FROM track_profiles WHERE track_id=?",
                            (tid,)).fetchone()
                        if pr:
                            prof = {"sub": pr[0], "genres": _jl(pr[1]), "moods": _jl(pr[2]),
                                    "scenes": _jl(pr[3]), "instr": _jl(pr[4]),
                                    "vocal": pr[5], "theme": pr[6], "arc": pr[7],
                                    "profile": pr[8], "valence": pr[9], "energy": pr[10],
                                    "warmth": pr[11], "lang": pr[12], "era": pr[13]}
                    except Exception:
                        prof = None
                    tags = {}
                    try:
                        for kind, tag in c2.execute(
                                "SELECT kind,tag FROM track_tags WHERE track_id=?", (tid,)).fetchall():
                            tags.setdefault(kind, []).append(tag)
                    except Exception:
                        pass
                    neigh = []
                    try:
                        nrows = c2.execute(
                            "SELECT n.sim_track_id, n.score, COALESCE(x.title,''),"
                            " COALESCE(x.artist,''), COALESCE(x.genre,'') FROM track_neighbors n"
                            " LEFT JOIN (SELECT id,title,artist,genre FROM collected_tracks"
                            "   UNION ALL SELECT id,title,artist,COALESCE(genre,'') FROM tracks) x"
                            " ON x.id=n.sim_track_id"
                            " WHERE n.track_id=? AND n.src='vec' ORDER BY n.rank LIMIT 12",
                            (tid,)).fetchall()
                        for sid, sc, t, a, g in nrows:
                            neigh.append({"id": sid, "score": round(float(sc or 0), 3),
                                          "title": t, "artist": a, "genre": g})
                    except Exception:
                        pass
                    emb = {}
                    try:
                        er = c2.execute("SELECT dim,tdim,updated FROM track_emb WHERE track_id=?",
                                        (tid,)).fetchone()
                        if er:
                            emb = {"dim": er[0], "tdim": er[1], "updated": er[2]}
                    except Exception:
                        pass
                    audio = {}
                    try:
                        ar = c2.execute("SELECT features FROM audio_features WHERE track_id=?",
                                        (tid,)).fetchone()
                        if ar and ar[0]:
                            audio = _jl(ar[0]) if isinstance(ar[0], str) else {}
                            for k in ("mfcc_mean", "mfcc_std", "bpm_band"):
                                audio.pop(k, None)
                    except Exception:
                        pass
                    self._json(200, {
                        "ok": True,
                        "track": {"id": row[0], "title": row[1], "artist": row[2],
                                  "album": row[3], "genre": row[4], "playCount": row[5],
                                  "likeCount": row[6], "hasLyric": bool(row[7])},
                        "profile": prof, "tags": tags, "neighbors": neigh,
                        "embedding": emb, "audio": audio})
                finally:
                    c2.close()
            except Exception as e:
                self._internal_error(e)
            return True
        if u.path.startswith("/api/admin/rec-eval"):
            # 推荐离线评估：GET 查趋势；?run=1 立即跑一轮并留档（2026-11-05）
            if not self._admin_auth():
                self._err(401, "unauthorized")
                return True
            qs = urllib.parse.parse_qs(u.query)
            if (qs.get("run") or [""])[0] in ("1", "true", "yes"):
                try:
                    from server import rec_eval
                    res = rec_eval.run_and_save(users=int((qs.get("users") or ["5"])[0]))
                    self._json(200, {"ok": True, "result": res})
                except Exception as e:
                    self._internal_error(e)
                return True
            try:
                from server import rec_eval
                days = int((qs.get("days") or ["30"])[0])
                self._json(200, {"ok": True, "trend": rec_eval.trend(days=days)})
            except Exception as e:
                self._internal_error(e)
            return True
        if u.path == "/api/admin/recommend-config":
            if not self._admin_auth(): self._err(401, "unauthorized"); return True
            if self.command == "GET":
                saved = {}
                route_stats = []
                imp_stats = {}
                try:
                    c2 = db()
                    try:
                        for k, v in c2.execute("SELECT key, value FROM ai_comment_cfg").fetchall():
                            if k.startswith("rec_"): saved[k] = v
                        # P1 归因闭环：各路完听率/跳过统计（listen 上报带 route 后自动累计）
                        route_stats = _route_metrics(c2, 7)
                        # 曝光/点击 CTR（2026-11-05）：分路 + 分位置，LTR 上线前的基线
                        try:
                            from server import impressions as _imp
                            imp_stats = _imp.stats(c2, 7)
                        except Exception:
                            imp_stats = {}
                    finally:
                        c2.close()
                except Exception:
                    pass
                self._json(200, {"ok": True, "saved": saved,
                                 "routeStats": route_stats,
                                 "impressionStats": imp_stats,
                                 "defaults": {
                    "rec_w_behavior": REC_WEIGHTS["behavior"],
                    "rec_w_genre": REC_WEIGHTS["genre"],
                    "rec_w_artist": REC_WEIGHTS["artist"],
                    "rec_w_scene": REC_WEIGHTS["scene"],
                    "rec_w_hot": REC_WEIGHTS["hot"],
                    "rec_w_fresh": REC_WEIGHTS["fresh"],
                    "rec_w_explore": REC_WEIGHTS["explore"],
                    "rec_w_audio_taste": REC_WEIGHTS.get("audio_taste", 1.8),
                    "rec_w_tag_taste": REC_WEIGHTS.get("tag_taste", 1.3),
                    "rec_w_audio_multi": REC_WEIGHTS.get("audio_multi", 1.5),
                    "rec_w_time": REC_WEIGHTS.get("time_taste", 0.8),
                    "rec_explore_pool": REC_EXPLORE_POOL,
                    "rec_scene_boost": REC_SCENE_BOOST,
                    "rec_noise": REC_NOISE,
                    "rec_max_per_artist": REC_MAX_PER_ARTIST,
                }}); return True
            if self.command == "POST":
                try:
                    body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 16 * 1024)).decode("utf-8") or "{}")
                except Exception:
                    self._err(400, "bad json"); return True
                allowed = {"rec_w_behavior", "rec_w_genre", "rec_w_artist", "rec_w_scene", "rec_w_hot",
                           "rec_w_fresh", "rec_w_explore", "rec_explore_pool", "rec_scene_boost",
                           "rec_noise", "rec_max_per_artist",
                           # 2026-11-05 新增可调参数（听感/心情/多兴趣/时段）
                           "rec_w_audio_taste", "rec_w_tag_taste", "rec_w_audio_multi", "rec_w_time"}
                c2 = db()
                try:
                    for k, v in body.items():
                        if k in allowed and (isinstance(v, (int, float)) or (isinstance(v, str) and v.strip())):
                            c2.execute("INSERT OR REPLACE INTO ai_comment_cfg(key,value) VALUES(?,?)", (k, str(v)))
                    c2.commit()
                finally:
                    c2.close()
                self._json(200, {"ok": True}); return True
        if u.path == "/api/admin/auth-check":
            if self.headers.get("X-Admin-Token", "").strip() != ADMIN_TOKEN:
                self._err(401, "unauthorized"); return True
            self._json(200, {"ok": True}); return True
        if u.path == "/admin.html":
            admin_html = _render_template("admin.html")
            body=admin_html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Content-Length",str(len(body)))
            self.send_header("Access-Control-Allow-Origin","*")
            self.end_headers();self.wfile.write(body);return True
        if u.path == "/api/admin/stats":
            if not self._admin_auth(): self._err(401,"unauthorized"); return True
            c=db()
            users=c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            tracks=c.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
            coll=c.execute("SELECT COUNT(*) FROM collected_tracks").fetchone()[0]
            plays=c.execute("SELECT COALESCE(SUM(play_count),0) FROM tracks").fetchone()[0] + c.execute("SELECT COALESCE(SUM(play_count),0) FROM collected_tracks").fetchone()[0]
            c.close()
            self._json(200,{"ok":True,"users":users,"tracks":tracks,"collected":coll,"plays":plays});return True
        if u.path == "/api/admin/users":
            if not self._admin_auth(): self._err(401,"unauthorized"); return True
            c=db()
            rows=c.execute("""SELECT u.id,u.account,u.name,u.avatar,u.gender,u.age,u.signature,u.ip_address,u.banned,u.ban_reason,u.created,
                              (SELECT COUNT(*) FROM tracks t WHERE t.user_id=u.id),
                              u.title,u.verified,u.verified_label,u.member_expire,u.svip_expire
                              FROM users u ORDER BY u.created DESC""").fetchall()
            # 在线状态：WebSocket 活跃连接快照（一次取全集，避免逐用户加锁）
            with WS_LOCK:
                online_set = set(WS_CLIENTS.keys())
            out=[]
            for r in rows:
                uid=r[0]
                dev=c.execute("SELECT platform FROM devices WHERE user_id=? ORDER BY last_seen DESC LIMIT 1",(uid,)).fetchone()
                out.append({"id":uid,"account":r[1] or "","name":r[2] or "","avatar":r[3] or "","gender":r[4] or "",
                            "age":r[5] or 0,"signature":r[6] or "","ip":r[7] or "","banned":r[8] or 0,
                            "ban_reason":r[9] or "","created":r[10] or 0,"track_count":r[11],
                            "platform":(dev[0] if dev else "") or "","is_online":uid in online_set,
                            "title":r[12] or "", "verified":r[13] or "", "verifiedLabel":r[14] or "",
                            "member": member_level(r[15], r[16]),
                            "memberExpire": r[15] or 0, "svipExpire": r[16] or 0})
            c.close()
            self._json(200,{"ok":True,"list":out});return True
        if u.path == "/api/admin/tracks":
            if not self._admin_auth(): self._err(401,"unauthorized"); return True
            qs = urllib.parse.parse_qs(u.query)
            page = max(1, int((qs.get("page") or ["1"])[0]))
            size = min(100, max(5, int((qs.get("size") or ["20"])[0])))
            c=db()
            total = c.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
            rows=c.execute("""SELECT t.id,t.title,t.artist,t.dur,t.size,t.play_count,t.created,t.user_id,t.offline,t.offline_reason,COALESCE(u.name,'')
                              FROM tracks t LEFT JOIN users u ON u.id=t.user_id
                              ORDER BY t.created DESC LIMIT ? OFFSET ?""",(size,(page-1)*size)).fetchall()
            c.close()
            self._json(200,{"ok":True,"total":total,"page":page,"size":size,"list":[{"id":r[0],"title":r[1] or "","artist":r[2] or "","dur":r[3] or 0,"size":r[4] or 0,"play_count":r[5] or 0,"created":r[6] or 0,"uname":r[10],"offline":r[8] or 0,"offline_reason":r[9] or ""} for r in rows]});return True
        if u.path == "/api/admin/collected":
            if not self._admin_auth(): self._err(401,"unauthorized"); return True
            qs = urllib.parse.parse_qs(u.query)
            page = max(1, int((qs.get("page") or ["1"])[0]))
            size = min(100, max(5, int((qs.get("size") or ["20"])[0])))
            c=db()
            total = c.execute("SELECT COUNT(*) FROM collected_tracks").fetchone()[0]
            rows=c.execute("""SELECT id,title,artist,source,duration,play_url,play_count,created,offline,offline_reason
                              FROM collected_tracks ORDER BY created DESC LIMIT ? OFFSET ?""",(size,(page-1)*size)).fetchall()
            c.close()
            self._json(200,{"ok":True,"total":total,"page":page,"size":size,"list":[{"id":r[0],"title":r[1] or "","artist":r[2] or "","source":r[3] or "","duration":r[4] or 0,"has_local":bool(r[5] and not str(r[5]).startswith("http")),"play_count":r[6] or 0,"created":r[7] or 0,"offline":r[8] or 0,"offline_reason":r[9] or ""} for r in rows]});return True
        if u.path == "/api/admin/comments":
            if not self._admin_auth(): self._err(401,"unauthorized"); return True
            qs = urllib.parse.parse_qs(u.query)
            page = max(1, int((qs.get("page") or ["1"])[0]))
            size = min(100, max(5, int((qs.get("size") or ["20"])[0])))
            tid = (qs.get("track_id") or [""])[0]
            c = db()
            where, args = "", []
            if tid:
                where = " WHERE cm.track_id=?"; args.append(tid)
            total = c.execute("SELECT COUNT(*) FROM comments cm" + where, args).fetchone()[0]
            rows = c.execute("""SELECT cm.id,cm.track_id,cm.user_name,cm.content,cm.created,
                                COALESCE(t.title,'(已删除)')
                                FROM comments cm LEFT JOIN tracks t ON t.id=cm.track_id
                                """ + where + " ORDER BY cm.created DESC LIMIT ? OFFSET ?",
                                tuple(args + [size, (page-1)*size])).fetchall()
            c.close()
            self._json(200, {"ok":True,"total":total,"page":page,"size":size,
                "list":[{"id":r[0],"track_id":r[1],"user":r[2] or "","content":r[3] or "","created":r[4] or 0,"track_title":r[5]} for r in rows]});return True
        if u.path == "/api/admin/ai-config":
            if not self._admin_auth(): self._err(401, "unauthorized"); return True
            if self.command == "GET":
                cfg = _ai_cfg()
                key = cfg.get("key") or ""
                masked = (key[:4] + "****" + key[-4:]) if len(key) > 8 else ("已配置" if key else "")
                self._json(200, {"ok": True, "base": cfg.get("base", ""),
                                 "model": cfg.get("model", ""),
                                 "keyMasked": masked, "hasKey": bool(key)})
                return True
            if self.command == "POST":
                try:
                    ln = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(ln).decode("utf-8") or "{}") if ln else {}
                except Exception:
                    body = {}
                _ai_save_config(base=body.get("base"), key=body.get("key"), model=body.get("model"))
                self._json(200, {"ok": True, "saved": True}); return True
            self._err(400, "bad request"); return True
        if u.path == "/api/admin/ai-profile":
            if not self._admin_auth(): self._err(401, "unauthorized"); return True
            p = _ai_profile()
            self._json(200, {"ok": True, **p}); return True
        if u.path.startswith("/api/admin/ai-comment"):
            if not self._admin_auth(): self._err(401, "unauthorized"); return True
            seg = u.path[len("/api/admin/ai-comment"):]
            if u.path == "/api/admin/ai-comment":
                if self.command == "GET":
                    stats = _ai_comment_stats()
                    c = db()
                    try:
                        logs = c.execute(
                            "SELECT id,run_type,track_id,track_title,status,comment_id,reason,created "
                            "FROM ai_comment_logs ORDER BY created DESC LIMIT 50").fetchall()
                    finally:
                        c.close()
                    _scfg = {}
                    c2 = db()
                    try:
                        for k, v in c2.execute("SELECT key,value FROM ai_comment_cfg WHERE key IN ('enabled','interval','count','max_per_track','skip_days','comment_image')").fetchall():
                            _scfg[k] = v
                    finally:
                        c2.close()
                    stats["settings"] = _scfg
                    stats["logs"] = [{"id": r[0], "runType": r[1], "trackId": r[2],
                                      "trackTitle": r[3], "status": r[4], "commentId": r[5],
                                      "reason": r[6], "created": r[7]} for r in logs]
                    self._json(200, {"ok": True, **stats}); return True
                if self.command == "POST":
                    try:
                        ln = int(self.headers.get("Content-Length") or 0)
                        body = json.loads(self.rfile.read(ln).decode("utf-8") or "{}") if ln else {}
                    except Exception:
                        body = {}
                    c = db()
                    try:
                        for k in ("enabled", "interval", "count", "max_per_track", "skip_days"):
                            if k in body:
                                c.execute("INSERT OR REPLACE INTO ai_comment_cfg(key,value) VALUES(?,?)",
                                          (k, str(body[k])))
                        c.commit()
                    finally:
                        c.close()
                    self._json(200, {"ok": True, "settings": _ai_auto_settings()}); return True
                self._err(400, "bad request"); return True
            if seg == "/run" and self.command == "POST":
                try:
                    ln = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(ln).decode("utf-8") or "{}") if ln else {}
                except Exception:
                    body = {}
                res = _ai_auto_run("manual", count=body.get("count"),
                                   force=bool(body.get("force", False)))
                self._json(200, {"ok": True, **res}); return True
            if seg.startswith("/") and len(seg) > 1 and self.command == "DELETE":
                cid = seg[1:]
                c = db()
                try:
                    c.execute("DELETE FROM comments WHERE id=? AND user_id=?", (cid, _ai_uid()))
                    c.commit()
                finally:
                    c.close()
                self._json(200, {"ok": True}); return True
            self._err(400, "bad request"); return True
        if u.path == "/api/admin/official-messages":
            admin_token = self.headers.get("X-Admin-Token", "").strip()
            if admin_token != _admin_token():
                self._err(401, "unauthorized"); return True
            c = db()
            rows = c.execute("""SELECT m.id, m.content, m.created, m.sender_name, m.receiver_user_id, COALESCE(u.name,'')
                                FROM official_messages m LEFT JOIN users u ON u.id=m.receiver_user_id
                                ORDER BY m.created DESC LIMIT 200""").fetchall()
            c.close()
            self._json(200, {"list": [{"id": r[0], "content": r[1], "created": r[2], "sender": r[3],
                                       "receiverUserId": r[4] or "", "receiverUserName": r[5] or ""} for r in rows]})
            return True
        if u.path == "/api/admin/splash-ads":
            if self.headers.get("X-Admin-Token", "").strip() != _admin_token():
                self._err(401, "unauthorized")
                return True
            c = db()
            try:
                rows = c.execute("SELECT id,name,title,link,active,created FROM splash_ads ORDER BY sort,id").fetchall()
            finally:
                c.close()
            self._json(200, {"list": [
                {"id": r[0], "name": r[1], "title": r[2] or "", "link": r[3] or "",
                 "active": r[4], "img": "/api/splash/" + r[1],
                 "created": r[5] or 0}
                for r in rows
            ]})
            return True
        # ===== 认证管理（2026-10-19 会员体系）=====
        if u.path == "/api/admin/verify-categories":
            if not self._admin_auth(): self._err(401, "unauthorized"); return True
            c = db()
            rows = c.execute("SELECT id, level1, level2, created FROM verification_categories ORDER BY created").fetchall()
            c.close()
            self._json(200, {"ok": True, "list": [{"id": r[0], "level1": r[1], "level2": r[2] or "",
                                                   "created": r[3] or 0} for r in rows]})
            return True
        if u.path == "/api/admin/verify-applications":
            if not self._admin_auth(): self._err(401, "unauthorized"); return True
            qs = urllib.parse.parse_qs(u.query)
            status = (qs.get("status") or [""])[0].strip() or "pending"
            c = db()
            rows = c.execute("""SELECT a.id, a.user_id, u.name, u.account, a.level1, a.level2, a.label, a.reason,
                                       a.status, a.created, a.reviewed_at, a.review_note
                                FROM verification_applications a LEFT JOIN users u ON u.id=a.user_id
                                WHERE a.status=? ORDER BY a.created DESC LIMIT 100""", (status,)).fetchall()
            c.close()
            self._json(200, {"ok": True, "list": [{"id": r[0], "userId": r[1], "userName": r[2] or "",
                                                   "account": r[3] or "", "level1": r[4] or "",
                                                   "level2": r[5] or "", "label": r[6] or "",
                                                   "reason": r[7] or "", "status": r[8],
                                                   "created": r[9] or 0, "reviewedAt": r[10] or 0,
                                                   "reviewNote": r[11] or ""} for r in rows]})
            return True
        return False

    def _h_post_admin(self, u):
        length = int(self.headers.get("Content-Length", 0))
        ADMIN_TOKEN = _admin_token()
        ADMIN_TOKEN = _admin_token()
        if u.path == "/api/admin/ai-config":
            if self.headers.get("X-Admin-Token", "").strip() != _admin_token():
                self._err(401, "unauthorized"); return True
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}
            except Exception:
                body = {}
            _ai_save_config(base=body.get("base"), key=body.get("key"), model=body.get("model"))
            self._json(200, {"ok": True, "saved": True}); return True
        if u.path == "/api/admin/ai-comment":
            if self.headers.get("X-Admin-Token", "").strip() != _admin_token():
                self._err(401, "unauthorized"); return True
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}
            except Exception:
                body = {}
            c = db()
            try:
                for k in ("enabled", "interval", "count", "max_per_track", "skip_days"):
                    if k in body:
                        c.execute("INSERT OR REPLACE INTO ai_comment_cfg(key,value) VALUES(?,?)",
                                  (k, str(body[k])))
                c.commit()
            finally:
                c.close()
            self._json(200, {"ok": True, "settings": _ai_auto_settings()}); return True
        if u.path == "/api/admin/ai-comment/run":
            if self.headers.get("X-Admin-Token", "").strip() != _admin_token():
                self._err(401, "unauthorized"); return True
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}
            except Exception:
                body = {}
            res = _ai_auto_run("manual", count=body.get("count"),
                               force=bool(body.get("force", False)))
            self._json(200, {"ok": True, **res}); return True
        if u.path == "/api/admin/ai-profile":
            if self.headers.get("X-Admin-Token", "").strip() != _admin_token():
                self._err(401, "unauthorized"); return True
            try:
                fields, files = self._parse_multipart(length)
            except Exception:
                self._err(400, "need multipart"); return True
            c = db()
            try:
                name = (fields.get("name") or "").strip()[:MAX_NAME] or "AI 乐评"
                signature = (fields.get("signature") or "").strip()[:100]
                avatar_fname = None
                bg_fname = None
                av = files.get("avatar")
                if av and av[1]:
                    aext = os.path.splitext(av[0])[1].lower()
                    if aext not in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
                        aext = detect_image_ext(av[1]) or ".jpg"
                    if not image_magic_ok(av[1], aext):
                        real = detect_image_ext(av[1])
                        if real is None:
                            c.close()
                            self._err(400, "头像文件格式校验失败")
                            return True
                        aext = real
                    # 文件名带时间戳：URL 随文件名变化，前端缓存自动失效（避免换头像后仍显示旧图）
                    avatar_fname = "{}_avatar_{}{}".format(_ai_uid(), int(time.time()), aext)
                    with open(os.path.join(FILES, avatar_fname), "wb") as f:
                        f.write(av[1])
                bg = files.get("background")
                if bg and bg[1]:
                    bext = os.path.splitext(bg[0])[1].lower()
                    if bext not in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
                        bext = detect_image_ext(bg[1]) or ".jpg"
                    if not image_magic_ok(bg[1], bext):
                        real = detect_image_ext(bg[1])
                        if real is None:
                            c.close()
                            self._err(400, "背景图文件格式校验失败")
                            return True
                        bext = real
                    bg_fname = "{}_bg_{}{}".format(_ai_uid(), int(time.time()), bext)
                    with open(os.path.join(FILES, bg_fname), "wb") as f:
                        f.write(bg[1])
                sets = ["name=?", "signature=?"]
                vals = [name, signature]
                if avatar_fname:
                    sets.append("avatar=?")
                    vals.append(avatar_fname)
                if bg_fname:
                    sets.append("background_image=?")
                    vals.append(bg_fname)
                vals.append(_ai_uid())
                c.execute("UPDATE users SET {} WHERE id=?".format(",".join(sets)), vals)
                c.commit()
            finally:
                c.close()
            p = _ai_profile()
            self._json(200, {"ok": True, **p}); return True
        if u.path == "/api/admin/recommend-config":
            if self.headers.get("X-Admin-Token", "").strip() != ADMIN_TOKEN:
                self._err(401, "unauthorized"); return True
            try:
                body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 16 * 1024)).decode("utf-8") or "{}")
            except Exception:
                self._err(400, "bad json"); return True
            allowed = {"rec_w_behavior", "rec_w_genre", "rec_w_artist", "rec_w_scene", "rec_w_hot",
                       "rec_w_fresh", "rec_w_explore", "rec_explore_pool", "rec_scene_boost",
                       "rec_noise", "rec_max_per_artist"}
            c2 = db()
            try:
                for k, v in body.items():
                    if k not in allowed:
                        continue
                    if isinstance(v, str) and not v.strip():
                        c2.execute("DELETE FROM ai_comment_cfg WHERE key=?", (k,))
                    elif isinstance(v, (int, float)) or (isinstance(v, str) and v.strip()):
                        c2.execute("INSERT OR REPLACE INTO ai_comment_cfg(key,value) VALUES(?,?)", (k, str(v)))
                c2.commit()
            finally:
                c2.close()
            self._json(200, {"ok": True}); return True
        if u.path == "/api/admin/site-config":
            if self.headers.get("X-Admin-Token", "").strip() != ADMIN_TOKEN:
                self._err(401, "unauthorized"); return True
            try:
                raw = self.rfile.read(min(int(self.headers.get("Content-Length",0)), 8 * 1024))
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._err(400, "bad json"); return True
            cfg = _write_site_config(data)
            self._json(200, {"ok": True, "config": cfg})
            return True
        if u.path == "/api/admin/announcement":
            if self.headers.get("X-Admin-Token", "").strip() != ADMIN_TOKEN:
                self._err(401, "unauthorized"); return True
            try:
                raw = self.rfile.read(min(int(self.headers.get("Content-Length",0)), 8 * 1024))
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._err(400, "bad json"); return True
            title = (data.get("title") or "").strip()
            content = (data.get("content") or "").strip()
            if not title or not content:
                self._err(400, "title and content required"); return True
            c = db()
            aid = secrets.token_hex(6)
            show_once = 1 if str(data.get("showOnce") or "0") in ("1", "true", "yes") else 0
            atype = data.get("type") or "announcement"
            force_update = 1 if str(data.get("forceUpdate") or "0") in ("1", "true", "yes") else 0
            download_url = data.get("downloadUrl") or ""
            download_url_ios = data.get("downloadUrlIos") or ""
            download_url_android = data.get("downloadUrlAndroid") or ""
            version_ios = data.get("versionIos") or ""
            version_android = data.get("versionAndroid") or ""
            c.execute("INSERT INTO announcements(id,title,content,version,show_once,type,force_update,"
                      "download_url,download_url_ios,download_url_android,version_ios,version_android,active,created)"
                      " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,?)",
                      (aid, title, content, data.get("version") or "", show_once, atype, force_update,
                       download_url, download_url_ios, download_url_android, version_ios, version_android,
                       time.time()))
            c.commit()
            c.close()
            self._json(200, {"ok": True, "id": aid}); return True
        if u.path == "/api/admin/announcement/update":
            if self.headers.get("X-Admin-Token", "").strip() != _admin_token():
                self._err(401, "unauthorized"); return True
            try:
                raw = self.rfile.read(min(int(self.headers.get("Content-Length",0)), 8 * 1024))
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._err(400, "bad json"); return True
            aid = (data.get("id") or "").strip()
            if not aid:
                self._err(400, "id required"); return True
            c = db()
            try:
                cur = c.execute("SELECT id FROM announcements WHERE id=?", (aid,)).fetchone()
                if not cur:
                    c.close(); self._err(404, "not found"); return True
                sets, vals = [], []
                for key, col in [("title", "title"), ("content", "content"), ("version", "version"),
                                 ("downloadUrl", "download_url"), ("downloadUrlIos", "download_url_ios"),
                                 ("downloadUrlAndroid", "download_url_android"),
                                 ("versionIos", "version_ios"), ("versionAndroid", "version_android")]:
                    if key in data:
                        sets.append("%s=?" % col); vals.append(str(data[key] or ""))
                for key, col in [("forceUpdate", "force_update"), ("showOnce", "show_once"), ("active", "active")]:
                    if key in data:
                        sets.append("%s=?" % col); vals.append(1 if str(data[key] or "0") in ("1", "true", "yes") else 0)
                if "type" in data:
                    sets.append("type=?"); vals.append(str(data["type"] or ""))
                if sets:
                    vals.append(aid)
                    c.execute("UPDATE announcements SET %s WHERE id=?" % ",".join(sets), vals)
                    c.commit()
                c.close()
                self._json(200, {"ok": True, "id": aid})
            except Exception as e:
                try: c.close()
                except Exception: pass
                self._internal_error(e)
            return True
        if u.path == "/api/admin/upload-package":
            if self.headers.get("X-Admin-Token", "").strip() != _admin_token():
                self._err(401, "unauthorized"); return True
            try:
                if length <= 0:
                    self._err(400, "empty body"); return True
                if length > MAX_PACKAGE + 64 * 1024:
                    self._err(413, "too large"); return True
                fields, files = self._parse_multipart(length)
                if fields is None:
                    self._err(400, "need multipart"); return True
                platform = (fields.get("platform") or "").strip()
                if platform not in ("android", "ios"):
                    self._err(400, "platform must be android|ios"); return True
                pkg = files.get("file")
                if not pkg or not pkg[0]:
                    self._err(400, "no file"); return True
                fname, fdata = pkg
                cext = os.path.splitext(fname)[1].lower()
                allow_ext = {".apk": "android", ".ipa": "ios"}
                if cext not in allow_ext or allow_ext[cext] != platform:
                    self._err(400, "platform/extension mismatch (android→.apk, ios→.ipa)"); return True
                if not fdata.startswith(b"PK\x03\x04"):
                    self._err(400, "安装包格式校验失败（APK/IPA 均为 zip 容器）"); return True
                stored = "package_" + uuid.uuid4().hex + cext
                with open(os.path.join(FILES, stored), "wb") as f:
                    f.write(fdata)
                self._json(200, {"ok": True, "url": "/music/api/package/" + stored, "size": len(fdata)})
            except Exception as e:
                self._internal_error(e)
            return True
        if u.path == "/api/admin/update":
            if self.headers.get("X-Admin-Token", "").strip() != ADMIN_TOKEN:
                self._err(401, "unauthorized"); return True
            try:
                raw = self.rfile.read(min(int(self.headers.get("Content-Length",0)), 8 * 1024 * 1024))
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._err(400, "bad json"); return True
            typ = data.get("type") or ""
            rid = data.get("id") or ""
            fields = data.get("fields") or {}
            if typ not in ("track","collected","user") or not rid:
                self._err(400, "bad params"); return True
            c = db()
            try:
                def _save_img(tid, val, kind):
                    """保存图片到 files/{tid}_{kind}_{ts}{ext}，返回本地文件名；失败返回 None"""
                    if not val: return None
                    val = str(val).strip()
                    if not val: return None
                    # base64 data URI
                    if val.startswith("data:image/"):
                        try:
                            hdr, b64 = val.split(",", 1)
                            ext = ""
                            m = re.search(r"image/(\w+)", hdr)
                            if m: ext = "." + m.group(1).lower()
                            if ext not in (".jpg", ".jpeg", ".png", ".webp", ".heic"): ext = ".jpg"
                            data = base64.b64decode(b64)
                            if len(data) > 3 * 1024 * 1024: return None
                            if not image_magic_ok(data, ext): return None
                            fn = "%s_%s_%d%s" % (tid, kind, int(time.time()), ext)
                            with open(os.path.join(FILES, fn), "wb") as f:
                                f.write(data)
                            return fn
                        except Exception:
                            return None
                    # http 链接：下载到本地
                    if val.startswith("http"):
                        try:
                            req = urllib.request.Request(val, headers={"User-Agent": "Mozilla/5.0"})
                            with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx()) as r:
                                img = r.read()
                            if not img or len(img) < 500: return None
                            ext = ".jpg"
                            if not image_magic_ok(img, ".jpg"):
                                if image_magic_ok(img, ".png"): ext = ".png"
                                elif image_magic_ok(img, ".webp"): ext = ".webp"
                            fn = "%s_%s_%d%s" % (tid, kind, int(time.time()), ext)
                            with open(os.path.join(FILES, fn), "wb") as f:
                                f.write(img)
                            return fn
                        except Exception:
                            return None
                    return None

                if typ == "track":
                    sets, vals = [], []
                    if "title" in fields: sets.append("title=?"); vals.append(str(fields["title"]).strip())
                    if "artist" in fields: sets.append("artist=?"); vals.append(str(fields["artist"]).strip())
                    if "user_id" in fields: sets.append("user_id=?"); vals.append(str(fields["user_id"]).strip())
                    if "cover" in fields:
                        cf = _save_img(rid, fields.get("cover"), "cover")
                        if cf is not None:
                            sets.append("cover=?"); vals.append(cf)
                    if sets:
                        vals.append(rid)
                        c.execute("UPDATE tracks SET "+",".join(sets)+" WHERE id=?", vals)
                elif typ == "collected":
                    sets, vals = [], []
                    if "title" in fields: sets.append("title=?"); vals.append(str(fields["title"]).strip())
                    if "artist" in fields: sets.append("artist=?"); vals.append(str(fields["artist"]).strip())
                    if "cover" in fields:
                        cf = _save_img(rid, fields.get("cover"), "cover")
                        if cf is not None:
                            sets.append("cover=?"); vals.append(cf)
                    if sets:
                        vals.append(rid)
                        c.execute("UPDATE collected_tracks SET "+",".join(sets)+" WHERE id=?", vals)
                elif typ == "user":
                    sets, vals = [], []
                    field_map = {"name":"name","account":"account",
                                 "gender":"gender","age":"age","signature":"signature"}
                    for k, col in field_map.items():
                        if k in fields:
                            sets.append(f"{col}=?")
                            vals.append(str(fields[k]).strip())
                    if "avatar" in fields:
                        af = _save_img(rid, fields.get("avatar"), "avatar")
                        if af is not None:
                            sets.append("avatar=?"); vals.append(af)
                    if "password" in fields and str(fields["password"]).strip():
                        sets.append("password_hash=?")
                        vals.append(hash_password(str(fields["password"]).strip()))
                    if sets:
                        vals.append(rid)
                        c.execute("UPDATE users SET "+",".join(sets)+" WHERE id=?", vals)
                c.commit()
            finally:
                c.close()
            self._json(200, {"ok": True}); return True
        if u.path == "/api/admin/offline":
            if self.headers.get("X-Admin-Token", "").strip() != ADMIN_TOKEN:
                self._err(401, "unauthorized"); return True
            try:
                raw = self.rfile.read(min(int(self.headers.get("Content-Length",0)), 65536))
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._err(400, "bad json"); return True
            typ = data.get("type") or ""
            rid = data.get("id") or ""
            reason = str(data.get("reason") or "").strip()
            action = data.get("action") or "off"  # off / on
            if typ not in ("track","collected") or not rid:
                self._err(400, "bad params"); return True
            tbl = {"track":"tracks","collected":"collected_tracks"}.get(typ)
            if not tbl:
                self._err(400, "bad type"); return True
            c = db()
            try:
                if action == "off":
                    c.execute(f"UPDATE {tbl} SET offline=1, offline_reason=? WHERE id=?", (reason, rid))
                else:
                    c.execute(f"UPDATE {tbl} SET offline=0, offline_reason='' WHERE id=?", (rid,))
                c.commit()
                # 实时数据事件：歌曲下架/恢复 → 全局列表更新（广播在线用户）
                ws_broadcast({"kind": "event", "event": "track_list", "reason": "offline"})
            finally:
                c.close()
            self._json(200, {"ok": True}); return True
        if u.path == "/api/admin/ban":
            if self.headers.get("X-Admin-Token", "").strip() != ADMIN_TOKEN:
                self._err(401, "unauthorized"); return True
            try:
                raw = self.rfile.read(min(int(self.headers.get("Content-Length",0)), 65536))
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._err(400, "bad json"); return True
            rid = data.get("id") or ""
            reason = str(data.get("reason") or "").strip()
            action = data.get("action") or "ban"  # ban / unban
            if not rid:
                self._err(400, "bad params"); return True
            c = db()
            try:
                if action == "ban":
                    c.execute("UPDATE users SET banned=1, ban_reason=? WHERE id=?", (reason, rid))
                else:
                    c.execute("UPDATE users SET banned=0, ban_reason='' WHERE id=?", (rid,))
                c.commit()
            finally:
                c.close()
            self._json(200, {"ok": True}); return True
        if u.path == "/api/admin/change_pwd":
            if self.headers.get("X-Admin-Token", "").strip() != _admin_token():
                self._err(401, "unauthorized"); return True
            try:
                raw = self.rfile.read(min(int(self.headers.get("Content-Length",0)), 65536))
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._err(400, "bad json"); return True
            old = str(data.get("old") or "").strip()
            pwd = str(data.get("pwd") or "").strip()
            if old != _admin_token():
                self._err(400, "当前密码不正确"); return True
            if len(pwd) < 6 or len(pwd) > 64:
                self._err(400, "新密码长度需在 6-64 位之间"); return True
            try:
                with open(ADMIN_PWD_FILE, "w", encoding="utf-8") as _f:
                    _f.write(pwd)
            except Exception:
                self._err(500, "密码保存失败"); return True
            self._json(200, {"ok": True}); return True
        if u.path.startswith("/api/admin/del/"):
            if self.headers.get("X-Admin-Token", "").strip() != ADMIN_TOKEN:
                self._err(401, "unauthorized"); return True
            parts = u.path.split("/")
            # /api/admin/del/<type>/<id>
            typ = parts[3] if len(parts) > 3 else ""
            tid = parts[4] if len(parts) > 4 else ""
            c = db()
            try:
                if typ == "track":
                    row = c.execute("SELECT fname FROM tracks WHERE id=?", (tid,)).fetchone()
                    c.execute("DELETE FROM tracks WHERE id=?", (tid,))
                    c.execute("DELETE FROM likes WHERE track_id=?", (tid,))
                    c.execute("DELETE FROM comments WHERE track_id=?", (tid,))
                    c.commit()
                    if row and row[0]:
                        fp = safe_file_path(row[0])
                        if fp and os.path.exists(fp):
                            try: os.remove(fp)
                            except Exception: pass
                    # 实时数据事件：删除歌曲 → 全局列表更新（广播在线用户）
                    ws_broadcast({"kind": "event", "event": "track_list", "reason": "delete"})
                elif typ == "collected":
                    row = c.execute("SELECT play_url FROM collected_tracks WHERE id=?", (tid,)).fetchone()
                    c.execute("DELETE FROM collected_tracks WHERE id=?", (tid,))
                    c.commit()
                    if row and row[0] and not str(row[0]).startswith("http"):
                        fp = safe_file_path(row[0])
                        if fp and os.path.exists(fp):
                            try: os.remove(fp)
                            except Exception: pass
                    # 实时数据事件：删除采集歌曲 → 全局列表更新
                    ws_broadcast({"kind": "event", "event": "track_list", "reason": "delete"})
                elif typ == "user":
                    c.execute("DELETE FROM users WHERE id=?", (tid,))
                    c.commit()
                elif typ == "comment":
                    c.execute("DELETE FROM comments WHERE id=?", (tid,))
                    c.commit()
                else:
                    self._err(400, "bad type"); return True
            finally:
                c.close()
            self._json(200, {"ok": True}); return True
        if u.path == "/api/admin/official-message":
            if self.headers.get("X-Admin-Token", "").strip() != _admin_token():
                self._err(401, "unauthorized"); return True
            try:
                raw = self.rfile.read(min(int(self.headers.get("Content-Length",0)), 16*1024))
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._err(400, "bad json"); return True
            msg_content = (data.get("content") or "").strip()
            if not msg_content:
                self._err(400, "content required"); return True
            # 定向发布：receiverUserId 非空=单独发给该用户；空=全广播（2026-10-19 会员/官方通知定向）
            receiver = (data.get("receiverUserId") or "").strip()
            import time as _t
            msg_id = secrets.token_hex(8)
            c = db()
            try:
                c.execute("INSERT INTO official_messages(id, content, created, sender_name, receiver_user_id) VALUES(?,?,?,?,?)",
                          (msg_id, msg_content, _t.time(), "流萤音乐官方", receiver))
                c.commit()
            finally:
                c.close()
            if receiver:
                # 定向：只推给该用户（在线则实时，离线打开 App 从官方消息列表可见）
                try:
                    ws_push(receiver, {
                        "kind": "official_message",
                        "message": {"id": msg_id, "content": msg_content,
                                    "sender": "流萤音乐官方", "created": _t.time()}})
                except Exception as e:
                    log.info("official directed push err: %s", e)
            else:
                # 公共广播：官方通知面向全体用户，只写一条公共消息 + ws_broadcast 一次推送给所有在线连接。
                # 不逐用户生成 notifications、不逐用户推送、不推极光（离线用户打开 App 后从官方消息列表看到）。
                try:
                    ws_broadcast({
                        "kind": "official_message",
                        "message": {"id": msg_id, "content": msg_content,
                                    "sender": "流萤音乐官方", "created": _t.time()}})
                except Exception as e:
                    log.info("official broadcast err: %s", e)
            self._json(200, {"ok": True, "id": msg_id})
            return True
        if u.path.startswith("/api/admin/splash-ads/"):
            # 更新已有广告配置（跳转链接/标题/启停），不替换图片
            if self.headers.get("X-Admin-Token", "").strip() != _admin_token():
                self._err(401, "unauthorized"); return True
            sid = u.path[len("/api/admin/splash-ads/"):]
            fields, files = self._parse_multipart(length)
            if fields is None:
                self._err(400, "need multipart"); return True
            new_link = str(fields.get("link") or "")[:300]
            new_title = str(fields.get("title") or "")[:60]
            active_raw = fields.get("active")
            active = None
            if active_raw is not None:
                active = 1 if str(active_raw).strip().lower() in ("1", "true", "on") else 0
            c = db()
            try:
                row = c.execute("SELECT id FROM splash_ads WHERE id=?", (sid,)).fetchone()
                if not row:
                    c.close()
                    self._err(404, "not found"); return True
                sets, params = ["link=?", "title=?"], [new_link, new_title]
                if active is not None:
                    sets.append("active=?")
                    params.append(active)
                params.append(sid)
                c.execute("UPDATE splash_ads SET " + ",".join(sets) + " WHERE id=?", params)
                c.commit()
            finally:
                c.close()
            self._json(200, {"ok": True}); return True
        if u.path == "/api/admin/splash-ads":
            if self.headers.get("X-Admin-Token", "").strip() != _admin_token():
                self._err(401, "unauthorized")
                return True
            try:
                if length <= 0:
                    self._err(400, "empty body")
                    return True
                if length > MAX_COMMENT_IMAGE + 1024 * 1024:
                    self._err(413, "too large")
                    return True
                fields, files = self._parse_multipart(length)
                if fields is None:
                    self._err(400, "need multipart")
                    return True
                img = files.get("image")
                if not img or not img[0]:
                    self._err(400, "no image")
                    return True
                fname, fdata = img
                if len(fdata) > MAX_COMMENT_IMAGE:
                    self._err(413, "too large")
                    return True
                cext = os.path.splitext(fname)[1].lower()
                if cext not in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
                    self._err(400, "unsupported image type")
                    return True
                if not image_magic_ok(fdata, cext):
                    # 扩展名与内容不一致（如 iPhone HEIC 伪装成 .jpg）时按魔数自动识别真实格式
                    real = detect_image_ext(fdata)
                    if real is None:
                        self._err(400, "图片文件格式校验失败")
                        return True
                    cext = real
                stored = "splash_" + uuid.uuid4().hex + cext
                with open(os.path.join(FILES, stored), "wb") as f:
                    f.write(fdata)
                c = db()
                try:
                    sort = c.execute("SELECT COALESCE(MAX(sort),0)+1 FROM splash_ads").fetchone()[0]
                    c.execute("INSERT INTO splash_ads(name,title,link,sort,active,created) VALUES(?,?,?,?,1,?)",
                              (stored, str(fields.get("title") or "")[:60],
                               str(fields.get("link") or "")[:300], sort, time.time()))
                    c.commit()
                    rid = c.execute("SELECT id FROM splash_ads WHERE name=?", (stored,)).fetchone()[0]
                finally:
                    c.close()
                self._json(200, {"id": rid, "name": stored, "img": "/api/splash/" + stored})
            except Exception as e:
                self._internal_error(e)
            return True
        if u.path == "/api/admin/banner":
            if self.headers.get("X-Admin-Token", "").strip() != _admin_token():
                self._err(401, "unauthorized")
                return True
            try:
                if length <= 0:
                    self._err(400, "empty body")
                    return True
                if length > MAX_COMMENT_IMAGE + 1024 * 1024:
                    self._err(413, "too large")
                    return True
                fields, files = self._parse_multipart(length)
                if fields is None:
                    self._err(400, "need multipart")
                    return True
                img = files.get("image")
                if not img or not img[0]:
                    self._err(400, "no image")
                    return True
                fname, fdata = img
                if len(fdata) > MAX_COMMENT_IMAGE:
                    self._err(413, "too large")
                    return True
                cext = os.path.splitext(fname)[1].lower()
                if cext not in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
                    self._err(400, "unsupported image type")
                    return True
                if not image_magic_ok(fdata, cext):
                    real = detect_image_ext(fdata)
                    if real is None:
                        self._err(400, "图片文件格式校验失败")
                        return True
                    cext = real
                stored = "banner_" + uuid.uuid4().hex + cext
                with open(os.path.join(FILES, stored), "wb") as f:
                    f.write(fdata)
                self._json(200, {"name": stored})
            except Exception as e:
                self._internal_error(e)
            return True
        # ===== 认证/头衔/会员管理（2026-10-19 会员体系）=====
        if u.path == "/api/admin/verify-category":
            if not self._admin_auth(): self._err(401, "unauthorized"); return True
            try:
                raw = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 16 * 1024))
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._err(400, "bad json"); return True
            level1 = (data.get("level1") or "").strip()
            level2 = (data.get("level2") or "").strip()
            if not level1:
                self._err(400, "level1 required"); return True
            c = db()
            c.execute("INSERT INTO verification_categories(id, level1, level2, created) VALUES(?,?,?,?)",
                      (uuid.uuid4().hex, level1[:20], level2[:20], time.time()))
            c.commit()
            c.close()
            self._json(200, {"ok": True}); return True
        if u.path == "/api/admin/verify-category/delete":
            if not self._admin_auth(): self._err(401, "unauthorized"); return True
            try:
                raw = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 16 * 1024))
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._err(400, "bad json"); return True
            cid = (data.get("id") or "").strip()
            if not cid:
                self._err(400, "id required"); return True
            c = db()
            c.execute("DELETE FROM verification_categories WHERE id=?", (cid,))
            c.commit()
            c.close()
            self._json(200, {"ok": True}); return True
        if u.path == "/api/admin/verify-application":
            if not self._admin_auth(): self._err(401, "unauthorized"); return True
            try:
                raw = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 16 * 1024))
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._err(400, "bad json"); return True
            app_id = (data.get("id") or "").strip()
            action = (data.get("action") or "").strip()
            note = (data.get("note") or "").strip()[:200]
            if not app_id or action not in ("approve", "reject"):
                self._err(400, "id and action(approve/reject) required"); return True
            if action == "reject" and not note:
                self._err(400, "note required when reject"); return True
            c = db()
            app = c.execute("SELECT user_id, level1, label FROM verification_applications WHERE id=?", (app_id,)).fetchone()
            if not app:
                c.close()
                self._err(404, "application not found"); return True
            if action == "approve":
                # 通过：写用户认证类型 + 自定义标签（蓝 V 固定 logo 由前端渲染）
                c.execute("UPDATE users SET verified=?, verified_label=? WHERE id=?",
                          (app[1], app[2] or "", app[0]))
                # 审核通过通知（官方通知聊天页面定向显示）
                _n_content = "恭喜，你的「%s」认证申请已通过审核%s" % (
                    app[1] or "", ("，审核意见：%s" % note) if note else "")
                try:
                    nid = uuid.uuid4().hex
                    _official_send(c, app[0], _n_content)
                    c.execute("""INSERT INTO notifications(id, user_id, type, title, content, from_user_id, from_user_name, track_id, track_title, is_read, created)
                                 VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                              (nid, app[0], "official", "认证通过",
                               _n_content,
                               "", "流萤音乐官方", "", "", 0, time.time()))
                    ws_push(app[0], {"kind": "notification",
                                     "notification": {"id": nid, "type": "official", "title": "认证通过",
                                                      "content": _n_content,
                                                      "fromUserId": "", "fromUser": "流萤音乐官方",
                                                      "trackId": "", "trackTitle": "", "isRead": 0,
                                                      "created": time.time()},
                                     "unread": _notif_unread(c, app[0])})
                except Exception as _e:
                    log.info("verify approve notif err: %s", _e)
            else:
                # 拒绝：写审核意见 + 官方通知聊天页面定向显示原因
                _n_content = "很遗憾，你的「%s」认证申请未通过审核。原因：%s" % (app[1] or "", note)
                try:
                    nid = uuid.uuid4().hex
                    _official_send(c, app[0], _n_content)
                    c.execute("""INSERT INTO notifications(id, user_id, type, title, content, from_user_id, from_user_name, track_id, track_title, is_read, created)
                                 VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                              (nid, app[0], "official", "认证未通过",
                               _n_content,
                               "", "流萤音乐官方", "", "", 0, time.time()))
                    ws_push(app[0], {"kind": "notification",
                                     "notification": {"id": nid, "type": "official", "title": "认证未通过",
                                                      "content": _n_content,
                                                      "fromUserId": "", "fromUser": "流萤音乐官方",
                                                      "trackId": "", "trackTitle": "", "isRead": 0,
                                                      "created": time.time()},
                                     "unread": _notif_unread(c, app[0])})
                except Exception as _e:
                    log.info("verify reject notif err: %s", _e)
            c.execute("UPDATE verification_applications SET status=?, reviewed_at=?, review_note=? WHERE id=?",
                      (action, time.time(), note, app_id))
            c.commit()
            c.close()
            self._json(200, {"ok": True}); return True
        if u.path == "/api/admin/user-title":
            if not self._admin_auth(): self._err(401, "unauthorized"); return True
            try:
                raw = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 16 * 1024))
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._err(400, "bad json"); return True
            uid = (data.get("userId") or "").strip()
            title = (data.get("title") or "").strip()[:20]
            if not uid:
                self._err(400, "userId required"); return True
            c = db()
            old_title = c.execute("SELECT title FROM users WHERE id=?", (uid,)).fetchone()
            old_title = (old_title[0] if old_title else "") or ""
            c.execute("UPDATE users SET title=? WHERE id=?", (title, uid))
            # 赠送头衔通知（官方通知聊天页面定向显示）
            try:
                if title:
                    _om_content = "官方赠送了你「%s」头衔，该头衔将显示在你的评论、主页与个人资料中。" % title
                else:
                    _om_content = "你的头衔「%s」已被官方移除。" % old_title if old_title else ""
                if _om_content:
                    nid = uuid.uuid4().hex
                    _official_send(c, uid, _om_content)
                    c.execute("""INSERT INTO notifications(id, user_id, type, title, content, from_user_id, from_user_name, track_id, track_title, is_read, created)
                                 VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                              (nid, uid, "official", "官方头衔",
                               _om_content,
                               "", "流萤音乐官方", "", "", 0, time.time()))
                    ws_push(uid, {"kind": "notification",
                                  "notification": {"id": nid, "type": "official", "title": "官方头衔",
                                                   "content": _om_content,
                                                   "fromUserId": "", "fromUser": "流萤音乐官方",
                                                   "trackId": "", "trackTitle": "", "isRead": 0,
                                                   "created": time.time()},
                                  "unread": _notif_unread(c, uid)})
            except Exception as _e:
                log.info("title notif err: %s", _e)
            c.commit()
            c.close()
            self._json(200, {"ok": True}); return True
        if u.path == "/api/admin/member":
            if not self._admin_auth(): self._err(401, "unauthorized"); return True
            try:
                raw = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 16 * 1024))
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._err(400, "bad json"); return True
            uid = (data.get("userId") or "").strip()
            plan = (data.get("plan") or "").strip()
            days = max(1, min(3650, int(data.get("days") or 30)))
            if not uid or plan not in ("vip", "svip"):
                self._err(400, "userId and plan(vip/svip) required"); return True
            c = db()
            row = c.execute("SELECT member_expire, svip_expire FROM users WHERE id=?", (uid,)).fetchone()
            if not row:
                c.close()
                self._err(404, "user not found"); return True
            now = time.time()
            if plan == "vip":
                base = max(row[0] or 0, now)
                new_expire = base + days * 86400
                c.execute("UPDATE users SET member_expire=?, member='vip' WHERE id=?", (new_expire, uid))
            else:
                base = max(row[1] or 0, now)
                new_expire = base + days * 86400
                c.execute("UPDATE users SET svip_expire=?, member='svip' WHERE id=?", (new_expire, uid))
            # 会员开通官方通知：后台可全广播，这里默认定向发给开通用户本人
            try:
                uname = c.execute("SELECT name FROM users WHERE id=?", (uid,)).fetchone()
                uname = (uname[0] if uname else "") or "用户"
                nid = uuid.uuid4().hex
                _om_content = _member_notif_text(plan, days, new_expire, source="gift")
                _official_send(c, uid, _om_content)
                c.execute("""INSERT INTO notifications(id, user_id, type, title, content, from_user_id, from_user_name, track_id, track_title, is_read, created)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                          (nid, uid, "official", "会员开通成功",
                           _om_content,
                           "", "流萤音乐官方", "", "", 0, now))
                ws_push(uid, {"kind": "notification",
                              "notification": {"id": nid, "type": "official", "title": "会员开通成功",
                                               "content": _om_content,
                                               "fromUserId": "", "fromUser": "流萤音乐官方",
                                               "trackId": "", "trackTitle": "", "isRead": 0, "created": now},
                              "unread": _notif_unread(c, uid)})
            except Exception as _e:
                log.info("member notif err: %s", _e)
            c.commit()
            c.close()
            self._json(200, {"ok": True, "expire": new_expire, "plan": plan}); return True
        return False
