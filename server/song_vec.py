# -*- coding: utf-8 -*-
"""歌曲语义向量层（2026-11-05）。

目标：让"这首歌是什么"变成一个可计算的向量，而不是一堆孤立标签。
数据来源（全部本地可得，不依赖外部 AI）：
  1. 文本侧：歌名 + 歌手 + 专辑 + 平台曲风 + 歌词片段 + LLM 标签 + LLM 画像
     → 中文字 bigram / 英文词 → TF-IDF → LSA 降维（scipy svds，160 维）
  2. 音频侧：MERT 768 维语义向量 + librosa 20 维音色/节奏特征（都在 audio_features 表里）
  3. 融合：两侧各自 L2 归一化后按权重拼接 → 余弦相似度 = 加权余弦和

产出：
  track_emb       每首歌的 text / fuse 向量（float32 blob）
  track_neighbors 每首歌的 topN "听起来像"邻居（供 i2i 召回，比 16 个用户的共现可靠）

用法：
    python3 -m server.song_vec --build          # 建/更新向量（全量，约 20~40s）
    python3 -m server.song_vec --neighbors      # 建邻居表（约 10s）
    python3 -m server.song_vec --all            # 两步一起
    python3 -m server.song_vec --stats
"""
import argparse
import json
import math
import re
import time
import zlib

from server.config import log
from server.database import db

EMB_VERSION = 1
TEXT_DIM = 160
NEIGHBOR_TOP = 30
MAX_TERMS = 40000
W_TEXT = 0.55      # 融合权重：语义（歌词/标签/画像）
W_AUDIO = 0.45     # 融合权重：听感（MERT + librosa）

