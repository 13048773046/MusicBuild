# -*- coding: utf-8 -*-
"""服务入口：初始化数据库并启动 ThreadingHTTPServer + AI 主动乐评后台线程。"""
import threading
import time
from http.server import ThreadingHTTPServer
from server.config import PORT, ROOT, AUTH_STRICT, log
from server import database
from server import ai
from server.common import cleanup_expired_data
from server.handler import H

# 审查 #5：行为/历史/通知等表膨胀清理线程（每 6 小时一次，daemon）
_CLEANUP_INTERVAL = 6 * 3600
# 推荐离线评估：每 24 小时跑一次并留档（趋势对比用，单轮约 2~4s）
_EVAL_INTERVAL = 24 * 3600


def _eval_loop():
    """启动 10 分钟后跑首次，之后每 24 小时一次（避开启动期资源争用）。"""
    try:
        time.sleep(600)
        from server import rec_eval
        rec_eval.run_and_save()
    except Exception as e:
        log.info("rec eval first run err: %s", e)
    while True:
        try:
            time.sleep(_EVAL_INTERVAL)
            from server import rec_eval
            rec_eval.run_and_save()
        except Exception as e:
            log.info("rec eval loop err: %s", e)


def _cleanup_loop():
    while True:
        try:
            time.sleep(_CLEANUP_INTERVAL)
            cleanup_expired_data()
        except Exception as e:
            log.info("cleanup loop err: %s", e)


def _warm_recommender():
    """启动预热（2026-11-05）：推荐索引/音频矩阵/MERT 矩阵/语义向量首次构建要 2~4s，
    放到后台线程预建，避免上线后第一个推荐请求（或缓存过期后的首个请求）明显变慢。"""
    try:
        from server.database import db
        from server.rec_taxonomy import get_taste_index
        from server.recommend import _load_audio_mat, _load_librosa_mat, _load_vec_mat
        c = db()
        t0 = time.time()
        get_taste_index(c)
        _load_audio_mat(c)
        _load_librosa_mat(c)
        _load_vec_mat(c)
        c.close()
        log.info("recommender warmup done in %.2fs", time.time() - t0)
    except Exception as e:
        log.info("recommender warmup err: %s", e)


def _vec_refresh_loop():
    """语义向量保鲜（2026-11-05）：歌名/歌词/标签/画像会持续变化（新入库、补打标），
    每天检查一次 track_emb 是否落后于曲库，落后超过 6 小时就在低峰重建（约 15s）。
    画像/标签批量跑完后，下一次检查会自动把新信息吃进向量。"""
    while True:
        try:
            time.sleep(3600)
            from server.database import db
            from server import song_vec
            c = db()
            try:
                lib = c.execute(
                    "SELECT (SELECT COUNT(*) FROM tracks WHERE offline=0)"
                    " + (SELECT COUNT(*) FROM collected_tracks WHERE offline=0"
                    "    AND COALESCE(play_url,'')!='')").fetchone()[0] or 0
                emb = c.execute("SELECT COUNT(*), MAX(updated) FROM track_emb").fetchone()
                n_emb, upd = int(emb[0] or 0), float(emb[1] or 0)
                stale = (time.time() - upd > 6 * 3600) or (lib and n_emb < lib * 0.9)
                if stale:
                    log.info("song_vec: 向量过期（库 %d / 向量 %d），后台重建", lib, n_emb)
                    out = song_vec.run_all(rebuild=True, neighbors=True)
                    log.info("song_vec: 重建完成 %s", out.get("stats"))
            finally:
                c.close()
        except Exception as e:
            log.info("song_vec refresh loop err: %s", e)


def main():
    database.db().close()
    from server import sentry
    sentry.init_sentry()
    ai.start_ai_worker()
    from server.stems import start_stem_worker
    start_stem_worker()
    threading.Thread(target=_cleanup_loop, daemon=True).start()
    threading.Thread(target=_eval_loop, daemon=True).start()
    threading.Thread(target=_warm_recommender, daemon=True).start()
    threading.Thread(target=_vec_refresh_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    srv.daemon_threads = True
    log.info("music server on 127.0.0.1:%d root=%s strict_auth=%s",
             PORT, ROOT, AUTH_STRICT)
    srv.serve_forever()


if __name__ == "__main__":
    main()
