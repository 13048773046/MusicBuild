# server/radio.py — 场景音乐 → 歌曲召回（2026-11-01）
# 用户口径：点「DJ 模式」就播 DJ 歌，且必须走个性化推荐（不能是固定歌单），
# 只推"这个类型的歌"。
# 两阶段：
#   A. 类型筛选（三路，解决 genre 标签稀疏——很多 DJ/EMO 歌没打标签）：
#      ① genre 标签路：genre LIKE 直接命中
#      ② 音频特征路：audio_features 的 mood/tempo 阈值过滤——覆盖"没标签但声学就是 DJ/EMO"
#      ③ MERT 语义路：以①②命中歌为种子，MERT 768 维全库余弦找声学/语义相近歌兜底
#   B. 个性化排序（用户口径：分类内也要个性化）：
#      候选与"用户音频偏好向量"（近 90 天行为歌 70/20 加权 + 爱听加分的 MERT 均值）做余弦，
#      按相似度降序——DJ 模式下优先推用户最爱听的那种 DJ；
#      无行为/无特征的冷启动用户回退场景种子向量（该场景代表性歌）。
import json
import logging
import random
import time

try:
    import numpy as np
except ImportError:
    np = None

log = logging.getLogger(__name__)

# 复用 recommend.py 的 MERT 矩阵缓存（30s），避免重复全量读库
from .recommend import _load_audio_mat, _load_vec_mat  # noqa: E402

_RADIO_FEAT_CACHE = {"t": 0.0, "rows": []}   # features JSON 全量缓存（60s）


def _feat_rows(c):
    now = time.time()
    if now - _RADIO_FEAT_CACHE["t"] < 60.0 and _RADIO_FEAT_CACHE["rows"]:
        return _RADIO_FEAT_CACHE["rows"]
    rows = []
    try:
        for tid, f in c.execute(
                "SELECT track_id, features FROM audio_features WHERE features IS NOT NULL AND features!=''"):
            try:
                d = json.loads(f)
                if isinstance(d, dict):
                    rows.append((tid, d))
            except Exception:
                pass
    except Exception:
        pass
    _RADIO_FEAT_CACHE.update({"t": now, "rows": rows})
    return rows


def _safe_tempo(d):
    try:
        return float(d.get("tempo") or 0.0)
    except (TypeError, ValueError):
        return 0.0


