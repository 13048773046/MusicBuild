# -*- coding: utf-8 -*-
"""歌曲画像 v2 · "系统到底懂不懂这首歌"（2026-11-05）

问题（线上实测）：
  - 旧 tagging 是把歌硬塞进 19 个风格词 / 15 个情绪词 / 12 个场景词的封闭词表，
    结果「深夜」占了 72%、「2020s」占了 86%——标签失去区分度，推荐和没标签一样；
  - 47% 的歌没有歌词，只靠歌名+歌手让模型"猜"，energy 也是猜的；
  - 没有「这首歌是什么」的自然语言理解，无法做语义相似 / 可解释推荐。

本模块做三件事：
  1. 把**真实音频事实**（BPM/能量/打击感/明亮度/动态/时长/有无歌词）喂给模型，
     让判断有据可依，而不是纯靠歌名脑补；
  2. 输出**开放词汇**的细分风格(sub) / 编制(instr) / 主题(theme) / 唱法(vocal) /
     情绪走向(arc) + 情绪三轴(valence/energy/warmth) + 带权场景(scenes{w}) +
     60~120 字自然语言画像(profile)；
  3. energy/valence 用「音频实测 × 模型判断」融合校准，era 允许「未知」不许瞎猜。

落库 `track_profiles`，供 song_embed.py 生成向量、recommend.py 召回排序、
管理端核对。CLI 支持增量/全量/强制重跑。

用法：
    python3 -m server.song_profile --limit 20 --dry-run     # 只打印，看质量
    python3 -m server.song_profile --limit 20               # 小批入库
    python3 -m server.song_profile                          # 全量（只补未画像）
    python3 -m server.song_profile --force --limit 200      # 重画
"""
import argparse
import json
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from server.ai import _ai_cfg
from server.config import log
from server.database import db

_TS_RE = re.compile(r"\[[^\]]*\]|<[^>]*>|<[\d,]+>")
_SPACE_RE = re.compile(r"\s+")
_JSON_RE = re.compile(r"\{.*\}", re.S)