_TS_RE = re.compile(r"\[[^\]]*\]|<[^>]*>|<[\d,]+>")
_CREDIT_RE = re.compile(
    r"(作词|作曲|编曲|制作人|混音|母带|录音|出品|监制|统筹|发行|企划|策划|营销|推广|"
    r"视觉设计|封面|OP|SP|词|曲)\s*[:：]\s*[^\s]{0,40}")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS track_emb(
    track_id TEXT PRIMARY KEY,
    dim      INTEGER DEFAULT 0,
    tdim     INTEGER DEFAULT 0,
    text     BLOB,
    fuse     BLOB,
    v        INTEGER DEFAULT 1,
    updated  REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS track_neighbors(
    track_id     TEXT NOT NULL,
    sim_track_id TEXT NOT NULL,
    rank         INTEGER DEFAULT 0,
    score        REAL DEFAULT 0,
    src          TEXT DEFAULT 'vec',
    built_at     REAL DEFAULT 0,
    PRIMARY KEY(track_id, sim_track_id)
);
CREATE INDEX IF NOT EXISTS idx_tn_track ON track_neighbors(track_id, rank);
"""


def _clean_lyric(raw, limit=420):
    if not raw:
        return ""
    s = _TS_RE.sub(" ", str(raw))
    s = _CREDIT_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def _tokens(text):
    """中文字 bigram + 英文/数字词。中文单字也保留（短文本信息量低时兜底）。"""
    s = (text or "").lower()
    out = []
    for w in re.findall(r"[a-z0-9][a-z0-9'\-]{1,}", s):
        out.append(w)
    for run in re.findall(r"[\u4e00-\u9fa5]+", s):
        n = len(run)
        if n == 1:
            out.append(run)
            continue
        for i in range(n - 1):
            out.append(run[i:i + 2])
    return out


def _doc(row, prof, tags):
    """拼每首歌的"文档"——喂给 TF-IDF 的文本。"""
    tid, title, artist, album, genre, lrc = row
    parts = [str(title or ""), str(artist or ""), str(artist or ""), str(album or ""),
             str(genre or "")]
    if tags:
        for kind, tag in tags:
            parts.append(tag)
            parts.append(tag)   # 标签加权：出现两次
            if kind == "mood":
                parts.append(tag)
    if prof:
        sub, genres, moods, instr, theme, arc, text = prof
        for x in (sub, theme, arc, text):
            if x:
                parts.append(x)
        for j in (genres, moods, instr):
            try:
                for t in json.loads(j or "[]"):
                    parts.append(str(t))
            except Exception:
                pass
    ly = _clean_lyric(lrc)
    if ly:
        parts.append(ly)
    return " ".join(parts)


def load_docs(c):
    """取全库歌曲行 + 画像 + 标签，返回 [(tid, doc_text, feats)]。"""
    rows = []
    try:
        rows += c.execute(
            "SELECT t.id,t.title,COALESCE(t.artist,''),COALESCE(t.album,''),COALESCE(t.genre,''),"
            "COALESCE(t.lrc,'') FROM tracks t WHERE t.offline=0 AND COALESCE(t.user_id,'')!=''"
        ).fetchall()
    except Exception:
        pass
    try:
        rows += c.execute(
            "SELECT t.id,t.title,COALESCE(t.artist,''),COALESCE(t.album,''),COALESCE(t.genre,''),"
            "COALESCE(t.lrc,'') FROM collected_tracks t"
            " WHERE t.offline=0 AND COALESCE(t.play_url,'')!=''").fetchall()
    except Exception:
        pass

    tags = {}
    try:
        for tid, kind, tag in c.execute("SELECT track_id,kind,tag FROM track_tags").fetchall():
            if tag and kind not in ("energy", "era", "language", "vocal", "flags"):
                tags.setdefault(tid, []).append((kind, tag))
    except Exception:
        pass

    profs = {}
    try:
        for tid, sub, genres, moods, instr, theme, arc, profile in c.execute(
                "SELECT track_id,sub,genres,moods,instr,theme,arc,profile FROM track_profiles"
        ).fetchall():
            profs[tid] = (sub, genres, moods, instr, theme, arc, profile)
    except Exception:
        pass

    feats = {}
    try:
        for tid, f, vec, raw in c.execute(
                "SELECT track_id,features,vec,raw_vec FROM audio_features").fetchall():
            feats[tid] = (f, vec, raw)
    except Exception:
        pass

    out = []
    for row in rows:
        tid = row[0]
        if not tid:
            continue
        out.append((tid, _doc(row, profs.get(tid), tags.get(tid)), feats.get(tid)))
    return out


def build_vectors(c, verbose=False):
    """TF-IDF + LSA 文本向量，融合 MERT/librosa 音频向量，写 track_emb。"""
    import numpy as np
    from scipy import sparse
    from scipy.sparse.linalg import svds

    t0 = time.time()
    docs = load_docs(c)
    n = len(docs)
    if n < 50:
        return {"ok": False, "err": "too_few_docs", "n": n}
    log.info("song_vec: 文档 %d 首", n)

    # ---------- 1) 词表 + 文档频率 ----------
    toks = []
    df = {}
    for _tid, text, _f in docs:
        tk = _tokens(text)
        toks.append(tk)
        for t in set(tk):
            df[t] = df.get(t, 0) + 1
    keep = {t: i for i, t in enumerate(
        sorted((t for t, d in df.items() if 2 <= d <= n * 0.6),
               key=lambda t: -df[t])[:MAX_TERMS])}
    vocab_n = len(keep)
    log.info("song_vec: 词表 %d（原 %d）", vocab_n, len(df))

    # ---------- 2) TF-IDF 稀疏矩阵 ----------
    indptr, indices, data = [0], [], []
    idf_by_col = [0.0] * vocab_n
    for t, j in keep.items():
        idf_by_col[j] = math.log((1.0 + n) / (1.0 + df[t])) + 1.0
    for tk in toks:
        cnt = {}
        for t in tk:
            j = keep.get(t)
            if j is not None:
                cnt[j] = cnt.get(j, 0) + 1
        for j, v in cnt.items():
            indices.append(j)
            data.append((1.0 + math.log(v)) * idf_by_col[j])
        indptr.append(len(indices))
    X = sparse.csr_matrix((np.array(data, dtype=np.float32), np.array(indices, dtype=np.int32),
                           np.array(indptr, dtype=np.int64)), shape=(n, vocab_n))
    # 行归一化（余弦等价）
    norms = np.sqrt(X.multiply(X).sum(axis=1)).A1
    norms[norms < 1e-9] = 1.0
    Xn = sparse.diags(1.0 / norms).dot(X)

    # ---------- 3) LSA 降维 ----------
    k = min(TEXT_DIM, max(16, min(n - 2, vocab_n - 2)))
    try:
        U, S, _Vt = svds(Xn, k=k, random_state=0)
        order = np.argsort(-S)
        T = U[:, order] * S[order]
    except Exception as e:
        log.info("song_vec: svds 失败，退回哈希投影: %s", e)
        T = _hash_project(toks, 1024)
    T = _l2(T)

    # ---------- 4) 音频向量 ----------
    A, audio_mask = _audio_matrix(docs, n)
    log.info("song_vec: 音频向量覆盖 %d/%d", int(audio_mask.sum()), n)

    # ---------- 5) 融合 ----------
    tw = math.sqrt(W_TEXT)
    aw = math.sqrt(W_AUDIO)
    F = np.zeros((n, T.shape[1] + A.shape[1]), dtype=np.float32)
    F[:, :T.shape[1]] = T * tw
    F[:, T.shape[1]:] = A * aw
    F = _l2(F)

    # ---------- 6) 写库 ----------
    now = time.time()
    rows = []
    for i, (tid, _txt, _f) in enumerate(docs):
        rows.append((tid, int(F.shape[1]), int(T.shape[1]),
                     T[i].astype(np.float32).tobytes(),
                     F[i].astype(np.float32).tobytes(), EMB_VERSION, now))
    c.execute("DELETE FROM track_emb")
    c.executemany(
        "INSERT OR REPLACE INTO track_emb(track_id,dim,tdim,text,fuse,v,updated)"
        " VALUES(?,?,?,?,?,?,?)", rows)
    c.commit()
    dt = round(time.time() - t0, 1)
    log.info("song_vec: 向量完成 %d 首，文本 %d 维 / 融合 %d 维，用时 %ss",
             n, T.shape[1], F.shape[1], dt)
    return {"ok": True, "tracks": n, "tdim": int(T.shape[1]), "dim": int(F.shape[1]),
            "elapsed": dt, "audio_cov": int(audio_mask.sum())}


def _l2(M):
    import numpy as np
    norms = np.linalg.norm(M, axis=1, keepdims=True)
    norms[norms < 1e-9] = 1.0
    return (M / norms).astype(np.float32)


def _audio_matrix(docs, n):
    """MERT 768 + librosa 20 → 每行 L2 归一化的音频向量（缺失行为全 0）。"""
    import numpy as np
    dim = 788
    A = np.zeros((n, dim), dtype=np.float32)
    mask = np.zeros(n, dtype=bool)
    for i, (_tid, _txt, f) in enumerate(docs):
        if not f:
            continue
        _feats, vec, raw = f
        try:
            v = np.array(json.loads(vec), dtype=np.float32) if vec else None
        except Exception:
            v = None
        try:
            r = np.array(json.loads(raw), dtype=np.float32) if raw else None
        except Exception:
            r = None
        if v is not None and v.size >= 64:
            v = v[:768]
            nv = float(np.linalg.norm(v))
            if nv > 1e-9:
                A[i, :v.size] = v / nv
                mask[i] = True
        if r is not None and 8 <= r.size <= 40:
            r = r[:20]
            nr = float(np.linalg.norm(r))
            if nr > 1e-9:
                A[i, 768:768 + r.size] = (r / nr) * 0.5   # librosa 权重减半，避免压过 MERT
                mask[i] = True
    # 行归一化（0 行保持 0）
    norms = np.linalg.norm(A, axis=1, keepdims=True)
    norms[norms < 1e-9] = 1.0
    return A / norms, mask


def _hash_project(toks, dim):
    import numpy as np
    M = np.zeros((len(toks), dim), dtype=np.float32)
    for i, tk in enumerate(toks):
        for t in tk:
            h = zlib.crc32(t.encode("utf-8"))
            j = h % dim
            sign = 1.0 if (h >> 16) & 1 else -1.0
            M[i, j] += sign
    return M


def build_neighbors(c, top=NEIGHBOR_TOP, block=256):
    """用融合向量算每首歌的 topN 邻居，写 track_neighbors。

    关键取舍：**排除同一歌手**。不排除时实测邻居几乎全是"同一位歌手的其他歌"
    （《十面埋伏》→《最佳损友》《富士山下》…），这对召回是废信息——歌手召回那条路
    本来就能覆盖；而《诀别书》（国风钢琴）能连到《梦中的婚礼》《城南花已开》这种
    跨歌手同质感，才是这一层真正的价值。
    """
    import numpy as np
    from server.rec_taxonomy import split_artists
    t0 = time.time()
    rows = c.execute("SELECT track_id, fuse, dim FROM track_emb").fetchall()
    if len(rows) < 50:
        return {"ok": False, "err": "no_vectors", "n": len(rows)}
    ids = [r[0] for r in rows]
    dim = int(rows[0][2] or 0)
    M = np.zeros((len(rows), dim), dtype=np.float32)
    for i, r in enumerate(rows):
        try:
            v = np.frombuffer(r[1], dtype=np.float32)
            if v.size == dim:
                M[i] = v
        except Exception:
            pass

    # 歌手 token → 用于排除同歌手邻居
    artist_of = {}
    try:
        for tid, artist in c.execute(
                "SELECT id,COALESCE(artist,'') FROM tracks WHERE offline=0"
                " UNION ALL SELECT id,COALESCE(artist,'') FROM collected_tracks WHERE offline=0"
        ).fetchall():
            artist_of[tid] = frozenset(split_artists(artist))
    except Exception:
        pass

    now = time.time()
    out = []
    same_artist_skipped = 0
    for s in range(0, len(ids), block):
        e = min(len(ids), s + block)
        sims = M[s:e].dot(M.T)
        for i in range(s, e):
            row = sims[i - s]
            row[i] = -2.0
            ai = artist_of.get(ids[i]) or frozenset()
            k = min(top * 3, len(ids) - 1)
            idx = np.argpartition(-row, k)[:k]
            idx = idx[np.argsort(-row[idx])]
            rank = 0
            for j in idx:
                sc = float(row[j])
                if sc <= 0.05:
                    break
                if ai and (artist_of.get(ids[j]) or frozenset()) & ai:
                    same_artist_skipped += 1
                    continue
                out.append((ids[i], ids[j], rank, round(sc, 4), "vec", now))
                rank += 1
                if rank >= top:
                    break
    c.execute("DELETE FROM track_neighbors WHERE src='vec'")
    c.executemany(
        "INSERT OR REPLACE INTO track_neighbors(track_id,sim_track_id,rank,score,src,built_at)"
        " VALUES(?,?,?,?,?,?)", out)
    c.commit()
    dt = round(time.time() - t0, 1)
    log.info("song_vec: 邻居表 %d 条（跳过同歌手 %d 次），用时 %ss",
             len(out), same_artist_skipped, dt)
    return {"ok": True, "edges": len(out), "tracks": len(ids), "elapsed": dt,
            "same_artist_skipped": same_artist_skipped}


def ensure_schema(c):
    for stmt in [s.strip() for s in SCHEMA_SQL.split(";") if s.strip()]:
        try:
            c.execute(stmt)
        except Exception as e:
            log.info("song_vec: schema skip %s (%s)", stmt[:50], e)
    c.commit()


def stats(c):
    out = {}
    try:
        r = c.execute("SELECT COUNT(*), MIN(tdim), MAX(dim) FROM track_emb").fetchone()
        out["emb_tracks"], out["tdim"], out["dim"] = r[0], r[1], r[2]
    except Exception as e:
        out["err"] = str(e)
    try:
        out["neighbors"] = c.execute(
            "SELECT COUNT(*) FROM track_neighbors WHERE src='vec'").fetchone()[0]
        out["neighbor_tracks"] = c.execute(
            "SELECT COUNT(DISTINCT track_id) FROM track_neighbors WHERE src='vec'").fetchone()[0]
    except Exception:
        pass
    try:
        out["profiles"] = c.execute("SELECT COUNT(*) FROM track_profiles").fetchone()[0]
    except Exception:
        pass
    return out


def run_all(rebuild=True, neighbors=True):
    c = db()
    try:
        ensure_schema(c)
        out = {}
        if rebuild:
            out["vectors"] = build_vectors(c)
        if neighbors:
            out["neighbors"] = build_neighbors(c)
        out["stats"] = stats(c)
        return out
    finally:
        c.close()


def main():
    ap = argparse.ArgumentParser(description="歌曲语义向量 / 邻居表")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--neighbors", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    if a.stats:
        c = db()
        try:
            print(json.dumps(stats(c), ensure_ascii=False, indent=2))
        finally:
            c.close()
        return
    if not (a.build or a.neighbors or a.all):
        ap.print_help()
        return
    print(json.dumps(run_all(rebuild=a.build or a.all,
                             neighbors=a.neighbors or a.all), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