# 12 个场景规则（key 与前端侧边栏标题一致；未知 tag 兜底走 DJ 模式）
# dj_signal=True 的场景（DJ/电音/驾车）需要"DJ 信号"才能从特征路进入候选，
# 防止把"快节奏流行歌"误当 DJ：genre 含 DJ/电子/舞曲，或标题含 DJ/Remix/电音，
# 或 mood=激昂（激昂基本就是 DJ 类声学特征）。
RADIO_RULES = {
    # 2026-11-03 调优：起床/洗澡 原规则字面完全相同（必然同一张歌单），
    # 助眠 ≤80 会把全部「舒缓」（tempo 最低 80.75）排除、与深夜 EMO 重合，
    # 通勤 0-150 无 mood 过滤近似全库 —— 按 tempo/mood 重新拉开区分度。
    # 2026-11-05 起每条规则额外带 `scenes` / `lmoods`：LLM 标签词表里的场景/情绪，
    # 用于把 3,298 首有标签的歌接进来（旧规则只看 4 类音频 mood，覆盖窄）。
    "通勤必听":  {"genres": ["流行"], "moods": ["欢快"],  "min_tempo": 85,  "max_tempo": 145,
                 "scenes": ["通勤"], "lmoods": ["轻松", "清新"], "no_dj": True},
    "驾车":      {"genres": ["电子/DJ"], "moods": ["激昂"], "min_tempo": 105, "max_tempo": 999,
                 "dj_signal": True, "scenes": ["驾车"], "lmoods": ["燃", "大气"]},
    "起床":      {"genres": [], "moods": ["欢快", "舒缓"], "min_tempo": 60, "max_tempo": 140,
                 "scenes": ["夏日"], "lmoods": ["清新", "温暖", "甜蜜"], "no_dj": True},
    "深夜 EMO":  {"genres": ["抒情"], "moods": ["低沉"], "min_tempo": 0, "max_tempo": 95,
                 "scenes": ["深夜"], "lmoods": ["伤感", "emo", "苦涩", "怀旧"], "no_dj": True},
    "DJ 模式":   {"genres": ["电子/DJ"], "moods": ["激昂", "欢快"], "min_tempo": 115, "max_tempo": 999,
                 "dj_signal": True, "scenes": ["派对"], "lmoods": ["燃", "热血"]},
    "助眠模式":  {"genres": ["纯音乐"], "moods": ["舒缓", "低沉"], "min_tempo": 0, "max_tempo": 75,
                 "scenes": ["助眠"], "lmoods": ["治愈", "空灵", "慵懒"], "no_dj": True},
    "动感健身":  {"genres": [], "moods": ["激昂", "欢快"], "min_tempo": 120, "max_tempo": 999,
                 "scenes": ["健身"], "lmoods": ["燃", "热血"]},
    "抖音漫游":  {"genres": ["流行"], "moods": ["欢快"], "min_tempo": 90, "max_tempo": 160,
                 "scenes": ["夏日"], "lmoods": ["甜蜜", "清新"]},
    "洗澡":      {"genres": [], "moods": ["欢快", "激昂"], "min_tempo": 110, "max_tempo": 175,
                 "scenes": [], "lmoods": ["轻松", "甜蜜", "热血"]},
    "Chill 放松": {"genres": ["民谣"], "moods": ["舒缓"], "min_tempo": 0, "max_tempo": 120,
                 "scenes": ["咖啡", "旅行"], "lmoods": ["治愈", "慵懒", "清新"], "no_dj": True},
    "快乐时光":  {"genres": [], "moods": ["欢快"], "min_tempo": 80, "max_tempo": 999,
                 "scenes": ["派对", "夏日"], "lmoods": ["甜蜜", "温暖", "轻松"]},
    "电音":      {"genres": ["电子/DJ"], "moods": ["激昂", "欢快"], "min_tempo": 115, "max_tempo": 999,
                 "dj_signal": True, "scenes": ["派对"], "lmoods": ["燃", "神秘"]},
}

_DJ_KW = ("dj", "remix", "电音", "舞曲", "蹦迪", "电子", "夜店")


def _tag_scene_map(c):
    """track_tags → {tid: {"scenes": {tag: w}, "moods": {tag: w}}}（60s 缓存）。

    2026-11-05：旧场景路只用 audio_features 的 4 类 mood（低沉/舒缓/欢快/激昂），
    全靠 tempo 阈值区分——「助眠」和「深夜 EMO」很容易撞车。
    LLM 标签有 12 类场景 + 15 类情绪、覆盖 3,298 首，能把场景区分度拉开。
    """
    now = time.time()
    if now - _TAG_MAP_CACHE["t"] < 60.0 and _TAG_MAP_CACHE["map"]:
        return _TAG_MAP_CACHE["map"]
    out = {}
    try:
        rows = c.execute("SELECT track_id,kind,tag FROM track_tags"
                         " WHERE kind IN ('scene','mood')").fetchall()
        for tid, kind, tag in rows:
            if not tid or not tag:
                continue
            d = out.setdefault(tid, {"scenes": {}, "moods": {}})
            (d["scenes"] if kind == "scene" else d["moods"])[tag] = 1.0
    except Exception:
        pass
    _TAG_MAP_CACHE.update({"t": now, "map": out})
    return out


_TAG_MAP_CACHE = {"t": 0.0, "map": {}}


def _is_dj_hint(title, genre):
    t = ((title or "") + "|" + (genre or "")).lower()
    return any(k in t for k in _DJ_KW)


_RADIO_TG_CACHE = {"t": 0.0, "map": {}}   # tid → (title, genre) 缓存（60s）


