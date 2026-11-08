# -*- coding: utf-8 -*-
"""流萤音乐 · 后端模块化包（由线上 server.py 机械重放，函数体零改写）。

P1 优化：db() 拆分为「一次性迁移 migrate() + 轻量连接 db()」。
迁移（建表/加列/建索引/清理）只在进程启动或首次连接时执行一次，
避免每个请求重复执行 ~30 条 DDL + 删除语句（原实现每请求 5-20ms 固定开销）。
"""
import sqlite3
import time
import threading
import uuid

from server.config import DB, log

__all__ = ['db', 'migrate']

_migrate_lock = threading.Lock()
_migrated = False


def migrate():
    """一次性执行全部建表/迁移/建索引/初始数据（幂等）。

    进程启动（main.py）或任意模块首次调用 db() 时执行一次。
    与业务连接分离：业务连接只做 connect + 连接级 PRAGMA。
    """
    c = sqlite3.connect(DB, timeout=10)
    try:
        c.execute("PRAGMA journal_mode=WAL")        # 读写并发，避免锁库
        c.execute("PRAGMA busy_timeout=5000")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("""CREATE TABLE IF NOT EXISTS tracks(
            id TEXT PRIMARY KEY, title TEXT, artist TEXT, album TEXT,
            fname TEXT, size INTEGER, dur REAL, created REAL)""")
        cols = [r[1] for r in c.execute("PRAGMA table_info(tracks)")]
        if "cover" not in cols:
            c.execute("ALTER TABLE tracks ADD COLUMN cover TEXT DEFAULT ''")
        if "lrc" not in cols:
            c.execute("ALTER TABLE tracks ADD COLUMN lrc TEXT DEFAULT ''")
        c.execute("""CREATE TABLE IF NOT EXISTS users(
            id TEXT PRIMARY KEY, account TEXT UNIQUE, password_hash TEXT,
            name TEXT, avatar TEXT, created REAL)""")
        ucols = [r[1] for r in c.execute("PRAGMA table_info(users)")]
        for _col, _ddl in [("gender", "TEXT DEFAULT ''"),
                           ("age", "INTEGER DEFAULT 0"),
                           ("background_image", "TEXT DEFAULT ''"),
                           ("signature", "TEXT DEFAULT ''"),
                           ("ip_address", "TEXT DEFAULT ''"),
                           ("city", "TEXT DEFAULT ''"),
                           ("banned", "INTEGER DEFAULT 0"),
                           ("ban_reason", "TEXT DEFAULT ''"),
                           ("is_ai", "INTEGER DEFAULT 0"),
                           ("official_read_at", "REAL DEFAULT 0"),
                           ("email", "TEXT DEFAULT ''"),
                           ("email_verified", "INTEGER DEFAULT 0")]:
            if _col not in ucols:
                c.execute("ALTER TABLE users ADD COLUMN %s %s" % (_col, _ddl))
        # AI 乐评账号（透明标识：主页/评论展示 AI 标签；不可登录，仅供 AI 评论发布）
        c.execute("""INSERT OR IGNORE INTO users(id,account,password_hash,name,avatar,created,is_ai,signature)
                     VALUES('ai_liuying_comment','000000','','AI 乐评','',0,1,'我是流萤 AI 乐评人，为你解析每首歌里的情绪与故事')""")
        tcols = [r[1] for r in c.execute("PRAGMA table_info(tracks)")]
        for _col, _ddl in [("user_id", "TEXT DEFAULT ''"),
                           ("play_count", "INTEGER DEFAULT 0"),
                           ("like_count", "INTEGER DEFAULT 0"),
                           ("share_count", "INTEGER DEFAULT 0"),
                           ("offline", "INTEGER DEFAULT 0"),
                           ("offline_reason", "TEXT DEFAULT ''"),
                           ("genre", "TEXT DEFAULT ''")]:
            if _col not in tcols:
                c.execute("ALTER TABLE tracks ADD COLUMN %s %s" % (_col, _ddl))
        c.execute("""CREATE TABLE IF NOT EXISTS likes(
            user_id TEXT, track_id TEXT, created REAL,
            PRIMARY KEY(user_id, track_id))""")
        c.execute("""CREATE TABLE IF NOT EXISTS comments(
            id TEXT PRIMARY KEY, track_id TEXT, user_id TEXT,
            user_name TEXT, content TEXT, created REAL)""")
        # 评论图片列（存 JSON 数组：服务端生成的文件名 cmt_xxx，经 /api/comment-image/ 访问）
        cmt_cols = [r[1] for r in c.execute("PRAGMA table_info(comments)")]
        if "images" not in cmt_cols:
            c.execute("ALTER TABLE comments ADD COLUMN images TEXT DEFAULT ''")
        c.execute("""CREATE TABLE IF NOT EXISTS tokens(
            token TEXT PRIMARY KEY, user_id TEXT, created REAL, expires REAL)""")
        # 顺手清理过期 token（迁移时执行一次；校验侧另有 expires>now 过滤兜底）
        c.execute("DELETE FROM tokens WHERE expires < ?", (time.time(),))
        c.execute("""CREATE TABLE IF NOT EXISTS share_tokens(
            token TEXT PRIMARY KEY, track_id TEXT, created REAL)""")
        scols = [r[1] for r in c.execute("PRAGMA table_info(share_tokens)")]
        if "short_code" not in scols:
            c.execute("ALTER TABLE share_tokens ADD COLUMN short_code TEXT DEFAULT ''")
        # 采集歌曲表：与用户上传表 tracks 分开，只存歌曲信息+上游播放地址，不落盘音频
        c.execute("""CREATE TABLE IF NOT EXISTS collected_tracks(
            id TEXT PRIMARY KEY, title TEXT, artist TEXT, album TEXT,
            cover TEXT, duration REAL, source TEXT, source_id TEXT,
            play_url TEXT, size REAL, created REAL, user_id TEXT,
            play_count INTEGER DEFAULT 0, like_count INTEGER DEFAULT 0,
            share_count INTEGER DEFAULT 0, lrc TEXT DEFAULT '',
            UNIQUE(source, source_id))""")
        ccols = [r[1] for r in c.execute("PRAGMA table_info(collected_tracks)")]
        for _col, _ddl in [("offline", "INTEGER DEFAULT 0"),
                           ("offline_reason", "TEXT DEFAULT ''"),
                           ("genre", "TEXT DEFAULT ''")]:
            if _col not in ccols:
                c.execute("ALTER TABLE collected_tracks ADD COLUMN %s %s" % (_col, _ddl))
        # 用户行为事件表（个性化推荐数据源）
        # event: like / comment_open / comment / share / listen
        # value: listen=累计收听秒数，like=1/0，其余 0
        # meta: JSON，如 {"fraction": 收听进度0~1, "plays": 本次会话播放次数}
        c.execute("""CREATE TABLE IF NOT EXISTS behavior_events(
            id TEXT PRIMARY KEY, user_id TEXT, track_id TEXT,
            event TEXT, value REAL DEFAULT 0, meta TEXT DEFAULT '',
            track_info TEXT DEFAULT '',
            created REAL)""")
        bev_cols = [r[1] for r in c.execute("PRAGMA table_info(behavior_events)")]
        if "track_info" not in bev_cols:
            c.execute("ALTER TABLE behavior_events ADD COLUMN track_info TEXT DEFAULT ''")
        c.execute("CREATE INDEX IF NOT EXISTS idx_bev_user ON behavior_events(user_id, created)")
        # 公告通知表：App 启动时从底部弹窗展示最新公告
        c.execute("""CREATE TABLE IF NOT EXISTS announcements(
            id TEXT PRIMARY KEY, title TEXT, content TEXT,
            version TEXT DEFAULT '', active INTEGER DEFAULT 1,
            show_once INTEGER DEFAULT 0,
            type TEXT DEFAULT 'announcement',
            force_update INTEGER DEFAULT 0,
            download_url TEXT DEFAULT '',
            created REAL)""")
        ann_cols = [r[1] for r in c.execute("PRAGMA table_info(announcements)")]
        for col, default in [("show_once","0"),("type","'announcement'"),("force_update","0"),
                             ("download_url","''"),("download_url_ios","''"),("download_url_android","''"),
                             ("version_ios","''"),("version_android","''")]:
            if col not in ann_cols:
                c.execute("ALTER TABLE announcements ADD COLUMN %s TEXT DEFAULT %s" % (col, default))
        # 通知表：互动通知（点赞/评论/分享）+ 官方通知记录；comment_id 用于评论通知点击定位
        c.execute("""CREATE TABLE IF NOT EXISTS notifications(
            id TEXT PRIMARY KEY, user_id TEXT, type TEXT,
            title TEXT DEFAULT '', content TEXT DEFAULT '',
            from_user_id TEXT DEFAULT '', from_user_name TEXT DEFAULT '',
            track_id TEXT DEFAULT '', track_title TEXT DEFAULT '',
            is_read INTEGER DEFAULT 0, created REAL)""")
        ncols = [r[1] for r in c.execute("PRAGMA table_info(notifications)")]
        if "comment_id" not in ncols:
            c.execute("ALTER TABLE notifications ADD COLUMN comment_id TEXT DEFAULT ''")
        # 官方消息表：聊天记录样式展示
        c.execute("""CREATE TABLE IF NOT EXISTS official_messages(
            id TEXT PRIMARY KEY, content TEXT DEFAULT '', created REAL,
            sender_name TEXT DEFAULT '流萤音乐官方')""")
        # 官方消息定向：receiver_user_id 为空=全广播，非空=定向单个用户
        omcols = [r[1] for r in c.execute("PRAGMA table_info(official_messages)")]
        if "receiver_user_id" not in omcols:
            c.execute("ALTER TABLE official_messages ADD COLUMN receiver_user_id TEXT DEFAULT ''")
        # 精准推荐（2026-10-24）：音频特征（feature_extract.py 填充）+ 协同过滤（train_collab.py 填充）
        c.execute("""CREATE TABLE IF NOT EXISTS audio_features(
            track_id TEXT PRIMARY KEY,
            features TEXT DEFAULT '',
            raw_vec TEXT DEFAULT '',
            vec TEXT DEFAULT '',
            analyzed_at REAL DEFAULT 0,
            version INTEGER DEFAULT 1)""")
        afcols = [r[1] for r in c.execute("PRAGMA table_info(audio_features)")]
        if "raw_vec" not in afcols:
            try:
                c.execute("ALTER TABLE audio_features ADD COLUMN raw_vec TEXT DEFAULT ''")
            except Exception:
                pass
        # 2026-11-01：mert_raw 列——MERT 768 维原始值单独存放，与 librosa 的 features/raw_vec 分开共存
        #（MERT 升级曾 INSERT OR REPLACE 整行覆盖 librosa 数据；embed_mert.py 现在只写 vec/mert_raw/version）
        if "mert_raw" not in afcols:
            try:
                c.execute("ALTER TABLE audio_features ADD COLUMN mert_raw TEXT DEFAULT ''")
            except Exception:
                pass
        c.execute("""CREATE TABLE IF NOT EXISTS collab_recs(
            user_id TEXT, track_id TEXT, rank INTEGER DEFAULT 0,
            score REAL DEFAULT 0, trained_at REAL DEFAULT 0,
            PRIMARY KEY(user_id, track_id))""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_collab_user ON collab_recs(user_id, rank)")
        # i2i 音频相似关联表（feature_extract.py --build-i2i 离线生成，2026-10-24 优化）
        c.execute("""CREATE TABLE IF NOT EXISTS item_sims(
            track_id TEXT, sim_track_id TEXT, rank INTEGER DEFAULT 0,
            score REAL DEFAULT 0, built_at REAL DEFAULT 0,
            PRIMARY KEY(track_id, sim_track_id))""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_item_sims_src ON item_sims(track_id, rank)")
        # ===== 会员/认证/头衔：users 扩展字段 =====
        _uc = [r[1] for r in c.execute("PRAGMA table_info(users)")]
        for _col, _ddl in [("title", "TEXT DEFAULT ''"),
                           ("verified", "TEXT DEFAULT ''"),          # 认证类型标识：''=无 / 'personal' / 'enterprise' / 'org'（后台审核通过时写入）
                           ("verified_label", "TEXT DEFAULT ''"),   # 认证自定义标签（如"XX官方账户"）
                           ("member", "TEXT DEFAULT ''"),            # 会员等级（冗余快照，实际以过期时间判定）
                           ("member_expire", "REAL DEFAULT 0"),      # VIP 到期时间戳
                           ("svip_expire", "REAL DEFAULT 0")]:       # SVIP 到期时间戳
            if _col not in _uc:
                c.execute("ALTER TABLE users ADD COLUMN %s %s" % (_col, _ddl))
        # 认证类别表（两级：level1=个人/企业/组织，level2=工作室等细分）
        c.execute("""CREATE TABLE IF NOT EXISTS verification_categories(
            id TEXT PRIMARY KEY, level1 TEXT NOT NULL, level2 TEXT DEFAULT '',
            created REAL)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_vcat_l1 ON verification_categories(level1)")
        # ===== 邮箱验证码表（登录/注册/绑定用）=====
        c.execute("""CREATE TABLE IF NOT EXISTS email_codes(
            email TEXT PRIMARY KEY, code TEXT NOT NULL,
            created REAL NOT NULL,
            last_sent REAL NOT NULL)""")
        # 验证码防爆破 / 每日发送限额（2026-10-23 审查加固）：
        # fail_count=连续校验失败次数（≥5 作废）；day_count/day_date=当日已发送条数（防邮箱轰炸）
        _ec_cols = [r[1] for r in c.execute("PRAGMA table_info(email_codes)")]
        for _col, _ddl in [("fail_count", "INTEGER DEFAULT 0"),
                           ("day_count", "INTEGER DEFAULT 0"),
                           ("day_date", "TEXT DEFAULT ''")]:
            if _col not in _ec_cols:
                c.execute("ALTER TABLE email_codes ADD COLUMN %s %s" % (_col, _ddl))
        # 预置默认认证类别（幂等：仅当表为空时插入；后台可继续增删）
        _vcnt = c.execute("SELECT COUNT(*) FROM verification_categories").fetchone()[0]
        if _vcnt == 0:
            _default_cats = [("个人", "歌手"), ("个人", "音乐人"), ("个人", "主播"),
                             ("企业", "音乐公司"), ("企业", "工作室"), ("企业", "唱片公司"),
                             ("组织", "音乐组织"), ("组织", "官方机构")]
            c.executemany(
                "INSERT INTO verification_categories(id,level1,level2,created) VALUES(?,?,?,?)",
                [(uuid.uuid4().hex, l1, l2, time.time()) for l1, l2 in _default_cats])
        # 认证申请表
        c.execute("""CREATE TABLE IF NOT EXISTS verification_applications(
            id TEXT PRIMARY KEY, user_id TEXT, level1 TEXT DEFAULT '',
            level2 TEXT DEFAULT '', label TEXT DEFAULT '', reason TEXT DEFAULT '',
            status TEXT DEFAULT 'pending', created REAL, reviewed_at REAL DEFAULT 0)""")
        _vapp_cols = [r[1] for r in c.execute("PRAGMA table_info(verification_applications)")]
        if "review_note" not in _vapp_cols:
            c.execute("ALTER TABLE verification_applications ADD COLUMN review_note TEXT DEFAULT ''")
        c.execute("CREATE INDEX IF NOT EXISTS idx_vapp_user ON verification_applications(user_id, created DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_vapp_status ON verification_applications(status, created DESC)")
        # 支付订单表（易支付预留：下单/回调/状态）
        c.execute("""CREATE TABLE IF NOT EXISTS payment_orders(
            id TEXT PRIMARY KEY, order_no TEXT UNIQUE, user_id TEXT,
            plan TEXT DEFAULT '', amount REAL DEFAULT 0, days INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending', created REAL, paid_at REAL DEFAULT 0)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_po_user ON payment_orders(user_id, created DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_po_order ON payment_orders(order_no)")
        # ===== 性能优化：补缺失索引 =====
        # 通知列表按用户+时间倒序查询
        c.execute("CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, created DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_notif_unread ON notifications(user_id, is_read)")
        # 评论按歌曲+时间倒序查询
        c.execute("CREATE INDEX IF NOT EXISTS idx_comment_track ON comments(track_id, created DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_comment_user ON comments(user_id, created DESC)")
        # 用户上传歌曲按时间倒序
        c.execute("CREATE INDEX IF NOT EXISTS idx_tracks_user ON tracks(user_id, created DESC)")
        # 采集歌曲按用户+时间
        c.execute("CREATE INDEX IF NOT EXISTS idx_collected_user ON collected_tracks(user_id, created DESC)")
        # 点赞列表按用户+时间
        c.execute("CREATE INDEX IF NOT EXISTS idx_likes_user ON likes(user_id, created DESC)")

        # ===== 新增功能表 =====
        # 播放历史表
        c.execute("""CREATE TABLE IF NOT EXISTS play_history(
            id TEXT PRIMARY KEY, user_id TEXT, track_id TEXT,
            track_title TEXT DEFAULT '', track_artist TEXT DEFAULT '',
            track_cover TEXT DEFAULT '', source TEXT DEFAULT 'upload',
            created REAL)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_history_user ON play_history(user_id, created DESC)")

        # 搜索历史表
        c.execute("""CREATE TABLE IF NOT EXISTS search_history(
            id TEXT PRIMARY KEY, user_id TEXT, keyword TEXT, created REAL)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_search_user ON search_history(user_id, created DESC)")

        # 歌单表
        c.execute("""CREATE TABLE IF NOT EXISTS playlists(
            id TEXT PRIMARY KEY, user_id TEXT, name TEXT,
            cover TEXT DEFAULT '', description TEXT DEFAULT '',
            track_count INTEGER DEFAULT 0, created REAL)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_playlist_user ON playlists(user_id, created DESC)")

        # 歌单-歌曲关联表
        c.execute("""CREATE TABLE IF NOT EXISTS playlist_tracks(
            playlist_id TEXT, track_id TEXT,
            track_title TEXT DEFAULT '', track_artist TEXT DEFAULT '',
            track_cover TEXT DEFAULT '', source TEXT DEFAULT 'upload',
            sort INTEGER DEFAULT 0, created REAL,
            PRIMARY KEY(playlist_id, track_id))""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_pltrack_playlist ON playlist_tracks(playlist_id, sort)")

        # 评论图片字段迁移
        cmt_cols = [r[1] for r in c.execute("PRAGMA table_info(comments)")]
        if "images" not in cmt_cols:
            c.execute("ALTER TABLE comments ADD COLUMN images TEXT DEFAULT ''")
        if "like_count" not in cmt_cols:
            c.execute("ALTER TABLE comments ADD COLUMN like_count INTEGER DEFAULT 0")
        if "is_ai" not in cmt_cols:
            c.execute("ALTER TABLE comments ADD COLUMN is_ai INTEGER DEFAULT 0")

        # 评论点赞表
        c.execute("""CREATE TABLE IF NOT EXISTS comment_likes(
            user_id TEXT, comment_id TEXT, created REAL,
            PRIMARY KEY(user_id, comment_id))""")

        # 通知软删除字段
        notif_cols = [r[1] for r in c.execute("PRAGMA table_info(notifications)")]
        if "is_deleted" not in notif_cols:
            c.execute("ALTER TABLE notifications ADD COLUMN is_deleted INTEGER DEFAULT 0")

        # 关注关系表
        c.execute("""CREATE TABLE IF NOT EXISTS follows(
            follower_id TEXT, following_id TEXT, created REAL,
            PRIMARY KEY(follower_id, following_id))""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_follow_follower ON follows(follower_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_follow_following ON follows(following_id)")

        # 设备上报表（前端启动/登录上报；后台用户管理据此显示设备平台）
        c.execute("""CREATE TABLE IF NOT EXISTS devices(
            device_id TEXT PRIMARY KEY, platform TEXT, app_version TEXT,
            user_id TEXT, first_seen REAL, last_seen REAL)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_devices_user ON devices(user_id, last_seen DESC)")

        # App 日志上报表（客户端崩溃/异常自动上报，管理端可查）
        c.execute("""CREATE TABLE IF NOT EXISTS app_logs(
            id TEXT PRIMARY KEY, platform TEXT, app_version TEXT,
            device_id TEXT, level TEXT, tag TEXT, message TEXT, stack TEXT,
            created REAL)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_app_logs_created ON app_logs(created DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_app_logs_dev ON app_logs(device_id, created DESC)")

        # 通知类型加 follow 支持（复用 notifications 表，type='follow'）

        # 歌曲语义标签（2026-11-05）：LLM 批量打标（genre/mood/scene/language/era/vocal/flags）
        # 解决 30% 采集歌无 genre、mood 只有 4 类的问题；供推荐理解层/召回/解释使用。
        c.execute("""CREATE TABLE IF NOT EXISTS track_tags(
            track_id TEXT NOT NULL, kind TEXT NOT NULL, tag TEXT NOT NULL,
            confidence REAL DEFAULT 1.0, source TEXT DEFAULT 'llm', created REAL,
            PRIMARY KEY(track_id, kind, tag))""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_track_tags_kind ON track_tags(kind, tag)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_track_tags_track ON track_tags(track_id)")

        # 推荐曝光日志（2026-11-05）：每次推荐落了哪些歌/哪一路/第几位/是否被点开。
        # 是学习排序（LTR）、分路 CTR 归因、Bandit/A-B 的数据地基；
        # 完听率不重复存——behavior_events 的 listen 事件已带 route + fraction。
        c.execute("""CREATE TABLE IF NOT EXISTS recommend_impressions(
            request_id TEXT NOT NULL, uid TEXT NOT NULL, track_id TEXT NOT NULL,
            route TEXT DEFAULT '', position INTEGER DEFAULT 0, source TEXT DEFAULT '',
            clicked INTEGER DEFAULT 0, created REAL NOT NULL,
            PRIMARY KEY(request_id, track_id))""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_rec_imp_uid_track"
                  " ON recommend_impressions(uid, track_id, created DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_rec_imp_created"
                  " ON recommend_impressions(created DESC)")

        # 推荐评估留档（2026-11-05）：每日跑一次离线评估，落趋势用
        c.execute("""CREATE TABLE IF NOT EXISTS rec_eval_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL NOT NULL,
            users_n INTEGER DEFAULT 0, metrics TEXT DEFAULT '')""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_rec_eval_created"
                  " ON rec_eval_history(created DESC)")

        # 显式负反馈（2026-11-05）：用户主动「不感兴趣」。
        # kind: track / artist / genre / mood / scene；value 对应 id 或标签。
        # 之前只有隐式信号（跳过/完听率）推断，"不喜欢"这件事从来没被明确表达过。
        c.execute("""CREATE TABLE IF NOT EXISTS rec_dislikes(
            uid TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL,
            reason TEXT DEFAULT '', created REAL NOT NULL,
            PRIMARY KEY(uid, kind, value))""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_rec_dislikes_uid ON rec_dislikes(uid)")

        # 推荐推送去重历史（按用户记录 3 小时内推送过的歌曲，窗口过期惰性清理）
        c.execute("""CREATE TABLE IF NOT EXISTS recommend_push_history(
            uid TEXT NOT NULL, track_id TEXT NOT NULL, pushed_at REAL NOT NULL,
            PRIMARY KEY(uid, track_id))""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_push_uid_ts ON recommend_push_history(uid, pushed_at)")

        # 评论意图推荐池（用户评论「想听 XX」命中曲库 → 混入其推荐流，不打扰式通知；窗口过期惰性清理）
        # 2026-10-24 升级：cnt=3 小时窗口内同一首歌被连续提及次数（≥2 保送第 1 位、权重加倍）
        c.execute("""CREATE TABLE IF NOT EXISTS wish_recommends(
            uid TEXT NOT NULL, track_id TEXT NOT NULL, kw TEXT DEFAULT '',
            cnt INTEGER DEFAULT 1, created REAL NOT NULL)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_wish_uid_ts ON wish_recommends(uid, created)")
        _wcols = [r[1] for r in c.execute("PRAGMA table_info(wish_recommends)")]
        if "cnt" not in _wcols:
            try:
                c.execute("ALTER TABLE wish_recommends ADD COLUMN cnt INTEGER DEFAULT 1")
            except Exception:
                pass

        # AI 主动乐评：动态配置表（key→value，网站管理页可改，DB 优先于 config.py 默认值）
        c.execute("""CREATE TABLE IF NOT EXISTS ai_comment_cfg(
            key TEXT PRIMARY KEY, value TEXT DEFAULT '')""")

        # AI 主动乐评执行记录（run_type: auto=定时/manual=手动；status: ok/fail）
        c.execute("""CREATE TABLE IF NOT EXISTS ai_comment_logs(
            id TEXT PRIMARY KEY, run_type TEXT DEFAULT 'auto', track_id TEXT, track_title TEXT,
            status TEXT DEFAULT 'ok', comment_id TEXT DEFAULT '', reason TEXT DEFAULT '', created REAL)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ai_log_created ON ai_comment_logs(created)")

        # 回填历史 AI 乐评的 is_ai 标识（早期被动评论未写该列）
        c.execute("UPDATE comments SET is_ai=1 WHERE user_id=? AND is_ai=0", ("ai_liuying_comment",))

        # 官网轮播图（后台「网站管理」动态管理，数据库持久化，重启/重开不丢失）
        c.execute("""CREATE TABLE IF NOT EXISTS banners(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            title TEXT DEFAULT '',
            link TEXT DEFAULT '',
            sort INTEGER DEFAULT 0)""")

        # 启动页广告（App 启动随机展示一张，非轮播；后台上传/删除，active=1 才参与随机）
        c.execute("""CREATE TABLE IF NOT EXISTS splash_ads(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            title TEXT DEFAULT '',
            link TEXT DEFAULT '',
            sort INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            created REAL DEFAULT 0)""")

        # ===== 审查加固（2026-10-23）=====

        # 分享短链 short_code 唯一索引：配合 content.py 的 IntegrityError 冲突重试，
        # 根除短码碰撞导致的分享串台（既有重复数据时建索引失败仅告警，不阻断启动）
        try:
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_share_short ON share_tokens(short_code)")
        except sqlite3.IntegrityError:
            log.warning("share_tokens.short_code 存在重复值，唯一索引创建失败（可清理后重建）")

        # FTS5 全文索引（trigram 分词，中文子串可检索）：
        # 搜索从 LIKE 全表扫改为 FTS 命中 + LIKE 兜底。SQLite < 3.34 无 trigram 时静默跳过，
        # 搜索接口自动回退 LIKE（try/except 兼容，不影响功能）。
        # 独立 FTS 表 + 触发器同步：仅 title/artist/album 变更时同步，避免 play_count 等高频
        # 计数更新反复触发 FTS 写放大。
        for _tbl, _cols in (("tracks", "title, artist, album"),
                            ("collected_tracks", "title, artist, album")):
            _col_list = _cols                          # title, artist, album
            _new_list = ", ".join("new." + x for x in _cols.split(", "))   # new.title, new.artist, new.album
            _old_list = ", ".join("old." + x for x in _cols.split(", "))   # old.title, old.artist, old.album
            try:
                _fts = _tbl + "_fts"
                c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS %s USING fts5(id, %s, tokenize='trigram')" % (_fts, _col_list))
                # 触发器同步 INSERT
                c.execute(("CREATE TRIGGER IF NOT EXISTS trg_%s_ai AFTER INSERT ON %s BEGIN "
                           "INSERT INTO %s(id, %s) VALUES(new.id, %s); END;") %
                          (_tbl, _tbl, _fts, _col_list, _new_list))
                # 触发器同步 UPDATE（仅文本列变化时触发，避免播放/点赞计数更新引发 FTS 写放大）
                c.execute(("CREATE TRIGGER IF NOT EXISTS trg_%s_au AFTER UPDATE OF %s ON %s BEGIN "
                           "INSERT INTO %s(%s, rowid, id, %s) VALUES('delete', old.rowid, old.id, %s); "
                           "INSERT INTO %s(id, %s) VALUES(new.id, %s); END;") %
                          (_tbl, _col_list, _tbl, _fts, _fts, _col_list, _old_list, _fts, _col_list, _new_list))
                # 触发器同步 DELETE
                c.execute(("CREATE TRIGGER IF NOT EXISTS trg_%s_ad AFTER DELETE ON %s BEGIN "
                           "INSERT INTO %s(%s, rowid, id, %s) VALUES('delete', old.rowid, old.id, %s); END;") %
                          (_tbl, _tbl, _fts, _fts, _col_list, _old_list))
                # 初始填充（幂等：仅当 FTS 空时）
                _fcnt = c.execute("SELECT COUNT(*) FROM %s" % _fts).fetchone()[0]
                if _fcnt == 0:
                    c.execute("INSERT INTO %s(id, %s) SELECT id, %s FROM %s" % (_fts, _col_list, _col_list, _tbl))
            except Exception as _e:
                log.warning("FTS5 init skipped for %s: %s", _tbl, _e)

        # AI 配色主题缓存（Folia major 移植）
        c.execute("""CREATE TABLE IF NOT EXISTS track_themes(
            track_id TEXT PRIMARY KEY,
            background_color TEXT NOT NULL,
            primary_color TEXT NOT NULL,
            accent_color TEXT NOT NULL,
            secondary_color TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')))""")

        # 全景音效分离音轨（2 轨：人声 vocals + 伴奏 accompaniment，opus）
        # status: pending（待处理）/ processing（分离中）/ done（完成）/ failed（失败）
        c.execute("""CREATE TABLE IF NOT EXISTS track_stems(
            track_id TEXT PRIMARY KEY,
            status TEXT DEFAULT 'pending',
            vocals TEXT DEFAULT '',
            accompaniment TEXT DEFAULT '',
            error TEXT DEFAULT '',
            created REAL DEFAULT 0,
            updated REAL DEFAULT 0)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_stems_status ON track_stems(status)")

        c.commit()
    finally:
        c.close()


def db():
    """业务连接：轻量 connect + 连接级 PRAGMA；表结构迁移由 migrate() 一次性完成。"""
    global _migrated
    if not _migrated:
        with _migrate_lock:
            if not _migrated:
                migrate()
                _migrated = True
    c = sqlite3.connect(DB, timeout=10)
    c.execute("PRAGMA busy_timeout=5000")
    c.execute("PRAGMA synchronous=NORMAL")
    return c
