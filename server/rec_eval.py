# -*- coding: utf-8 -*-
"""推荐系统离线评估 + 趋势留档（2026-11-05）。

为什么需要：改一次算法，"变好还是变坏"必须能量化。此前评估都是临时脚本，
改完只能靠肉眼看几条结果，容易自我说服。这里把它固化成可定时运行、可查趋势的模块。

指标（全部可从现有数据真实算出，不编造）：
  coverage   推荐覆盖的曲库比例（是否总推同几百首）
  novelty    推荐歌里"近 90 天没听过"的比例
  diversity  每首推荐涉及的歌手 token 数（口味宽度）
  tag_hit    推荐歌命中用户 top5 情绪 / top3 场景 的比例（LLM 标签画像吻合度）
  bucket_hit 命中用户 top3 听感桶（mood|tempo）的比例
  avg_hot    推荐歌平均 play_count（是不是全靠热门兜底）
  routes     各召回路的占比
另外每次评估可附带线上曝光 CTR（impressions.stats），作为线上口径对照。

说明：`recall@N` 需要有"历史快照 + 未来行为"的严格切分，当前数据量（16 用户）
不足以支撑稳定结论，故不在此伪造；等曝光数据积累 2 周后用 impressions 的
clicked 做线上 CTR 才是可靠口径。
"""
import json
import random
import time

from server.config import log
from server.database import db
from server.rec_taxonomy import get_taste_index
from server.recommend import build_recommendations, _REC_SHOWN_CACHE

EVAL_MIN_EVENTS = 100     # 只评估行为足够多的用户（否则指标噪声大）
EVAL_MAX_USERS = 8
PROFILE_DAYS = 90


def _profile(c, idx, uid, now):
    """用户画像快照：top 歌手 token / 听感桶 / 情绪 / 场景 + 听过集合。"""
    hist = c.execute(
        "SELECT track_id,event,value,meta,created FROM behavior_events"
        " WHERE user_id=? AND created>=?", (uid, now - PROFILE_DAYS * 86400.0)).fetchall()
    art, bucket, mood, scene, heard = {}, {}, {}, {}, set()
    for tid, ev, _val, meta, _ts in hist:
        if not tid:
            continue
        heard.add(tid)
        if ev != "listen":
            continue
        m = idx.get(tid) if idx is not None else None
        if not m:
            continue
        try:
            frac = float(json.loads(meta or "{}").get("fraction") or 0)
        except (TypeError, ValueError):
            frac = 0.0
        if frac < 0.3:
            continue
        for a in m["artists"]:
            art[a] = art.get(a, 0) + 1
        if m["bucket"]:
            bucket[m["bucket"]] = bucket.get(m["bucket"], 0) + 1
        for t in (m.get("moods") or ()):
            mood[t] = mood.get(t, 0) + 1
        for t in (m.get("scenes") or ()):
            scene[t] = scene.get(t, 0) + 1
    top = lambda d, k: {x for x, _ in sorted(d.items(), key=lambda kv: -kv[1])[:k]}
    return {
        "artists": top(art, 10), "buckets": top(bucket, 3),
        "moods": top(mood, 5), "scenes": top(scene, 3), "heard": heard,
    }