def _title_genre_map(c):
    now = time.time()
    if now - _RADIO_TG_CACHE["t"] < 60.0 and _RADIO_TG_CACHE["map"]:
        return _RADIO_TG_CACHE["map"]
    m = {}
    try:
        for tid, title, genre in c.execute("SELECT id, title, genre FROM tracks"):
            m[tid] = (title, genre)
        for tid, title, genre in c.execute("SELECT id, title, genre FROM collected_tracks"):
            m.setdefault(tid, (title, genre))
    except Exception:
        pass
    _RADIO_TG_CACHE.update({"t": now, "map": m})
    return m


def _user_base(c, viewer_id, now=None):
    """用户行为打分 {track_id: score}（90 天，口径与推荐画像一致）。

    listen：秒数×时间衰减 + 完听/反复听加分；like +3 / comment +2 / share +2 / search +1 / skip -0.5。
    抽出来供两条路径共用：MERT 偏好向量、948 维融合偏好向量。
    """
    if not viewer_id:
        return {}
    if now is None:
        now = time.time()
    try:
        evs = c.execute(
            "SELECT track_id,event,value,meta,created FROM behavior_events"
            " WHERE user_id=? AND created>=? ORDER BY created DESC LIMIT 400",
            (viewer_id, now - 90 * 86400.0)).fetchall()
    except Exception:
        return {}
    if not evs:
        return {}
    base = {}
    lst = {}
    for tid, event, value, meta, created in evs:
        age_days = max(now - float(created or now), 0) / 86400.0
        decay = 0.7 if age_days <= 3.0 else 0.2
        if event == "listen":
            try:
                m = json.loads(meta or "{}")
            except Exception:
                m = {}
            try:
                frac = float(m.get("fraction") or 0)
            except (TypeError, ValueError):
                frac = 0.0
            try:
                plays = int(float(m.get("plays") or 0))
            except (TypeError, ValueError):
                plays = 0
            a = lst.setdefault(tid, {"sec": 0.0, "frac": 0.0, "plays": 0, "decay": decay})
            a["sec"] += max(float(value or 0), 0.0)
            a["frac"] = max(a["frac"], frac)
            a["plays"] = max(a["plays"], plays)
            a["decay"] = max(a["decay"], decay)
        else:
            w = {"like": 3.0 if float(value or 0) > 0 else -1.0,
                 "comment_open": 0.4, "comment": 2.0, "share": 2.0,
                 "search": 1.0, "skip": -0.5}.get(event)
            if w is None:
                continue
            base[tid] = base.get(tid, 0.0) + w * decay
    for tid, a in lst.items():
        if a["frac"] >= 0.5 or a["plays"] >= 1:
            w = 2.0 + min(a["sec"], 300.0) / 300.0
            if a["plays"] >= 2:
                w += 1.0
            if a["frac"] >= 0.9:
                w += 1.0
            base[tid] = base.get(tid, 0.0) + w * a["decay"]
    return base


def _user_pref_vec_fused(c, viewer_id, vec_ids, vec_mat, now=None):
    """用户「融合口味向量」：行为 top 歌的 948 维 song_vec 向量加权均值（标签+歌词+听感）。"""
    if np is None or vec_mat is None or not viewer_id:
        return None
    base = _user_base(c, viewer_id, now)
    if not base:
        return None
    pos = {tid: i for i, tid in enumerate(vec_ids)}
    acc, wsum = None, 0.0
    for tid, sc in sorted(base.items(), key=lambda kv: -kv[1])[:30]:
        i = pos.get(tid)
        if i is None or sc <= 0:
            continue
        acc = vec_mat[i] * sc if acc is None else acc + vec_mat[i] * sc
        wsum += sc
    if acc is None or wsum <= 1e-9:
        return None
    v = acc / wsum
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else None


