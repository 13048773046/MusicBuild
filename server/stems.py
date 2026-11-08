# -*- coding: utf-8 -*-
"""流萤音乐 · 全景音效分离【主服务调度模块】。

职责：
  - track_stems 状态查询/初始化；
  - 按需异步触发分离（subprocess 跑 server.stem_worker），全局锁保证同一时刻只分离一首；
  - 后台「热门补齐」线程：按播放量挑未处理的歌，低优先级慢慢补。

端上在全景模式先查 get_status()：done 就拉 opus 音轨，否则回退端上 HRTF，
同时可调用 request_separate() 让服务端后台分离，下次播放即生效。
"""
import os
import sys
import time
import subprocess
import threading

from server.config import ROOT, log, STEM_WORKER_BATCH, STEM_WORKER_IDLE
from server.database import db

# 全局分离锁：同一时刻只允许一个分离进程（4 核/3.6G，避免与在线服务争抢）
_sep_lock = threading.Lock()
# 已在处理的曲目（防重复触发）
_inflight = set()
_inflight_guard = threading.Lock()


# ---------------- 状态 ----------------

def ensure_row(tid):
    """确保 track_stems 有该曲记录（pending）。"""
    c = db()
    try:
        c.execute(
            "INSERT OR IGNORE INTO track_stems(track_id, status, created) VALUES(?, 'pending', ?)",
            (tid, time.time()))
        c.commit()
    finally:
        c.close()


def get_status(tid):
    """返回 {status, vocals, accompaniment} 或 None（无记录）。"""
    c = db()
    try:
        r = c.execute(
            "SELECT status, vocals, accompaniment FROM track_stems WHERE track_id=?",
            (tid,)).fetchone()
    finally:
        c.close()
    if not r:
        return None
    return {"status": r[0], "vocals": r[1], "accompaniment": r[2]}


# ---------------- 分离执行（全局锁） ----------------

def _separate_locked(tid):
    with _sep_lock:
        # 等待锁期间可能已被其他路径处理 → 双重检查
        st = get_status(tid)
        if st and st["status"] == "done":
            return
        ensure_row(tid)
        c = db()
        try:
            c.execute("UPDATE track_stems SET status='processing', updated=? WHERE track_id=?",
                      (time.time(), tid))
            c.commit()
        finally:
            c.close()
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "server.stem_worker", "--track", tid],
                cwd=ROOT, capture_output=True, timeout=2400)
            if proc.returncode != 0:
                log.warning("stem worker 失败 tid=%s err=%s",
                            tid, proc.stderr.decode(errors="ignore")[-300:])
        except subprocess.TimeoutExpired:
            log.warning("stem worker 超时 tid=%s", tid)
        except Exception as e:
            log.warning("stem worker 异常 tid=%s %s", tid, e)


def request_separate(tid):
    """按需触发：done/processing 或已在队列则跳过，否则后台线程分离。"""
    st = get_status(tid)
    if st and st["status"] in ("done", "processing"):
        return
    with _inflight_guard:
        if tid in _inflight:
            return
        _inflight.add(tid)

    def _job():
        try:
            _separate_locked(tid)
        finally:
            with _inflight_guard:
                _inflight.discard(tid)

    threading.Thread(target=_job, daemon=True, name="stem-on-demand").start()


# ---------------- 热门补齐后台线程 ----------------

def _hot_candidates(limit):
    """按播放量挑未完成的在线歌曲（tracks + collected），返回 track_id 列表。"""
    c = db()
    try:
        t_rows = c.execute("""
            SELECT t.id FROM tracks t
            LEFT JOIN track_stems s ON s.track_id=t.id
            WHERE t.offline=0 AND COALESCE(s.status,'') NOT IN ('done','processing')
            ORDER BY t.play_count DESC LIMIT ?""", (limit,)).fetchall()
        c_rows = c.execute("""
            SELECT t.id FROM collected_tracks t
            LEFT JOIN track_stems s ON s.track_id=t.id
            WHERE t.play_url!='' AND t.offline=0
              AND COALESCE(s.status,'') NOT IN ('done','processing')
            ORDER BY t.play_count DESC LIMIT ?""", (limit,)).fetchall()
    finally:
        c.close()
    return [r[0] for r in t_rows] + [r[0] for r in c_rows]


def _catchup_loop():
    time.sleep(180)   # 启动后等 3 分钟，避开服务启动期
    while True:
        try:
            ids = _hot_candidates(STEM_WORKER_BATCH)
            for tid in ids:
                with _inflight_guard:
                    if tid in _inflight:
                        continue
                    _inflight.add(tid)
                try:
                    _separate_locked(tid)
                finally:
                    with _inflight_guard:
                        _inflight.discard(tid)
        except Exception as e:
            log.warning("stem catchup loop err: %s", e)
        time.sleep(STEM_WORKER_IDLE)


_STARTED = False


def reset_stuck_processing():
    """服务启动时把残留的 processing 重置为 pending。

    进程被 kill / 服务重启意味着上次的分离进程已不存在，
    若不重置，这些歌会被 _hot_candidates 永久排除、无法重试。
    """
    c = db()
    try:
        n = c.execute(
            "UPDATE track_stems SET status='pending', updated=? WHERE status='processing'",
            (time.time(),)).rowcount
        c.commit()
    finally:
        c.close()
    if n:
        log.info("重置 %d 首卡住的 processing 为 pending", n)


def start_stem_worker():
    """服务入口调用：启动热门补齐后台线程（幂等）。"""
    global _STARTED
    if _STARTED:
        return
    _STARTED = True
    reset_stuck_processing()
    threading.Thread(target=_catchup_loop, daemon=True, name="stem-catchup").start()
