# -*- coding: utf-8 -*-
"""流萤音乐 · 后端模块化包（由线上 server.py 机械重放，函数体零改写）。"""
import os
import json
import ssl
import sqlite3
import time
import math
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
from server.rec_taxonomy import get_taste_index, split_artists, canon_genres

__all__ = ['_rec_age_weight', '_rec_next_seq', '_rec_pieces', '_rec_reason', '_rec_recent_pushed', '_rec_remember_pushed', '_rec_seq_lock', '_rec_seq_state', '_rec_sim', '_rec_title_key', '_rec_title_sim', '_fetch_weather', '_scene_for', 'build_recommendations', '_comment_wish_push', '_COMMENT_WISH_WINDOW',
           '_audio_pref_vec', '_audio_top', '_collab_top', '_i2i_top', '_fts_rows']

# ---------------- 后台动态推荐参数（ai_comment_cfg 表 rec_ 前缀覆盖 config.py 默认值，5s 缓存） ----------------
_REC_CFG_CACHE = {"t": 0.0, "kv": {}}


def _rec_cfg_map():
    now = time.time()
    if now - _REC_CFG_CACHE["t"] < 5:
        return _REC_CFG_CACHE["kv"]
    kv = {}
    try:
        c = db()
        try:
            for k, v in c.execute("SELECT key, value FROM ai_comment_cfg").fetchall():
                if k.startswith("rec_"):
                    kv[k] = v
        finally:
            c.close()
    except Exception:
        pass
    _REC_CFG_CACHE.update({"t": now, "kv": kv})
    return kv


def _rec_cfg_float(key, default):
    try:
        return float(_rec_cfg_map().get(key) or default)
    except Exception:
        return default


def _rec_cfg_int(key, default):
    try:
        return int(_rec_cfg_map().get(key) or default)
    except Exception:
        return default


def _rec_age_weight(age_days):
    """行为事件时间窗口权重（2026-11-01 用户口径）：
    最新 3 天内行为 = 最新口味（权重 0.7），3 天前~90 天内 = 过去口味（权重 0.2）；
    90 天前不参与（画像 SQL 已加 created>=now-90d 窗口）——
    推送以用户最新爱听为主，旧口味只作辅助，防陈年口味长期垄断。"""
    return 0.7 if age_days <= 3.0 else 0.2


_rec_seq_lock = threading.Lock()


_rec_seq_state = {}


def _rec_recent_pushed(viewer_id, now):
    """返回该用户 REC_PUSH_WINDOW 内已推送过的歌曲 id 集合。

    2026-11-05 修复：旧实现每次调用都先 DELETE 再 commit——这会把「读推荐」变成
    「写数据库」，一旦有其它写事务（批量打标/画像）持锁，整个 /api/recommend 直接
    500（线上实测 `database is locked`）。改为：读不加锁、清理按概率/时间片触发且
    全程 best-effort，读路径永远不因锁失败。
    """
    key = viewer_id or "anon"
    c = db()
    rows = []
    try:
        rows = c.execute(
            "SELECT track_id FROM recommend_push_history WHERE uid=? AND pushed_at > ?",
            (key, now - REC_PUSH_WINDOW)).fetchall()
    except Exception as e:
        log.info("rec_recent_pushed read skip: %s", e)
        return set()
    finally:
        c.close()
    # 过期清理：每 60 次调用才做一次，且吞掉异常（绝不影响推荐）
    if random.random() < (1.0 / 60.0):
        try:
            c2 = db()
            try:
                c2.execute("DELETE FROM recommend_push_history WHERE uid=? AND pushed_at <= ?",
                           (key, now - REC_PUSH_WINDOW))
                c2.commit()
            finally:
                c2.close()
        except Exception:
            pass
    return {r[0] for r in rows}


def _rec_remember_pushed(viewer_id, track_ids, now):
    """记录一批刚刚推送的歌曲到数据库（覆盖时间戳），供窗口期内去重。
    顺带清理全表过期记录（按概率触发，避免每次推荐都做一次大表 DELETE）。"""
    if not track_ids:
        return
    key = viewer_id or "anon"
    c = db()
    try:
        if random.random() < 0.05:
            c.execute("DELETE FROM recommend_push_history WHERE pushed_at <= ?",
                      (now - REC_PUSH_WINDOW,))
        c.executemany(
            "INSERT OR REPLACE INTO recommend_push_history(uid, track_id, pushed_at)"
            " VALUES(?,?,?)",
            [(key, tid, now) for tid in track_ids])
        c.commit()
    except Exception as e:
        log.info("rec_remember_pushed skip: %s", e)
    finally:
        try:
            c.close()
        except Exception:
            pass


# ---------------- 重复曝光抑制（2026-11-05） ----------------
# 线上复现（真实用户 a334105a，连续 6 次拿推荐）：只返回 46 首不同歌曲，
# 相邻两批重叠 69%，其中 8 首每次都出现、第一首 6 次里 5 次相同——用户视角就是
# 「一直在那几首转」。根因不是去重没做，而是融合分排序是**确定性的**：
# 同一个用户的行为画像几小时才变一次，取 top-160 再 MMR 自然每次都挑同样 20 首。
# 修法：
#   ① 曝光历史降权：最近 N 天推过没点的歌 ×0.45，推过且点开过 ×0.85（不惩罚真喜欢的）；
#   ② 候选池改为 Plackett-Luce 加权抽样（Gumbel-top-k），分高的更容易被抽中但不是必中，
#      每批种子不同 → 批次之间自然换池，同时不丢相关性；
#   ③ 池容量从 160 提到 260，MMR 有更大空间做多样性。
_REC_SHOWN_CACHE = {}


def _rec_shown(c, viewer_id, now, days=None):
    """该用户近 N 天的曝光历史 → {tid: (最近曝光时间, 曝光次数)}（2 分钟缓存，best-effort）。"""
    if not viewer_id:
        return {}
    days = float(days or _rec_cfg_float("rec_shown_days", 10.0))
    hit = _REC_SHOWN_CACHE.get(viewer_id)
    if hit and now - hit[0] < 120.0:
        return hit[1]
    out = {}
    try:
        rows = c.execute(
            "SELECT track_id, MAX(created), COUNT(*) FROM recommend_impressions"
            " WHERE uid=? AND created>=? GROUP BY track_id",
            (viewer_id, now - days * 86400.0)).fetchall()
        for tid, ts, n in rows:
            if tid:
                out[tid] = (float(ts or 0.0), int(n or 1))
    except Exception:
        out = {}
    if len(_REC_SHOWN_CACHE) > 3000:
        _REC_SHOWN_CACHE.clear()
    _REC_SHOWN_CACHE[viewer_id] = (now, out)
    return out


def _rec_shown_weight(shown, tid, now):
    """曝光降权系数（2026-11-05）：越是被反复推、越久没人点，降得越狠。

    只按"最近一次时间"降权（×0.45）不够——被反复推的歌每一批都是 ×0.45，
    分数照样碾压新歌，用户依然"一直看到那几首"。这里把**曝光次数**也算进去：
      推过 1 次 → ×0.45（3 天内）/ ×0.65（10 天内）
      推过 2~3 次 → ×0.32 / ×0.45
      推过 4 次以上 → ×0.18 / ×0.30
    等于让"反复推不动的歌"主动退场，把位置让给没露过面的歌。
    """
    rec = shown.get(tid)
    if not rec:
        return 1.0
    ts, n = rec if isinstance(rec, tuple) else (rec, 1)
    age = now - float(ts or 0.0)
    recent = age <= 3 * 86400.0
    if n >= 4:
        return 0.18 if recent else 0.30
    if n >= 2:
        return 0.32 if recent else 0.45
    return 0.45 if recent else 0.65


def _rec_dislikes(c, viewer_id, now=None):
    """该用户的显式「不感兴趣」集合 → (tracks, artists, genres, moods)，60s 缓存。

    2026-11-05：之前只有隐式信号（跳过、完听率）推断喜好，"我明确不想听这个"
    从来没有被表达过。这里把负反馈拆成四种粒度：
      track  → 直接排除；
      artist → 该歌手所有歌降权（×0.25）；
      genre  → 该风格降权（×0.5）；
      mood/scene → 该情绪/场景降权（×0.55）。
    """
    if not viewer_id:
        return set(), set(), set(), set()
    hit = _REC_DISLIKE_CACHE.get(viewer_id)
    if hit and (now or time.time()) - hit[0] < 60.0:
        return hit[1]
    tr, ar, gr, mo = set(), set(), set(), set()
    try:
        for kind, value in c.execute(
                "SELECT kind, value FROM rec_dislikes WHERE uid=?", (viewer_id,)).fetchall():
            v = str(value or "").strip()
            if not v:
                continue
            if kind == "track":
                tr.add(v)
            elif kind == "artist":
                ar.add(v.lower())
            elif kind == "genre":
                gr.add(v.lower())
            else:
                mo.add(v)
    except Exception:
        pass
    out = (tr, ar, gr, mo)
    if len(_REC_DISLIKE_CACHE) > 3000:
        _REC_DISLIKE_CACHE.clear()
    _REC_DISLIKE_CACHE[viewer_id] = (time.time(), out)
    return out


_REC_DISLIKE_CACHE = {}


def _rec_pl_pool(items, rnd, n, gamma):
    """Plackett-Luce 加权抽样（Gumbel-top-k）：按 score^gamma 抽 n 个候选。

    score 相差大时高分项仍占优（gamma 越大越接近确定性 top-k），
    但每批抽样不同 → 覆盖率高、批次之间不重复；纯 top-k 会造成「永远是那几首」。
    """
    if len(items) <= n:
        return list(items)
    top = max(1e-9, max(it["score"] for _t, it in items))
    keys = []
    for tid, it in items:
        s = it["score"] / top
        if s <= 0:
            s = 1e-6
        try:
            u = rnd.random()
            g = -math.log(-math.log(max(u, 1e-12)))
        except ValueError:
            g = 0.0
        keys.append((gamma * math.log(s) + g, tid, it))
    keys.sort(key=lambda x: -x[0])
    return [(tid, it) for _k, tid, it in keys[:n]]


