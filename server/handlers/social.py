# -*- coding: utf-8 -*-
"""P2-7：social 域路由（mixin，由 server/handler.py 的 H 类继承）。
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


class SocialMixin:

    def _h_delete_social(self, u):
        length = int(self.headers.get("Content-Length", 0))
        if u.path == "/api/search/history":
            c = db()
            uid = resolve_token(c, self.headers.get("Authorization", ""))
            if not uid:
                c.close()
                self._err(401, "unauthorized"); return True
            c.execute("DELETE FROM search_history WHERE user_id=?", (uid,))
            c.commit()
            c.close()
            self._json(200, {"ok": True}); return True
        if u.path.startswith("/api/playlists/") and "/tracks/" in u.path:
            parts = u.path.split("/")
            pid = parts[3]
            tid = parts[5]
            c = db()
            uid = resolve_token(c, self.headers.get("Authorization", ""))
            if not uid:
                c.close()
                self._err(401, "unauthorized"); return True
            # 验证歌单属于当前用户
            pl = c.execute("SELECT id, cover FROM playlists WHERE id=? AND user_id=?", (pid, uid)).fetchone()
            if not pl:
                c.close()
                self._err(404, "playlist not found"); return True
            old_cover = pl[1] or ""
            c.execute("DELETE FROM playlist_tracks WHERE playlist_id=? AND track_id=?", (pid, tid))
            c.execute("UPDATE playlists SET track_count=MAX(0, track_count-1) WHERE id=?", (pid,))
            # 如果删除的是当前封面对应的歌曲，更新封面为最新一首的封面
            if old_cover:
                newest = c.execute("SELECT track_cover FROM playlist_tracks WHERE playlist_id=? AND track_cover != '' ORDER BY created DESC LIMIT 1", (pid,)).fetchone()
                new_cover = newest[0] if newest else ""
                c.execute("UPDATE playlists SET cover=? WHERE id=?", (new_cover, pid))
            c.commit()
            c.close()
            self._json(200, {"ok": True}); return True
        if u.path.startswith("/api/notifications/"):
            nid = u.path.rsplit("/", 1)[-1]
            c = db()
            uid = resolve_token(c, self.headers.get("Authorization", ""))
            if not uid:
                c.close()
                self._err(401, "unauthorized"); return True
            c.execute("UPDATE notifications SET is_deleted=1 WHERE id=? AND user_id=?", (nid, uid))
            c.commit()
            c.close()
            self._json(200, {"ok": True}); return True
        return False

    def _h_get_social(self, u):
        if u.path == "/api/my-stats":
            c = db()
            uid = resolve_token(c, self.headers.get("Authorization", ""))
            if not uid:
                c.close()
                self._json(200, {"totalSeconds": 0, "trackCount": 0})
                return True
            row = c.execute("SELECT COALESCE(SUM(value),0) FROM behavior_events"
                            " WHERE user_id=? AND event='listen'", (uid,)).fetchone()
            total_sec = int(row[0]) if row else 0
            row2 = c.execute("SELECT COUNT(DISTINCT track_id) FROM behavior_events"
                            " WHERE user_id=? AND event='listen'", (uid,)).fetchone()
            track_cnt = int(row2[0]) if row2 else 0
            c.close()
            self._json(200, {"totalSeconds": total_sec, "trackCount": track_cnt})
            return True
        if u.path.startswith("/api/user/") and u.path.endswith("/profile"):
            target_uid = u.path.split("/")[3]
            c = db()
            # 当前登录用户（判断是否已关注）
            me_uid = resolve_token(c, self.headers.get("Authorization", ""))
            # 用户基本信息（含会员/认证/头衔字段）
            urow = c.execute("SELECT id, account, name, avatar, signature, created, gender, age, is_ai,"
                             " background_image, city, ip_address, title, verified, verified_label,"
                             " member_expire, svip_expire FROM users WHERE id=?",
                             (target_uid,)).fetchone()
            if not urow:
                c.close()
                self._err(404, "user not found"); return True
            # 上传歌曲数
            track_count = c.execute("SELECT COUNT(*) FROM tracks WHERE user_id=? AND offline=0", (target_uid,)).fetchone()[0]
            # 获赞总数
            like_count = c.execute("SELECT COALESCE(SUM(like_count),0) FROM tracks WHERE user_id=?", (target_uid,)).fetchone()[0]
            # 粉丝数（关注我的人数）
            follower_count = c.execute("SELECT COUNT(*) FROM follows WHERE following_id=?", (target_uid,)).fetchone()[0]
            # 关注数（我关注的人数）
            following_count = c.execute("SELECT COUNT(*) FROM follows WHERE follower_id=?", (target_uid,)).fetchone()[0]
            # 互关数（主页主人 target 的互关人数：target 关注了 X 且 X 也关注 target）
            # 注意：互关数是主页主人的属性，与查看者无关（此前误用查看者 me_uid 导致别人主页互关恒为 0）
            mutual_count = c.execute("""SELECT COUNT(*) FROM follows f1
                                        INNER JOIN follows f2 ON f1.follower_id=f2.following_id AND f1.following_id=f2.follower_id
                                        WHERE f1.follower_id=?""", (target_uid,)).fetchone()[0]
            # 当前用户是否已关注该用户
            is_following = False
            if me_uid and me_uid != target_uid:
                exist = c.execute("SELECT follower_id FROM follows WHERE follower_id=? AND following_id=?", (me_uid, target_uid)).fetchone()
                is_following = bool(exist)
            # 大致地址（城市）：后台异步定位（P1：不再同步阻塞请求线程）。
            # 首次查看返回空 city，daemon 线程定位成功后落库，下次请求命中。
            city = urow[10] or ""
            if not city and urow[11]:
                _geo_city_async(target_uid, urow[11])
            # 在线状态（WebSocket 活跃连接快照）
            with WS_LOCK:
                target_online = target_uid in set(WS_CLIENTS.keys())
            c.close()
            self._json(200, {
                "userId": urow[0], "account": urow[1], "name": urow[2],
                "avatar": urow[3] or "", "signature": urow[4] or "",
                "created": urow[5], "gender": urow[6] or "", "age": urow[7] or 0,
                "trackCount": track_count, "likeCount": like_count,
                "followerCount": follower_count, "followingCount": following_count,
                "mutualCount": mutual_count, "isFollowing": is_following,
                "isAi": bool(urow[8]),
                "backgroundImage": urow[9] or "", "city": city,
                "isOnline": target_online,
                "title": urow[12] or "",
                "verified": urow[13] or "",
                "verifiedLabel": urow[14] or "",
                "member": member_level(urow[15], urow[16]),
                "memberExpire": urow[15] or 0,
                "svipExpire": urow[16] or 0,
            })
            return True
        if u.path.startswith("/api/user/") and u.path.endswith("/tracks"):
            target_uid = u.path.split("/")[3]
            c = db()
            rows = c.execute("""SELECT id,title,artist,album,cover,fname,size,dur,created,
                                play_count,like_count,share_count,user_id,lrc
                                FROM tracks WHERE user_id=? AND offline=0
                                ORDER BY created DESC LIMIT 50""", (target_uid,)).fetchall()
            c.close()
            lst = [{"id": r[0], "title": r[1], "artist": r[2] or "", "album": r[3] or "",
                    "cover": r[4] or "", "fname": r[5] or "",
                    "size": int(r[6]) if r[6] is not None else 0,
                    "dur": r[7], "created": r[8],
                    "playCount": r[9] or 0, "likeCount": r[10] or 0,
                    "shareCount": r[11] or 0, "userId": r[12] or "",
                    "hasLyric": bool(r[13])} for r in rows]
            self._json(200, {"list": lst})
            return True
        if u.path == "/api/my-tracks":
            c = db()
            uid = resolve_token(c, self.headers.get("Authorization", ""))
            if not uid:
                c.close()
                self._json(200, {"list": [], "total": 0, "hasMore": False})
                return True
            qs = urllib.parse.parse_qs(u.query)
            page, page_size = parse_page(qs)
            sel = """SELECT t.id,t.title,t.artist,t.album,t.cover,t.fname,t.size,t.dur,t.created,
                     t.play_count,t.like_count,t.share_count,t.user_id,t.lrc,
                     COALESCE(u.name,''),
                     (SELECT COUNT(*) FROM comments cm WHERE cm.track_id=t.id),
                     MAX(CASE WHEN l.user_id IS NOT NULL THEN 1 ELSE 0 END)
                     FROM tracks t
                     LEFT JOIN users u ON u.id=t.user_id
                     LEFT JOIN likes l ON l.track_id=t.id AND l.user_id=?
                     WHERE t.user_id=? AND t.offline=0
                     GROUP BY t.id ORDER BY t.created DESC"""
            args = [uid, uid]
            total = c.execute("SELECT COUNT(*) FROM tracks WHERE user_id=? AND offline=0",
                              (uid,)).fetchone()[0]
            if page is None:
                rows = c.execute(sel, args).fetchall()
            else:
                rows = c.execute(sel + " LIMIT ? OFFSET ?",
                                 args + [page_size, (page - 1) * page_size]).fetchall()
            c.close()
            lst = [{"id": r[0], "title": r[1], "artist": r[2], "album": r[3] or "",
                    "cover": r[4] or "", "fname": r[5] or "",
                    "size": int(r[6]) if r[6] is not None else 0,
                    "dur": r[7], "durText": _fmt_dur(r[7]), "created": r[8],
                    "playCount": r[9] or 0, "likeCount": r[10] or 0,
                    "shareCount": r[11] or 0, "userId": r[12] or "",
                    "userName": r[14] or "", "hasLyric": bool(r[13]),
                    "commentCount": r[15] or 0, "isLiked": bool(r[16])} for r in rows]
            self._json(200, {"list": lst, "total": total,
                             "hasMore": bool(page is not None and page * page_size < total)})
            return True
        if u.path == "/api/my-likes":
            c = db()
            uid = resolve_token(c, self.headers.get("Authorization", ""))
            if not uid:
                c.close()
                self._json(200, {"list": [], "total": 0, "hasMore": False})
                return True
            qs = urllib.parse.parse_qs(u.query)
            page, page_size = parse_page(qs)
            # 归属账号昵称（采集歌曲统一展示用，与 /api/tracks 口径一致）
            uname = ""
            urow = c.execute(
                "SELECT name FROM users WHERE id=(SELECT user_id FROM collected_tracks"
                " WHERE user_id!='' LIMIT 1)").fetchone()
            if urow:
                uname = urow[0]
            # 上传歌曲点赞子查询（is_liked 恒为 1：行本身来自当前用户的点赞记录）
            sub1 = """SELECT t.id,t.title,t.artist,t.album,t.cover,t.fname,t.size,t.dur,t.created,
                      t.play_count,t.like_count,t.share_count,t.user_id,t.lrc,
                      COALESCE(u.name,''),
                      (SELECT COUNT(*) FROM comments cm WHERE cm.track_id=t.id),
                      1 AS is_liked, l.created AS lc
                      FROM likes l JOIN tracks t ON t.id=l.track_id
                      LEFT JOIN users u ON u.id=t.user_id
                      WHERE l.user_id=? AND t.offline=0"""
            # 采集歌曲点赞子查询（cover 返回占位 "1"，字段结构与上传分支对齐）
            sub2 = """SELECT c.id,c.title,c.artist,c.album,'1','',0,c.duration,c.created,
                      c.play_count,c.like_count,c.share_count,c.user_id,c.lrc,
                      ?, 0, 1, l.created AS lc
                      FROM likes l JOIN collected_tracks c ON c.id=l.track_id
                      WHERE l.user_id=? AND c.offline=0"""
            total1 = c.execute("""SELECT COUNT(*) FROM likes l JOIN tracks t ON t.id=l.track_id
                                  WHERE l.user_id=? AND t.offline=0""", (uid,)).fetchone()[0]
            total2 = c.execute("""SELECT COUNT(*) FROM likes l JOIN collected_tracks c
                                  ON c.id=l.track_id WHERE l.user_id=? AND c.offline=0""", (uid,)).fetchone()[0]
            total = total1 + total2
            args = [uid, uname, uid]
            if page is None:
                rows = c.execute(sub1 + " UNION ALL " + sub2, args).fetchall()
            else:
                rows = c.execute("SELECT * FROM (" + sub1 + " UNION ALL " + sub2
                                 + ") ORDER BY lc DESC LIMIT ? OFFSET ?",
                                 args + [page_size, (page - 1) * page_size]).fetchall()
            c.close()
            lst = [{"id": r[0], "title": r[1], "artist": r[2], "album": r[3] or "",
                    "cover": r[4] or "", "fname": r[5] or "",
                    "size": int(r[6]) if r[6] is not None else 0,
                    "dur": r[7], "durText": _fmt_dur(r[7]), "created": r[8],
                    "playCount": r[9] or 0, "likeCount": r[10] or 0,
                    "shareCount": r[11] or 0, "userId": r[12] or "",
                    "userName": r[14] or "", "hasLyric": bool(r[13]),
                    "commentCount": r[15] or 0, "isLiked": bool(r[16])} for r in rows]
            self._json(200, {"list": lst, "total": total,
                             "hasMore": bool(page is not None and page * page_size < total)})
            return True
        if u.path == "/api/my-comments":
            c = db()
            uid = resolve_token(c, self.headers.get("Authorization", ""))
            if not uid:
                c.close()
                self._json(200, {"list": [], "total": 0, "hasMore": False})
                return True
            qs = urllib.parse.parse_qs(u.query)
            page, page_size = parse_page(qs)
            sel = """SELECT c.track_id, c.content, c.created,
                     COALESCE((SELECT title FROM tracks WHERE id=c.track_id),
                              (SELECT title FROM collected_tracks WHERE id=c.track_id)),
                     COALESCE((SELECT artist FROM tracks WHERE id=c.track_id),
                              (SELECT artist FROM collected_tracks WHERE id=c.track_id))
                     FROM comments c WHERE c.user_id=? ORDER BY c.created DESC"""
            total = c.execute("SELECT COUNT(*) FROM comments WHERE user_id=?",
                              (uid,)).fetchone()[0]
            if page is None:
                rows = c.execute(sel, (uid,)).fetchall()
            else:
                rows = c.execute(sel + " LIMIT ? OFFSET ?",
                                 (uid, page_size, (page - 1) * page_size)).fetchall()
            c.close()
            lst = [{"trackId": r[0], "content": r[1], "created": r[2],
                    "title": r[3] or "", "artist": r[4] or ""} for r in rows]
            self._json(200, {"list": lst, "total": total,
                             "hasMore": bool(page is not None and page * page_size < total)})
            return True
        return False

    def _h_post_social(self, u):
        length = int(self.headers.get("Content-Length", 0))
        if u.path.startswith("/api/user/") and u.path.endswith("/follow"):
            target_uid = u.path.split("/")[3]
            c = db()
            uid = resolve_token(c, self.headers.get("Authorization", ""))
            if not uid:
                c.close()
                self._err(401, "unauthorized"); return True
            if target_uid == uid:
                c.close()
                self._err(400, "cannot follow yourself"); return True
            # 检查目标用户是否存在
            target = c.execute("SELECT id, name FROM users WHERE id=?", (target_uid,)).fetchone()
            if not target:
                c.close()
                self._err(404, "user not found"); return True
            # 切换关注状态（显式事务：关注关系与通知一次提交；并发重复关注捕获主键冲突，不报 500）
            existing = c.execute("SELECT follower_id FROM follows WHERE follower_id=? AND following_id=?", (uid, target_uid)).fetchone()
            if existing:
                # 取消关注
                c.execute("DELETE FROM follows WHERE follower_id=? AND following_id=?", (uid, target_uid))
                is_following = False
                c.commit()
            else:
                # 关注：BEGIN IMMEDIATE 加写锁，防并发下双写同一关注关系
                c.execute("BEGIN IMMEDIATE")
                try:
                    c.execute("INSERT INTO follows(follower_id, following_id, created) VALUES(?,?,?)",
                              (uid, target_uid, time.time()))
                    is_following = True
                    # 检查是否互关（对方也关注了我）
                    mutual = c.execute("SELECT follower_id FROM follows WHERE follower_id=? AND following_id=?", (target_uid, uid)).fetchone()
                    # 获取关注者信息
                    me = c.execute("SELECT name FROM users WHERE id=?", (uid,)).fetchone()
                    my_name = me[0] if me else "有人"
                    # 发通知给被关注者
                    notif_id = uuid.uuid4().hex
                    if mutual:
                        # 互关了，通知"回关了你"
                        content = f"{my_name}回关了你"
                    else:
                        # 新关注，通知"关注了你"
                        content = f"{my_name}关注了你"
                    c.execute("""INSERT INTO notifications(id, user_id, type, title, content, from_user_id, from_user_name, track_id, track_title, is_read, created)
                                 VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                              (notif_id, target_uid, "follow", "新粉丝",
                               content, uid, my_name, "", "", 0, time.time()))
                    c.commit()
                except sqlite3.IntegrityError:
                    # 并发下关注关系已存在（主键冲突）：回滚并按已关注返回，不重复发通知
                    c.rollback()
                    log.warning("follow race: %s -> %s (duplicate ignored)", uid, target_uid)
                    c.close()
                    self._json(200, {"ok": True, "isFollowing": True, "duplicate": True})
                    return True
                # WebSocket 推送（事务外，推送失败不影响落库）
                ws_push(target_uid, {
                    "kind": "notification",
                    "notification": {"id": notif_id, "type": "follow",
                                     "title": "新粉丝",
                                     "content": content,
                                     "fromUserId": uid, "fromUser": my_name,
                                     "trackId": "", "trackTitle": "",
                                     "isRead": 0, "created": time.time()},
                    "unread": _notif_unread(c, target_uid)})
            c.close()
            self._json(200, {"ok": True, "isFollowing": is_following})
            return True
        if u.path == "/api/history":
            c = db()
            uid = resolve_token(c, self.headers.get("Authorization", ""))
            if not uid:
                c.close()
                self._err(401, "unauthorized"); return True
            try:
                raw = self.rfile.read(min(length, 8192))
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                c.close()
                self._err(400, "bad json"); return True
            tid = (data.get("trackId") or "").strip()
            if tid:
                # 同一首歌1分钟内重复播放，只更新时间不新增记录
                recent = c.execute("SELECT id FROM play_history WHERE user_id=? AND track_id=? AND created > ? ORDER BY created DESC LIMIT 1",
                                 (uid, tid, time.time() - 60)).fetchone()
                if recent:
                    c.execute("UPDATE play_history SET created=? WHERE id=?", (time.time(), recent[0]))
                else:
                    c.execute("""INSERT INTO play_history(id, user_id, track_id, track_title, track_artist, track_cover, source, created)
                                 VALUES(?,?,?,?,?,?,?,?)""",
                              (uuid.uuid4().hex, uid, tid,
                               data.get("title") or "", data.get("artist") or "",
                               data.get("cover") or "", data.get("source") or "upload", time.time()))
            c.commit()
            c.close()
            self._json(200, {"ok": True})
            return True
        if u.path == "/api/search/history":
            c = db()
            uid = resolve_token(c, self.headers.get("Authorization", ""))
            if not uid:
                c.close()
                self._err(401, "unauthorized"); return True
            try:
                raw = self.rfile.read(min(length, 4096))
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                c.close()
                self._err(400, "bad json"); return True
            kw = (data.get("keyword") or "").strip()
            if kw:
                # 同关键词1分钟内不重复记录
                recent = c.execute("SELECT id FROM search_history WHERE user_id=? AND keyword=? AND created > ? ORDER BY created DESC LIMIT 1",
                                 (uid, kw, time.time() - 60)).fetchone()
                if recent:
                    c.execute("UPDATE search_history SET created=? WHERE id=?", (time.time(), recent[0]))
                else:
                    c.execute("INSERT INTO search_history(id, user_id, keyword, created) VALUES(?,?,?,?)",
                              (uuid.uuid4().hex, uid, kw, time.time()))
                # 最多保留20条
                c.execute("""DELETE FROM search_history WHERE user_id=? AND id NOT IN
                             (SELECT id FROM search_history WHERE user_id=? ORDER BY created DESC LIMIT 20)""",
                          (uid, uid))
            c.commit()
            c.close()
            self._json(200, {"ok": True})
            return True
        if u.path == "/api/playlists":
            c = db()
            uid = resolve_token(c, self.headers.get("Authorization", ""))
            if not uid:
                c.close()
                self._err(401, "unauthorized"); return True
            try:
                raw = self.rfile.read(min(length, 4096))
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                c.close()
                self._err(400, "bad json"); return True
            name = (data.get("name") or "").strip()
            if not name:
                c.close()
                self._err(400, "name required"); return True
            pid = uuid.uuid4().hex
            c.execute("""INSERT INTO playlists(id, user_id, name, cover, description, track_count, created)
                         VALUES(?,?,?,?,?,?,?)""",
                      (pid, uid, name, data.get("cover") or "", data.get("description") or "", 0, time.time()))
            c.commit()
            c.close()
            self._json(200, {"ok": True, "playlistId": pid})
            return True
        if u.path.startswith("/api/playlists/") and u.path.endswith("/tracks"):
            pid = u.path.split("/")[3]
            c = db()
            uid = resolve_token(c, self.headers.get("Authorization", ""))
            if not uid:
                c.close()
                self._err(401, "unauthorized"); return True
            pl = c.execute("SELECT id FROM playlists WHERE id=? AND user_id=?", (pid, uid)).fetchone()
            if not pl:
                c.close()
                self._err(404, "playlist not found"); return True
            try:
                raw = self.rfile.read(min(length, 8192))
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                c.close()
                self._err(400, "bad json"); return True
            tid = (data.get("trackId") or "").strip()
            if tid:
                # 检查是否已存在
                existing = c.execute("SELECT 1 FROM playlist_tracks WHERE playlist_id=? AND track_id=?", (pid, tid)).fetchone()
                if not existing:
                    cover = data.get("cover") or ""
                    c.execute("""INSERT INTO playlist_tracks(playlist_id, track_id, track_title, track_artist, track_cover, source, created)
                                 VALUES(?,?,?,?,?,?,?)""",
                              (pid, tid, data.get("title") or "", data.get("artist") or "",
                               cover, data.get("source") or "upload", time.time()))
                    c.execute("UPDATE playlists SET track_count=track_count+1 WHERE id=?", (pid,))
                    # 自动更新歌单封面：用最新添加的歌曲封面
                    if cover:
                        c.execute("UPDATE playlists SET cover=? WHERE id=?", (cover, pid))
            c.commit()
            c.close()
            self._json(200, {"ok": True})
            return True
        if u.path.startswith("/api/comments/") and u.path.endswith("/like"):
            cid = u.path.split("/")[3]
            c = db()
            uid = resolve_token(c, self.headers.get("Authorization", ""))
            if not uid:
                c.close()
                self._err(401, "unauthorized"); return True
            # 检查评论是否存在（含评论作者/所属歌曲，用于点赞互动通知）
            comment = c.execute("SELECT id, like_count, user_id, track_id FROM comments WHERE id=?", (cid,)).fetchone()
            if not comment:
                c.close()
                self._err(404, "comment not found"); return True
            # 检查是否已点赞
            liked = c.execute("SELECT user_id FROM comment_likes WHERE user_id=? AND comment_id=?", (uid, cid)).fetchone()
            if liked:
                # 取消点赞
                c.execute("DELETE FROM comment_likes WHERE user_id=? AND comment_id=?", (uid, cid))
                new_count = max(0, (comment[1] or 0) - 1)
            else:
                # 点赞
                c.execute("INSERT INTO comment_likes(user_id, comment_id, created) VALUES(?,?,?)", (uid, cid, time.time()))
                new_count = (comment[1] or 0) + 1
                # 点赞互动通知：给评论作者（与评论通知一致：notifications + WebSocket 实时推送）
                comment_owner = comment[2]
                if comment_owner and comment_owner != uid:
                    try:
                        me = c.execute("SELECT name FROM users WHERE id=?", (uid,)).fetchone()
                        my_name = (me[0] if me else "") or "用户"
                        track_id = comment[3] or ""
                        track_title = ""
                        if track_id:
                            tr = c.execute("SELECT title FROM tracks WHERE id=?", (track_id,)).fetchone()
                            track_title = tr[0] if tr else ""
                        nid = uuid.uuid4().hex
                        c.execute("""INSERT INTO notifications(id, user_id, type, title, content, from_user_id, from_user_name, track_id, track_title, is_read, created, comment_id)
                                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                                  (nid, comment_owner, "comment_like", "收到新赞",
                                   "%s赞了你的评论%s" % (my_name, ("：" + track_title) if track_title else ""),
                                   uid, my_name, track_id, track_title, 0, time.time(), cid))
                        ws_push(comment_owner, {
                            "kind": "notification",
                            "notification": {"id": nid, "type": "comment_like",
                                             "title": "收到新赞",
                                             "content": "%s赞了你的评论%s" % (my_name, ("：" + track_title) if track_title else ""),
                                             "fromUserId": uid, "fromUser": my_name,
                                             "commentId": cid,
                                             "trackId": track_id, "trackTitle": track_title,
                                             "isRead": 0, "created": time.time()},
                            "unread": _notif_unread(c, comment_owner)})
                    except Exception as _e:
                        log.info("comment_like notif err: %s", _e)
            c.execute("UPDATE comments SET like_count=? WHERE id=?", (new_count, cid))
            c.commit()
            c.close()
            self._json(200, {"ok": True, "liked": not liked, "likeCount": new_count})
            return True
        if u.path == "/api/official-messages/read":
            c = db()
            tok_uid = resolve_token(c, self.headers.get("Authorization", ""))
            if not tok_uid:
                c.close()
                self._err(401, "unauthorized"); return True
            import time as _t
            c.execute("UPDATE users SET official_read_at=? WHERE id=?", (_t.time(), tok_uid))
            c.commit()
            c.close()
            self._json(200, {"ok": True})
            return True
        if u.path == "/api/notifications/read":
            c = db()
            tok_uid = resolve_token(c, self.headers.get("Authorization", ""))
            if not tok_uid:
                c.close()
                self._err(401, "unauthorized"); return True
            qs = urllib.parse.parse_qs(u.query)
            ntype = (qs.get("type", ["all"])[0] or "all").strip()
            if ntype == "interactive":
                # 互动通知：点赞/评论/分享类
                c.execute("UPDATE notifications SET is_read=1 WHERE user_id=? AND type IN ('like','comment','share')", (tok_uid,))
            elif ntype == "official":
                # 官方通知：系统公告类
                c.execute("UPDATE notifications SET is_read=1 WHERE user_id=? AND type NOT IN ('like','comment','share')", (tok_uid,))
            else:
                # 默认全部已读
                c.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (tok_uid,))
            c.commit()
            c.close()
            self._json(200, {"ok": True})
            return True
        if u.path.startswith("/api/play/"):
            if not rate_limit(self, "play"):
                self._err(429, "请求过于频繁，请稍后再试")
                return True
            try:
                tid = u.path.rsplit("/", 1)[-1]
                c = db()
                uid, via_token = self.auth_user(c)
                if not uid:
                    c.close()
                    self._err(401, "未登录或登录已过期")
                    return True
                # S1：采集歌播放计数同步自增（热度路需要采集歌计数；like/share 已同步，见 /api/like、/api/share）
                c.execute("UPDATE tracks SET play_count=play_count+1 WHERE id=?", (tid,))
                c.execute("UPDATE collected_tracks SET play_count=play_count+1 WHERE id=?", (tid,))
                c.commit()
                # 曝光→点击归因（2026-11-05）：把该用户最近一条未点击曝光标记为已点击，
                # 用于分路 CT
                try:
                    from server import impressions as _imp
                    _imp.mark_clicked(c, uid, tid)
                except Exception:
                    pass
                row = c.execute("SELECT play_count FROM tracks WHERE id=?", (tid,)).fetchone()
                if not row:
                    row = c.execute("SELECT play_count FROM collected_tracks WHERE id=?", (tid,)).fetchone()
                c.close()
                self._json(200, {"playCount": row[0] if row else 0})
            except Exception as e:
                self._internal_error(e)
            return True
        if u.path.startswith("/api/like/"):
            if not rate_limit(self, "like"):
                self._err(429, "请求过于频繁，请稍后再试")
                return True
            try:
                tid = u.path.rsplit("/", 1)[-1]
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except Exception:
                    body = {}
                c = db()
                uid, via_token = self.auth_user(c, body)
                if not uid:
                    c.close()
                    self._err(401, "未登录或登录已过期")
                    return True
                ex = c.execute("SELECT 1 FROM likes WHERE user_id=? AND track_id=?",
                               (uid, tid)).fetchone()
                if ex:
                    c.execute("DELETE FROM likes WHERE user_id=? AND track_id=?", (uid, tid))
                    c.execute("UPDATE tracks SET like_count=MAX(0,like_count-1) WHERE id=?", (tid,))
                    c.execute("UPDATE collected_tracks SET like_count=MAX(0,like_count-1) WHERE id=?", (tid,))
                    liked = False
                else:
                    c.execute("INSERT OR IGNORE INTO likes(user_id,track_id,created) VALUES(?,?,?)",
                              (uid, tid, time.time()))
                    c.execute("UPDATE tracks SET like_count=like_count+1 WHERE id=?", (tid,))
                    c.execute("UPDATE collected_tracks SET like_count=like_count+1 WHERE id=?", (tid,))
                    liked = True
                c.execute("""INSERT INTO behavior_events(id,user_id,track_id,event,value,meta,track_info,created)
                             VALUES(?,?,?,?,?,?,?,?)""",
                          (uuid.uuid4().hex, uid, tid, "like", 1.0 if liked else 0.0,
                           "", _track_info(c, tid), time.time()))
                # 点赞通知：给歌曲上传者（用户上传表 + 采集表都覆盖）
                if liked:
                    owner_id, owner_title = _track_owner(c, tid)
                    if owner_id and owner_id != uid:
                        from_user_name = c.execute("SELECT name FROM users WHERE id=?", (uid,)).fetchone()
                        uname = (from_user_name[0] if from_user_name else "").strip() or "有人"
                        # 去重：10分钟内同一用户对同一首歌的未读点赞通知，不重复插入
                        ten_min_ago = time.time() - 600
                        existing = c.execute("""SELECT id FROM notifications
                                               WHERE user_id=? AND from_user_id=? AND track_id=?
                                               AND type='like' AND is_read=0 AND created > ?
                                               ORDER BY created DESC LIMIT 1""",
                                            (owner_id, uid, tid, ten_min_ago)).fetchone()
                        if existing:
                            # 更新已有通知的时间和内容（聚合为"又赞了"）
                            notif_id = existing[0]
                            c.execute("""UPDATE notifications SET created=?, content=?, title=?
                                         WHERE id=?""",
                                      (time.time(), f"{uname}赞了你的歌曲《{owner_title}》",
                                       "收到新点赞", notif_id))
                        else:
                            # 首次点赞，插入新通知
                            notif_id = uuid.uuid4().hex
                            c.execute("""INSERT INTO notifications(id, user_id, type, title, content, from_user_id, from_user_name, track_id, track_title, is_read, created)
                                         VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                                      (notif_id, owner_id, "like", "收到新点赞",
                                       f"{uname}赞了你的歌曲《{owner_title}》",
                                       uid, uname, tid, owner_title, 0, time.time()))
                        # 实时推送（WebSocket）
                        ws_push(owner_id, {
                            "kind": "notification",
                            "notification": {"id": notif_id, "type": "like",
                                             "title": "收到新点赞",
                                             "content": f"{uname}赞了你的歌曲《{owner_title}》",
                                             "fromUserId": uid, "fromUser": uname,
                                             "trackId": tid, "trackTitle": owner_title,
                                             "isRead": 0, "created": time.time()},
                            "unread": _notif_unread(c, owner_id)})
                c.commit()
                row = c.execute("SELECT like_count FROM tracks WHERE id=?", (tid,)).fetchone()
                c.close()
                self._json(200, {"liked": liked, "likeCount": row[0] if row else 0})
            except Exception as e:
                self._internal_error(e)
            return True
        if u.path.startswith("/api/share/"):
            if not rate_limit(self, "share"):
                self._err(429, "请求过于频繁，请稍后再试")
                return True
            try:
                tid = u.path.rsplit("/", 1)[-1]
                c = db()
                uid, via_token = self.auth_user(c)
                if not uid:
                    c.close()
                    self._err(401, "未登录或登录已过期")
                    return True
                c.execute("UPDATE tracks SET share_count=share_count+1 WHERE id=?", (tid,))
                c.execute("UPDATE collected_tracks SET share_count=share_count+1 WHERE id=?", (tid,))
                c.execute("""INSERT INTO behavior_events(id,user_id,track_id,event,value,meta,track_info,created)
                             VALUES(?,?,?,?,?,?,?,?)""",
                          (uuid.uuid4().hex, uid, tid, "share", 1.0,
                           "", _track_info(c, tid), time.time()))
                # 分享通知：给歌曲上传者（用户上传表 + 采集表都覆盖）
                # #13：与点赞一致做 10 分钟聚合去重——同一歌曲短期内多次被分享
                # 不重复堆通知，复用未读通知并刷新内容（避免高频分享刷屏）
                owner_id, owner_title = _track_owner(c, tid)
                if owner_id and owner_id != uid:
                    now = time.time()
                    from_user = c.execute("SELECT name FROM users WHERE id=?", (uid,)).fetchone()
                    uname = (from_user[0] if from_user else "").strip() or "有人"
                    dup = c.execute("""SELECT id FROM notifications
                                       WHERE user_id=? AND type='share' AND track_id=? AND is_read=0
                                         AND created > ? LIMIT 1""",
                                    (owner_id, tid, now - 600)).fetchone()
                    if dup:
                        # 已有 10 分钟内的未读分享通知：复用并更新内容/时间（刷新通知栏置顶）
                        notif_id = dup[0]
                        c.execute("""UPDATE notifications
                                     SET content=?, from_user_id=?, from_user_name=?, created=? WHERE id=?""",
                                  (f"{uname}分享了你的歌曲《{owner_title}》", uid, uname, now, notif_id))
                        _is_new = 0
                    else:
                        notif_id = uuid.uuid4().hex
                        c.execute("""INSERT INTO notifications(id, user_id, type, title, content, from_user_id, from_user_name, track_id, track_title, is_read, created)
                                     VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                                  (notif_id, owner_id, "share", "收到新分享",
                                   f"{uname}分享了你的歌曲《{owner_title}》",
                                   uid, uname, tid, owner_title, 0, now))
                        _is_new = 1
                    # 实时推送（WebSocket）
                    ws_push(owner_id, {
                        "kind": "notification",
                        "notification": {"id": notif_id, "type": "share",
                                         "title": "收到新分享",
                                         "content": f"{uname}分享了你的歌曲《{owner_title}》",
                                         "fromUserId": uid, "fromUser": uname,
                                         "trackId": tid, "trackTitle": owner_title,
                                         "isRead": 0, "created": now, "isNew": _is_new},
                        "unread": _notif_unread(c, owner_id)})
                c.commit()
                row = c.execute("SELECT share_count FROM tracks WHERE id=?", (tid,)).fetchone()
                if not row:
                    row = c.execute("SELECT share_count FROM collected_tracks WHERE id=?", (tid,)).fetchone()
                c.close()
                self._json(200, {"shareCount": row[0] if row else 0})
            except Exception as e:
                self._internal_error(e)
            return True
        if u.path == "/api/upload-comment-image":
            if not rate_limit(self, "comment_image"):
                self._err(429, "上传过于频繁，请稍后再试")
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
                c = db()
                uid, via_token = self.auth_user(c, fields)
                if not uid:
                    c.close()
                    self._err(401, "未登录或登录已过期")
                    return True
                img = files.get("image")
                if not img or not img[0]:
                    c.close()
                    self._err(400, "no image")
                    return True
                fname, fdata = img
                if len(fdata) > MAX_COMMENT_IMAGE:
                    c.close()
                    self._err(413, "too large")
                    return True
                cext = os.path.splitext(fname)[1].lower()
                if cext not in (".jpg", ".jpeg", ".png", ".webp"):
                    c.close()
                    self._err(400, "unsupported image type")
                    return True
                if not image_magic_ok(fdata, cext):
                    c.close()
                    self._err(400, "图片文件格式校验失败")
                    return True
                stored = "cmt_" + uuid.uuid4().hex + cext
                with open(os.path.join(FILES, stored), "wb") as f:
                    f.write(fdata)
                c.close()
                self._json(200, {"name": stored})
            except Exception as e:
                self._internal_error(e)
            return True
        if u.path.startswith("/api/ai/generate-theme"):
            # AI 配色主题生成（Folia major 移植）
            try:
                tid = u.path.rsplit("/", 1)[-1]
                if not tid or tid == "generate-theme":
                    self._err(400, "缺少歌曲ID")
                    return True
                c = db()
                cached = c.execute(
                    "SELECT background_color, primary_color, accent_color, secondary_color FROM track_themes WHERE track_id=?",
                    (tid,)).fetchone()
                if cached:
                    c.close()
                    self._json(200, {"ok": True, "theme": {
                        "backgroundColor": cached[0], "primaryColor": cached[1],
                        "accentColor": cached[2], "secondaryColor": cached[3]
                    }, "cached": True})
                    return True
                row = c.execute("SELECT title,artist,genre FROM tracks WHERE id=?", (tid,)).fetchone()
                if not row:
                    row = c.execute("SELECT title,artist,genre FROM collected_tracks WHERE id=?", (tid,)).fetchone()
                if not row:
                    c.close()
                    self._err(404, "歌曲不存在")
                    return True
                theme = _ai_generate_theme(row[0], row[1], row[2])
                if not theme:
                    c.close()
                    self._err(502, "AI 配色生成失败")
                    return True
                c.execute("""INSERT OR REPLACE INTO track_themes
                             (track_id, background_color, primary_color, accent_color, secondary_color)
                             VALUES(?,?,?,?,?)""",
                          (tid, theme["backgroundColor"], theme["primaryColor"],
                           theme["accentColor"], theme["secondaryColor"]))
                c.commit()
                c.close()
                self._json(200, {"ok": True, "theme": theme, "cached": False})
            except Exception as e:
                self._internal_error(e)
            return True
        if u.path.startswith("/api/ai-comment/site/"):
            # 官网试听播放器专用：免登录生成 AI 乐评，不落库（不污染真实评论区）
            # 路径 /api/ai-comment/site/{tid}，body: {"style": "hot|praise|stk"}
            if not rate_limit(self, "ai_comment"):
                self._err(429, "请求过于频繁，请稍后再试")
                return True
            try:
                tid = u.path.split("/")[4]
                body = {}
                try:
                    ln = int(self.headers.get("Content-Length") or 0)
                    if ln > 0 and ln < 65536:
                        body = json.loads(self.rfile.read(ln).decode("utf-8") or "{}")
                except Exception:
                    body = {}
                style = body.get("style") or "hot"
                if style not in ("hot", "praise", "stk"):
                    style = "hot"
                c = db()
                row = c.execute("SELECT id,title,artist,album,cover,genre FROM tracks WHERE id=?", (tid,)).fetchone()
                if not row:
                    row = c.execute("SELECT id,title,artist,album,cover,genre FROM collected_tracks WHERE id=?", (tid,)).fetchone()
                if not row:
                    c.close()
                    self._err(404, "歌曲不存在")
                    return True
                text = _ai_generate_comment(row[1], row[2], row[3], row[5], style)
                c.close()
                if not text:
                    self._err(502, "AI 乐评生成失败，请稍后再试")
                    return True
                images = []
                # stk（表情包向）按概率附一张表情包图；其余风格不附带
                if style == "stk" and random.random() * 100 < MEME_PROB_PCT:
                    m = _fetch_meme_image()
                    if m:
                        images = [m]
                self._json(200, {"ok": True, "comment": {
                    "content": text, "images": images, "isAi": True,
                    "userName": _ai_display_name()}})
            except Exception as e:
                self._internal_error(e)
            return True
        if u.path.startswith("/api/ai-comment/"):
            if not rate_limit(self, "ai_comment"):
                self._err(429, "AI 乐评生成过于频繁，请稍后再试")
                return True
            try:
                tid = u.path.split("/")[3]
                body = {}
                try:
                    ln = int(self.headers.get("Content-Length") or 0)
                    if ln > 0 and ln < 65536:
                        body = json.loads(self.rfile.read(ln).decode("utf-8") or "{}")
                except Exception:
                    body = {}
                c = db()
                uid, _via = self.auth_user(c, body)
                if not uid:
                    c.close()
                    self._err(401, "未登录或登录已过期")
                    return True
                # 歌曲信息：用户上传表优先，其次采集表
                row = c.execute("SELECT id,title,artist,album,cover,genre FROM tracks WHERE id=?", (tid,)).fetchone()
                src = "tracks"
                if not row:
                    row = c.execute("SELECT id,title,artist,album,cover,genre FROM collected_tracks WHERE id=?", (tid,)).fetchone()
                    src = "collected_tracks"
                if not row:
                    c.close()
                    self._err(404, "歌曲不存在")
                    return True
                # 每首歌最多 2 条 AI 乐评，已有则直接返回（不重复生成）
                existing = c.execute(
                    "SELECT id,content,created FROM comments WHERE track_id=? AND user_id=? ORDER BY created",
                    (tid, _ai_uid())).fetchall()
                ai_name = _ai_display_name()
                if len(existing) >= 2:
                    c.close()
                    self._json(200, {"ok": True, "comment": {
                        "id": existing[-1][0], "userId": _ai_uid(),
                        "userName": ai_name, "content": existing[-1][1],
                        "images": [], "created": existing[-1][2],
                        "isAi": True, "duplicate": True}})
                    return True
                text = _ai_generate_comment(row[1], row[2], row[3], row[5])
                if not text:
                    c.close()
                    self._err(502, "AI 乐评生成失败，请稍后再试")
                    return True
                cid = uuid.uuid4().hex
                now = time.time()
                images = "[]"
                if random.random() * 100 < MEME_PROB_PCT:
                    m = _fetch_meme_image()
                    if m:
                        images = json.dumps([m])
                c.execute("""INSERT INTO comments(id,track_id,user_id,user_name,content,images,created,is_ai)
                             VALUES(?,?,?,?,?,?,?,1)""",
                          (cid, tid, _ai_uid(), ai_name, text, images, now))
                c.commit()
                # 通知歌曲上传者（AI 评论了你的歌曲）
                owner_id, owner_title = _track_owner(c, tid)
                if owner_id and owner_id != uid:
                    c.execute("""INSERT INTO notifications(id, user_id, type, title, content, from_user_id, from_user_name, track_id, track_title, is_read, created)
                                 VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                              (uuid.uuid4().hex, owner_id, "comment", "收到 AI 乐评",
                               f"{ai_name} 评论了你的歌曲《{owner_title}》",
                               _ai_uid(), ai_name, tid, owner_title, 0, now))
                c.commit()
                c.close()
                self._json(200, {"ok": True, "comment": {
                    "id": cid, "userId": _ai_uid(), "userName": ai_name,
                    "content": text, "images": [], "created": now, "isAi": True}})
            except Exception as e:
                self._internal_error(e)
            return True
        if u.path.startswith("/api/comment/"):
            if not rate_limit(self, "comment"):
                self._err(429, "评论过于频繁，请稍后再试")
                return True
            try:
                tid = u.path.rsplit("/", 1)[-1]
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except Exception:
                    body = {}
                c = db()
                uid, via_token = self.auth_user(c, body)
                if not uid:
                    c.close()
                    self._err(401, "未登录或登录已过期")
                    return True
                uname = ((body.get("userName") or "").strip() or "一位用户")[:20]
                content = (body.get("content") or "").strip()[:MAX_COMMENT]
                # 过滤控制字符（防注入/异常渲染）
                content = "".join(ch for ch in content if ch >= " " or ch == "\n").strip()
                # 评论图片：仅接受本服务上传接口生成的 cmt_ 文件（白名单正则，防路径/文件名注入）
                images = body.get("images") or []
                if isinstance(images, str):
                    images = [i.strip() for i in images.split(",") if i.strip()]
                if not isinstance(images, list):
                    images = []
                images = [i for i in images[:6]
                          if isinstance(i, str)
                          and re.fullmatch(r"cmt_[0-9a-f]{32}\.(?:jpg|jpeg|png|webp)", i)]
                # 文字与图片至少有一项才能发布（支持纯图片评论）
                if not content and not images:
                    c.close()
                    self._err(400, "评论内容不能为空")
                    return True
                cid = uuid.uuid4().hex
                c.execute("""INSERT INTO comments(id,track_id,user_id,user_name,content,images,created)
                             VALUES(?,?,?,?,?,?,?)""",
                          (cid, tid, uid, uname, content,
                           json.dumps(images, ensure_ascii=False), time.time()))
                c.execute("""INSERT INTO behavior_events(id,user_id,track_id,event,value,meta,track_info,created)
                             VALUES(?,?,?,?,?,?,?,?)""",
                          (uuid.uuid4().hex, uid, tid, "comment", 1.0,
                           "", _track_info(c, tid), time.time()))
                # 评论意图 → 个性化推荐：「想听 XX」类评论命中曲库 → 写入意图推荐池，
                # 由 /api/recommend 混入该用户推荐流（刷到即见，不打扰式通知）
                try:
                    wish = _comment_wish_push(c, uid, content)
                    if wish:
                        log.info("comment wish marked: uid=%s track=%s kw=%s",
                                 uid[:8], wish[0][:8], content[:24])
                except Exception as _e:
                    log.info("comment wish push err: %s", _e)
                # 评论通知：给歌曲上传者（用户上传表 + 采集表都覆盖）
                owner_id, owner_title = _track_owner(c, tid)
                if owner_id and owner_id != uid:
                    notif_id = uuid.uuid4().hex
                    c.execute("""INSERT INTO notifications(id, user_id, type, title, content, from_user_id, from_user_name, track_id, track_title, is_read, created, comment_id)
                                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                              (notif_id, owner_id, "comment", "收到新评论",
                               f"{uname}评论了你的歌曲《{owner_title}》：{content[:50]}",
                               uid, uname, tid, owner_title, 0, time.time(), cid))
                    # 实时推送（WebSocket）
                    ws_push(owner_id, {
                        "kind": "notification",
                        "notification": {"id": notif_id, "type": "comment",
                                         "title": "收到新评论",
                                         "content": f"{uname}评论了你的歌曲《{owner_title}》：{content[:50]}",
                                         "fromUserId": uid, "fromUser": uname,
                                         "commentId": cid,
                                         "trackId": tid, "trackTitle": owner_title,
                                         "isRead": 0, "created": time.time()},
                        "unread": _notif_unread(c, owner_id)})
                c.commit()
                c.close()
                self._json(200, {"id": cid})
            except Exception as e:
                self._internal_error(e)
            return True
        if u.path == "/api/behavior":
            if not rate_limit(self, "behavior"):
                self._err(429, "请求过于频繁，请稍后再试")
                return True
            try:
                raw = self.rfile.read(min(length, 256 * 1024)) if length > 0 else b""
                data = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                self._err(400, "bad json")
                return True
            c = db()
            uid, via_token = self.auth_user(c, data)
            if not uid:
                c.close()
                self._err(401, "未登录或登录已过期")
                return True
            evs = data.get("events") or []
            if not isinstance(evs, list) or not evs or len(evs) > 30:
                c.close()
                self._err(400, "events must be 1..30")
                return True
            now = time.time()
            ins, has_listen = _ingest_behavior(c, uid, evs, now)
            c.commit()
            # 实时数据事件：任意行为变化后节流推送个性化推荐刷新（15s），
            # 收听事件额外附带听歌时长统计；前端收到 recommend 事件自动刷新「猜你喜欢」
            if ins:
                _ws_refresh_recommend(c, uid, has_listen)
            c.close()
            self._json(200, {"ok": True, "count": ins})
            return True
        return False
