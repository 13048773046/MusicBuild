# -*- coding: utf-8 -*-
"""推荐系统 · 全维度歌曲理解层（2026-11-05）。

背景（线上数据实测）：
  - 3026 首采集歌里 903 首没有 genre 标签（30%）；
  - 17 + 161 首歌的歌手是「A / B」「A & B」「A feat. B」这类复合串，
    旧路径 `artist = ?` 精确匹配直接漏掉；
  - genre 是「粤语,Pop」「电子/DJ」「DJ,Others」这类混合串，LIKE 只能部分命中；
  - audio_features 覆盖 3298 首（mood / tempo / energy），此前只有 VIP 精准路在用。

本模块把「歌曲理解」统一成三个可索引维度，供 recommend.py 召回 / 排序 / 多样性使用：
  1. artists：复合歌手拆分 + 归一化（"周杰伦 / 阿信" → {"周杰伦","阿信"}）
  2. genres ：风格词规范化（DJ/电子/舞曲/EDM → electronic；流行/Pop → pop …）
  3. audio  ：听感桶（mood | tempo 档），让"没标签"的歌也能按听感被召回

索引 60s TTL 内存缓存（~3.3k 首，构建 <50ms），提供 id→meta 与三个倒排表，
顺带消除旧代码里逐候选 `SELECT ... WHERE id=?` 的 N+1 查询。
"""
import json
import os
import re
import threading
import time

from .config import REC_LIBROSA_DIM  # noqa: F401  （保持与配置层同包导入关系）

_INDEX_TTL = 300.0
_index_lock = threading.Lock()
_index_cache = {"t": 0.0, "idx": None}

# ---------------------------------------------------------------- 歌手拆分

_ARTIST_SPLIT_RE = re.compile(
    r"\s*(?:/|、|，|,|;|；|\+|&|＆|×|·|\bwith\b|\bfeat\.?\b|\bft\.?\b|\bvs\.?\b)\s*",
    re.IGNORECASE,
)
_ARTIST_DROP = {
    "", "未知", "未知歌手", "未知艺术家", "群星", "various artists", "various",
    "佚名", "anonymous", "unknown", "n/a", "na", "-",
}


def norm_artist(name):
    """歌手归一化：小写、去首尾标点/空格、压缩空白。"""
    s = str(name or "").strip().lower()
    s = re.sub(r"[\s]+", " ", s)
    return s.strip(" .-_()[]（）【】")


def split_artists(artist, limit=6):
    """复合歌手拆分为归一化 token 列表（去重、限量）。"""
    raw = str(artist or "")
    if not raw:
        return []
    parts = _ARTIST_SPLIT_RE.split(raw)
    out, seen = [], set()
    for p in parts:
        k = norm_artist(p)
        if not k or k in _ARTIST_DROP or k in seen:
            continue
        # 纯符号/纯数字串（时长、编号）不算歌手
        if not re.search(r"[0-9a-z\u4e00-\u9fa5]", k):
            continue
        seen.add(k)
        out.append(k)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- 风格规范化

_GENRE_SYNONYMS = {
    # 电子 / 舞曲 / DJ
    "电子": "electronic", "电音": "electronic", "dj": "electronic", "舞曲": "electronic",
    "electronic": "electronic", "edm": "electronic", "house": "electronic",
    "techno": "electronic", "trance": "electronic", "dance": "electronic",
    "蹦迪": "electronic", "夜店": "electronic",
    # 流行
    "流行": "pop", "pop": "pop", "chinese pop": "pop", "国语流行": "pop",
    "华语流行": "pop", "粤语流行": "pop", "mandopop": "pop", "cantopop": "pop",
    # 摇滚
    "摇滚": "rock", "rock": "rock", "punk": "rock", "朋克": "rock",
    # 民谣
    "民谣": "folk", "folk": "folk", "乡村": "folk", "country": "folk",
    # 说唱
    "说唱": "rap", "嘻哈": "rap", "hip hop": "rap", "hip-hop": "rap",
    "rap": "rap", "trap": "rap",
    # R&B / 灵魂
    "r&b": "rnb", "rnb": "rnb", "soul": "rnb", "灵魂": "rnb",
    # 古风 / 国风
    "古风": "guofeng", "国风": "guofeng", "中国风": "guofeng",
    # 纯音乐 / 轻音乐 / BGM
    "纯音乐": "instrumental", "轻音乐": "instrumental", "instrumental": "instrumental",
    "bgm": "instrumental", "轻音乐/纯音乐": "instrumental",
    # 现场 / 影视 / 二次元
    "live": "live", "现场": "live",
    "影视": "ost", "ost": "ost", "原声": "ost", "soundtrack": "ost",
    "动漫": "anime", "二次元": "anime", "anime": "anime",
    # 爵士 / 古典
    "爵士": "jazz", "jazz": "jazz", "古典": "classical", "classical": "classical",
}
_GENRE_DROP = {
    "", "其他", "其它", "others", "other", "未知", "未分类", "unknown", "misc",
    "国语", "华语", "普通话", "中文", "chinese", "vocal", "vocaloid",
}