def _user_pref_vec(c, viewer_id, now=None):
    """用户音频偏好向量（2026-11-01，口径与推荐画像一致）：
    近 90 天行为事件打分：listen（秒数×70/20 时间权重 + 反复听/高完听各 +1.0）、
    like +3.0 / comment +2.0 / share +2.0 / search +1.0 / skip -0.5，
    取 top 8 高分歌的 MERT vec 加权均值；无行为/无特征 → None（调用方回退场景种子）。"""
    if not viewer_id:
        return None
    if now is None:
        now = time.time()
    try:
        evs = c.execute(
            "SELECT track_id,event,value,meta,created FROM behavior_events"
            " WHERE user_id=? AND created>=? ORDER BY created DESC LIMIT 400",
            (viewer_id, now - 90 * 86400.0)).fetchall()
    except Exception:
        return None
    if not evs:
        return None
    base = {}
    lst = {}
    for tid, event, value, meta, created in evs:
        age_days = max(now - float(created or now), 0) / 86400.0
        decay = 0.7 if age_days <= 3.0 else 0.2
        if event == "listen":
            try:
                m = json.loads(meta or "{}")
            except Exception:
                m = {}
            try:
                frac = float(m.get("fraction") or 0)
            except (TypeError, ValueError):
                frac = 0.0
            try:
                plays = int(float(m.get("plays") or 0))
            except (TypeError, ValueError):
                plays = 0
            a = lst.setdefault(tid, {"sec": 0.0, "frac": 0.0, "plays": 0, "decay": decay})
            a["sec"] += max(float(value or 0), 0.0)
            a["frac"] = max(a["frac"], frac)
            a["plays"] = max(a["plays"], plays)
            a["decay"] = max(a["decay"], decay)
        else:
            w = {"like": 3.0 if float(value or 0) > 0 else -1.0,
                 "comment_open": 0.4, "comment": 2.0, "share": 2.0,
                 "search": 1.0, "skip": -0.5}.get(event)
            if w is None:
                continue
            base[tid] = base.get(tid, 0.0) + w * decay
    for tid, a in lst.items():
        if a["frac"] >= 0.5 or a["plays"] >= 1:
            w = 2.0 + min(a["sec"], 300.0) / 300.0
            if a["plays"] >= 2:
                w += 1.0
            if a["frac"] >= 0.9:
                w += 1.0
            base[tid] = base.get(tid, 0.0) + w * a["decay"]
    top = sorted(base.items(), key=lambda kv: -kv[1])[:8]
    if not top:
        return None
    vecs, wsum = [], 0.0
    for tid, sc in top:
        try:
            r = c.execute("SELECT vec FROM audio_features WHERE track_id=?", (tid,)).fetchone()
            if r and r[0]:
                arr = json.loads(r[0])
                if arr and len(arr) >= 64:   # MERT 768 维（防旧 20 维混入）
                    a = np.array(arr, dtype=float)
                    n = np.linalg.norm(a)
                    if n > 1e-9:
                        vecs.append(a / n * sc)
                        wsum += sc
        except Exception:
            pass
    if not vecs or wsum <= 1e-9:
        return None
    v = sum(vecs) / wsum
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else None