PROFILE_VERSION = 2
LYRIC_LIMIT = 700

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS track_profiles(
    track_id TEXT PRIMARY KEY,
    sub      TEXT DEFAULT '',   -- 细分风格（开放词）
    genres   TEXT DEFAULT '',   -- JSON 数组：大类风格
    moods    TEXT DEFAULT '',   -- JSON 数组：情绪词
    scenes   TEXT DEFAULT '',   -- JSON 对象：{"深夜":0.8} 带权场景
    instr    TEXT DEFAULT '',   -- JSON 数组：编制/器乐
    vocal    TEXT DEFAULT '',   -- 唱法（"男声-气声"）
    theme    TEXT DEFAULT '',   -- 主题（"失恋后自我和解"）
    arc      TEXT DEFAULT '',   -- 情绪走向
    profile  TEXT DEFAULT '',   -- 自然语言画像
    valence  REAL DEFAULT 0.5,
    energy   REAL DEFAULT 0.5,
    warmth   REAL DEFAULT 0.5,
    lang     TEXT DEFAULT '',
    era      TEXT DEFAULT '',
    v        INTEGER DEFAULT 2,
    src      TEXT DEFAULT 'llm',
    updated  REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tp_updated ON track_profiles(updated);
"""

SYSTEM_PROMPT = """你是资深音乐编辑/乐评人，负责给曲库做"这首歌到底是什么"的画像。
用户会给你歌曲事实：歌名、歌手、专辑、平台曲风、真实音频指标（BPM/能量/打击感/明亮度/动态/时长）、是否有歌词、歌词片段。

只输出一个 JSON 对象，不要 markdown、不要解释。字段与要求：
{
 "sub": "细分风格，2~10字，开放词，如 CityPop/国风流行/Trap/钢琴叙事/慢摇R&B/校园民谣；不要只写'流行'",
 "genres": ["大类风格，1~3个，可自由，如 流行/电子/国风/说唱/民谣/摇滚/R&B/爵士/纯音乐/二次元/影视原声"],
 "moods": ["情绪词 2~4个，开放词，如 怅然/释然/燥/黏腻/清冷/热恋感；不要只写 伤感/治愈"],
 "valence": 0.0, "energy": 0.0, "warmth": 0.0,
 "scenes": {"场景": 权重0~1},
 "instr": ["主要器乐/音色 2~4个，如 电钢琴/失真吉他/808鼓/古筝/弦乐铺底/合成器Pad"],
 "vocal": "唱法，如 男声-低吟/女声-气声/说唱-快嘴/童声/纯音乐",
 "theme": "歌词在唱什么，8~20字，如 异地恋的克制思念；纯音乐写'无词，情绪表达'",
 "arc": "情绪走向，10~25字，如 低吟铺垫→副歌克制爆发→尾奏回落",
 "profile": "60~120字自然语言画像：听起来是什么质感、唱的什么、什么人在什么时候会想听。必须具体，禁止套话",
 "lang": "国语/粤语/英语/日语/韩语/纯音乐/其他",
 "era": "1980s/1990s/2000s/2010s/2020s/未知"
}

硬性规则（违反视为失败）：
1. 场景 scenes 只有**确实贴合**才给，最多 3 个；能不给就不给，给 {} 合法。禁止所有歌都写"深夜"。
   允许的场景词：通勤/驾车/健身/助眠/深夜/学习/雨天/夏日/冬日/派对/咖啡/旅行/独处/工作/睡前/清晨。
2. era 没有可靠线索（老歌翻红、翻唱、纯网络发行）一律给 "未知"，禁止默认 2020s。
3. BPM<90 且打击感弱 → energy 不得高于 0.45；BPM>125 或打击感强 → energy 不得低于 0.6。
   已有音频指标时以指标为准，你的判断只做微调（±0.1）。
4. valence：苦涩/失恋/压抑 → 0.2~0.4；温暖/甜/轻快 → 0.6~0.85；燥/燃 但情绪正向 → 0.55~0.8。
5. 没有歌词时，theme/arc 要基于曲风与音频推断，并在文字里体现"推测"语气（如"疑似…"），不要编造具体故事情节。
6. profile 里不要出现"这首歌"三个字开头、不要写"值得一听""非常好听"这类空话。
"""


def _clean_lyric(raw, limit=LYRIC_LIMIT):
    if not raw:
        return ""
    s = _TS_RE.sub(" ", str(raw))
    s = _SPACE_RE.sub(" ", s).strip()
    # 去掉 LRC 头部制作人员行（作词/作曲/编曲/制作人…），它们对理解歌曲几乎无用
    s = re.sub(r"(作词|作曲|编曲|制作人|混音|母带|录音|出品|监制|统筹|发行|OP|SP)\s*[:：]\s*[^ ]{0,40}", " ", s)
    s = _SPACE_RE.sub(" ", s).strip()
    return s[:limit]


def _audio_facts(features):
    """音频特征 dict → 人类可读事实串 + 归一化 energy。"""
    if not isinstance(features, dict):
        return "", None
    try:
        bpm = float(features.get("tempo") or 0)
    except (TypeError, ValueError):
        bpm = 0.0
    onset = float(features.get("onset_rate") or 0)
    energy = float(features.get("energy") or 0)
    dyn = float(features.get("dynamic") or 0)
    cent = float(features.get("centroid") or 0)
    parts = []
    if bpm > 0:
        parts.append("BPM %.0f（%s）" % (bpm, "慢板" if bpm < 90 else ("中速" if bpm <= 120 else "快歌")))
    if onset > 0:
        parts.append("打击密度 %.2f/秒（%s）" % (onset, "律动强" if onset >= 4 else ("中等" if onset >= 2 else "很静")))
    parts.append("响度能量 %.2f" % energy)
    if dyn:
        parts.append("动态范围 %.2f（%s）" % (dyn, "起伏大" if dyn > 0.25 else "平稳"))
    if cent > 0:
        parts.append("音色明亮度 %.0fHz（%s）" % (cent, "明亮" if cent > 2200 else ("偏暗" if cent < 1200 else "中性")))
    # 音频侧的 energy 估计 0..1：BPM/打击感/响度/动态 各占一份，压到合理区间
    est = None
    if bpm > 0:
        e_bpm = min(1.0, max(0.0, (bpm - 60.0) / 90.0))
        e_on = min(1.0, onset / 6.0) if onset > 0 else e_bpm
        est = round(0.45 * e_bpm + 0.35 * e_on + 0.20 * min(1.0, energy * 1.6), 3)
    return "；".join(parts), est


def _facts(row, feats, tags):
    tid, title, artist, album, genre, lrc, dur = row
    af, est = _audio_facts(feats)
    lines = ["歌名：%s" % (title or "未知")]
    lines.append("歌手：%s" % (artist or "未知"))
    if album:
        lines.append("专辑：%s" % album)
    if genre:
        lines.append("平台曲风：%s" % genre)
    if dur:
        try:
            lines.append("时长：%d秒" % int(float(dur)))
        except (TypeError, ValueError):
            pass
    if af:
        lines.append("音频指标：%s" % af)
    lyric = _clean_lyric(lrc)
    if lyric:
        lines.append("歌词片段：%s" % lyric)
    else:
        lines.append("歌词：无（只能依据音频与曲风推断，theme/arc 需注明推测）")
    if tags:
        lines.append("旧标签（仅供参考，可推翻）：%s" % tags)
    return "\n".join(lines), est


def _call_llm(facts, cfg, timeout=45):
    body = {
        "model": cfg.get("profile_model") or cfg["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": facts},
        ],
        "temperature": 0.4,
        "max_tokens": 700,
    }
    req = urllib.request.Request(
        cfg["base"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer %s" % cfg["key"]},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode("utf-8"))
    return (resp.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""


def _num(v, lo=0.0, hi=1.0, default=0.5):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return round(min(hi, max(lo, x)), 3)


def _strlist(v, cap=4, maxlen=14):
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, (list, tuple)):
        return []
    out, seen = [], set()
    for x in v:
        t = str(x or "").strip().strip(",，。、")
        if not t or len(t) > maxlen or t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= cap:
            break
    return out


_SCENE_ALLOW = {"通勤", "驾车", "健身", "助眠", "深夜", "学习", "雨天", "夏日", "冬日",
                "派对", "咖啡", "旅行", "独处", "工作", "睡前", "清晨"}


def _scenes(v):
    """{"深夜":0.8} 或 ["深夜"] → 清洗后的 dict（只保留白名单、权重 0~1、最多 3 个）。"""
    out = {}
    if isinstance(v, dict):
        items = list(v.items())
    elif isinstance(v, (list, tuple)):
        items = [(x, 0.6) for x in v]
    else:
        return out
    for k, w in items:
        k = str(k or "").strip()
        if k not in _SCENE_ALLOW or k in out:
            continue
        out[k] = _num(w, 0.0, 1.0, 0.6)
    if len(out) > 3:
        out = dict(sorted(out.items(), key=lambda kv: -kv[1])[:3])
    return out


def parse_profile(text, est_energy=None):
    """模型输出 → 规范化画像 dict；失败返回 None。"""
    if not text:
        return None
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        d = json.loads(m.group())
    except Exception:
        try:
            d = json.loads(m.group().replace("'", '"'))
        except Exception:
            return None
    if not isinstance(d, dict):
        return None
    prof = {
        "sub": str(d.get("sub") or "").strip()[:24],
        "genres": _strlist(d.get("genres"), 3, 12),
        "moods": _strlist(d.get("moods"), 4, 8),
        "scenes": _scenes(d.get("scenes")),
        "instr": _strlist(d.get("instr"), 4, 14),
        "vocal": str(d.get("vocal") or "").strip()[:20],
        "theme": str(d.get("theme") or "").strip()[:40],
        "arc": str(d.get("arc") or "").strip()[:60],
        "profile": _SPACE_RE.sub(" ", str(d.get("profile") or "")).strip()[:220],
        "valence": _num(d.get("valence")),
        "energy": _num(d.get("energy")),
        "warmth": _num(d.get("warmth")),
        "lang": str(d.get("lang") or "").strip()[:8],
        "era": str(d.get("era") or "未知").strip()[:8],
    }
    if prof["era"] not in ("1980s", "1990s", "2000s", "2010s", "2020s"):
        prof["era"] = "未知"
    if est_energy is not None:
        # 音频实测 × 模型判断 融合：实测权重更高（模型容易把安静歌判成 0.4+）
        prof["energy"] = _num(0.6 * est_energy + 0.4 * prof["energy"])
    if len(prof["profile"]) < 13 and not prof["sub"]:
        return None
    return prof


def _save(c, tid, p):
    now = time.time()
    c.execute(
        "INSERT OR REPLACE INTO track_profiles(track_id,sub,genres,moods,scenes,instr,vocal,"
        "theme,arc,profile,valence,energy,warmth,lang,era,v,src,updated)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, p["sub"], json.dumps(p["genres"], ensure_ascii=False),
         json.dumps(p["moods"], ensure_ascii=False),
         json.dumps(p["scenes"], ensure_ascii=False),
         json.dumps(p["instr"], ensure_ascii=False),
         p["vocal"], p["theme"], p["arc"], p["profile"],
         p["valence"], p["energy"], p["warmth"], p["lang"], p["era"],
         PROFILE_VERSION, "llm", now))


def candidates(c, limit, offset, force=False):
    """待画像歌曲（tracks + collected_tracks 合并，排除下架/无音源）。"""
    base = (
        "SELECT t.id id,t.title title,COALESCE(t.artist,'') artist,COALESCE(t.album,'') album,"
        "COALESCE(t.genre,'') genre,COALESCE(t.lrc,'') lrc,COALESCE(t.dur,0) dur,"
        "COALESCE(t.offline,0) offline FROM tracks t"
        " UNION ALL "
        "SELECT t.id,t.title,COALESCE(t.artist,''),COALESCE(t.album,''),"
        "COALESCE(t.genre,''),COALESCE(t.lrc,''),COALESCE(t.duration,0),"
        "COALESCE(t.offline,0) FROM collected_tracks t"
    )
    sql = "SELECT id,title,artist,album,genre,lrc,dur FROM (%s) WHERE offline=0" % base
    if not force:
        sql += " AND id NOT IN (SELECT track_id FROM track_profiles)"
    sql += " ORDER BY id LIMIT ? OFFSET ?"
    return c.execute(sql, (limit, offset)).fetchall()


def pending_count(c):
    sql = ("SELECT COUNT(*) FROM ("
           " SELECT id,COALESCE(offline,0) o FROM tracks"
           " UNION ALL SELECT id,COALESCE(offline,0) o FROM collected_tracks"
           " WHERE COALESCE(play_url,'')!='')"
           " WHERE o=0 AND id NOT IN (SELECT track_id FROM track_profiles)")
    row = c.execute(sql).fetchone()
    return int(row[0] if row else 0)


def _ensure_schema(c):
    for stmt in [s.strip() for s in SCHEMA_SQL.split(";") if s.strip()]:
        try:
            c.execute(stmt)
        except Exception:
            log.info("song_profile: schema skip %s", stmt[:60])
    c.commit()


def _profile_one(row, feats, tags, cfg):
    facts, est = _facts(row, feats, tags)
    last = None
    for attempt in range(2):
        try:
            txt = _call_llm(facts, cfg)
            p = parse_profile(txt, est)
            if p:
                return p, facts
            last = "parse_fail"
        except Exception as e:
            last = str(e)[:120]
            time.sleep(0.8 * (attempt + 1))
    log.info("song_profile: 失败 %s -> %s", (row[1] or "")[:20], last)
    return None, facts


def run(limit=200, offset=0, workers=6, force=False, dry_run=False, verbose=False):
    cfg = _ai_cfg()
    if not cfg.get("key") and not dry_run:
        log.error("song_profile: 未配置 AI key，退出")
        return {"ok": False, "err": "no_key"}
    c = db()
    _ensure_schema(c)
    rows = candidates(c, limit, offset, force)
    pend = pending_count(c)
    log.info("song_profile: 本批 %d 首，待画像 %d 首", len(rows), pend)

    # 预取音频特征与旧标签（只读，主线程一次查完，避免每首两次查询）
    ids = [r[0] for r in rows]
    feats, tags = {}, {}
    if ids:
        marks = ",".join("?" * len(ids))
        for tid, f in c.execute(
                "SELECT track_id,features FROM audio_features WHERE track_id IN (%s)" % marks, ids):
            try:
                feats[tid] = json.loads(f) if f else {}
            except Exception:
                feats[tid] = {}
        for tid, kind, tag in c.execute(
                "SELECT track_id,kind,tag FROM track_tags WHERE track_id IN (%s)" % marks, ids):
            tags.setdefault(tid, []).append("%s:%s" % (kind, tag))

    if dry_run:
        out = []
        for r in rows[:max(1, min(limit, 8))]:
            p, facts = _profile_one(r, feats.get(r[0]), " ".join(tags.get(r[0], []))[:160], cfg)
            out.append({"id": r[0], "title": r[1], "artist": r[2], "facts": facts, "profile": p})
        c.close()
        return {"ok": True, "dry": True, "sample": out, "pending": pend}

    stats = {"ok": 0, "fail": 0}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(_profile_one, r, feats.get(r[0]), " ".join(tags.get(r[0], []))[:160], cfg): r
                for r in rows}
        for fut in as_completed(futs):
            r = futs[fut]
            try:
                p, _facts_txt = fut.result()
            except Exception:
                p = None
            if not p:
                stats["fail"] += 1
                continue
            try:
                _save(c, r[0], p)
                stats["ok"] += 1
                # 短事务：每首立即提交。WAL 下提交极便宜，但把写锁持有时间从
                # “50 首 ≈ 40 秒”压到毫秒级——否则 web 服务启动/推荐接口会
                # 撞上 database is locked（线上已复现一次启动失败）。
                c.commit()
            except Exception as e:
                stats["fail"] += 1
                log.info("song_profile: 写库失败 %s %s", r[0], e)
                try:
                    c.rollback()
                except Exception:
                    pass
            if stats["ok"] and stats["ok"] % 50 == 0:
                log.info("song_profile: %d/%d（失败 %d）", stats["ok"], len(rows), stats["fail"])
                if verbose:
                    print(json.dumps(p, ensure_ascii=False))
    c.commit()
    after = pending_count(c)
    c.close()
    dt = round(time.time() - t0, 1)
    log.info("song_profile: 完成 ok=%d fail=%d 用时 %ss 剩余 %d",
             stats["ok"], stats["fail"], dt, after)
    return {"ok": True, "batch": len(rows), "done": stats["ok"], "failed": stats["fail"],
            "pending": after, "elapsed": dt}


def main():
    ap = argparse.ArgumentParser(description="歌曲画像 v2（LLM 歌曲理解）")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    out = run(limit=a.limit, offset=a.offset, workers=a.workers,
              force=a.force, dry_run=a.dry_run, verbose=a.verbose)
    print(json.dumps(out, ensure_ascii=False, indent=2)[:8000])


if __name__ == "__main__":
    main()