def canon_genres(genre, limit=4):
    """风格串 → 规范化风格集合（小写英文 tag）。"""
    raw = str(genre or "")
    if not raw:
        return []
    out, seen = [], set()
    for part in re.split(r"[,/、，;；|]+", raw):
        k = part.strip().lower()
        if not k or k in _GENRE_DROP:
            continue
        tag = _GENRE_SYNONYMS.get(k)
        if tag is None:
            # 未收录：保留原文（截断）作为弱标签，便于小众风格召回
            tag = k if len(k) <= 12 else None
        if not tag or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- 听感桶

_MOODS = ("低沉", "舒缓", "欢快", "激昂")


def _tempo_band(tempo):
    try:
        t = float(tempo or 0)
    except (TypeError, ValueError):
        return ""
    if t <= 0:
        return ""
    if t < 90:
        return "慢"
    if t <= 120:
        return "中"
    return "快"


def audio_bucket(features):
    """audio_features.features(dict) → 听感桶 "mood|tempo档"，无特征返回 ""。"""
    if not isinstance(features, dict):
        return ""
    mood = features.get("mood")
    if mood not in _MOODS:
        mood = "未定"
    band = _tempo_band(features.get("tempo"))
    if not band:
        return ""
    return mood + "|" + band


# ---------------------------------------------------------------- 索引