def _rec_next_seq(viewer_id, bucket, now, fresh=False):
    """返回 (seq, rnd)：时间桶 + 会话内批次轮换。

    同桶主批（seq=0）由 (user, bucket) 种子确定 → 可复现；
    会话内连续请求 seq 递增 → 每批结果不同（不固定同一批）；
    距上次请求超时 / 跨桶 / fresh 会话 → seq 归零（回到该桶稳定主批）。
    """
    key = "%s|%d" % (viewer_id or "anon", bucket)
    with _rec_seq_lock:
        if len(_rec_seq_state) > 5000:
            for k, v in list(_rec_seq_state.items()):
                if now - v[0] > max(REC_SESSION_IDLE * 4, 3600):
                    _rec_seq_state.pop(k, None)
        st = _rec_seq_state.get(key)
        if fresh or st is None or now - st[0] > REC_SESSION_IDLE:
            # 冷启动/下拉刷新：按当前分钟轮换批次起点，避免每次打开 App 第一首都是同一首
            seq = int(now // 60) % 6
        else:
            seq = st[1] + 1
        _rec_seq_state[key] = [now, seq]
    rnd = random.Random("%s|%d|%d" % (viewer_id or "anon", bucket, seq))
    return seq, rnd


def _rec_pieces(s):
    """歌名/专辑关键词片段（去括号、按常见分隔符切分）"""
    s = re.sub(r"[（(【\[].*?[)）】\]]", "", s or "")
    return [p.strip() for p in re.split(r"[·\-–/、，,]", s) if len(p.strip()) >= 2]


def _rec_sim(a, b):
    """两首歌的相似度 0..1（MMR 多样性用）：
    歌手一致 0.7，专辑一致 0.3，歌名关键词有重合 0.2"""
    sim = 0.0
    if a["artist"] and a["artist"] == b["artist"]:
        sim += 0.7
    if a["album"] and a["album"] == b["album"]:
        sim += 0.3
    ka, kb = set(_rec_pieces(a["title"])), set(_rec_pieces(b["title"]))
    if ka and kb and (ka & kb):
        sim += 0.2
    return min(sim, 1.0)


def _rec_title_key(title):
    """歌名相似度比较用的规范化键：
    转小写、去空格、去括号及括号内容（(Live)/(伴奏)/remix 等版本后缀），
    让《晴天》与《晴天 (Live)》规整为同一键，按相似度拦截。"""
    t = (title or "").strip().lower()
    t = re.sub(r"[（(][^）)]*[)）]", "", t)
    return re.sub(r"\s+", "", t)


def _rec_title_sim(a, b):
    """两首歌「歌名相似度」0..1（规范化后 bigram Dice 系数）。
    空歌名不参与歌名相似约束（返回 0），避免空串误伤。
    2026-11-05：从 difflib.SequenceMatcher 换成 bigram Dice——MMR 单次要做上千次比较，
    实测 SequenceMatcher 占整次推荐 0.16s（35%），Dice 精度等价且快 8~10 倍；
    先做长度下界剪枝（Dice 上限 = 2·min/(min+max)）。"""
    ka = a.get("_tk")
    if ka is None:
        ka = _rec_title_key(a.get("title") or "")
        a["_tk"] = ka
    kb = b.get("_tk")
    if kb is None:
        kb = _rec_title_key(b.get("title") or "")
        b["_tk"] = kb
    if not ka or not kb:
        return 0.0
    lo, hi = (len(ka), len(kb)) if len(ka) <= len(kb) else (len(kb), len(ka))
    if hi <= 1:
        return 1.0 if ka == kb else 0.0
    if 2.0 * lo / float(lo + hi) < REC_TITLE_SIM_THRESHOLD:
        return 0.0
    ba = a.get("_bg")
    if ba is None:
        ba = {ka[i:i + 2] for i in range(len(ka) - 1)}
        a["_bg"] = ba
    bb = b.get("_bg")
    if bb is None:
        bb = {kb[i:i + 2] for i in range(len(kb) - 1)}
        b["_bg"] = bb
    if not ba or not bb:
        return 0.0
    inter = len(ba & bb)
    return 2.0 * inter / float(len(ba) + len(bb))


# 2026-11-05：版本/翻制类标记——线上实测音频相似路会大量召回「DJ版/Remix/翻唱/伴奏/片段」
# 等同曲异版，用户感知为"推来推去都是这些"。不硬删（有人就爱听 DJ 版），只降权 + 批次限额。
_REC_VERSION_RE = re.compile(
    r"(dj\s*版|dj版|remix|re-mix|混音|重混|翻唱|cover|伴奏|instrumental|片段|试听|铃声|"
    r"加速|减速|降速|慢速|升调|降调|0\.8x|1\.2x|1\.5x|女版|男版|烟嗓|童声|完整版|"
    r"live\s*版|现场版|抖音版|热搜版|热播版)", re.IGNORECASE)


def _rec_versioned(title):
    """歌名是否带「同曲异版」标记（DJ版/Remix/翻唱…）。"""
    return bool(_REC_VERSION_RE.search(title or ""))


def _rec_reason(sources):
    """从召回来源集合推导推荐理由标签：按「可解释性」优先级取一路——
    为你精推（音频特征/协同过滤）> 常听歌手 > 同风格 > 相似歌曲 > 应景推荐 > 新歌 > 热门 > 为你探索
    （不用权重：行为相似路常因歌手关键词命中而覆盖歌手路，权重优先会让"常听歌手"几乎不出现）"""
    _labels = {"artist": "常听歌手", "genre": "同风格", "behavior": "相似歌曲",
               "audio_taste": "相似听感", "tag_taste": "契合心情", "audio_multi": "为你精推",
               "time_taste": "此刻爱听",
               "scene": "应景推荐", "fresh": "新歌", "hot": "热门", "explore": "为你探索",
               "precise_audio": "为你精推", "precise_collab": "为你精推", "precise_i2i": "为你精推"}
    for s in ("precise_audio", "precise_collab", "precise_i2i", "artist", "genre",
              "behavior", "audio_taste", "tag_taste", "time_taste", "audio_multi",
              "scene", "fresh", "hot", "explore"):
        if s in sources:
            return _labels[s]
    return "推荐"


def _route_quality(c, viewer_id, now):
    """按「推荐来源路」的近期完听率自调权（2026-11-05，落地此前的 P1 归因闭环承诺）。

    数据来源：listen 事件的 meta.route（客户端上报的推荐来源路）+ meta.fraction。
    对每条路算完听率，向平台先验 0.45 做拉普拉斯平滑，再线性映射到 0.85~1.15：
    完听率高的路权重上调、被反复跳过的路下调；样本 < 30 的路保持 1.0。
    """
    if not viewer_id:
        return {}
    # 5 分钟缓存：该查询要扫 5 万+ 行为行并 json_extract，不能每个请求都算
    _hit = _ROUTE_Q_CACHE.get(viewer_id)
    if _hit and now - _hit[0] < 300.0:
        return _hit[1]
    try:
        rows = c.execute(
            "SELECT json_extract(meta,'$.route') AS r,"
            " AVG(CAST(json_extract(meta,'$.fraction') AS REAL)) AS f, COUNT(*) AS n"
            " FROM behavior_events WHERE user_id=? AND event='listen' AND created>=?"
            " AND COALESCE(json_extract(meta,'$.route'),'')!=''"
            " GROUP BY 1", (viewer_id, now - 90 * 86400.0)).fetchall()
    except Exception:
        return {}
    prior, out = 0.45, {}
    # 客户端的 route 是「来源路逗号拼接」（如 "artist,precise_i2i"）：按单个来源路归并统计，
    # 否则组合串永远匹配不到 _wmap 里的单路 key。
    agg = {}
    for route, avg_f, n in rows:
        if not n:
            continue
        try:
            avg = float(avg_f)
        except (TypeError, ValueError):
            continue
        for src in str(route or "").split(","):
            src = src.strip()
            if not src:
                continue
            a = agg.setdefault(src, [0.0, 0])
            a[0] += avg * n
            a[1] += n
    for src, (s, n) in agg.items():
        if n < 30:
            continue
        smoothed = (s + prior * 50.0) / (n + 50.0)   # 先验权重 50 次
        out[src] = 1.0 + max(-0.15, min(0.15, (smoothed - prior) * 0.6))
    _ROUTE_Q_CACHE[viewer_id] = (now, out)
    return out



# ---------------- 精准推荐路（2026-10-24）：音频内容特征 + 协同过滤 ----------------
# 与文本路（歌名/歌手/专辑/标签关键词、风格、常听歌手、热度、新歌、探索、场景）搭配融合，
# 不替代任何现有路。audio_features 表由 server/feature_extract.py 离线填充；
# collab_recs 表由 server/train_collab.py 离线训练填充。

_AUDIO_MAT_CACHE = {"t": 0.0, "ids": [], "mat": None}
_LIBROSA_MAT_CACHE = {"t": 0.0, "ids": [], "mat": None}
# 归因自调权缓存（uid -> (ts, {route: mult})）：5 分钟，避免每请求扫 5 万行为行
_ROUTE_Q_CACHE = {}


def _load_librosa_mat(c):
    """全库 librosa 特征向量矩阵（raw_vec 应为 20 维，行归一化），30s 缓存。
    raw_vec 维度必须 == REC_LIBROSA_DIM 才视为 librosa 数据（MERT 覆盖期维度为 768，
    维度不符 → (None, None)，audio 路退回纯 MERT，librosa 筛选静默跳过）。"""
    now = time.time()
    if now - _LIBROSA_MAT_CACHE["t"] < 300.0 and _LIBROSA_MAT_CACHE["mat"] is not None:
        return _LIBROSA_MAT_CACHE["ids"], _LIBROSA_MAT_CACHE["mat"]
    try:
        import numpy as np
    except ImportError:
        return None, None
    try:
        rows = c.execute(
            "SELECT track_id, raw_vec FROM audio_features"
            " WHERE raw_vec IS NOT NULL AND raw_vec!=''").fetchall()
    except Exception:
        return None, None
    ids, vecs = [], []
    for tid, v in rows:
        try:
            arr = json.loads(v)
            if arr and len(arr) == REC_LIBROSA_DIM:
                a = np.array(arr, dtype=float)
                n = np.linalg.norm(a)
                if n > 1e-9:
                    vecs.append(a / n)
                    ids.append(tid)
        except Exception:
            pass
    mat = np.array(vecs, dtype=float) if len(vecs) >= 10 else None
    _LIBROSA_MAT_CACHE.update({"t": now, "ids": ids, "mat": mat})
    return ids, mat


def _load_audio_mat(c):
    """全库音频特征向量矩阵（id 列表 + 行归一化 np 矩阵），30s 缓存。
    numpy 缺失/无特征数据 → 返回 (None, None)，音频路静默跳过。"""
    now = time.time()
    if now - _AUDIO_MAT_CACHE["t"] < 300.0 and _AUDIO_MAT_CACHE["mat"] is not None:
        return _AUDIO_MAT_CACHE["ids"], _AUDIO_MAT_CACHE["mat"]
    try:
        import numpy as np
    except ImportError:
        return None, None
    try:
        rows = c.execute("SELECT track_id, vec FROM audio_features WHERE vec IS NOT NULL AND vec!=''").fetchall()
    except Exception:
        return None, None
    ids, vecs = [], []
    for tid, v in rows:
        try:
            arr = json.loads(v)
            if arr and len(arr) >= 64:   # vec 只存 MERT 768 维；≥64 防旧 20 维 librosa 混入矩阵
                a = np.array(arr, dtype=float)
                n = np.linalg.norm(a)
                if n > 1e-9:
                    vecs.append(a / n)
                    ids.append(tid)
        except Exception:
            pass
    mat = np.array(vecs, dtype=float) if len(vecs) >= 10 else None
    _AUDIO_MAT_CACHE.update({"t": now, "ids": ids, "mat": mat})
    return ids, mat


def _audio_pref_vec(c, viewer_id, base):
    """用户音频偏好（2026-11-01 双数据）：
    近期高分行为歌（base 得分 top N，base 已含 70/30 两段时间加权 + 爱听歌加分）
    的 MERT vec 加权均值（主，余弦打分用）+ librosa raw_vec 加权均值（筛选用）。
    返回 (mert_pref, librosa_pref)；无行为/无特征 → (None, None)（音频路跳过）。"""
    if not viewer_id or not base:
        return None, None
    top = sorted(base.items(), key=lambda kv: -kv[1])[:REC_PREF_TOP]
    mvecs, lvecs, wsum = [], [], 0.0
    for tid, sc in top:
        try:
            r = c.execute("SELECT vec, raw_vec FROM audio_features WHERE track_id=?", (tid,)).fetchone()
        except Exception:
            continue
        if not r:
            continue
        wsum += sc
        try:
            import numpy as np
            if r[0]:
                arr = json.loads(r[0])
                if arr and len(arr) >= 64:   # MERT 768 维（防旧 20 维混入）
                    a = np.array(arr, dtype=float)
                    n = np.linalg.norm(a)
                    if n > 1e-9:
                        mvecs.append((a / n, sc))
            if r[1]:
                larr = json.loads(r[1])
                if larr and len(larr) == REC_LIBROSA_DIM:
                    la = np.array(larr, dtype=float)
                    ln = np.linalg.norm(la)
                    if ln > 1e-9:
                        lvecs.append((la / ln, sc))
        except Exception:
            continue
    if not mvecs or wsum <= 0:
        return None, None
    try:
        import numpy as np
    except ImportError:
        return None, None
    pref = np.zeros(len(mvecs[0][0]), dtype=float)
    for arr, sc in mvecs:
        pref += arr * sc
    pref /= wsum
    n = np.linalg.norm(pref)
    if n < 1e-9:
        return None, None
    mpref = pref / n
    lpref = None
    if lvecs:
        lp = np.zeros(len(lvecs[0][0]), dtype=float)
        lw = sum(w for _, w in lvecs)
        for arr, sc in lvecs:
            lp += arr * sc
        lp /= lw
        ln = np.linalg.norm(lp)
        if ln > 1e-9:
            lpref = lp / ln
    return mpref, lpref


def _audio_top(c, viewer_id, base, exclude_self, pushed, k=REC_AUDIO_TOP):
    """音频内容相似召回路（2026-11-01 双数据）：
    MERT 语义余弦排序为主；候选歌的 librosa 20 维向量与用户 librosa 偏好的余弦
    < REC_LIBROSA_FILTER（节奏/能量/音色差异过大）→ 剔除，直到凑满 k。
    librosa 数据缺失/维度不符 → 纯 MERT，筛选静默跳过。
    返回 [(tid, cos), ...]（已排除本人上传/已听过/3 小时内已推）。"""
    ids, mat = _load_audio_mat(c)
    if mat is None:
        return []
    mpref, lpref = _audio_pref_vec(c, viewer_id, base)
    if mpref is None:
        return []
    lids, lmat = _load_librosa_mat(c)
    lmap = {}
    if lmat is not None and lids:
        lmap = {tid: lmat[i] for i, tid in enumerate(lids)}
    import numpy as np
    dots = mat.dot(mpref)
    order = np.argsort(-dots)
    out = []
    for i in order:
        tid = ids[i]
        if tid in exclude_self or (pushed and tid in pushed):
            continue
        if lpref is not None and lmap:
            lv = lmap.get(tid)
            if lv is not None and float(lv.dot(lpref)) < REC_LIBROSA_FILTER:
                continue        # 节奏/能量/音色差异过大 → 剔除（无 librosa 数据则放行）
        out.append((tid, float(dots[i])))
        if len(out) >= k:
            break
    return out


def _audio_multi_top(c, viewer_id, base, exclude_self, pushed, k=3, per_center=40):
    """多兴趣音频召回（MIND-lite，2026-11-05）。

    旧 `_audio_pref_vec` 把用户高分歌的 MERT 向量平均成**一个**偏好向量：
    同时爱听「DJ 电子」和「伤感民谣」的用户，平均后两边都不像（这是多口味用户
    "推荐不像我"的最大精度损失点）。这里改为加权 k-means 得到 k 个兴趣簇，
    每簇单独召回 top 歌，再按簇的质量（行为分之和）加权融合。
    返回 [(tid, score), ...]（已排除本人上传/听过/已推）。
    """
    if not viewer_id or not base:
        return []
    try:
        import numpy as np
    except ImportError:
        return []
    ids, mat = _load_audio_mat(c)
    if ids is None or mat is None:
        return []
    idx_map = {tid: i for i, tid in enumerate(ids)}
    seeds = []
    for tid, sc in sorted(base.items(), key=lambda kv: -kv[1])[:60]:
        i = idx_map.get(tid)
        if i is not None:
            seeds.append((i, max(float(sc), 0.1)))
    if len(seeds) < 6:
        return []
    X = mat[[i for i, _ in seeds]]
    w = np.array([s for _, s in seeds], dtype=float)
    k = max(1, min(int(k), len(seeds) // 4))

    # 确定性 k-means++：第一个中心取权重最大的歌，之后取「距离 × 权重」最大者
    centers = [X[int(np.argmax(w))]]
    for _ in range(1, k):
        d = np.min(np.stack([1.0 - (X @ c) for c in centers]), axis=0)
        centers.append(X[int(np.argmax(d * w))])
    C = np.stack(centers)
    for _ in range(8):   # Lloyd 迭代（固定 8 次，保证确定性与低开销）
        assign = np.argmax(X @ C.T, axis=1)
        for j in range(k):
            m = assign == j
            if not m.any():
                continue
            v = (X[m] * w[m, None]).sum(axis=0)
            n = float(np.linalg.norm(v))
            if n > 1e-9:
                C[j] = v / n

    assign = np.argmax(X @ C.T, axis=1)
    out = {}
    for j in range(k):
        m = assign == j
        mass = float(w[m].sum())
        if mass <= 0:
            continue
        sims = mat @ C[j]
        order = np.argsort(-sims)[:per_center]
        for r, i in enumerate(order):
            tid = ids[i]
            if tid in exclude_self or (pushed and tid in pushed):
                continue
            val = mass * max(0.0, 1.0 - r / float(per_center))
            if val > out.get(tid, 0.0):
                out[tid] = val
    return sorted(out.items(), key=lambda kv: -kv[1])[:120]


def _collab_top(c, viewer_id, exclude_self, pushed, k=REC_COLLAB_TOP):
    """协同过滤召回路：collab_recs 表（implicit ALS 训练结果）取该用户 topk。
    无训练数据/无该用户 → []。"""
    if not viewer_id:
        return []
    try:
        rows = c.execute(
            "SELECT track_id, score FROM collab_recs WHERE user_id=? AND score > -1e17 ORDER BY rank ASC LIMIT ?",
            (viewer_id, k)).fetchall()
    except Exception:
        return []
    out = []
    for tid, score in rows:
        if tid in exclude_self or (pushed and tid in pushed):
            continue
        out.append((tid, float(score or 0)))
    return out


def _i2i_top(c, viewer_id, base, exclude_self, pushed, k=REC_LIMITS["i2i"]):
    """i2i 关联召回路（2026-10-24 优化，Spotify 协同 encoder 轻量版）：
    用户常听 topN 歌 → item_sims 表取每首音频特征最相似的歌（离线余弦生成），
    返回 [(tid, 种子偏好×相似排名分)]。无 item_sims 数据 → []。"""
    if not viewer_id or not base:
        return []
    seeds = sorted(base.items(), key=lambda kv: -kv[1])[:REC_I2I_SEED]
    out, seen = [], set()
    try:
        for tid, sc in seeds:
            rows = c.execute(
                "SELECT sim_track_id, rank FROM item_sims WHERE track_id=? ORDER BY rank ASC LIMIT ?",
                (tid, REC_I2I_PER)).fetchall()
            for stid, rank in rows:
                if stid in seen or stid in exclude_self or (pushed and stid in pushed):
                    continue
                seen.add(stid)
                sim_score = max(0.0, 1.0 - int(rank or 0) / float(REC_I2I_PER))
                out.append((stid, float(sc or 0) * sim_score))
                if len(out) >= k:
                    return out
    except Exception:
        return []
    return out



_VEC_MAT_CACHE = {"t": 0.0, "ids": [], "mat": None}


def _load_vec_mat(c):
    """全库语义向量矩阵（song_vec 生成的 948 维融合向量），300s 缓存。

    返回 (ids, mat)。numpy 缺失 / track_emb 为空 → (None, None)，语义路静默跳过。
    """
    now = time.time()
    if now - _VEC_MAT_CACHE["t"] < 300.0 and _VEC_MAT_CACHE["mat"] is not None:
        return _VEC_MAT_CACHE["ids"], _VEC_MAT_CACHE["mat"]
    try:
        import numpy as np
    except ImportError:
        return None, None
    ids, vecs, dim = [], [], 0
    try:
        rows = c.execute("SELECT track_id, fuse, dim FROM track_emb").fetchall()
    except Exception:
        return None, None
    if rows:
        dim = int(rows[0][2] or 0)
    for tid, blob, _d in rows:
        try:
            v = np.frombuffer(blob, dtype=np.float32)
            if dim and v.size == dim:
                ids.append(tid)
                vecs.append(v)
        except Exception:
            pass
    if len(vecs) < 50:
        return None, None
    mat = np.vstack(vecs)
    _VEC_MAT_CACHE.update({"t": now, "ids": ids, "mat": mat})
    return ids, mat


def _vec_pref_vec(c, viewer_id, base, ids, mat):
    """用户"口味向量"：近期高分歌的语义向量加权均值（权重 = 行为分）。"""
    if not viewer_id or not base or mat is None:
        return None
    try:
        import numpy as np
    except ImportError:
        return None
    pos = {tid: i for i, tid in enumerate(ids)}
    acc, wsum = None, 0.0
    for tid, sc in sorted(base.items(), key=lambda kv: -kv[1])[:40]:
        i = pos.get(tid)
        if i is None:
            continue
        w = max(float(sc), 0.05)
        acc = mat[i] * w if acc is None else acc + mat[i] * w
        wsum += w
    if acc is None or wsum <= 0:
        return None
    v = acc / wsum
    nv = float(np.linalg.norm(v))
    return (v / nv) if nv > 1e-9 else None


def _semantic_top(c, viewer_id, base, exclude_self, pushed, k=80):
    """语义向量召回路：用户口味向量 × 全库余弦。"""
    ids, mat = _load_vec_mat(c)
    if mat is None:
        return []
    pref = _vec_pref_vec(c, viewer_id, base, ids, mat)
    if pref is None:
        return []
    try:
        import numpy as np
    except ImportError:
        return []
    dots = mat.dot(pref)
    order = np.argsort(-dots)
    out = []
    for i in order:
        tid = ids[i]
        if tid in exclude_self or (pushed and tid in pushed):
            continue
        out.append((tid, float(dots[i])))
        if len(out) >= k:
            break
    return out


def _vec_i2i_top(c, viewer_id, base, exclude_self, pushed, k=60, seeds=6, per=12):
    """语义邻居召回路：常听歌 → track_neighbors（跨歌手"听起来像"）→ 候选。

    score = 种子行为分 × 邻居相似度；同一候选取最高分（多首种子都指向它 → 更强）。
    """
    if not viewer_id or not base:
        return []
    out = {}
    try:
        for tid, sc in sorted(base.items(), key=lambda kv: -kv[1])[:seeds]:
            rows = c.execute(
                "SELECT sim_track_id, score FROM track_neighbors"
                " WHERE track_id=? AND src='vec' ORDER BY rank ASC LIMIT ?",
                (tid, per)).fetchall()
            for stid, sim in rows:
                if stid in exclude_self or (pushed and stid in pushed):
                    continue
                val = float(sc) * float(sim or 0.0)
                if val > out.get(stid, 0.0):
                    out[stid] = val
    except Exception:
        return []
    return sorted(out.items(), key=lambda kv: -kv[1])[:k]


def _fts_rows(c, kw, limit):
    """FTS5 trigram 子串检索（kw≥3 字符）→ 完整行 (id,title,artist,album,genre)。
    trigram 索引对 3+ 字符子串可用；kw<3 或 FTS 不可用时回退 None（调用方走 LIKE）。"""
    kw = (kw or "").strip().replace('"', '')
    if len(kw) < 3:
        return None
    try:
        q = '"%s"' % kw
        trows = c.execute(
            "SELECT id FROM tracks_fts WHERE tracks_fts MATCH ? LIMIT ?", (q, limit)).fetchall()
        crows = c.execute(
            "SELECT id FROM collected_tracks_fts WHERE collected_tracks_fts MATCH ? LIMIT ?",
            (q, limit)).fetchall()
        ids = [r[0] for r in trows] + [r[0] for r in crows]
        if not ids:
            return []
        ph = ",".join("?" * len(ids))
        rows = c.execute(
            "SELECT id,title,artist,album,COALESCE(genre,'') FROM tracks WHERE id IN (%s)" % ph,
            ids).fetchall()
        rows += c.execute(
            "SELECT id,title,artist,album,COALESCE(genre,'') FROM collected_tracks WHERE id IN (%s)" % ph,
            ids).fetchall()
        order = {i: n for n, i in enumerate(ids)}
        rows.sort(key=lambda r: order.get(r[0], 999))
        return rows
    except Exception:
        return None


# ---------------- M2：天气×时段心情场景（个性化×场景双向融合） ----------------

_WEATHER_CACHE = {}
_WEATHER_CACHE_LOCK = threading.Lock()


def _fetch_weather(lat, lng):
    """调 Open-Meteo 取当前天气（TTL 缓存 WEATHER_TTL 秒）。
    失败/参数无效返回 None，绝不抛异常（前端静默降级，推荐照常）。"""
    if lat is None or lng is None:
        return None
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    key = "%.3f,%.3f" % (lat, lng)
    now = time.time()
    with _WEATHER_CACHE_LOCK:
        hit = _WEATHER_CACHE.get(key)
        if hit and now - hit[0] < WEATHER_TTL:
            return hit[1]
        if len(_WEATHER_CACHE) > 2000:
            _WEATHER_CACHE.clear()
    try:
        url = ("%s?latitude=%.4f&longitude=%.4f&current=temperature_2m,weather_code"
               "&timezone=auto" % (WEATHER_ENDPOINT, lat, lng))
        req = urllib.request.Request(url, headers={"User-Agent": "LiuyingMusic/1.0"})
        with urllib.request.urlopen(req, timeout=WEATHER_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
        cur = (data or {}).get("current") or {}
        out = {"temp": cur.get("temperature_2m"), "code": cur.get("weather_code")}
        with _WEATHER_CACHE_LOCK:
            _WEATHER_CACHE[key] = (now, out)
        return out
    except Exception:
        return None


def _hour_band(hour):
    """晴天按时段选场景：清晨 / 午后 / 傍晚"""
    if 5 <= hour < 11:
        return "clear_day_morning"
    if 11 <= hour < 18:
        return "clear_day_afternoon"
    return "clear_day_evening"


def _day_part(ts=None):
    """时间戳 → 时段标签（深夜/清晨/午后/夜晚）。

    2026-11-05 时段画像用：统计「这个用户在该时段爱听什么」，
    与天气场景（外部条件）互补——时段是用户自己的作息规律。
    """
    try:
        h = time.localtime(float(ts)).tm_hour if ts is not None else time.localtime().tm_hour
    except (TypeError, ValueError, OSError):
        h = 12
    if h < 5:
        return "深夜"
    if h < 11:
        return "清晨"
    if h < 18:
        return "午后"
    return "夜晚"


_SCENES = {
    "rain":  {"key": "rain",  "label": "雨天治愈", "kw": ["雨", "下雨", "rain", "治愈", "温柔"]},
    "snow":  {"key": "snow",  "label": "冬日暖歌", "kw": ["雪", "冬天", "圣诞", "温暖"]},
    "hot":   {"key": "hot",   "label": "夏日清凉", "kw": ["夏天", "夏日", "清凉", "海风"]},
    "cold":  {"key": "cold",  "label": "冬日温暖", "kw": ["冷", "冬天", "温暖", "炉火"]},
    "clear_day_morning":   {"key": "clear_day_morning",   "label": "元气清晨", "kw": ["清晨", "早安", "morning", "元气", "阳光"]},
    "clear_day_afternoon": {"key": "clear_day_afternoon", "label": "午后阳光", "kw": ["午后", "下午", "阳光", "温柔", "咖啡"]},
    "clear_day_evening":   {"key": "clear_day_evening",   "label": "宁静傍晚", "kw": ["傍晚", "黄昏", "日落", "夕阳"]},
    "night": {"key": "night", "label": "深夜陪伴", "kw": ["深夜", "晚安", "陪伴", "安静", "星空"]},
    "cloudy": {"key": "cloudy", "label": "阴天舒缓", "kw": ["阴", "cloudy", "舒缓", "慢"]},
}


def _scene_for(code, temp, hour=None):
    """天气条件 × 时段 → 心情场景（返回 {key,label,kw} 副本）。
    深夜（22 点-5 点）优先"深夜陪伴"；无天气数据时按时段兜底；输入无效返回 None。"""
    hour = max(0, min(23, int(hour if hour is not None else time.localtime().tm_hour)))
    if hour >= 22 or hour < 5:
        return dict(_SCENES["night"])
    if code is None:
        return dict(_SCENES[_hour_band(hour)])
    try:
        code = int(code)
    except (TypeError, ValueError):
        return dict(_SCENES[_hour_band(hour)])
    if 51 <= code <= 67 or 80 <= code <= 82 or 95 <= code <= 99:   # 毛毛雨/雨/阵雨/雷暴
        return dict(_SCENES["rain"])
    if code in (71, 73, 75, 77, 85, 86):                            # 雪
        return dict(_SCENES["snow"])
    if temp is not None:
        try:
            temp = float(temp)
            if temp >= 30:
                return dict(_SCENES["hot"])
            if temp <= 5:
                return dict(_SCENES["cold"])
        except (TypeError, ValueError):
            pass
    if code <= 3:                                                   # 晴/少云/多云 → 按时段
        return dict(_SCENES[_hour_band(hour)])
    return dict(_SCENES["cloudy"])                                  # 阴/雾/其他 → 阴天舒缓


def build_recommendations(c, viewer_id, exclude=None, fresh=False, rnd=None, pushed=None, scene=None, precise=False):
    """多路召回 → 加权融合 → MMR 重排，返回推荐 (歌曲id, 推荐理由) 列表（≤20，去重、排除本人上传与 exclude）。

    exclude: 已推荐过的歌曲 id 集合（推荐流无限续推时防重复推荐同一批）。
    fresh: 长时间重开 App 的「新鲜会话」标记——加大随机扰动并提高探索路权重，
           让重新开始的一批推荐明显不同于上次（不固定重复同一批）。
    rnd: 批次随机源（random.Random 实例，种子=用户|时间桶|批次序号）——
         同一时间桶内的主批稳定可复现，会话内连续获取轮换不同批；
         不传时回退全局 random（兼容直接调用场景）。
    pushed: 3 小时内已推送过的歌曲 id 集合——本次推荐直接排除，不重复推送。
    scene: 天气×时段心情场景 {key,label,kw}（M2）——场景路召回 + 个性化×场景双向融合：
           应景歌吃吻合分（常听歌手/常听风格/行为关键词 ×REC_SCENE_PROFILE），
           已进候选的近期爱听歌若含场景关键词则加权前移（×REC_SCENE_BOOST）。
           None/无效时整路静默跳过（推荐照常，向后兼容）。
    viewer_id 为空/未登录时：无行为画像，走「热度 + 新歌 + 随机探索」三路兜底。
    """
    # 后台动态推荐参数（ai_comment_cfg 覆盖，5s 缓存；未配置用 config.py 默认值）
    _wb = _rec_cfg_float("rec_w_behavior", REC_WEIGHTS["behavior"])
    _wg = _rec_cfg_float("rec_w_genre", REC_WEIGHTS["genre"])
    _wa = _rec_cfg_float("rec_w_artist", REC_WEIGHTS["artist"])
    _ws = _rec_cfg_float("rec_w_scene", REC_WEIGHTS["scene"])
    _wh = _rec_cfg_float("rec_w_hot", REC_WEIGHTS["hot"])
    # 2026-10-24 优化：登录用户热度路降为兜底（×0.4）——热度只表达"大众存量"，个性化价值低，
    # 仅冷启动/未登录用户由热度主导；登录用户把位置让给个性化路
    _wh_eff = _wh * 0.4 if viewer_id else _wh
    _wf = _rec_cfg_float("rec_w_fresh", REC_WEIGHTS["fresh"])
    _we = _rec_cfg_float("rec_w_explore", REC_WEIGHTS["explore"])
    _sboost = _rec_cfg_float("rec_scene_boost", REC_SCENE_BOOST)
    _noise = _rec_cfg_float("rec_noise", REC_NOISE)
    _epool = _rec_cfg_int("rec_explore_pool", REC_EXPLORE_POOL)
    _mpa = _rec_cfg_int("rec_max_per_artist", REC_MAX_PER_ARTIST)
    _wtime = _rec_cfg_float("rec_w_time", REC_WEIGHTS.get("time_taste", 0.8))
    # 2026-10-24 主辅重构（会员分级）：普通用户只享受粗略算法（文本+热度+探索，
    # 精准三路 audio/collab/i2i 不启用）；VIP/SVIP 享受完整精准算法——
    # 精准路为主（权重上调 ×REC_PRECISE_VIP_BOOST），文本路保持降权后不再额外上调。
    _precise_on = bool(precise)
    if precise:
        _vip_boost = REC_PRECISE_VIP_BOOST
        _noise = _noise * 0.6
        _we = _we * 0.7
    else:
        _vip_boost = 1.0
    now = time.time()
    # 全维度歌曲理解索引（2026-11-05）：歌手 token / 规范化风格 / 听感桶倒排表 + id→meta，
    # 供「歌手召回、风格召回、听感召回、MMR 多样性」共用；构建失败返回 None 时全部回退旧逻辑。
    try:
        idx = get_taste_index(c)
    except Exception:
        idx = None
    # 推荐来源路的完听率自调权（P1 归因闭环：route → 完听率 → 权重系数）
    try:
        _rq = _route_quality(c, viewer_id, now)
    except Exception:
        _rq = {}
    # F9：探索路按时间桶做全池 8 桶轮转（同桶可复现、跨桶换池，解决探索池固定为"入库前 120 首"的盲区）
    _bucket = int(now // REC_BUCKET_SECONDS)
    rnd = rnd or random
    # 新鲜重开：随机扰动 ×3、探索路权重 ×2，结果带随机性又不脱离行为画像
    noise = _noise * 3 if fresh else _noise
    w_explore = _we * 2.0 if fresh else _we
    exclude_self = {r[0] for r in c.execute(
        "SELECT id FROM tracks WHERE user_id=?", (viewer_id or "",)).fetchall()}
    if exclude:
        exclude_self |= set(exclude)
    # 显式负反馈：拉黑的歌直接排除，拉黑的歌手/风格/情绪在打分阶段降权
    try:
        _dl_tracks, _dl_artists, _dl_genres, _dl_moods = _rec_dislikes(c, viewer_id, now)
    except Exception:
        _dl_tracks, _dl_artists, _dl_genres, _dl_moods = set(), set(), set(), set()
    if _dl_tracks:
        exclude_self |= _dl_tracks
    # 长期收听去重（2026-10-24 优化：两条 SQL 合并为一次查询，内存分层）：
    # ① 30 天内听完成 ≥ REC_REPLAY_FRACTION_MIN 的歌 → 长期不重推（听来听去都是那几首的根治）
    # ② 7 天内任意听过（未听完）→ 近期不回头（与①互补）
    # ③ 近 30 天听过但不到阈值的歌：记入 _heard，召回时降权（2026-11-05）
    _heard = set()
    if viewer_id:
        try:
            _hist = c.execute(
                "SELECT track_id, CAST(json_extract(meta,'$.fraction') AS REAL), created"
                " FROM behavior_events WHERE user_id=? AND event='listen' AND created >= ?",
                (viewer_id, now - REC_REPLAY_DAYS * 86400.0)).fetchall()
            _cut7 = now - 7 * 86400.0
            for tid, frac, created in _hist:
                if not tid:
                    continue
                _heard.add(tid)
                if (frac or 0) >= REC_REPLAY_FRACTION_MIN:
                    exclude_self.add(tid)
                elif (created or 0) >= _cut7:
                    exclude_self.add(tid)
        except Exception:
            pass

    # 候选池：tid -> {score, parts, sources, artist, album, title, genre}
    # parts: {source: 该路原始分数}——融合阶段各路内部 max 归一化 × 权重（量级统一）
    cand = {}

    def _pick(ids, head, tail):
        """倒排表召回采样：热度头部 + 随机尾部。
        旧实现直接取热度前 N 首 → 热门歌霸榜、命中大量"听过但没拉黑"的老歌；
        头部保证相关性，尾部随机保证新鲜度/长尾覆盖。"""
        ids = list(ids)
        if len(ids) <= head + tail:
            return ids
        return ids[:head] + rnd.sample(ids[head:], min(tail, len(ids) - head))

    def _add(tid, score, source, artist="", album="", title="", genre=""):
        if not tid or tid in exclude_self:
            return
        if pushed and tid in pushed:   # 3 小时内已推送过：跳过，不重复推
            return
        if tid in _heard:
            score *= 0.65   # 近 30 天听过：降权而不是排除（保留"想再听"的空间，但明显让位给新内容）
        if title and _rec_versioned(title):
            score *= 0.7    # 同曲异版（DJ版/Remix/翻唱…）：降权，避免版本洪流淹没原曲与新歌
        it = cand.setdefault(tid, {"score": 0.0, "parts": {}, "sources": set(),
                                   "artist": artist or "", "album": album or "",
                                   "title": title or "", "genre": genre or ""})
        it["score"] += score
        it["parts"][source] = it["parts"].get(source, 0.0) + score
        it["sources"].add(source)

    # ---------- 0) 行为画像：事件打分 + 高分歌 → 关键词 / 风格 / 常听歌手 ----------
    base = {}
    kw_list = []
    genre_set = {}
    artist_hist = {}
    # 2026-11-05 全维度画像：歌手 token（复合歌手拆分）、听感桶（mood|tempo）各维度亲和度
    artist_token_hist = {}
    bucket_hist = {}
    # LLM 语义标签画像（track_tags）：mood/scene 亲和度（比 4 类音频 mood 细得多）
    mood_hist = {}
    scene_hist = {}
    # 负反馈：被跳过的歌所属歌手 token → 降权（"不喜欢这个歌手"不该继续被推）
    artist_penalty = {}
    # 负反馈扩展（2026-11-05）：被跳过歌的 LLM 情绪/场景标签 → 同类降权（"不爱这种味道"）
    mood_penalty = {}
    scene_penalty = {}
    # 时段画像（2026-11-05）：每个时段（深夜/清晨/午后/夜晚）用户偏好的 mood/scene/听感桶
    # band_hist = {"夜晚": {"moods": {tag: w}, "scenes": {...}, "buckets": {...}}}
    band_hist = {}
    avoid = set()
    if viewer_id:
        evs = c.execute(
            "SELECT track_id,event,value,meta,created FROM behavior_events"
            " WHERE user_id=? AND created>=? ORDER BY created DESC LIMIT 800",
            (viewer_id, now - REC_PROFILE_WINDOW_DAYS * 86400.0)).fetchall()
        lst = {}
        for tid, event, value, meta, created in evs:
            age_days = max(now - float(created or now), 0) / 86400.0
            decay = _rec_age_weight(age_days)
            if event == "listen":
                try:
                    m = json.loads(meta or "{}")
                except Exception:
                    m = {}
                frac = float(m.get("fraction") or 0)
                try:
                    plays = int(float(m.get("plays") or 0))
                except (TypeError, ValueError):
                    plays = 0
                a = lst.setdefault(tid, {"sec": 0.0, "frac": 0.0, "plays": 0, "decay": decay})
                a["sec"] += max(float(value or 0), 0.0)
                a["frac"] = max(a["frac"], frac)
                a["plays"] = max(a["plays"], plays)
                a["decay"] = max(a["decay"], decay)
                # 时段画像：只在"真的听进去了"时计入（完听 ≥50%），避免一轮滑歌污染作息规律
                if idx is not None and frac >= 0.5:
                    _bm = idx.get(tid)
                    if _bm:
                        _bd = band_hist.setdefault(_day_part(created),
                                                   {"moods": {}, "scenes": {}, "buckets": {}})
                        for _t in (_bm.get("moods") or ()):
                            _bd["moods"][_t] = _bd["moods"].get(_t, 0.0) + decay
                        for _t in (_bm.get("scenes") or ()):
                            _bd["scenes"][_t] = _bd["scenes"].get(_t, 0.0) + decay
                        if _bm["bucket"]:
                            _bd["buckets"][_bm["bucket"]] = _bd["buckets"].get(_bm["bucket"], 0.0) + decay
            else:
                w = {"like": 3.0 if float(value or 0) > 0 else -1.0,
                     "comment_open": 0.4, "comment": 2.0, "share": 2.0,
                     "search": 1.0, "skip": -0.5}.get(event)
                if w is None:
                    continue
                base[tid] = base.get(tid, 0.0) + w * decay
                if event == "skip" and idx is not None:
                    # 跳过信号下沉到多个维度（单曲已被 -0.5 处理，这里额外扣维度亲和度）：
                    # 歌手 token / LLM 情绪标签 / 场景标签 —— 「这个歌手/这种味道，我不爱」
                    _sm = idx.get(tid)
                    if _sm:
                        for _tok in _sm["artists"]:
                            artist_penalty[_tok] = artist_penalty.get(_tok, 0.0) + 0.8 * decay
                        for _t in (_sm.get("moods") or ()):
                            mood_penalty[_t] = mood_penalty.get(_t, 0.0) + 0.5 * decay
                        for _t in (_sm.get("scenes") or ()):
                            scene_penalty[_t] = scene_penalty.get(_t, 0.0) + 0.5 * decay
        for tid, a in lst.items():
            if a["frac"] >= 0.5 or a["plays"] >= 1:
                w = 2.0 + min(a["sec"], 300.0) / 300.0
                # 爱听歌加分（2026-11-01 用户口径：爱听的歌推荐多一点）：反复听/高完听 各 +1.0（原 +0.5）
                if a["plays"] >= 2:
                    w += 1.0
                if a["frac"] >= 0.9:
                    w += 1.0
                base[tid] = base.get(tid, 0.0) + w * a["decay"]
        # 已强互动的歌曲不重复推荐（只用它提取特征）
        avoid = {tid for tid, sc in base.items() if sc >= 2.0}
        top = sorted(base.items(), key=lambda kv: -kv[1])[:12]
        for rank, (tid, sc) in enumerate(top):
            title = artist = album = genre = ""
            _m = idx.get(tid) if idx is not None else None
            if _m is not None:
                # 2026-11-05：优先走理解索引（一次建索引替代逐曲 3 条 SQL）
                title, artist, album, genre = _m["title"], _m["artist"], _m["album"], _m["genre"]
            else:
                # 回退旧路径：行为快照 → 上传表 → 采集表
                _ti = c.execute(
                    "SELECT track_info FROM behavior_events WHERE user_id=? AND track_id=?"
                    " AND track_info!='' ORDER BY created DESC LIMIT 1", (viewer_id, tid)).fetchone()
                if _ti and _ti[0]:
                    try:
                        _info = json.loads(_ti[0])
                        title = _info.get("title") or ""
                        artist = _info.get("artist") or ""
                        album = _info.get("album") or ""
                    except Exception:
                        pass
                if not title and not artist and not album:
                    row = c.execute("SELECT title,artist,album,genre FROM tracks WHERE id=?", (tid,)).fetchone()
                    if not row:
                        row = c.execute("SELECT title,artist,album,genre FROM collected_tracks WHERE id=?", (tid,)).fetchone()
                    if not row:
                        continue
                    title, artist, album, genre = row
            if rank < 6:
                # 关键词/常听歌手只取最像的前 6 首（避免长尾噪声），风格/听感用前 12 首
                for kw in (_rec_pieces(title)
                           + ([artist] if artist and len(artist) >= 2 else [])
                           + ([album] if album and len(album) >= 2 else [])):
                    if kw and kw not in ("未知歌手", "未命名"):
                        kw_list.append((kw, sc))
                if artist and len(artist) >= 2:
                    artist_hist[artist] = artist_hist.get(artist, 0.0) + sc
            # 歌手 token：复合歌手拆分后逐 token 累计亲和度（"A / B" 两个人都算）
            for _at in ((_m["artists"] if _m is not None else split_artists(artist)) or ()):
                artist_token_hist[_at] = artist_token_hist.get(_at, 0.0) + sc
            # 风格：规范化后累计（DJ/电子/舞曲 统一到 electronic，避免 LIKE 只命中一部分）
            for _gt in ((_m["genres"] if _m is not None else canon_genres(genre)) or ()):
                genre_set[_gt] = genre_set.get(_gt, 0.0) + sc
            # 听感桶：mood|tempo（有 audio_features 才计入）
            if _m is not None and _m["bucket"]:
                bucket_hist[_m["bucket"]] = bucket_hist.get(_m["bucket"], 0.0) + sc
            # LLM 语义 mood/scene（track_tags）：细粒度情绪与场景画像
            if _m is not None:
                for _mt in (_m.get("moods") or ()):
                    mood_hist[_mt] = mood_hist.get(_mt, 0.0) + sc
                for _st in (_m.get("scenes") or ()):
                    scene_hist[_st] = scene_hist.get(_st, 0.0) + sc
        # 用户主动搜索过的关键词也进入相似召回池
        skws = c.execute(
            "SELECT meta FROM behavior_events WHERE user_id=? AND event='search'"
            " ORDER BY created DESC LIMIT 50", (viewer_id,)).fetchall()
        for (meta_s,) in skws:
            try:
                _kw = str(json.loads(meta_s or "{}").get("kw") or "").strip()
            except Exception:
                _kw = ""
            if len(_kw) >= 2:
                kw_list.append((_kw, 1.0))

    seen_kw, dedup = set(), []
    for kw, sc in kw_list:
        k = kw.lower()
        if k in seen_kw:
            continue
        seen_kw.add(k)
        dedup.append((kw, sc))

    def _rows(where_sql, args, limit):
        """上传 + 采集两张表统一查候选（offline=0，排除本人上传）"""
        return c.execute(
            "SELECT id,title,artist,album,COALESCE(genre,'') FROM ("
            " SELECT id,title,artist,album,genre FROM tracks WHERE offline=0 AND user_id!=?"
            " UNION ALL"
            " SELECT id,title,artist,album,genre FROM collected_tracks WHERE offline=0 AND play_url!=''"
            ") WHERE " + where_sql + " LIMIT ?",
            ([viewer_id or ""] + list(args) + [limit])).fetchall()

    # ---------- 路 1：行为相似召回（关键词 title/album 匹配 + 风格精确匹配，排除已强互动） ----------
    # 2026-10-24 优化：①去掉 artist 字段 LIKE——常听歌手已由路 2 精确匹配专管，消除双路重复打分；
    #                  ②关键词 ≥3 字走 FTS5 trigram 索引（tracks_fts/collected_tracks_fts），
    #                    2 字词回退 LIKE（trigram 需 ≥3 字符），曲库扩容后文本检索不再全表扫。
    # 停用词/短词误伤率高：2字打4折、3字打7折，4字以上满分；纯停用词直接跳过
    _STOP_KWS = {"你", "我", "他", "她", "的", "了", "在", "是", "和", "就", "不", "人",
                 "这", "那", "一", "上", "下", "中", "里", "着", "也", "都", "要", "会",
                 "对", "与", "及", "或", "而", "从", "到", "为", "什么", "怎么", "我们",
                 "你们", "他们", "可以", "就是", "一个", "没有", "知道", "时候", "自己"}
    for kw, sc in dedup[:8]:
        if kw in _STOP_KWS or len(kw) < 2:
            continue
        kw_bonus = 0.4 if len(kw) <= 2 else (0.7 if len(kw) == 3 else 1.0)
        like = "%" + kw + "%"
        if len(kw) >= 3:
            rows = _fts_rows(c, kw, REC_LIMITS["behavior"])
            if rows is None:      # FTS 不可用 → 回退 LIKE
                rows = _rows("(title LIKE ? OR album LIKE ?)", [like, like],
                             REC_LIMITS["behavior"])
        else:
            rows = _rows("(title LIKE ? OR album LIKE ?)", [like, like],
                         REC_LIMITS["behavior"])
        for rid, rtitle, rartist, ralbum, rgenre in rows:
            if rid in avoid:
                continue
            _add(rid, sc * _wb * kw_bonus, "behavior",
                 rartist, ralbum, rtitle, rgenre)
    # 路 1b：同风格召回（2026-11-05 规范化 + 倒排索引）：
    # 旧实现按原始 genre 字符串 LIKE——"DJ,Others"/"电子/DJ" 只能命中一部分，且 30% 采集歌无标签；
    # 新实现把画像里的规范 tag（electronic/pop/…）直接查索引倒排表；索引不可用时回退旧 LIKE。
    for g, sc in sorted(genre_set.items(), key=lambda x: -x[1])[:3]:
        if idx is not None:
            _gids = idx.by_genre(g)
            if not _gids:
                continue
            for rid in _pick(_gids, 8, 16):
                if rid in avoid:
                    continue
                _m = idx.get(rid)
                if not _m:
                    continue
                _add(rid, sc * _wg, "genre",
                     _m["artist"], _m["album"], _m["title"], _m["genre"])
        else:
            like = "%" + g + "%"
            for rid, rtitle, rartist, ralbum, rgenre in _rows(
                    "genre LIKE ?", [like], REC_LIMITS["genre"]):
                if rid in avoid:
                    continue
                _add(rid, sc * _wg, "genre",
                     rartist, ralbum, rtitle, rgenre)

    # ---------- 路 2：常听歌手召回（2026-11-05 token 化 + 索引） ----------
    # 旧实现 `artist = ?` 精确匹配：线上 178 首是「A / B」「A & B」「A feat. B」复合歌手，
    # 直接漏召回（约占曲库 5%）。新实现按拆分后的歌手 token 查倒排表，
    # 复合歌手任一方都能命中，且不依赖字符串完全一致。
    _artist_scores = {_a: max(0.0, _sc - artist_penalty.get(_a, 0.0))
                      for _a, _sc in artist_token_hist.items()}
    for artist, sc in sorted(_artist_scores.items(), key=lambda x: -x[1])[:8]:
        if sc <= 0:
            continue
        if idx is not None:
            _aids = idx.by_artist(artist)
            if not _aids:
                continue
            for rid in _pick(_aids, 15, 8):
                if rid in avoid:
                    continue
                _m = idx.get(rid)
                if not _m:
                    continue
                _add(rid, sc * _wa, "artist",
                     _m["artist"], _m["album"], _m["title"], _m["genre"])
        else:
            for rid, rtitle, rartist, ralbum, rgenre in _rows(
                    "artist = ?", [artist], REC_LIMITS["artist"]):
                if rid in avoid:
                    continue
                _add(rid, sc * _wa, "artist",
                     rartist, ralbum, rtitle, rgenre)

    # ---------- 路 3：全局热度召回（大众口味兜底，权重较低） ----------
    hot = c.execute(
        "SELECT id,title,artist,album,COALESCE(genre,'') FROM ("
        " SELECT id,title,artist,album,genre,play_count,like_count,share_count FROM tracks"
        " WHERE offline=0 AND user_id!=?"
        " UNION ALL"
        " SELECT id,title,artist,album,genre,play_count,like_count,share_count FROM collected_tracks"
        " WHERE offline=0 AND play_url!=''"
        ") ORDER BY (play_count + like_count*3 + share_count*5) DESC LIMIT ?",
        (viewer_id or "", REC_HOT_TOP)).fetchall()
    for rank, (rid, rtitle, rartist, ralbum, rgenre) in enumerate(hot):
        if rank >= REC_LIMITS["hot"]:
            break
        if rid in avoid:
            continue
        _add(rid, max(0.0, 1.0 - rank / float(REC_HOT_TOP)) * _wh,
             "hot", rartist, ralbum, rtitle, rgenre)

    # ---------- 路 4：新歌新鲜度召回（最近 REC_FRESH_DAYS 天入库，权重中等） ----------
    fresh = c.execute(
        "SELECT id,title,artist,album,COALESCE(genre,'') FROM ("
        " SELECT id,title,artist,album,genre,created FROM tracks WHERE offline=0 AND user_id!=?"
        " UNION ALL"
        " SELECT id,title,artist,album,genre,created FROM collected_tracks WHERE offline=0 AND play_url!=''"
        ") WHERE created >= ? ORDER BY created DESC LIMIT ?",
        (viewer_id or "", now - REC_FRESH_DAYS * 86400.0, REC_LIMITS["fresh"])).fetchall()
    for rid, rtitle, rartist, ralbum, rgenre in fresh:
        if rid in avoid:
            continue
        _add(rid, _wf, "fresh", rartist, ralbum, rtitle, rgenre)

    # ---------- 路 5：随机探索召回（保证新内容被看到，权重最低） ----------
    # 候选池按批次种子洗牌截断（替代 SQL RANDOM()）：同批次可复现、不同批次轮换
    # F9：全池按 id 哈希 8 桶轮转抽样（同桶取同一池、跨桶换池），替代固定"入库顺序前 120 首"——
    # 旧实现对听歌多的用户等于"探索池 = 已听过池"（实测 114/120 已听过），未听过的新内容进不了候选。
    # 哈希注意：不能用 CAST(substr(id,1,8) AS INTEGER)（hex 含字母时 SQLite 返回 0 → 全落桶 0），
    # 用 unicode() 取 id 前两字符码点和 %8，实测 8 桶均匀（267-325）
    explore = c.execute(
        "SELECT id,title,artist,album,COALESCE(genre,'') FROM ("
        " SELECT id,title,artist,album,genre FROM tracks WHERE offline=0 AND user_id!=?"
        " UNION ALL"
        " SELECT id,title,artist,album,genre FROM collected_tracks WHERE offline=0 AND play_url!=''"
        ") WHERE (unicode(substr(id,1,1)) + unicode(substr(id,2,1))) % 8 = ? ORDER BY id LIMIT ?",
        (viewer_id or "", _bucket % 8, _epool)).fetchall()
    explore = list(explore)
    rnd.shuffle(explore)
    for rid, rtitle, rartist, ralbum, rgenre in explore[:REC_LIMITS["explore"]]:
        if rid in cand or rid in avoid:
            continue
        _add(rid, w_explore, "explore", rartist, ralbum, rtitle, rgenre)

    # ---------- 路 6：听感相似召回（2026-11-05，全用户可用，非 VIP 也有音频能力） ----------
    # 旧版音频特征（audio_features 的 mood/tempo）只有 VIP 精准路在用，普通用户等于没有"听感"；
    # 3026 首采集歌里 903 首连 genre 都没有，文本路对它们几乎无识别能力。
    # 新路：把用户最常听歌曲映射成听感桶（mood|tempo档），从同桶里召回——
    # 完全绕开歌名/歌手/标签，解决"没标签的歌推不出来 / 推得不像"的问题。
    _wat = _rec_cfg_float("rec_w_audio_taste", REC_WEIGHTS.get("audio_taste", 1.2))
    if idx is not None and bucket_hist:
        _bmax = max(bucket_hist.values()) or 1.0
        _abudget = REC_LIMITS.get("audio_taste", 30)
        for _bk, _bsc in sorted(bucket_hist.items(), key=lambda kv: -kv[1])[:3]:
            _bids = list(idx.by_bucket(_bk))
            if not _bids:
                continue
            # 热度前 60 + 随机 40：既有该听感的代表作，也保证口味流动
            _pool = _bids[:60]
            if len(_bids) > 60:
                _pool += rnd.sample(_bids[60:], min(40, len(_bids) - 60))
            for rid in _pool[:_abudget]:
                if rid in avoid:
                    continue
                _m = idx.get(rid)
                if not _m:
                    continue
                _add(rid, (_bsc / _bmax) * _wat, "audio_taste",
                     _m["artist"], _m["album"], _m["title"], _m["genre"])

    # ---------- 路 6b：LLM 语义标签召回（2026-11-05，track_tags） ----------
    # audio_features 只有 4 类 mood；LLM 打标给出 20+ 情绪/场景标签（治愈/emo/空灵/深夜/雨天…），
    # 画像命中后按标签召回，比"同风格"更贴近"此刻心情"。
    _wtag = _rec_cfg_float("rec_w_tag_taste", REC_WEIGHTS.get("tag_taste", 1.3))
    if idx is not None and (mood_hist or scene_hist):
        _allv = list(mood_hist.values()) + list(scene_hist.values())
        _tmax = max(_allv) if _allv else 1.0
        for _tag, _sc in sorted(mood_hist.items(), key=lambda kv: -kv[1])[:2]:
            _ids = list(idx.by_mood(_tag))
            if not _ids:
                continue
            for rid in _pick(_ids, 8, 12):
                if rid in avoid:
                    continue
                _m = idx.get(rid)
                if not _m:
                    continue
                _add(rid, (_sc / _tmax) * _wtag, "tag_taste",
                     _m["artist"], _m["album"], _m["title"], _m["genre"])
        for _tag, _sc in sorted(scene_hist.items(), key=lambda kv: -kv[1])[:2]:
            _ids = list(idx.by_scene(_tag))
            if not _ids:
                continue
            for rid in _pick(_ids, 6, 10):
                if rid in avoid:
                    continue
                _m = idx.get(rid)
                if not _m:
                    continue
                _add(rid, (_sc / _tmax) * _wtag, "tag_taste",
                     _m["artist"], _m["album"], _m["title"], _m["genre"])

    # ---------- 路 6c：多兴趣音频召回（MIND-lite，2026-11-05） ----------
    # 用户多口味（DJ 电子 + 伤感民谣）时，单一平均向量会互相抵消；这里对用户高分歌的
    # MERT 向量做加权 k-means，得到 3 个兴趣簇，每簇单独召回，再按簇质量加权融合。
    _wam = _rec_cfg_float("rec_w_audio_multi", REC_WEIGHTS.get("audio_multi", 1.5))
    if _wam > 0 and viewer_id:
        try:
            _multi_hits = _audio_multi_top(c, viewer_id, base, exclude_self, pushed)
        except Exception:
            _multi_hits = []
        _mn = max(len(_multi_hits), 1)
        for i, (tid, _msc) in enumerate(_multi_hits):
            if tid in avoid:
                continue
            sc = max(0.0, 1.0 - i / float(_mn)) * _wam
            _m = idx.get(tid) if idx is not None else None
            if _m is None:
                row = c.execute(
                    "SELECT id,title,artist,album,COALESCE(genre,'') FROM ("
                    " SELECT id,title,artist,album,genre FROM tracks WHERE id=?"
                    " UNION ALL SELECT id,title,artist,album,genre FROM collected_tracks WHERE id=?)",
                    (tid, tid)).fetchone()
                if not row:
                    continue
                _add(tid, sc, "audio_multi", row[2], row[3], row[1], row[4])
            else:
                _add(tid, sc, "audio_multi",
                     _m["artist"], _m["album"], _m["title"], _m["genre"])

    # ---------- 路 6d：全库内容亲和扫描（2026-11-05「覆盖度」修复） ----------
    # 线上实测（用户 a334105a）：2,857 首可用库里 1,897 首被「30 天内听完/7 天内听过」硬排除，
    # 而各路召回又都在他的口味邻域里取歌 → **候选池只有约 200 首**，
    # 小于 MMR 抽样池容量 → 抽样形同虚设 → 每次推荐的 20 首几乎是同一批
    # （复现：连续 6 次开 App 只拿到 46 首不同歌，相邻两批重叠 69%，8 首歌每次都出现）。
    # 这里对**全库每一首未被排除的歌**做一次轻量内容亲和打分（情绪/场景/风格/听感/歌手
    # + 热度先验），把候选池从 ~200 拉到接近「可播放全集」，MMR/抽样才有轮换空间。
    _wsweep = _rec_cfg_float("rec_w_catalog", 0.30)
    if idx is not None and viewer_id and _wsweep > 0:
        def _norm_aff(d):
            mx = max(d.values()) if d else 0.0
            return {k: (v / mx) for k, v in d.items()} if mx > 0 else {}

        _mood_aff = _norm_aff(mood_hist)
        _scene_aff = _norm_aff(scene_hist)
        _genre_aff = _norm_aff(genre_set)
        _bkt_aff = _norm_aff(bucket_hist)
        _art_aff = _norm_aff(artist_token_hist)
        _ncat = 0
        for tid, _m0 in idx.meta.items():
            if tid in exclude_self or tid in cand:
                continue
            sc = 0.0
            for _t in (_m0["moods"] or ()):
                sc += _mood_aff.get(_t, 0.0)
            for _t in (_m0["scenes"] or ()):
                sc += _scene_aff.get(_t, 0.0)
            for _t in (_m0["genres"] or ()):
                sc += _genre_aff.get(_t, 0.0)
            if _m0["bucket"]:
                sc += _bkt_aff.get(_m0["bucket"], 0.0)
            for _t in (_m0["artists"] or ()):
                sc += 1.6 * _art_aff.get(_t, 0.0)
            if sc <= 0:
                continue
            sc = min(sc, 3.0)
            # 热度先验：完全没人听过的歌不该仅凭标签冲上来（0.55~1.0 平滑）
            sc *= 0.55 + 0.45 * min(1.0, idx.hot(tid) / 6.0)
            _add(tid, sc * _wsweep, "catalog",
                 _m0["artist"], _m0["album"], _m0["title"], _m0["genre"])
            _ncat += 1
        if _ncat:
            log.info("recommend catalog=%d cand=%d", _ncat, len(cand))

    # ---------- 路 7：天气×时段心情场景召回（M2，个性化×场景双向融合） ----------
    # 场景上下文由 handler 层天气+时段生成（失败为 None → 整路静默跳过，推荐照常）。
    # 融合语义（用户已确认）：不是"纯应景歌单"，而是——
    #   ① 应景歌也吃个性化吻合分（常听歌手/常听风格/行为关键词 × REC_SCENE_PROFILE），
    #      保证"你喜欢的雨歌"排在"随便一首雨歌"前面；
    #   ② 你近期爱听的歌若与场景吻合，整体前移（× REC_SCENE_BOOST）。
    if scene and scene.get("kw"):
        _skw = list(scene["kw"])[:6]
        for kw in _skw:
            like = "%" + kw + "%"
            if len(kw) >= 3:
                rows = _fts_rows(c, kw, REC_LIMITS["scene"])
                if rows is None:
                    rows = _rows("(title LIKE ? OR artist LIKE ? OR album LIKE ?)",
                                 [like, like, like], REC_LIMITS["scene"])
            else:
                rows = _rows("(title LIKE ? OR artist LIKE ? OR album LIKE ?)",
                             [like, like, like], REC_LIMITS["scene"])
            for rid, rtitle, rartist, ralbum, rgenre in rows:
                sc = _ws * _sboost
                # 吻合分：常听歌手
                if rartist and artist_hist.get(rartist):
                    sc += artist_hist[rartist] * REC_SCENE_PROFILE
                # 吻合分：常听风格
                if rgenre:
                    for _g in rgenre.split(","):
                        _g = _g.strip()
                        if genre_set.get(_g):
                            sc += genre_set[_g] * REC_SCENE_PROFILE
                            break
                # 吻合分：行为关键词（歌名/歌手命中用户近期搜索或常听关键词）
                for _k, _ksc in dedup[:8]:
                    if _k.lower() in (rtitle or "").lower() or _k.lower() in (rartist or "").lower():
                        sc += _ksc * REC_SCENE_PROFILE
                        break
                _add(rid, sc, "scene", rartist, ralbum, rtitle, rgenre)
        # 近期爱听歌场景前移：已进候选（行为/歌手/风格等路）的歌若含场景关键词，加权并挂场景标签
        _boost = _ws * _sboost
        for tid, it in list(cand.items()):
            _t = ((it["title"] or "") + " " + (it["artist"] or "")).lower()
            if any(k.lower() in _t for k in _skw):
                it["score"] += _boost
                it["parts"]["scene"] = it["parts"].get("scene", 0.0) + _boost
                it["sources"].add("scene")

    # 精准路权重默认 0：普通用户（_precise_on=False）不启用精准四路，融合段引用零权重
    _waud = _wcol = _wi2i = _wsess = 0.0
    if _precise_on:
        # ---------- 路 8：音频内容特征相似召回（精准，2026-10-24） ----------
        # 用歌曲本身的节奏/能量/音色/情绪特征匹配用户近期常听画像（余弦相似度），
        # 完全绕开文本——歌名/歌手/标签都不同但听感相似的新歌也能精准命中。
        # 分数用「相似度排名分」（与热度路同量级），避免原始余弦高分霸榜，
        # 与文本路「搭配融合」：已被文本路命中的歌叠加精准分并挂精准标签（不互斥、不替代）。
        # 2026-10-24 会员分级：仅 VIP/SVIP（precise=True）启用；普通用户走粗略算法不启用。
        _waud = _rec_cfg_float("rec_w_audio", REC_WEIGHTS["audio"])
        try:
            _audio_hits = _audio_top(c, viewer_id, base, exclude_self, pushed, REC_AUDIO_TOP)
        except Exception:
            _audio_hits = []
        _audio_n = max(len(_audio_hits), 1)
        for i, (tid, cos) in enumerate(_audio_hits):
            if tid in avoid:
                continue
            sc = max(0.0, 1.0 - i / float(_audio_n)) * _waud   # 排名分：越相似分越高
            if tid in cand:
                cand[tid]["score"] += sc
                cand[tid]["parts"]["precise_audio"] = cand[tid]["parts"].get("precise_audio", 0.0) + sc
                cand[tid]["sources"].add("precise_audio")
                continue
            row = c.execute(
                "SELECT id,title,artist,album,COALESCE(genre,'') FROM ("
                " SELECT id,title,artist,album,genre FROM tracks WHERE id=?"
                " UNION ALL SELECT id,title,artist,album,genre FROM collected_tracks WHERE id=?)",
                (tid, tid)).fetchone()
            if not row:
                continue
            _add(tid, sc, "precise_audio", row[2], row[3], row[1], row[4])

        # ---------- 路 9：协同过滤召回（精准，2026-10-24） ----------
        # 全体用户真实听歌行为（implicit ALS）学出的「听 A 的人也在听 B」规律——
        # 完全不用文本，靠群体行为判断这首歌适合谁。与各文本路搭配融合。
        # 同样使用排名分（避免 ALS 原始分量级不稳）。
        _wcol = _rec_cfg_float("rec_w_collab", REC_WEIGHTS["collab"])
        try:
            _collab_hits = _collab_top(c, viewer_id, exclude_self, pushed, REC_COLLAB_TOP)
        except Exception:
            _collab_hits = []
        _collab_n = max(len(_collab_hits), 1)
        for i, (tid, score) in enumerate(_collab_hits):
            if tid in avoid:
                continue
            sc = max(0.0, 1.0 - i / float(_collab_n)) * _wcol   # 排名分：rank 越前分越高
            if tid in cand:
                cand[tid]["score"] += sc
                cand[tid]["parts"]["precise_collab"] = cand[tid]["parts"].get("precise_collab", 0.0) + sc
                cand[tid]["sources"].add("precise_collab")
                continue
            row = c.execute(
                "SELECT id,title,artist,album,COALESCE(genre,'') FROM ("
                " SELECT id,title,artist,album,genre FROM tracks WHERE id=?"
                " UNION ALL SELECT id,title,artist,album,genre FROM collected_tracks WHERE id=?)",
                (tid, tid)).fetchone()
            if not row:
                continue
            _add(tid, sc, "precise_collab", row[2], row[3], row[1], row[4])

        # ---------- 路 10：i2i 关联召回（精准，2026-10-24 优化） ----------
        # Spotify 协同 encoder 的轻量实现：常听歌 → item_sims 音频相似关联歌。
        # 与路 8（画像→全库相似）互补：路 8 找"整体像你口味"的歌，路 10 找"像你某首爱听歌"的歌。
        _wi2i = _rec_cfg_float("rec_w_i2i", REC_WEIGHTS["i2i"])
        try:
            _i2i_hits = _i2i_top(c, viewer_id, base, exclude_self, pushed, REC_LIMITS["i2i"])
        except Exception:
            _i2i_hits = []
        _i2i_n = max(len(_i2i_hits), 1)
        for i, (tid, score) in enumerate(_i2i_hits):
            if tid in avoid:
                continue
            sc = max(0.0, 1.0 - i / float(_i2i_n)) * _wi2i      # 排名分：种子偏好×相似度归一
            if tid in cand:
                cand[tid]["score"] += sc
                cand[tid]["parts"]["precise_i2i"] = cand[tid]["parts"].get("precise_i2i", 0.0) + sc
                cand[tid]["sources"].add("precise_i2i")
                continue
            row = c.execute(
                "SELECT id,title,artist,album,COALESCE(genre,'') FROM ("
                " SELECT id,title,artist,album,genre FROM tracks WHERE id=?"
                " UNION ALL SELECT id,title,artist,album,genre FROM collected_tracks WHERE id=?)",
                (tid, tid)).fetchone()
            if not row:
                continue
            _add(tid, sc, "precise_i2i", row[2], row[3], row[1], row[4])

    # ---------- 路 6e：语义向量召回（2026-11-05，song_vec 生成的 948 维融合向量） ----------
    # 每首歌的向量 = 歌词/标签/画像的 TF-IDF+LSA（文本语义） + MERT 768/librosa（听感）。
    # 两个新路：
    #   ① semantic：用户口味向量（近期高分歌向量加权均值）→ 全库余弦，找"整體像你会喜欢"的新歌，
    #      绕开歌名/歌手/标签的字符串匹配，对"没标签、没歌词"的歌同样有效；
    #   ② vec_i2i：用户常听歌 → track_neighbors（跨歌手"听起来像"邻居）→ 找"像你某首爱听歌"的歌，
    #      比旧 item_sims（16 个用户的共现，几乎不可用）可靠得多。
    _wsem = _rec_cfg_float("rec_w_semantic", 1.6)
    if _wsem > 0 and viewer_id and base:
        try:
            _sem_hits = _semantic_top(c, viewer_id, base, exclude_self, pushed, 90)
        except Exception:
            _sem_hits = []
        if _sem_hits:
            _smx = max(sc for _t, sc in _sem_hits) or 1.0
            for tid, sc in _sem_hits:
                if tid in avoid:
                    continue
                _m = idx.get(tid) if idx is not None else None
                if _m is None:
                    continue
                _add(tid, (max(0.0, sc) / _smx) * _wsem, "semantic",
                     _m["artist"], _m["album"], _m["title"], _m["genre"])
    _wvi = _rec_cfg_float("rec_w_vec_i2i", 1.1)
    if _wvi > 0 and viewer_id and base:
        try:
            _vi_hits = _vec_i2i_top(c, viewer_id, base, exclude_self, pushed, 70)
        except Exception:
            _vi_hits = []
        if _vi_hits:
            _vmx = max(sc for _t, sc in _vi_hits) or 1.0
            for tid, sc in _vi_hits:
                if tid in avoid:
                    continue
                _m = idx.get(tid) if idx is not None else None
                if _m is None:
                    continue
                _add(tid, (max(0.0, sc) / _vmx) * _wvi, "vec_i2i",
                     _m["artist"], _m["album"], _m["title"], _m["genre"])

    # ---------- 融合：各路内部 max 归一化 × 权重（2026-10-24 优化） ----------
    # 之前直接相加：行为路原始分可达 12、其他路 ≤1.5，行为路事实垄断，"权重"失真为加法系数。
    # 现在每路候选除以其路内最高分 → [0,1]，再 × 该路权重——权重真正表达"路的重要性"，
    # 行为/歌手/音频/协同/探索各路的相对贡献由权重统一控制，量级一致、可解释。
    # 2026-10-24 主辅重构：精准路权重 ×_vip_boost（VIP/SVIP 加成）；普通用户（_precise_on=False）
    # 时精准权重为 0（普通用户不启用精准四路，只走文本+热度+探索粗略算法）。
    _wmap = {"behavior": _wb, "genre": _wg, "artist": _wa, "hot": _wh_eff,
             "fresh": _wf, "explore": w_explore, "scene": _ws,
             "catalog": _rec_cfg_float("rec_w_catalog", 0.30),
             "audio_taste": _wat,
             "tag_taste": _wtag,
             "audio_multi": _wam,
             "semantic": _wsem,
             "vec_i2i": _wvi,
             "precise_audio": _waud * _vip_boost, "precise_collab": _wcol * _vip_boost,
             "precise_i2i": _wi2i * _vip_boost}
    # 2026-11-05：按「该路近期完听率」自调权（_rq: route→0.85~1.15）。
    # 只调个性化文本/听感/精准路；hot/fresh/explore/scene 是兜底与场景，不参与自调。
    if _rq:
        for _k in ("behavior", "artist", "genre", "audio_taste", "tag_taste", "audio_multi",
                   "precise_audio", "precise_collab", "precise_i2i"):
            _m2 = _rq.get(_k)
            if _m2:
                _wmap[_k] = _wmap.get(_k, 0.0) * _m2
    _road_max = {}
    for it in cand.values():
        for src, sc in it["parts"].items():
            if sc > _road_max.get(src, 0.0):
                _road_max[src] = sc
    for it in cand.values():
        s = 0.0
        for src, sc in it["parts"].items():
            m = _road_max.get(src) or 1.0
            s += (sc / m) * _wmap.get(src, 1.0)
        it["score"] = s

    # ---------- 显式负反馈降权（2026-11-05） ----------
    # 拉黑歌手 ×0.25 / 风格 ×0.5 / 情绪场景 ×0.55。比隐式跳过信号强得多——
    # 用户主动点「不感兴趣」一次，抵得上十次推断，而且不会被行为数据淹没。
    if cand and (_dl_artists or _dl_genres or _dl_moods):
        _dn = 0
        for tid, it in cand.items():
            _m = idx.get(tid) if idx is not None else None
            mul = 1.0
            if _dl_artists:
                if it["artist"] and it["artist"].lower() in _dl_artists:
                    mul *= 0.25
                elif _m and (_m["artists"] & _dl_artists):
                    mul *= 0.25
            if _dl_genres and _m and (_m["genres"] & _dl_genres):
                mul *= 0.5
            if _dl_moods and _m and ((_m.get("moods") or set()) & _dl_moods):
                mul *= 0.55
            if mul < 1.0:
                it["score"] *= mul
                _dn += 1
        if _dn:
            log.info("recommend dislike_pen=%d/%d", _dn, len(cand))

    # ---------- 重复曝光降权（2026-11-05） ----------
    # 最近推过、但用户没点开的歌：这一批明显让位（×0.45/×0.65），
    # 推过且点开过的只轻微降权（×0.85，真喜欢可以再出现）。
    # 这是「同一个首页反复那几首」的直接解药，且比硬排除更温和（不会被 exclusion 掏空候选池）。
    _shown = {}
    if viewer_id and cand:
        try:
            _shown = _rec_shown(c, viewer_id, now)
        except Exception:
            _shown = {}
        if _shown:
            _sw = 0
            for tid, it in cand.items():
                w = _rec_shown_weight(_shown, tid, now)
                if w < 1.0:
                    it["score"] *= w
                    _sw += 1
            if _sw:
                log.info("recommend shown_pen=%d/%d", _sw, len(cand))

    # ---------- 时段画像加权（2026-11-05） ----------
    # 用户自己的作息规律：深夜爱听什么、清晨爱听什么。与天气场景（外部条件）互补。
    # 只做温和乘性加权（最多 ×(1+1.5×_wtime)），不改变召回范围，避免"到点只剩一种歌"；
    # 该时段样本不足时自动跳过（band_hist 为空）。
    _cur_band = _day_part(now)
    _bd = band_hist.get(_cur_band) or {}
    if _wtime > 0 and _bd and cand and idx is not None:
        _aff = {}
        for _kind in ("moods", "scenes", "buckets"):
            _d = _bd.get(_kind) or {}
            if not _d:
                continue
            _mx = max(_d.values()) or 1.0
            for _k, _v in _d.items():
                _aff[(_kind, _k)] = _v / _mx
        if _aff:
            _hit = 0
            for tid, it in cand.items():
                m = idx.get(tid)
                if not m:
                    continue
                _bon = 0.0
                for _t in (m.get("moods") or ()):
                    _bon += _aff.get(("moods", _t), 0.0)
                for _t in (m.get("scenes") or ()):
                    _bon += _aff.get(("scenes", _t), 0.0)
                if m["bucket"]:
                    _bon += _aff.get(("buckets", m["bucket"]), 0.0)
                if _bon > 0:
                    it["score"] *= (1.0 + _wtime * min(_bon, 1.5))
                    it["sources"].add("time_taste")
                    _hit += 1
            log.info("recommend time_band=%s boosted=%d", _cur_band, _hit)

    # ---------- 负反馈（mood/scene 维度，2026-11-05） ----------
    # 被跳过次数多的情绪/场景标签，同类候选温和降权（最多 -25%），
    # 与「歌手维度惩罚」互补：同一个歌手的不同风格歌也能区分开。
    if idx is not None and cand and (mood_penalty or scene_penalty):
        _pmx = max(list(mood_penalty.values()) + list(scene_penalty.values()) or [1.0]) or 1.0
        for tid, it in cand.items():
            m = idx.get(tid)
            if not m:
                continue
            _pen = 0.0
            for _t in (m.get("moods") or ()):
                _pen += mood_penalty.get(_t, 0.0) / _pmx
            for _t in (m.get("scenes") or ()):
                _pen += scene_penalty.get(_t, 0.0) / _pmx
            if _pen > 0:
                it["score"] *= max(0.75, 1.0 - 0.25 * min(_pen, 1.0))

    # ---------- MMR 重排（相关分 − 与已选歌的相似度；同歌手/同风格设上限） ----------
    # 2026-11-05 性能：候选先预截断（业界 MMR/DPP 的标准做法），否则 800+ 候选 × 20 轮
    # × 每轮 20 次歌名相似比较会让单次推荐 >1s。
    # 2026-11-05 覆盖度：预截断从「确定性 top160」改为「Plackett-Luce 加权抽样 260」——
    # 确定性 topN 是「每次都那几首」的根因（画像几小时不变 → top160 不变 → MMR 挑出同样 20 首）；
    # 加权抽样保留相关性（gamma 控制贪心程度）又让每批从不同切片取歌。
    _pool_n = max(REC_BUDGET * 4, _rec_cfg_int("rec_pool_size", 260))
    _pool_gamma = _rec_cfg_float("rec_pool_gamma", 2.5)
    _cand_list = list(cand.items())
    log.info("recommend cand=%d pool_n=%d", len(_cand_list), _pool_n)
    if len(_cand_list) > _pool_n:
        pool = _rec_pl_pool(_cand_list, rnd, _pool_n, _pool_gamma)
        pool.sort(key=lambda kv: -kv[1]["score"])
    else:
        pool = sorted(_cand_list, key=lambda kv: -kv[1]["score"])
    ranked = []
    used_artist = {}
    used_genre = {}
    used_precise = 0
    used_versioned = 0   # 同曲异版（DJ版/Remix…）批次限额
    _ver_max = 8
    _mpc = _rec_cfg_int("rec_max_precise", REC_MAX_PRECISE)
    while len(ranked) < REC_BUDGET and pool:
        best_i, best_score = -1, -1e18
        for i, (tid, it) in enumerate(pool):
            if it["artist"] and used_artist.get(it["artist"], 0) >= _mpa:
                continue
            if used_versioned >= _ver_max and _rec_versioned(it["title"]):
                continue
            if it["genre"] and used_genre.get(it["genre"], 0) >= REC_MAX_PER_GENRE:
                continue
            # 「为你精推」路配额：每批最多 REC_MAX_PRECISE 首（默认 50%），
            # 保证文本路标签（常听歌手/同风格/应景/新歌…）也有展示位，避免单一精准路垄断
            if it["sources"] & {"precise_audio", "precise_collab", "precise_i2i"} and used_precise >= _mpc:
                continue
            # 歌名相似硬约束：与任一已选歌歌名相似度 ≥ 阈值（80%）则跳过，
            # 同一批推荐不推送歌名高度相似的歌（90% 相似更不会同批出现）
            if ranked and any(_rec_title_sim(it, r) >= REC_TITLE_SIM_THRESHOLD for _, r in ranked):
                continue
            if idx is not None:
                # 2026-11-05：MMR 相似度改用「语义相似」（歌手 token/风格/听感桶/专辑），
                # 而不是旧的精确歌手指符串——复合歌手（A / B）与同听感歌也能被去重。
                div = max((idx.similar(tid, _rid) for _rid, _ in ranked), default=0.0) if ranked else 0.0
            else:
                div = max((_rec_sim(it, r) for _, r in ranked), default=0.0) if ranked else 0.0
            s = REC_MMR_LAMBDA * it["score"] - (1.0 - REC_MMR_LAMBDA) * div \
                + rnd.uniform(0, noise)
            if s > best_score:
                best_score, best_i = s, i
        if best_i < 0:
            break
        tid, it = pool.pop(best_i)
        ranked.append((tid, it))
        if it["sources"] & {"precise_audio", "precise_collab", "precise_i2i"}:
            used_precise += 1
        if _rec_versioned(it["title"]):
            used_versioned += 1
        if it["artist"]:
            used_artist[it["artist"]] = used_artist.get(it["artist"], 0) + 1
        if it["genre"]:
            used_genre[it["genre"]] = used_genre.get(it["genre"], 0) + 1
    # ---------- 数量兜底（2026-11-05） ----------
    # 配额（同歌手/同风格/同曲异版/精推比例）+ 歌名相似硬约束可能让 MMR 提前收敛，
    # 返回不足一批（实测偶尔 17/20）。这里用剩余候选按分补齐，保证每批满额，
    # 但补齐项仍然遵守「歌名不重复」这条最影响体感的约束。
    if len(ranked) < REC_BUDGET and pool:
        _left = sorted(pool, key=lambda kv: -kv[1]["score"])
        for tid, it in _left:
            if len(ranked) >= REC_BUDGET:
                break
            if ranked and any(_rec_title_sim(it, r) >= REC_TITLE_SIM_THRESHOLD for _, r in ranked):
                continue
            ranked.append((tid, it))
        pool = []
    # ---------- 探索保底（2026-11-01 防信息茧房，综合调研后新增） ----------
    # 纯权重竞争下，行为/精准/歌手等"像你听过"的路会淹没新歌与随机探索（信息茧房根源）。
    # 业界成熟做法是显式配额保底（网易云 CCDF 可控类别多样性、抖音「一键破茧」、
    # MMR/DPP 后处理重排）——这里在 MMR 之后按 REC_EXPLORE_MIN 保底：
    # 若 fresh(新歌)+explore(随机探索) 占比低于下限，用候选池中未入选的探索/新歌
    # 替换末尾最相关（同质）项，保证每次推荐都露出足够的新内容。
    _explore_min = _rec_cfg_float("rec_explore_min", REC_EXPLORE_MIN)
    if _explore_min > 0.0 and ranked:
        _exp_idx = {i for i, (_t, _it) in enumerate(ranked)
                    if _it["sources"] & {"fresh", "explore"}}
        _need = max(int(len(ranked) * _explore_min + 0.999) - len(_exp_idx), 0)
        if _need > 0:
            _ranked_ids = {t for t, _ in ranked}
            _exp_pool = [(t, it) for t, it in pool
                         if (it["sources"] & {"fresh", "explore"}) and t not in _ranked_ids]
            # 探索路（随机未听过）优先于新歌路：两者都算新鲜内容
            _exp_pool.sort(key=lambda x: 0 if "explore" in x[1]["sources"] else 1)
            for _t, _it in _exp_pool:
                if _need <= 0:
                    break
                if ranked and any(_rec_title_sim(_it, r) >= REC_TITLE_SIM_THRESHOLD
                                  for _, r in ranked):
                    continue
                # 从末尾向上找第一个非探索项替换（保护已入选的探索/新歌，占比只增不减）
                _ri = len(ranked) - 1
                while _ri >= 0 and ranked[_ri][1]["sources"] & {"fresh", "explore"}:
                    _ri -= 1
                if _ri < 0:
                    continue
                ranked.pop(_ri)
                ranked.append((_t, _it))
                _need -= 1
    # 返回 (歌曲id, 推荐理由标签, 来源路route) 三元组；前端据此展示「为什么推荐这首歌」，
    # 并在 listen 上报时携带 route 形成归因闭环（P1：按路完听率统计/自调权）。
    return [(tid, _rec_reason(it["sources"]), ",".join(sorted(it["sources"])))
            for tid, it in ranked]


# ===== 评论意图 → 个性化推送（2026-10-23） =====
# 用户评论中出现「想听 XX / 求 XX / 有没有 XX」等意图 → 命中曲库写入意图池，
# 由 /api/recommend 混入该用户推荐流（刷到即见，不打扰式通知）。
# 意图池/意图去重窗口独立为 3 小时（用户口径「这几个小时」），不影响推荐流 24h 去重。
_COMMENT_WISH_WINDOW = 3 * 3600
_COMMENT_WISH_RE = re.compile(
    r"(?:求推荐|求一首|想听|有没有|想要|希望听|点一首|来一首|推荐一首|安排一首|能推荐一首|要听|能推荐|推荐下|求)"
    r"([\u4e00-\u9fa5A-Za-z0-9·&.\- （）()《》【】\"'，、！？!?,]{2,24}?)"
    r"(?:\s*(?:这首歌|这首|的歌|呀|啊|呢|吧|吗|哈|谢谢|感谢|？|\?)|$)"
)
# 清洗歌名中的装饰符号（括号/书名号/引号/标点），只保留真实歌名字符
_COMMENT_WISH_CLEAN = str.maketrans("", "", "（）()《》【】\"'，、！？!?")
_COMMENT_WISH_STOP = {
    "一首", "这个", "那个", "什么", "一下", "这些", "那些", "歌", "歌曲", "音乐",
    "你们", "大家", "有人", "朋友", "时候", "样子", "感觉", "味道", "故事", "东西",
    "纯音乐", "好听的歌", "安静的歌",
}
# 2026-10-24 类型提及（「想听伤感的歌 / 想听古风」）：情绪词 → mood 标签召回，风格词 → genre/标题召回
_COMMENT_MOOD_WORDS = {
    "伤感": "低沉", "悲伤": "低沉", "难过": "低沉", "低沉": "低沉", "忧郁": "低沉",
    "失恋": "低沉", "悲情": "低沉", "丧": "低沉",
    "安静": "舒缓", "舒缓": "舒缓", "温柔": "舒缓", "治愈": "舒缓", "放松": "舒缓", "催眠": "舒缓",
    "欢快": "欢快", "开心": "欢快", "轻快": "欢快", "高兴": "欢快", "快乐": "欢快",
    "激昂": "激昂", "燃": "激昂", "热血": "激昂", "炸": "激昂",
}
_COMMENT_GENRE_WORDS = {
    "古风", "国风", "轻音乐", "纯音乐", "电音", "电子", "民谣", "摇滚", "流行",
    "爵士", "说唱", "嘻哈", "粤语", "英文歌", "日语", "动漫", "影视", "夜店",
    "蹦迪", "情歌", "伤感歌", "DJ", "dj",
}


def _comment_type(kw):
    """识别评论里的「类型」意图：返回 (kind, tag) 或 None。
    kind=mood → audio_features.mood 召回；kind=genre → genre/标题 LIKE 召回。"""
    for w, mood in _COMMENT_MOOD_WORDS.items():
        if w in kw:
            return ("mood", mood)
    for w in _COMMENT_GENRE_WORDS:
        if w in kw:
            return ("genre", w)
    return None


def _wish_upsert(c, uid, tid, kw, now):
    """意图池写入/连续提及计数：3 小时窗口内同一首歌再次被提及 → cnt+1（保送更靠前、权重加倍）。"""
    row = c.execute(
        "SELECT cnt FROM wish_recommends WHERE uid=? AND track_id=? AND created>?",
        (uid, tid, now - _COMMENT_WISH_WINDOW)).fetchone()
    if row:
        c.execute(
            "UPDATE wish_recommends SET cnt=?, created=? WHERE uid=? AND track_id=? AND created>?",
            (int(row[0]) + 1, now, uid, tid, now - _COMMENT_WISH_WINDOW))
    else:
        c.execute("INSERT INTO wish_recommends(uid, track_id, kw, cnt, created) VALUES(?,?,?,1,?)",
                  (uid, tid, kw, now))


def _comment_wish_push(c, uid, content):
    """评论意图解析：命中曲库 → 写入用户「意图推荐池」（wish_recommends）。
    2026-10-24 升级：①类型提及（「想听伤感的歌/古风」→ mood/genre 批量召回 3~5 首）；
    ②连续提及加权（3h 窗口内同歌再次提及 cnt+1，推荐时保送更靠前）；
    ③仍由 /api/recommend 混入推荐流（刷到即见，不打扰式通知）。
    返回 (tid, ttitle, label) 供日志；无意图 / 无命中 → None。"""
    if not content:
        return None
    m = _COMMENT_WISH_RE.search(content)
    if not m:
        return None
    _raw = m.group(1).strip()
    kw = _raw.translate(_COMMENT_WISH_CLEAN).strip()
    # 泛化请求过滤：「几首XX」「好听/安静」类没有具体歌名的请求不推
    if len(kw) < 2 or len(kw) > 24 or kw in _COMMENT_WISH_STOP:
        return None
    if kw.startswith(("几首", "一首", "两首", "三首", "点几首")) or \
       ("好听" in kw and len(kw) <= 8):
        return None
    now = time.time()
    # 窗口过期惰性清理意图池（保留 3 小时内记录）
    c.execute("DELETE FROM wish_recommends WHERE uid=? AND created <= ?",
              (uid, now - _COMMENT_WISH_WINDOW))
    # 3 小时内已在推荐流推过的歌不重复标记（discover 源除外，见 content.py 组装）
    pushed = {r[0] for r in c.execute(
        "SELECT track_id FROM recommend_push_history WHERE uid=? AND pushed_at > ?",
        (uid, now - _COMMENT_WISH_WINDOW)).fetchall()}

    # ---- 分支 1：类型提及（「想听XX类型的歌」）→ mood/genre 批量召回 3~5 首 ----
    _tp = _comment_type(kw)
    if _tp:
        kind, tag = _tp
        tcands = []
        if kind == "mood":
            _pat = '%"mood": "{m}"%'.format(m=tag)
            rows = c.execute(
                "SELECT track_id FROM audio_features WHERE features LIKE ? LIMIT 8",
                (_pat,)).fetchall()
            for (tid,) in rows:
                tcands.append(tid)
        else:
            like = "%" + tag + "%"
            rows = c.execute(
                "SELECT id FROM (SELECT id,title,artist,album,genre FROM tracks WHERE offline=0 AND user_id!=?"
                " UNION ALL SELECT id,title,artist,album,genre FROM collected_tracks WHERE offline=0 AND play_url!='')"
                " WHERE title LIKE ? OR genre LIKE ? OR artist LIKE ? LIMIT 8",
                (uid, like, like, like)).fetchall()
            tcands = [r[0] for r in rows]
        if not tcands:
            return None
        first = None
        for tid in tcands[:5]:
            if tid in pushed:
                continue
            row = c.execute(
                "SELECT title,artist FROM (SELECT id,title,artist FROM tracks WHERE id=?"
                " UNION ALL SELECT id,title,artist FROM collected_tracks WHERE id=?)",
                (tid, tid)).fetchone()
            if not row:
                continue
            _wish_upsert(c, uid, tid, kw, now)
            if first is None:
                first = (tid, row[0] or "", row[1] or "")
        if first is None:
            return None
        tid, ttitle, tartist = first
        label = "《%s》" % ttitle + ((" - " + tartist) if tartist else "")
        return (tid, ttitle, label)

    # ---- 分支 2：具体歌名/歌手（原逻辑）→ 单首 UPSERT（连续提及计数） ----
    # 匹配关键词序列：整串优先 → 按括号/标点/空格切分原始串后的片段回退
    _parts = [p.translate(_COMMENT_WISH_CLEAN).strip()
              for p in re.split(r"[（）()《》【】\"'，、！？!?\s]+", _raw) if p.strip()]
    _like_seq = [kw]
    for _p in _parts:
        if len(_p) >= 2 and _p not in _like_seq:
            _like_seq.append(_p)
    _seen_kw = set()
    cands = []
    for _kw in _like_seq:
        if len(_kw) < 2 or _kw in _seen_kw:
            continue
        _seen_kw.add(_kw)
        like = "%" + _kw + "%"
        # 1) 用户上传曲库：标题包含关键词（按播放量/新歌排序）
        rows = c.execute(
            "SELECT id,title,artist FROM tracks WHERE title LIKE ? "
            "ORDER BY play_count DESC, created DESC LIMIT 5", (like,)).fetchall()
        cands = [(r[0], r[1], r[2]) for r in rows if r[0] not in pushed]
        # 2) 采集曲库：标题包含关键词
        if not cands:
            rows = c.execute(
                "SELECT id,title,artist FROM collected_tracks WHERE title LIKE ? LIMIT 5",
                (like,)).fetchall()
            cands = [(r[0], r[1], r[2]) for r in rows if r[0] not in pushed]
        # 3) 相关：关键词当歌手名 → 推同歌手歌曲
        if not cands:
            rows = c.execute(
                "SELECT id,title,artist FROM tracks WHERE artist LIKE ? AND title NOT LIKE ? "
                "ORDER BY play_count DESC, created DESC LIMIT 5", (like, like)).fetchall()
            cands = [(r[0], r[1], r[2]) for r in rows if r[0] not in pushed]
        if not cands:
            rows = c.execute(
                "SELECT id,title,artist FROM collected_tracks WHERE artist LIKE ? AND title NOT LIKE ? "
                "LIMIT 5", (like, like)).fetchall()
            cands = [(r[0], r[1], r[2]) for r in rows if r[0] not in pushed]
        if cands:
            break
    if not cands:
        return None

    tid, ttitle, tartist = cands[0]
    author = (tartist or "").strip()
    label = "《%s》" % ttitle + ((" - " + author) if author else "")
    # 写入意图推荐池（不落通知、不 ws 推送；由 /api/recommend 混入推荐流）
    _wish_upsert(c, uid, tid, kw, now)
    return (tid, ttitle, label)
