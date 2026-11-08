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
from server.media import image_magic_ok
from server.config import AI_CFG_FILE, AI_API_BASE_DEFAULT, AI_MODEL_DEFAULT, AI_UID

__all__ = ['_ai_cfg', '_ai_generate_comment', '_ai_generate_theme', '_ai_uid',
           '_ai_auto_settings', '_ai_auto_run', '_ai_comment_stats',
           '_ai_save_config', '_ai_profile', '_ai_display_name',
           '_fetch_hot_pool', '_match_hot_words', '_hot_pool_info',
           '_fetch_meme_image', 'start_ai_worker']

def _ai_cfg():
    """读取 AI 接入配置：优先 ai_config.json（权限 600），环境变量兜底。密钥不写入代码/日志。"""
    cfg = {}
    try:
        if os.path.exists(AI_CFG_FILE):
            with open(AI_CFG_FILE, encoding="utf-8") as f:
                cfg = json.loads(f.read() or "{}")
    except Exception:
        cfg = {}
    cfg.setdefault("base", os.environ.get("AI_API_BASE", "https://ws-5usvd1tklaq38lj3.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"))
    cfg.setdefault("key", os.environ.get("AI_API_KEY", ""))
    cfg.setdefault("model", os.environ.get("AI_MODEL", "qwen-turbo"))
    return cfg