def evaluate(users=EVAL_MAX_USERS, budget=20, with_ctr=True, batches=5):
    """跑一轮离线评估，返回汇总 dict。失败返回 {"ok": False, "err": ...}。

    batches：每个用户连续模拟取几批推荐（不同批次种子、不传 exclude，模拟用户
    "反复打开首页"的真实场景）。只有当 batches>1 时 coverage/repeat 才有意义——
    旧版每个用户只取 1 批，20 首 × 8 人最多覆盖 160 首，指标先天封顶，
    完全看不出"一直在那几首转"。
    """
    c = db()
    try:
        idx = get_taste_index(c)
        now = time.time()
        uids = [r[0] for r in c.execute(
            "SELECT user_id, COUNT(*) n FROM behavior_events WHERE user_id!=''"
            " GROUP BY 1 HAVING n>=? ORDER BY n DESC LIMIT ?",
            (EVAL_MIN_EVENTS, max(1, users))).fetchall()]
        if not uids:
            return {"ok": False, "err": "no_users"}
        lib_size = int(c.execute(
            "SELECT (SELECT COUNT(*) FROM tracks WHERE offline=0)"
            " + (SELECT COUNT(*) FROM collected_tracks WHERE offline=0"
            "    AND COALESCE(play_url,'')!='')").fetchone()[0] or 1)

        per_user, seen_all = [], set()
        for uid in uids:
            prof = _profile(c, idx, uid, now)
            ids, routes_all, total = [], {}, 0
            # 曝光回环模拟：线上每批推荐都会写 recommend_impressions，下一批的
            # "重复曝光降权"才生效。离线评估不能真往库里写曝光（会污染线上 CTR 口径），
            # 所以用内存缓存注入同样的历史（键与 _rec_shown 的 uid 缓存一致）。
            shown = {}
            for b in range(max(1, batches)):
                try:
                    _REC_SHOWN_CACHE[uid] = (now + 3600.0, dict(shown))
                except Exception:
                    pass
                try:
                    recs = build_recommendations(
                        c, uid, rnd=random.Random("%s|eval|%d" % (uid, b)))
                except Exception as e:
                    log.info("rec_eval: user %s batch %d failed: %s", uid[:8], b, e)
                    continue
                batch_ids = [r[0] for r in recs][:budget]
                ids += batch_ids
                total += len(recs)
                for _tid in batch_ids:
                    _ts, _n = shown.get(_tid, (0.0, 0))
                    shown[_tid] = (now, _n + 1)
                for _tid, _reason, route in recs:
                    for s in (route or "").split(","):
                        if s:
                            routes_all[s] = routes_all.get(s, 0) + 1
            try:
                _REC_SHOWN_CACHE.pop(uid, None)
            except Exception:
                pass
            meta = [m for m in ((idx.get(i) if idx is not None else None) for i in ids) if m]
            if not meta:
                continue
            n = float(len(meta))
            seen_all.update(ids)
            per_user.append({
                "user": uid[:8],
                "n": len(ids),
                "novelty": round(sum(1 for i in ids if i not in prof["heard"]) / max(len(ids), 1), 3),
                "diversity": round(len({a for m in meta for a in m["artists"]}) / n, 2),
                "tag_hit": round(sum(1 for m in meta
                                    if (m.get("moods") or set()) & prof["moods"]
                                    or (m.get("scenes") or set()) & prof["scenes"]) / n, 3),
                "bucket_hit": round(sum(1 for m in meta
                                       if m["bucket"] and m["bucket"] in prof["buckets"]) / n, 3),
                "avg_hot": round(sum(m["plays"] for m in meta) / n, 1),
                # 重复度：同一用户多批之间有多少是重复推送的
                "repeat": round(1.0 - len(set(ids)) / float(max(1, len(ids))), 3),
                "routes": routes_all,
            })

        if not per_user:
            return {"ok": False, "err": "no_recs"}
        keys = ("novelty", "diversity", "tag_hit", "bucket_hit", "avg_hot", "repeat")
        agg = {k: round(sum(u[k] for u in per_user) / len(per_user), 3) for k in keys}
        agg["coverage"] = round(len(seen_all) / float(lib_size), 3)
        agg["library"] = lib_size
        agg["batches"] = max(1, batches)
        route_mix = {}
        for u in per_user:
            for k, v in u["routes"].items():
                route_mix[k] = route_mix.get(k, 0) + v
        agg["routes"] = route_mix
        out = {"ok": True, "created": now, "users": per_user, "avg": agg}
        if with_ctr:
            try:
                from server import impressions
                out["ctr"] = impressions.stats(c, 7)
            except Exception:
                out["ctr"] = {}
        return out
    finally:
        c.close()


def save(result):
    """把一次评估结果落 rec_eval_history（趋势用）。"""
    if not result or not result.get("ok"):
        return 0
    c = db()
    try:
        c.execute(
            "INSERT INTO rec_eval_history(created, users_n, metrics) VALUES(?,?,?)",
            (result.get("created") or time.time(), len(result.get("users") or []),
             json.dumps({"avg": result.get("avg"), "users": result.get("users")},
                        ensure_ascii=False)))
        c.commit()
        return 1
    except Exception as e:
        log.info("rec_eval.save skip: %s", e)
        return 0
    finally:
        c.close()


def trend(days=30, limit=60):
    """历史评估趋势（新→旧）。"""
    c = db()
    try:
        rows = c.execute(
            "SELECT created, users_n, metrics FROM rec_eval_history"
            " WHERE created>=? ORDER BY created DESC LIMIT ?",
            (time.time() - days * 86400.0, max(1, min(limit, 200)))).fetchall()
    except Exception:
        rows = []
    finally:
        c.close()
    out = []
    for created, users_n, metrics in rows:
        try:
            m = json.loads(metrics or "{}").get("avg") or {}
        except Exception:
            m = {}
        out.append({"created": created,
                    "at": time.strftime("%Y-%m-%d %H:%M", time.localtime(created or 0)),
                    "users": users_n, "avg": m})
    return out


def run_and_save(users=EVAL_MAX_USERS):
    r = evaluate(users=users)
    if r.get("ok"):
        save(r)
        log.info("rec_eval: %s", json.dumps(r.get("avg"), ensure_ascii=False))
    else:
        log.info("rec_eval skipped: %s", r.get("err"))
    return r


def main():
    print(json.dumps(run_and_save(), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
