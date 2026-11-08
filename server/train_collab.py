# -*- coding: utf-8 -*-
"""流萤音乐 · 协同过滤训练（2026-10-24 个性化推送精准化 · 路 B）。

用全体用户的真实听歌行为（behavior_events）训练 implicit ALS 隐式反馈模型，
学到「听 A 歌的人也在听 B 歌」的群体规律 → 输出每用户 topN 推荐曲，
供 build_recommendations 作为协同过滤召回路（不依赖任何文本/元数据）。

隐式反馈权重：
  listen  → 收听秒数（≤300 封顶）归一化 ×1
  like    → ×3
  comment → ×2
  share   → ×2
  skip    → ×(-0.5)（负反馈，压低权重）

用法：
  python3 train_collab.py             # 全量训练（隐式矩阵 + ALS，结果写 collab_recs）
  python3 train_collab.py --min-users 3   # 只保留被 ≥3 个用户听过的歌
"""
import os
import sys
import json
import time
import sqlite3
import argparse
import traceback

ROOT = "/www/wwwroot/music"
DB_PATH = os.path.join(ROOT, "tracks.db")

try:
    import numpy as np
    from implicit.als import AlternatingLeastSquares
    from scipy.sparse import csr_matrix, coo_matrix
except ImportError:
    sys.stderr.write("需要安装 implicit（pip3 install implicit）\n")
    sys.exit(1)

MODEL_FACTORS = 32
MODEL_ITERATIONS = 18
MODEL_REG = 0.1
MODEL_ALPHA = 40.0
TOP_N = 100          # 每用户保留前 100 首
MIN_USER_EVENTS = 2  # 用户至少 2 条行为才参与训练（冷启动小数据下放宽，避免过拟合过滤把曲库滤光）
DEFAULT_MIN_USERS = 2  # 只保留被 ≥N 个用户听过的歌（小数据下 3 会把曲库滤到 10 首，放宽到 2）


def db():
    c = sqlite3.connect(DB_PATH)
    c.execute("PRAGMA busy_timeout=5000")
    c.execute("""CREATE TABLE IF NOT EXISTS collab_recs(
        user_id TEXT, track_id TEXT, rank INTEGER, score REAL,
        trained_at REAL, PRIMARY KEY(user_id, track_id))""")
    return c