class TasteIndex(object):
    """歌曲理解索引：id→meta + 歌手/风格/听感倒排表（60s 缓存，线程安全）。"""

    __slots__ = ("meta", "_artists", "_genres", "_buckets", "_moods", "_scenes", "built_at", "n")

    def __init__(self):
        self.meta = {}
        self._artists = {}
        self._genres = {}
        self._buckets = {}
        self._moods = {}
        self._scenes = {}
        self.built_at = 0.0
        self.n = 0

    # ---- 访问器 ----
    def get(self, tid):
        return self.meta.get(tid)

    def by_artist(self, token):
        return self._artists.get(token) or ()

    def by_genre(self, tag):
        return self._genres.get(tag) or ()

    def by_bucket(self, bucket):
        return self._buckets.get(bucket) or ()

    def by_mood(self, mood):
        return self._moods.get(mood) or ()

    def by_scene(self, scene):
        return self._scenes.get(scene) or ()

    @property
    def buckets(self):
        return list(self._buckets.keys())

    def hot(self, tid):
        m = self.meta.get(tid)
        if not m:
            return 0.0
        return float(m.get("plays") or 0) + float(m.get("likes") or 0) * 3.0

    def similar(self, a, b):
        """两首歌的语义相似度 0..1（歌手 token 共享 / 风格共享 / 同听感桶）。"""
        ma, mb = self.meta.get(a), self.meta.get(b)
        if not ma or not mb:
            return 0.0
        sim = 0.0
        sa, sb = ma["artists"], mb["artists"]
        if sa and sb:
            inter = len(sa & sb)
            if inter:
                sim += 0.55 * min(1.0, inter / float(min(len(sa), len(sb))))
        ga, gb = ma["genres"], mb["genres"]
        if ga and gb and (ga & gb):
            sim += 0.25
        if ma["bucket"] and ma["bucket"] == mb["bucket"]:
            sim += 0.15
        if ma.get("scenes") and mb.get("scenes") and (ma["scenes"] & mb["scenes"]):
            sim += 0.1
        if ma["album"] and ma["album"] == mb["album"]:
            sim += 0.1
        return min(sim, 1.0)

    # ---- 构建 ----
    def build(self, c):
        rows = []
        try:
            rows += c.execute(
                "SELECT t.id,t.title,COALESCE(t.artist,''),COALESCE(t.album,''),COALESCE(t.genre,''),"
                " COALESCE(t.play_count,0),COALESCE(t.like_count,0),COALESCE(t.created,0),f.features"
                " FROM tracks t LEFT JOIN audio_features f ON f.track_id=t.id"
                " WHERE t.offline=0 AND COALESCE(t.user_id,'')!=''").fetchall()
        except Exception:
            pass
        try:
            rows += c.execute(
                "SELECT t.id,t.title,COALESCE(t.artist,''),COALESCE(t.album,''),COALESCE(t.genre,''),"
                " COALESCE(t.play_count,0),COALESCE(t.like_count,0),COALESCE(t.created,0),f.features"
                " FROM collected_tracks t LEFT JOIN audio_features f ON f.track_id=t.id"
                " WHERE t.offline=0 AND COALESCE(t.play_url,'')!=''").fetchall()
        except Exception:
            pass

        meta, artists, genres, buckets = {}, {}, {}, {}
        for tid, title, artist, album, genre, plays, likes, created, feat in rows:
            if not tid:
                continue
            try:
                feats = json.loads(feat) if feat else {}
            except Exception:
                feats = {}
            if not isinstance(feats, dict):
                feats = {}
            at = split_artists(artist)
            gt = canon_genres(genre)
            bk = audio_bucket(feats)
            meta[tid] = {
                "id": tid, "title": title or "", "artist": artist or "",
                "album": album or "", "genre": genre or "",
                "artists": frozenset(at), "genres": frozenset(gt),
                "bucket": bk, "plays": plays or 0, "likes": likes or 0,
                "created": created or 0,
                "moods": frozenset(), "scenes": frozenset(), "flags": frozenset(),
            }
            for t in at:
                artists.setdefault(t, []).append(tid)
            for g in gt:
                genres.setdefault(g, []).append(tid)
            if bk:
                buckets.setdefault(bk, []).append(tid)

        # 倒排表按热度倒序 → 召回天然优先热歌，且结果确定性（不依赖 set 迭代顺序）
        def _sort_map(m):
            for k, ids in m.items():
                ids.sort(key=lambda x: (-(meta[x]["plays"] + meta[x]["likes"] * 3), x))
            return m

        # 2026-11-05 LLM 语义标签（track_tags）：genre 并入风格倒排表，mood/scene 单独建索引
        moods, scenes = {}, {}
        try:
            for tid, kind, tag in c.execute(
                    "SELECT track_id,kind,tag FROM track_tags").fetchall():
                m = meta.get(tid)
                if not m or not tag:
                    continue
                if kind == "genre":
                    for g in canon_genres(tag):
                        if g not in m["genres"]:
                            m["genres"] = m["genres"] | {g}
                            genres.setdefault(g, []).append(tid)
                elif kind == "mood":
                    m["moods"] = m["moods"] | {tag}
                    moods.setdefault(tag, []).append(tid)
                elif kind == "scene":
                    m["scenes"] = m["scenes"] | {tag}
                    scenes.setdefault(tag, []).append(tid)
                elif kind == "flags":
                    m["flags"] = m["flags"] | {tag}
        except Exception:
            pass

        self.meta = meta
        self._artists = _sort_map(artists)
        self._genres = _sort_map(genres)
        self._buckets = _sort_map(buckets)
        self._moods = _sort_map(moods)
        self._scenes = _sort_map(scenes)
        self.n = len(meta)
        self.built_at = time.time()
        return self


def get_taste_index(c):
    """取歌曲理解索引（60s TTL）。构建失败返回 None（调用方回退旧逻辑）。"""
    now = time.time()
    with _index_lock:
        idx = _index_cache.get("idx")
        if idx is not None and now - float(_index_cache.get("t") or 0.0) < _INDEX_TTL:
            return idx
        try:
            idx = TasteIndex().build(c)
            _index_cache["idx"] = idx
            _index_cache["t"] = now
            return idx
        except Exception:
            return None


def invalidate_taste_index():
    """强制失效（上传/采集/特征写入后调用；幂等）。"""
    with _index_lock:
        _index_cache["idx"] = None
        _index_cache["t"] = 0.0
