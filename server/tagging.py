# -*- coding: utf-8 -*-
"""歌曲语义标签批量生成（LLM 打标）· 2026-11-05

背景：线上 3,026 首采集歌里 903 首没有 genre（30%），audio_features 只有 4 类 mood
（低沉/舒缓/欢快/激昂）——系统"听不懂"没标签的歌，是推荐识别不准的根源。

做法：复用项目已有的 AI 配置（server/ai.py `_ai_cfg()`，qwen-turbo），按歌名/歌手/专辑/
已有曲风/歌词片段批量生成结构化标签，落 `track_tags`：
  genre / mood / scene / language / era / vocal / flags / energy
一次全库约 3.3k 首 ≈ 2.7M tokens（qwen-turbo 量级 ¥1~3），新歌可增量补。

用法：
    python3 server/tagging.py --limit 50                 # 小批验证
    python3 server/tagging.py                            # 全量（只补未打标）
    python3 server/tagging.py --offset 1600 --workers 4  # 分片/并发
    python3 server/tagging.py --force --limit 100        # 重打
"""
import argparse
import json
import logging
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from server.ai import _ai_cfg
from server.config import log
from server.database import db

_TS_RE = re.compile(r"\[[^\]]*\]|<[^>]*>")

SYSTEM_PROMPT = """你是音乐资料库的标签标注员。根据歌曲信息（歌名/歌手/专辑/已有曲风/歌词片段）判断歌曲属性。
只返回一个 JSON 对象，不要解释、不要 markdown，格式：
{"genre":["国风"],"mood":["治愈","空灵"],"scene":["深夜"],"language":"国语","era":"2020s","vocal":"女声","flags":[],"energy":0.4}

规则：
- genre：中文短标签，从 流行/国风/古风/电子/DJ/舞曲/说唱/嘻哈/民谣/摇滚/抒情/纯音乐/轻音乐/爵士/古典/粤语/日语/影视原声/二次元 里选，最多 4 个；不确定按唱法/题材估计
- mood：从 治愈/温暖/伤感/emo/燃/热血/慵懒/轻松/空灵/神秘/怀旧/甜蜜/苦涩/大气/清新 里选 1~3 个
- scene：从 通勤/驾车/健身/助眠/深夜/学习/雨天/夏日/冬日/派对/咖啡/旅行 里选 0~2 个
- language：国语/粤语/英语/日语/韩语/纯音乐/其他（看歌名与歌手判断，拿不准给 其他）
- era：1980s/1990s/2000s/2010s/2020s/未知
- vocal：男声/女声/合唱/纯音乐/未知
- flags：翻唱/Remix/DJ版/Live/伴奏 中命中的，没有给 []
- energy：0~1 的小数（安静=0.2，激烈=0.9）
禁止编造具体不存在的标签；字段必须有。"""


def _lyric_snippet(raw, limit=260):
    """歌词 → 纯文本片段（去 LRC/KRC 时间标签，截前 limit 字）。"""
    if not raw:
        return ""
    s = _TS_RE.sub("", str(raw))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def _norm_list(v, cap=4, maxlen=10):
    out, seen = [], set()
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, (list, tuple)):
        return out
    for x in v:
        t = str(x or "").strip().strip(",，。")
        if not t or len(t) > maxlen or t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= cap:
            break
    return out


def _parse_tags(text):
    """解析模型输出为清洗后的标签 dict；失败返回 None。"""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group())
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    tags = {
        "genre": _norm_list(d.get("genre"), 4, 10),
        "mood": _norm_list(d.get("mood"), 3, 6),
        "scene": _norm_list(d.get("scene"), 2, 6),
        "flags": _norm_list(d.get("flags"), 3, 8),
    }
    for k in ("language", "era", "vocal"):
        v = str(d.get(k) or "").strip()[:8]
        if v:
            tags[k] = [v]
    try:
        e = float(d.get("energy"))
        tags["energy"] = [str(round(min(max(e, 0.0), 1.0), 2))]
    except (TypeError, ValueError):
        pass
    if not any(tags.values()):
        return None
    return tags