def build_matrix():
    """返回 (user_ids, track_ids, uid_map, tid_map, matrix_coo)"""
    c = db()
    try:
        rows = c.execute(
            "SELECT user_id, track_id, event, value, meta FROM behavior_events"
            " WHERE track_id!='' ORDER BY created ASC").fetchall()
    finally:
        c.close()
    if not rows:
        return None
    # 用户/歌曲 ID → 连续索引
    uids, tids = [], []
    umap, tmap = {}, {}
    for user_id, track_id, *_ in rows:
        if not user_id or not track_id:
            continue
        if user_id not in umap:
            umap[user_id] = len(uids)
            uids.append(user_id)
        if track_id not in tmap:
            tmap[track_id] = len(tids)
            tids.append(track_id)
    n_u, n_t = len(uids), len(tids)
    rows_u, rows_t, rows_v = [], [], []
    for user_id, track_id, event, value, meta in rows:
        if not user_id or not track_id:
            continue
        ui, ti = umap[user_id], tmap[track_id]
        try:
            m = json.loads(meta or "{}")
        except Exception:
            m = {}
        v = 0.0
        if event == "listen":
            sec = min(max(float(value or 0), 0), 300.0)
            frac = max(float(m.get("fraction") or 0), 0)
            plays = max(int(float(m.get("plays") or 0)), 0)
            v = sec / 300.0 + frac * 0.5 + min(plays, 3) * 0.3
        elif event == "like":
            v = 3.0 if float(value or 0) > 0 else -1.0
        elif event == "comment":
            v = 2.0
        elif event == "share":
            v = 2.0
        elif event == "skip":
            v = -0.5
        else:
            continue
        rows_u.append(ui)
        rows_t.append(ti)
        rows_v.append(max(v, 0))   # ALS 隐式反馈需非负
    mat = coo_matrix((rows_v, (rows_u, rows_t)), shape=(n_u, n_t)).tocsr()
    return uids, tids, umap, tmap, mat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-users", type=int, default=DEFAULT_MIN_USERS)
    args = ap.parse_args()

    print("构建隐式反馈矩阵 ...")
    built = build_matrix()
    if built is None:
        print("无行为数据，跳过训练")
        return
    uids, tids, umap, tmap, mat = built
    print("用户 %d，歌曲 %d，非零 %d" % (mat.shape[0], mat.shape[1], mat.nnz))

    # 过滤：只保留被 ≥min-users 用户听过的歌（列）与行为 ≥MIN_USER_EVENTS 的用户（行）
    col_cnt = np.asarray((mat > 0).sum(axis=0)).ravel()
    row_cnt = np.asarray((mat > 0).sum(axis=1)).ravel()
    keep_t = col_cnt >= args.min_users
    keep_u = row_cnt >= MIN_USER_EVENTS
    if not keep_t.all() or not keep_u.all():
        mat = mat[keep_u][:, keep_t]
        uids = [u for u, k in zip(uids, keep_u) if k]
        tids = [t for t, k in zip(tids, keep_t) if k]
        print("过滤后：用户 %d，歌曲 %d" % (mat.shape[0], mat.shape[1]))

    model = AlternatingLeastSquares(factors=MODEL_FACTORS, iterations=MODEL_ITERATIONS,
                                    regularization=MODEL_REG, alpha=MODEL_ALPHA,
                                    random_state=42)
    print("训练 ALS（factors=%d iters=%d）..." % (MODEL_FACTORS, MODEL_ITERATIONS))
    # implicit 惯例：fit 接受 user×item 矩阵（行=用户，列=歌曲）
    model.fit(csr_matrix(mat))

    now = time.time()
    c = db()
    try:
        # 为每个用户算 topN（直接用因子矩阵点乘，绕开 implicit recommend 的形状坑）
        c.execute("DELETE FROM collab_recs")
        item_factors = model.item_factors       # (n_items, factors)
        user_factors = model.user_factors       # (n_users, factors)
        n_items = item_factors.shape[0]
        all_scores = np.asarray(user_factors).dot(np.asarray(item_factors).T)   # (n_users, n_items)
        batch = []
        for ui in range(len(uids)):
            user_vec = mat.getrow(ui)
            if user_vec.nnz == 0:
                continue
            scores = all_scores[ui]             # (n_items,) 该用户对每首歌的协同分
            # 已交互过的歌不推荐（与长期去重语义一致）
            liked = list(set(user_vec.indices.tolist()))
            if liked:
                scores = scores.copy()
                scores[liked] = -1e18
            # 彻底剔除占位分（用户已听歌很多时 topN 可能混入 -1e18）
            valid = np.nonzero(scores > -1e17)[0]
            order = valid[np.argsort(-scores[valid])][:TOP_N]
            rank = 0
            for item_id in order:
                tid = tids[item_id]
                rank += 1
                batch.append((uids[ui], tid, rank, float(scores[item_id]), now))
            if len(batch) >= 500:
                c.executemany(
                    "INSERT OR REPLACE INTO collab_recs(user_id, track_id, rank, score, trained_at)"
                    " VALUES(?,?,?,?,?)", batch)
                batch = []
                c.commit()
        if batch:
            c.executemany(
                "INSERT OR REPLACE INTO collab_recs(user_id, track_id, rank, score, trained_at)"
                " VALUES(?,?,?,?,?)", batch)
            c.commit()
    finally:
        c.close()
    print("完成：写入 collab_recs（%d 用户 × top%d）" % (len(uids), TOP_N))


if __name__ == "__main__":
    main()
