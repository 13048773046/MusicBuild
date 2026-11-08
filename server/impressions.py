# -*- coding: utf-8 -*-
"""推荐曝光日志（2026-11-05）。

为什么需要：现在后端只知道「推了什么」（recommend_push_history 只记 id），
不知道「推出去之后有没有被点开/听完」。没有曝光数据，
- 学习排序（LTR）没有负样本，"推了没点"这类最有价值的信息丢失；
- 分路 CTR / 完听率无法按位置归因（第 1 位和第 18 位不可比）；
- Bandit / A/B 实验无从评估。

设计（尽量轻，不拖慢推荐接口）：
- 每次 /api/recommend 返回后，落一行/首：request_id、uid、track_id、route、position、source、ts；
- 客户端已在播放时调 /api/play/{id}，这里把该用户最近一条未点击曝光标记 clicked=1；
- 完听情况不重复存：behavior_events 的 listen 事件已带 route + fraction，
  分析时用 (uid, track_id, 时间窗) 关联即可。
- 写入全部 best-effort：任何异常都吞掉，绝不影响推荐响应。
"""
import logging
import time
import uuid

log = logging.getLogger(__name__)

# 曝光保留天数（与 behavior_events 一致）
RETENTION_DAYS = 90


def new_request_id():
    return uuid.uuid4().hex[:16]


def record(c, uid, request_id, rows, source="", now=None):
    """记录一批曝光。rows: [(track_id, route), ...]（顺序即展示位置）。

    失败静默：曝光是"锦上添花"的数据，绝不能因为它让推荐接口报错。
    """
    if not uid or not rows:
        return 0
    now = now or time.time()
    try:
        c.executemany(
            "INSERT OR IGNORE INTO recommend_impressions"
            "(request_id, uid, track_id, route, position, source, clicked, created)"
            " VALUES(?,?,?,?,?,?,0,?)",
            [(request_id, uid, str(tid), str(route or ""), i, str(source or ""), now)
             for i, (tid, route) in enumerate(rows)])
        c.commit()
        return len(rows)
    except Exception as e:
        log.info("impressions.record skip: %s", e)
        try:
            c.rollback()
        except Exception:
            pass
        return 0


def mark_clicked(c, uid, track_id, window=6 * 3600):
    """把该用户最近 window 内、这首歌的一条未点击曝光标记为已点击。"""
    if not uid or not track_id:
        return 0
    try:
        row = c.execute(
            "SELECT request_id, position FROM recommend_impressions"
            " WHERE uid=? AND track_id=? AND clicked=0 AND created>=?"
            " ORDER BY created DESC LIMIT 1",
            (uid, track_id, time.time() - window)).fetchone()
        if not row:
            return 0
        c.execute(
            "UPDATE recommend_impressions SET clicked=1"
            " WHERE request_id=? AND uid=? AND track_id=?",
            (row[0], uid, track_id))
        c.commit()
        return 1
    except Exception as e:
        log.info("impressions.mark_clicked skip: %s", e)
        try:
            c.rollback()
        except Exception:
            pass
        return 0


def stats(c, days=7):
    """分路曝光/点击/CTR + 分位置 CTR（给后台看，也是 LTR 上线前的基线）。"""
    since = time.time() - days * 86400.0
    out = {"days": days, "routes": [], "positions": [], "total": 0, "clicked": 0}
    try:
        rows = c.execute(
            "SELECT COALESCE(route,''), COUNT(*) n, SUM(clicked) k"
            " FROM recommend_impressions WHERE created>=? GROUP BY 1 ORDER BY n DESC",
            (since,)).fetchall()
    except Exception:
        return out
    for route, n, k in rows:
        n = int(n or 0)
        k = int(k or 0)
        out["total"] += n
        out["clicked"] += k
        out["routes"].append({
            "route": route or "(none)", "impressions": n, "clicks": k,
            "ctr": round(k / float(n), 4) if n else 0.0})
    try:
        prows = c.execute(
            "SELECT position, COUNT(*) n, SUM(clicked) k"
            " FROM recommend_impressions WHERE created>=? GROUP BY 1 ORDER BY 1",
            (since,)).fetchall()
        for pos, n, k in prows:
            n = int(n or 0)
            k = int(k or 0)
            out["positions"].append({
                "position": int(pos or 0), "impressions": n, "clicks": k,
                "ctr": round(k / float(n), 4) if n else 0.0})
    except Exception:
        pass
    out["ctr"] = round(out["clicked"] / float(out["total"]), 4) if out["total"] else 0.0
    return out