def _llm_tag(title, artist, album, genre, lyric, timeout=25):
    cfg = _ai_cfg()
    if not cfg.get("key"):
        log.warning("tagging: AI key 未配置")
        return None
    facts = "歌名：%s\n歌手：%s" % (title or "未知", artist or "未知")
    if album:
        facts += "\n专辑：%s" % album
    if genre:
        facts += "\n已有曲风：%s" % genre
    if lyric:
        facts += "\n歌词片段：%s" % lyric
    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": facts},
        ],
        "temperature": 0.2,
        "max_tokens": 260,
    }
    req = urllib.request.Request(
        cfg["base"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer %s" % cfg["key"]},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8"))
        text = (resp.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
        return _parse_tags(text)
    except Exception as e:
        log.info("tagging: LLM 失败 %s/%s: %s", title, artist, e)
        return None


def _save_tags(c, tid, tags):
    now = time.time()
    n = 0
    for kind, values in tags.items():
        for tag in values:
            try:
                c.execute(
                    "INSERT OR REPLACE INTO track_tags(track_id,kind,tag,confidence,source,created)"
                    " VALUES(?,?,?,?,?,?)", (tid, kind, tag, 1.0, "llm", now))
                n += 1
            except Exception:
                pass
    return n


def _candidates(c, limit, offset, force):
    """待打标歌曲：默认只取完全没有标签的（两张表合并，排除下架/无音源）。"""
    base = (
        "SELECT t.id,t.title,COALESCE(t.artist,''),COALESCE(t.album,''),COALESCE(t.genre,''),COALESCE(t.lrc,'')"
        " FROM tracks t WHERE t.offline=0"
        " UNION ALL "
        "SELECT t.id,t.title,COALESCE(t.artist,''),COALESCE(t.album,''),COALESCE(t.genre,''),COALESCE(t.lrc,'')"
        " FROM collected_tracks t WHERE t.offline=0 AND COALESCE(t.play_url,'')!=''"
    )
    sql = "SELECT * FROM (%s)" % base
    args = []
    if not force:
        sql += " WHERE id NOT IN (SELECT track_id FROM track_tags)"
    sql += " ORDER BY id LIMIT ? OFFSET ?"
    args += [limit, offset]
    return c.execute(sql, args).fetchall()


def _pending_count(c):
    row = c.execute(
        "SELECT COUNT(*) FROM ("
        " SELECT id FROM tracks WHERE offline=0"
        " UNION ALL SELECT id FROM collected_tracks WHERE offline=0 AND COALESCE(play_url,'')!=''"
        ") WHERE id NOT IN (SELECT track_id FROM track_tags)").fetchone()
    return int(row[0] if row else 0)


def run(limit=200, offset=0, workers=4, force=False, dry_run=False):
    cfg = _ai_cfg()
    if not cfg.get("key") and not dry_run:
        log.error("tagging: 未配置 AI key（ai_config.json / AI_API_KEY），退出")
        return {"ok": False, "err": "no_key"}
    c = db()
    rows = _candidates(c, limit, offset, force)
    pending_before = _pending_count(c)
    log.info("tagging: 本批 %d 首，剩余未打标 %d 首", len(rows), pending_before)
    if dry_run:
        c.close()
        return {"ok": True, "dry": True, "batch": len(rows), "pending": pending_before}

    lock = threading.Lock()
    stats = {"ok": 0, "fail": 0, "tags": 0}
    t0 = time.time()

    def work(row):
        tid, title, artist, album, genre, lrc = row
        return tid, _llm_tag(title, artist, album, genre, _lyric_snippet(lrc))

    # 并发只负责 LLM 网络调用；SQLite 写入统一回主线程（连接不可跨线程使用）
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = [ex.submit(work, r) for r in rows]
        for fut in as_completed(futs):
            try:
                tid, tags = fut.result()
            except Exception:
                with lock:
                    stats["fail"] += 1
                continue
            if not tags:
                with lock:
                    stats["fail"] += 1
                continue
            stats["ok"] += 1
            stats["tags"] += _save_tags(c, tid, tags)
            if stats["ok"] % 25 == 0:
                c.commit()
                log.info("tagging: %d/%d 完成（失败 %d）", stats["ok"], len(rows), stats["fail"])
    c.commit()
    pending_after = _pending_count(c)
    c.close()
    elapsed = round(time.time() - t0, 1)
    log.info("tagging: 完成 ok=%d fail=%d tags=%d 用时 %ss 剩余 %d",
             stats["ok"], stats["fail"], stats["tags"], elapsed, pending_after)
    return {"ok": True, "batch": len(rows), "tagged": stats["ok"], "failed": stats["fail"],
            "tags": stats["tags"], "pending": pending_after, "elapsed": elapsed}


def main():
    ap = argparse.ArgumentParser(description="LLM 歌曲语义标签批量生成")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    out = run(limit=a.limit, offset=a.offset, workers=a.workers,
              force=a.force, dry_run=a.dry_run)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