def _ai_generate_theme(title, artist, genre=None):
    """根据歌曲信息调用 AI 生成配色主题（参考 Folia major 方案）。

    返回 dict: {backgroundColor, primaryColor, accentColor, secondaryColor} 或 None。
    规则：深色背景（避免纯黑）、根据歌曲情绪选色、文字对比度足够。
    """
    cfg = _ai_cfg()
    if not cfg.get("key"):
        return None
    sys_prompt = """你是一个音乐 APP 的配色设计师。根据给定的歌曲信息，生成一套沉浸式深色主题配色。

规则：
1. 背景色必须是深色（避免纯黑 #000000），根据歌曲情绪选择：
   - 深夜/忧郁/治愈 → 深蓝、深紫、炭灰
   - 夏日/欢快/活力 → 深橙、深红、深绿
   - 电子/潮酷 → 深青、深灰、深蓝
   - 国风/古典 → 深棕、深墨绿、深褐
2. primaryColor：主强调色，和背景形成对比但不刺眼
3. accentColor：点缀色，用来做按钮、高亮
4. secondaryColor：次要文字色（歌名、歌手名用），必须和背景有足够对比度，清晰可读

只返回 JSON，不要任何其他文字，格式：
{"backgroundColor": "#1a1a2e", "primaryColor": "#e94560", "accentColor": "#0f3460", "secondaryColor": "#c4c4c4"}"""

    facts = "歌曲名：%s\n歌手：%s" % (title or "未知", artist or "未知")
    if genre:
        facts += "\n曲风：%s" % genre
    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": facts}
        ],
        "temperature": 0.7,
        "max_tokens": 200
    }
    req = urllib.request.Request(
        cfg["base"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer %s" % cfg["key"]},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode("utf-8"))
        text = (d.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
        text = text.strip()
        m = re.search(r'\{[^}]+\}', text)
        if not m:
            return None
        theme = json.loads(m.group())
        required = ["backgroundColor", "primaryColor", "accentColor", "secondaryColor"]
        if not all(k in theme for k in required):
            return None
        return theme
    except Exception:
        return None


def _ai_generate_comment(title, artist, album, genre, style=None):
    """按歌曲歌名/作者（专辑、曲风）调用 qwen-turbo 生成乐评。

    先用歌曲信息匹配当前网络热梗（实时抓取+本地匹配），把相关热梗注入上下文；
    匹配不到则注入热词池兜底；热梗源不可用时退回纯乐评。返回纯文本或 None（失败）。

    style（官网试听播放器快捷按钮用，可选）：
      None/hot —— 默认：自然融入 1~2 个热梗，听感/情绪/画面为主
      praise   —— 夸赞向：真诚夸赞歌曲亮点
      stk      —— 表情包向：风格更俏皮活泼，适合配表情包
    """
    cfg = _ai_cfg()
    if not cfg.get("key"):
        return None
    sys_prompt = ("你是流萤音乐的 AI 乐评人。为给定歌曲写一段贴合歌曲本身的短乐评，"
                  "像懂音乐的朋友在分享感受：可以写听感、情绪、画面感或对歌词/旋律的理解。"
                  "可以适当融入一两个网络热梗或流行语（优先使用用户消息中提供的热梗，"
                  "如 DNA动了、上头、单曲循环、泪目、爷青回、绝了、破防、意难平），"
                  "让乐评更有网感，但必须自然贴切、点到为止，不能硬凑、不能低俗、不能有攻击性，"
                  "热梗最多一两个，乐评主体仍以听感/情绪/画面为主。"
                  "要求：中文，60~120 字，真诚不浮夸，不堆砌辞藻，不编造歌手名以外的信息，"
                  "不要使用感叹号刷屏，不要出现\"AI\"\"机器人\"等字样，结尾不要加话题标签。")
    if style == "praise":
        sys_prompt = ("你是流萤音乐的 AI 乐评人。为给定歌曲写一段真诚的夸赞向短乐评："
                      "重点表达这首歌值得一听的亮点（旋律记忆点、情绪共鸣、歌词画面感、"
                      "歌手嗓音等），语气像安利好歌给朋友，克制不浮夸，不硬凑网络梗。"
                      "要求：中文，60~120 字，真诚、有细节，不堆砌辞藻，"
                      "不编造歌手名以外的信息，不要使用感叹号刷屏，不要出现\"AI\"\"机器人\"字样，"
                      "结尾不要加话题标签。")
    elif style == "stk":
        sys_prompt = ("你是流萤音乐的 AI 乐评人。为给定歌曲写一段俏皮活泼的短乐评："
                      "风格更年轻有网感，可以自然融入 1~2 个网络热梗或流行语"
                      "（如 DNA动了、上头、单曲循环、泪目、爷青回、绝了、破防、意难平、杀我别用XX刀），"
                      "也可以适当加入（狗头）（大哭）这类轻量表情符号文案，但最多一处；"
                      "主体仍是贴合歌曲的听感/情绪。不低俗、不攻击、不硬凑。"
                      "要求：中文，50~110 字，俏皮但真诚，不编造歌手名以外的信息，"
                      "不要出现\"AI\"\"机器人\"字样，结尾不要加话题标签。")
    facts = "歌曲名：%s\n歌手：%s" % (title or "未知", artist or "未知")
    if album:
        facts += "\n专辑：%s" % album
    if genre:
        facts += "\n曲风：%s" % genre
    hot, pool = _match_hot_words(title, artist, genre)
    if hot:
        facts += ("\n\n与这首歌相关的当前网络热梗：%s\n"
                  "（可从其中自然融入 1~2 个热梗/流行语，让乐评更有网感；没有合适的就不必用）"
                  % "、".join(hot))
    elif pool:
        facts += ("\n\n当前网络热词：%s\n"
                  "（若合适可自然融入 1~2 个，让乐评更有网感；没有合适的就不必用）"
                  % "、".join(pool[:10]))
    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": facts}
        ],
        "temperature": 0.9,
        "max_tokens": 300
    }
    req = urllib.request.Request(
        cfg["base"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer %s" % cfg["key"]},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            d = json.loads(r.read().decode("utf-8"))
        text = (d.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
        text = text.strip().strip('"').strip("“”")
        if not text or len(text) < 10:
            return None
        return text
    except Exception:
        return None


def _ai_uid():
    return "ai_liuying_comment"


def _ai_display_name():
    """AI 乐评账号当前昵称（网站管理可改），兜底 'AI 乐评'。"""
    try:
        c = db()
        try:
            r = c.execute("SELECT name FROM users WHERE id=?", (_ai_uid(),)).fetchone()
            return (r[0] or "AI 乐评") if r else "AI 乐评"
        finally:
            c.close()
    except Exception:
        return "AI 乐评"


def _ai_profile():
    """AI 乐评账号形象（昵称/签名/头像/背景图文件名），供网站管理页展示与编辑。"""
    try:
        c = db()
        try:
            r = c.execute("SELECT name, signature, avatar, background_image FROM users WHERE id=?",
                          (_ai_uid(),)).fetchone()
            if not r:
                return {"name": "AI 乐评", "signature": "", "avatar": "", "background": ""}
            return {"name": r[0] or "AI 乐评", "signature": r[1] or "", "avatar": r[2] or "",
                    "background": r[3] or ""}
        finally:
            c.close()
    except Exception:
        return {"name": "AI 乐评", "signature": "", "avatar": "", "background": ""}


# ---------------- 实时热梗池（百度热搜 + 贴吧热议，按歌曲信息匹配） ----------------

_HOT_CACHE = {"ts": 0, "pool": []}


def _fetch_hot_pool():
    """抓取当前网络热梗池（百度热搜 word + 贴吧热议 topic_name），TTL 缓存，失败返回 []（绝不抛错）。"""
    now = time.time()
    if _HOT_CACHE["pool"] and now - _HOT_CACHE["ts"] < HOT_WORDS_TTL:
        return _HOT_CACHE["pool"]
    pool = []
    seen = set()
    for url, parse in ((HOT_WORDS_ENDPOINT, _parse_baidu_hot),
                       (HOT_WORDS_ENDPOINT_2, _parse_tieba_hot)):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=HOT_WORDS_TIMEOUT) as r:
                d = json.loads(r.read().decode("utf-8"))
            for w in parse(d):
                w = (w or "").strip()
                if w and w not in seen:
                    seen.add(w)
                    pool.append(w)
        except Exception:
            continue
    if pool:
        _HOT_CACHE["pool"] = pool[:HOT_WORDS_LIMIT]
        _HOT_CACHE["ts"] = now
    return _HOT_CACHE["pool"]


def _parse_baidu_hot(d):
    """百度热搜：data.cards[0].content[0].content[].word"""
    out = []
    for x in (d.get("data") or {}).get("cards", [{}])[0].get("content", [{}])[0].get("content", []):
        w = (x.get("word") or "").strip()
        if w:
            out.append(w)
    return out


def _parse_tieba_hot(d):
    """贴吧热议：data.bang_topic.topic_list[].topic_name"""
    out = []
    for x in ((d.get("data") or {}).get("bang_topic") or {}).get("topic_list", []):
        w = (x.get("topic_name") or "").strip()
        if w:
            out.append(w)
    return out


def _match_hot_words(title, artist, genre):
    """用歌曲信息（歌名 n-gram/歌手/曲风）在热梗池中匹配相关热梗。

    返回 (matched_list, full_pool)；匹配不到时 matched 为空，由调用方用池兜底。
    """
    pool = _fetch_hot_pool()
    if not pool:
        return [], []
    keys = set()
    for s in (title or ""):
        s = s.strip()
        if not s:
            continue
        if len(s) >= 2:
            keys.add(s)
        for n in (2, 3, 4):
            if len(s) >= n:
                for i in range(len(s) - n + 1):
                    keys.add(s[i:i + n])
    for s in (artist or ""):
        s = s.strip()
        if len(s) >= 2:
            keys.add(s)
    if genre:
        g = genre.strip()
        if len(g) >= 2:
            keys.add(g)
    keys = {k for k in keys if len(k) >= 2}
    scored = []
    for w in pool:
        wl = w.lower()
        score = 0
        for k in keys:
            kl = k.lower()
            if kl in wl:
                score += len(k)
            elif wl in kl:
                score += len(w)
        if score > 0:
            scored.append((score, w))
    scored.sort(key=lambda x: -x[0])
    return [w for _, w in scored[:HOT_WORDS_MATCH]], pool


def _hot_pool_info():
    """网站管理页展示的热词状态：条数/抓取时间/示例。"""
    pool = _fetch_hot_pool()
    return {"count": len(pool), "fetchedAt": _HOT_CACHE["ts"],
            "words": pool[:10]}


def _fetch_meme_image():
    """抓取随机表情包并下载到本地（FILES/cmt_<hex>.<ext>，与评论图片同规范）。

    返回本地文件名，前端经 /api/comment-image/{name} 展示；任何失败返回 None（静默降级，评论仍正常发布）。
    """
    try:
        req = urllib.request.Request(MEME_ENDPOINT, headers={
            "User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=MEME_TIMEOUT) as r:
            d = json.loads(r.read().decode("utf-8"))
        url = (d.get("url") or "").strip()
        if not url or d.get("nsfw"):
            return None
        req2 = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req2, timeout=MEME_TIMEOUT) as r:
            data = r.read(MEME_MAX_BYTES + 1)
        if not data or len(data) > MEME_MAX_BYTES:
            return None
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            ext = ""
        if ext:
            if not image_magic_ok(data, ext):
                return None
        else:
            ext = next((cand for cand in (".png", ".jpg", ".webp")
                        if image_magic_ok(data, cand)), "")
            if not ext:
                return None
        stored = "cmt_" + uuid.uuid4().hex + ext
        with open(os.path.join(FILES, stored), "wb") as f:
            f.write(data)
        return stored
    except Exception:
        return None


# ---------------- AI 主动乐评（定时自动评论 + 后台管理） ----------------

_AI_WORKER_STARTED = False


def _ai_auto_settings():
    """合并 网站管理页动态配置（ai_comment_cfg 表，优先）+ config.py 默认值。"""
    cfg = {}
    try:
        c = db()
        try:
            for k, v in c.execute("SELECT key, value FROM ai_comment_cfg").fetchall():
                cfg[k] = v
        finally:
            c.close()
    except Exception:
        cfg = {}

    def _int(name, default):
        try:
            return int(cfg.get(name) or default)
        except Exception:
            return default

    return {
        "enabled": cfg.get("enabled", "1") != "0",
        "interval": max(60, _int("interval", AI_AUTO_INTERVAL)),
        "count": max(1, min(10, _int("count", AI_AUTO_COUNT))),
        "max_per_track": max(1, _int("max_per_track", AI_AUTO_MAX_PER_TRACK)),
        "skip_days": max(1, _int("skip_days", AI_AUTO_SKIP_DAYS)),
    }


def _ai_save_config(base=None, key=None, model=None):
    """保存 AI 接口接入配置到 ai_config.json（权限 600）。空值表示保持原值。"""
    cfg = _ai_cfg()
    if base is not None and str(base).strip():
        cfg["base"] = str(base).strip()
    if key is not None and str(key).strip():
        cfg["key"] = str(key).strip()
    if model is not None and str(model).strip():
        cfg["model"] = str(model).strip()
    tmp = AI_CFG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, AI_CFG_FILE)
    try:
        os.chmod(AI_CFG_FILE, 0o600)
    except Exception:
        pass
    return cfg


def _ai_auto_run(run_type="auto", count=None, force=False):
    """主动乐评一轮：挑歌 → 生成 → 发布 → 通知 → 记日志。

    - enabled 关闭时跳过；后台手动触发可 force=True 强制执行。
    - 候选 = tracks(offline=0) + collected(play_url 非空)；
      排除：已有 AI 乐评 >= max_per_track 的歌、近 skip_days 已自动评过的歌。
    - 返回 {"ok":bool, "ran":n, "ok_count":n, "fail_count":n, "comments":[...]}。
    """
    cfg = _ai_auto_settings()
    if not cfg["enabled"] and not force:
        return {"ok": True, "ran": 0, "ok_count": 0, "fail_count": 0,
                "comments": [], "skipped": "disabled"}
    n = max(1, min(10, int(count or cfg["count"])))
    c = db()
    try:
        now = time.time()
        rows = []
        rows += c.execute("SELECT id,title,artist,album,genre FROM tracks WHERE offline=0").fetchall()
        rows += c.execute("SELECT id,title,artist,album,genre FROM collected_tracks WHERE play_url!=''").fetchall()
        if not rows:
            return {"ok": True, "ran": 0, "ok_count": 0, "fail_count": 0,
                    "comments": [], "skipped": "no_tracks"}
        # 近 skip_days 已自动评论过的歌（ok 记录）不再选
        skip_ts = now - cfg["skip_days"] * 86400
        recent = set(r[0] for r in c.execute(
            "SELECT track_id FROM ai_comment_logs WHERE status='ok' AND created>=?",
            (skip_ts,)).fetchall())
        # 已有 AI 乐评达到单曲上限的歌不再选
        full = {}
        for tid, cnt in c.execute(
                "SELECT track_id, COUNT(*) FROM comments WHERE user_id=? GROUP BY track_id",
                (_ai_uid(),)).fetchall():
            if cnt >= cfg["max_per_track"]:
                full[tid] = True
        cand = [r for r in rows if r[0] not in recent and r[0] not in full]
        random.shuffle(cand)
        pick = cand[:n]
        results = []
        for tid, title, artist, album, genre in pick:
            log_id = uuid.uuid4().hex
            text = _ai_generate_comment(title, artist, album, genre)
            if not text:
                c.execute("INSERT INTO ai_comment_logs(id,run_type,track_id,track_title,status,reason,created) "
                          "VALUES(?,?,?,?,?,?,?)",
                          (log_id, run_type, tid, title or "", "fail", "generate_failed", now))
                results.append({"trackId": tid, "trackTitle": title or "",
                                "status": "fail", "reason": "generate_failed"})
                continue
            cid = uuid.uuid4().hex
            ai_name = _ai_display_name()
            images = "[]"
            if random.random() * 100 < MEME_PROB_PCT:
                m = _fetch_meme_image()
                if m:
                    images = json.dumps([m])
            c.execute("""INSERT INTO comments(id,track_id,user_id,user_name,content,images,created,is_ai)
                         VALUES(?,?,?,?,?,?,?,1)""",
                      (cid, tid, _ai_uid(), ai_name, text, images, now))
            # 通知歌曲上传者（AI 评论了你的歌曲）
            owner_id, owner_title = _track_owner(c, tid)
            if owner_id:
                c.execute("""INSERT INTO notifications(id,user_id,type,title,content,from_user_id,from_user_name,track_id,track_title,is_read,created)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                          (uuid.uuid4().hex, owner_id, "comment", "收到 AI 乐评",
                           "%s 评论了你的歌曲《%s》" % (ai_name, owner_title or title or ""),
                           _ai_uid(), ai_name, tid, owner_title or title or "", 0, now))
            c.execute("INSERT INTO ai_comment_logs(id,run_type,track_id,track_title,status,comment_id,created) "
                      "VALUES(?,?,?,?,?,?,?)",
                      (log_id, run_type, tid, title or "", "ok", cid, now))
            results.append({"trackId": tid, "trackTitle": title or "",
                            "status": "ok", "commentId": cid})
        c.commit()
        return {"ok": True, "ran": len(pick),
                "ok_count": sum(1 for r in results if r["status"] == "ok"),
                "fail_count": sum(1 for r in results if r["status"] == "fail"),
                "comments": results}
    finally:
        c.close()


def _ai_comment_stats():
    """网站管理页展示的状态汇总。"""
    cfg = _ai_auto_settings()
    c = db()
    try:
        last = c.execute("SELECT created FROM ai_comment_logs ORDER BY created DESC LIMIT 1").fetchone()
        ok_total = c.execute("SELECT COUNT(*) FROM ai_comment_logs WHERE status='ok'").fetchone()[0]
        fail_total = c.execute("SELECT COUNT(*) FROM ai_comment_logs WHERE status='fail'").fetchone()[0]
        today = time.time() - 86400
        ok_today = c.execute(
            "SELECT COUNT(*) FROM ai_comment_logs WHERE status='ok' AND created>=?", (today,)).fetchone()[0]
        ai_total = c.execute("SELECT COUNT(*) FROM comments WHERE user_id=?",
                             (_ai_uid(),)).fetchone()[0]
    finally:
        c.close()
    hp = _hot_pool_info()
    return {"enabled": cfg["enabled"], "interval": cfg["interval"], "count": cfg["count"],
            "max_per_track": cfg["max_per_track"], "skip_days": cfg["skip_days"],
            "lastRun": last[0] if last else 0,
            "okTotal": ok_total, "failTotal": fail_total, "okToday": ok_today,
            "aiCommentTotal": ai_total, "keyConfigured": bool(_ai_cfg().get("key")),
            "hotPool": hp,
            "memeInfo": {"endpoint": MEME_ENDPOINT, "probPct": MEME_PROB_PCT}}


def _ai_auto_worker():
    """定时主动乐评 daemon 线程：按 interval 循环，任何异常都不中断。"""
    while True:
        try:
            _ai_auto_run("auto")
        except Exception:
            pass
        try:
            time.sleep(max(60, _ai_auto_settings()["interval"]))
        except Exception:
            time.sleep(3600)


def start_ai_worker():
    """服务入口调用：启动主动乐评后台线程（幂等，测试可重复调用）。"""
    global _AI_WORKER_STARTED
    if _AI_WORKER_STARTED:
        return
    _AI_WORKER_STARTED = True
    t = threading.Thread(target=_ai_auto_worker, daemon=True, name="ai-comment-auto")
    t.start()