def radio_tracks(c, tag, viewer_id=None, limit=60, seed=None):
    """按场景返回歌曲 id 列表。
    A. 三路类型筛选（genre 标签 + 音频特征阈值 + MERT 场景种子相似）→ 候选集；
    B. 个性化排序：用户音频偏好向量余弦降序；无行为回退场景种子向量；
    C. 分层随机（seed）：保持"类型 + 个性化"大方向，打散同档顺序。

    为什么需要 seed（2026-11-05 用户反馈「场景音乐每次拉都是同一批、顺序一模一样」）：
    A/B 两步完全确定性（余弦排序 + 无扰动），同一用户任何时候拉同一场景结果都一样，
    听几次就没有新鲜感。这里不动候选集（类型严格性不变），只对排序做「分档窗口洗牌」：
      头部 1/3 视为高相关档、其后每档 ~8 首，档内随机排列；
    同一 seed 内顺序稳定（一次播放会话可复现），不同 seed 顺序不同（每次点场景不一样）。
    """
    rule = RADIO_RULES.get(tag) or RADIO_RULES.get("DJ 模式")
    genres = rule.get("genres") or []
    moods = rule.get("moods") or []
    lo, hi = rule["min_tempo"], rule["max_tempo"]
    dj_signal = bool(rule.get("dj_signal"))
    uid = viewer_id or ""
    # 本人上传的歌不进场景歌单（旧实现只在 tracks 路的 SQL 里排除，collected 路没排）
    avoid_ids = set()
    if uid:
        try:
            avoid_ids = {r[0] for r in c.execute(
                "SELECT id FROM tracks WHERE user_id=?", (uid,)).fetchall()}
        except Exception:
            avoid_ids = set()

    # ---------- A. 类型筛选：① genre 标签路 ----------
    ids = set()
    if genres:
        if dj_signal:
            # DJ 类场景：genre 含 DJ / 电子 / 舞曲 任一即命中（覆盖 'DJ,Others' 这类未打"电子/DJ"的歌）
            cond = "(genre LIKE '%DJ%' OR genre LIKE '%电子%' OR genre LIKE '%舞曲%')"
        else:
            like = "%" + genres[0] + "%"
            cond = "genre LIKE ?"
        try:
            q1 = "SELECT id FROM tracks WHERE offline=0 AND user_id!=? AND " + cond
            q2 = "SELECT id FROM collected_tracks WHERE offline=0 AND play_url!='' AND " + cond
            if dj_signal:
                ids.update(tid for (tid,) in c.execute(q1 + " UNION " + q2, (uid,)))
            else:
                # 2026-11-03 修复：q1 有 2 个占位符（uid/genre）+ q2 有 1 个，共 3 个；
                # 原实现只传 (uid, like) 触发 ProgrammingError 且被 except 静默吞掉，
                # 导致 9 个非 DJ 场景的 genre 标签路从未生效（候选只剩特征路/MERT 路）。
                ids.update(tid for (tid,) in c.execute(q1 + " UNION " + q2, (uid, like, like)))
        except Exception:
            log.exception("radio genre path failed: tag=%s", tag)

    # ---------- A. ② 音频特征路（覆盖未打标签的歌；tempo 缺失=特征不足，不参与） ----------
    tg_map = _title_genre_map(c)
    no_dj = bool(rule.get("no_dj"))
    for tid, d in _feat_rows(c):
        m = d.get("mood")
        t = _safe_tempo(d)
        ok = (m in moods and t > 0 and lo <= t <= hi) if moods else (t > 0 and lo <= t <= hi)
        if not ok:
            continue
        if no_dj:
            # 「起床/通勤/助眠」这类场景不该出现 DJ 版/Remix 快歌：
            # 旧实现只按 tempo/mood 过滤，实测「起床」第一条就是《爱(DJ版)》。
            title, genre = tg_map.get(tid, ("", ""))
            if _is_dj_hint(title, genre):
                continue
        if dj_signal:
            # DJ 类场景需要"DJ 信号"：genre/标题含 DJ 类关键词，或 mood=激昂——防止快节奏流行歌混入
            title, genre = tg_map.get(tid, ("", ""))
            if m != "激昂" and not _is_dj_hint(title, genre):
                continue
        ids.add(tid)

    # ---------- A. ③ MERT 语义路：场景种子相似补足（必须过特征校验，防类型跑偏） ----------
    # 只捞"声学像场景种子 + 自身 mood/tempo 也符合场景"的歌——没标签但真是 DJ/EMO 的歌
    # 特征必然达标；mood 不符的（如古风、民谣）即使声学结构相似也不进候选。
    audio_ids, audio_mat = _load_audio_mat(c)
    # 特征校验表：tid → (mood, tempo)
    feat_map = {tid: (d.get("mood"), _safe_tempo(d)) for tid, d in _feat_rows(c)}
    cand = {tid: 1.0 for tid in ids}        # 无向量种子保底分
    seed_vec = None
    if np is not None and audio_ids is not None and audio_mat is not None:
        idx_map = {tid: i for i, tid in enumerate(audio_ids)}
        sv = [idx_map[s] for s in ids if s in idx_map]
        if sv:
            try:
                seed_vec = audio_mat[sv].mean(axis=0)
                sims = audio_mat @ seed_vec
                order = np.argsort(-sims)
                got = 0
                for i in order:
                    tid = audio_ids[i]
                    if tid in cand:
                        # 已命中候选：取更高相似度
                        cand[tid] = max(cand[tid], float(sims[i]))
                        continue
                    # 补足候选必须过特征校验（mood 匹配场景 + tempo 严格达标；tempo 缺失=特征不足不参与）
                    fm, ft = feat_map.get(tid, (None, 0.0))
                    if moods and fm not in moods:
                        continue
                    if ft <= 0 or not (lo <= ft <= hi):
                        continue
                    if dj_signal:
                        title, genre = tg_map.get(tid, ("", ""))
                        if fm != "激昂" and not _is_dj_hint(title, genre):
                            continue
                    cand[tid] = float(sims[i])
                    got += 1
                    if got >= limit * 4:   # 2026-11-05：池子留 4 倍余量，供 seed 随机取样
                        break
            except Exception:
                pass

    # ---------- B. 个性化排序（用户口径：点 DJ 模式 = 按我的数据推我喜欢的 DJ 歌） ----------
    # 候选集已经是严格类型筛选（全 DJ），排序完全按用户音频偏好向量余弦降序——
    # DJ 候选里优先推"用户数据里最喜欢的那种 DJ"；用户无行为数据（冷启动）
    # 才回退场景种子向量（该场景代表性歌，保证不空）。
    if np is not None and audio_ids is not None and audio_mat is not None and seed_vec is not None:
        idx_map = {tid: i for i, tid in enumerate(audio_ids)}
        pref_vec = _user_pref_vec(c, viewer_id)
        if pref_vec is not None:
            sims = audio_mat @ pref_vec
            for tid in cand:
                i = idx_map.get(tid)
                if i is None:
                    cand[tid] = 0.5    # 无向量（collected）：类型命中可信但无法个性化打分，排中间
                    continue
                cand[tid] = float(sims[i])
        else:
            for tid in cand:
                i = idx_map.get(tid)
                if i is None:
                    cand[tid] = 0.5
                    continue
                cand[tid] = float(seed_vec @ audio_mat[i])

    # ---------- B2. 场景语义层（2026-11-05）：标签场景 + 948 维融合向量 ----------
    # 上面 A/B 用的是「4 类音频 mood + tempo 阈值 + MERT 余弦」，只能回答"节奏对不对"，
    # 回答不了"这是不是睡前该听的歌"。这里补的一层：
    #   ① 用 LLM 场景/情绪标签（通勤/助眠/深夜/健身…）把 3,298 首有标签的歌接进候选；
    #   ② 用 song_vec 的 948 维融合向量算"场景中心"（标签命中歌的加权均值），
    #      再混入用户口味向量 → 既像场景，又像你；
    #   ③ 最终分 = 0.55×向量余弦 + 0.45×标签强度，取代原来纯 MERT 余弦排序。
    try:
        tag_map = _tag_scene_map(c)
        scenes_want = rule.get("scenes") or []
        lmoods = rule.get("lmoods") or []
        tag_w = {}
        if scenes_want or lmoods:
            for tid, d in tag_map.items():
                w = 0.0
                for s in scenes_want:
                    if s in d["scenes"]:
                        w = max(w, 1.0)
                for m in lmoods:
                    if m in d["moods"]:
                        w = max(w, 0.75)
                if w > 0:
                    tag_w[tid] = w
        # 标签命中但 tempo 明显不搭的剔除（防"助眠"里混进 130BPM 的燃曲）
        # 同时 no_dj 场景剔除 DJ 版/Remix（标签路也会命中这些歌）
        feat_map2 = {tid: _safe_tempo(d) for tid, d in _feat_rows(c)}
        for tid in list(tag_w.keys()):
            t = feat_map2.get(tid, 0.0)
            if t > 0 and not (lo <= t <= hi):
                tag_w.pop(tid, None)
                continue
            if no_dj:
                title, genre = tg_map.get(tid, ("", ""))
                if _is_dj_hint(title, genre):
                    tag_w.pop(tid, None)
        if tag_w:
            for tid, w in tag_w.items():
                if tid in avoid_ids:
                    continue
                if tid not in cand:
                    cand[tid] = 0.0
                cand[tid] = max(cand[tid], 0.35 + 0.25 * w)
        # 场景中心向量
        vec_ids, vec_mat = _load_vec_mat(c)
        if vec_ids is not None and vec_mat is not None:
            pos = {tid: i for i, tid in enumerate(vec_ids)}
            idxs, ws = [], []
            for tid, w in tag_w.items():
                i = pos.get(tid)
                if i is not None:
                    idxs.append(i)
                    ws.append(1.0 + w)
            for tid in ids:
                i = pos.get(tid)
                if i is not None:
                    idxs.append(i)
                    ws.append(0.6)
            if len(idxs) >= 5:
                M = vec_mat[np.array(idxs, dtype=np.int64)]
                wv = np.array(ws, dtype=np.float32)
                centro = (M * wv[:, None]).sum(axis=0)
                cn = float(np.linalg.norm(centro))
                if cn > 1e-9:
                    centro = centro / cn
                    # 混入用户口味（有行为才混，冷启动纯场景中心）
                    pref = _user_pref_vec_fused(c, viewer_id, vec_ids, vec_mat)
                    if pref is not None:
                        centro = centro * 0.75 + pref * 0.25
                        nn = float(np.linalg.norm(centro))
                        if nn > 1e-9:
                            centro = centro / nn
                    sims = vec_mat @ centro
                    # 语义扩展：向量最像场景中心、且「要么有标签、要么过规则」的歌
                    extra = 0
                    for i in np.argsort(-sims):
                        if extra >= limit * 3:
                            break
                        tid = vec_ids[int(i)]
                        if tid in avoid_ids:
                            continue
                        if tid not in cand:
                            if tid not in tag_w and tid not in ids:
                                continue          # 只扩已经"类型像"的歌，不放开类型
                            cand[tid] = 0.1
                        extra += 1
                    for tid in list(cand.keys()):
                        i = pos.get(tid)
                        cos = float(sims[i]) if i is not None else 0.0
                        tw = tag_w.get(tid, 0.0)
                        rule_hit = 1.0 if tid in ids else 0.0
                        cand[tid] = 0.55 * max(cos, 0.0) + 0.45 * tw + 0.15 * rule_hit
    except Exception:
        log.exception("radio semantic layer failed: tag=%s", tag)

    ranked = sorted(cand.items(), key=lambda kv: -kv[1])
    pool_ids = [tid for tid, _ in ranked]
    if seed is None or len(pool_ids) <= 3:
        return pool_ids[:limit]
    # ---------- C. 头部保相关 + 其余随机取样 ----------
    # 用户口径（2026-11-05）：每次点场景，**歌本身**要不一样，不只是顺序。
    # 做法：最相关的头部固定（保证"这是我的口味"），其余从更大的候选池按 seed 随机取样，
    # 因此每次进来的歌不同；同一 seed 结果完全可复现（一次播放会话内不会跳变）。
    # 2026-11-05 再调：头部**也**在 top 段内随 seed 打散——原来第一首永远固定，
    # 用户每次点场景都是同一首开场（"怎么还是这首"），观感上等于没变。
    rng = random.Random(str(seed))
    head_n = max(1, min(limit // 4, len(pool_ids)))
    band_n = min(len(pool_ids), max(head_n * 3, 24))
    band = pool_ids[:band_n]
    tail = pool_ids[band_n:]
    rng.shuffle(band)
    head = band[:head_n]
    rest = band[head_n:] + tail
    need = min(limit - head_n, len(rest))
    picked = rng.sample(rest, need) if need > 0 else []
    return head + picked
