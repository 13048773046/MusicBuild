#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""流萤音乐 · 统一配置层（架构规范化）

配置优先级（高 → 低）：
    1. 环境变量（MUSIC_ROOT / MUSIC_PORT / MUSIC_DEBUG / MUSIC_AUTH_STRICT /
       MUSIC_SIGN_STRICT / MUSIC_SIGN_SECRET / MUSIC_PUBLIC_URL / MUSIC_ADMIN_PWD_FILE /
       COLLECT_KEY / MUSIC_REC_BUCKET / MUSIC_REC_IDLE / MUSIC_CONFIG）
    2. config.json（由自动安装程序生成；放在部署根目录，或用 MUSIC_CONFIG 指定）
    3. 内置默认值（与历史线上行为一致）

设计要点：
    - 本模块是零内部依赖的“叶子模块”，其余模块统一从这里取常量与派生路径，
      彻底消除散落在各文件里的硬编码 /www/wwwroot/music。
    - 旧部署没有 config.json 也能运行（回退默认值 + 环境变量），平滑兼容。
"""
import os
import json
import logging
import secrets

# ---------------- 部署根与 config.json 定位 ----------------

# server/config.py 的上两级即部署根目录（<root>/server/config.py）
_PKG_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_user_config():
    """读取 config.json（安装程序生成）。找不到或解析失败时静默回退空配置。"""
    cfg_path = os.environ.get("MUSIC_CONFIG", "") or os.path.join(_PKG_PARENT, "config.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_USER_CFG = _load_user_config()


def _cfg_str(key, env_name, default):
    """字符串配置：环境变量优先，其次 config.json，最后默认值。"""
    v = os.environ.get(env_name)
    if v is not None and v != "":
        return v
    v = _USER_CFG.get(key)
    if isinstance(v, str) and v != "":
        return v
    return default


def _cfg_int(key, env_name, default):
    v = os.environ.get(env_name)
    if v is not None and v != "":
        try:
            return int(v)
        except ValueError:
            pass
    v = _USER_CFG.get(key)
    if isinstance(v, int):
        return v
    return default


def _cfg_bool(env_name, default=False):
    return os.environ.get(env_name, "1" if default else "0") == "1"


def _cfg_path(key, default_subpath):
    """路径配置：优先 config.json 的 paths.<key>（绝对路径）；
    否则以 ROOT 拼接历史相对路径。"""
    paths = _USER_CFG.get("paths")
    if isinstance(paths, dict):
        v = paths.get(key)
        if isinstance(v, str) and v:
            return v
    return os.path.join(ROOT, default_subpath)


# ---------------- 基础路径与端口 ----------------

ROOT = _cfg_str("root", "MUSIC_ROOT", "/www/wwwroot/music")
FILES = _cfg_path("files", "files")
FILES_LOW = _cfg_path("files_low", "files_low")
# 全景音效分离音轨目录（人声/伴奏 opus），部署时在 config.json paths.stems 指向数据盘
STEMS = _cfg_path("stems", "stems")
DB = _cfg_path("db", "tracks.db")
_ports_cfg = _USER_CFG.get("ports") if isinstance(_USER_CFG.get("ports"), dict) else {}
PORT = int(os.environ.get("MUSIC_PORT") or _ports_cfg.get("api")
           or _USER_CFG.get("port") or 8788)
BRIDGE_PORT = int(os.environ.get("BRIDGE_PORT") or _ports_cfg.get("bridge") or 8789)

# 音源桥 / 采集相关目录与文件（取代历史硬编码绝对路径）
BRIDGE_DIR = _cfg_path("bridge_dir", "source-bridge")
COLLECT_TARGET_FILE = _cfg_path("collect_target", "source-bridge/collect_target.json")
COLLECT_RUN_FILE = _cfg_path("collect_run", "source-bridge/collect_run.json")
COLLECT_LOG_FILE = _cfg_path("collect_log", "source-bridge/collect_v3.log")
COLLECT_V3 = _cfg_path("collect_v3", "source-bridge/collect_v3.py")
DOWNLOAD_COLLECTED = _cfg_path("download_collected", "source-bridge/download_collected.py")
QS_SESSION_FILE = _cfg_path("qs_session", "source-bridge/qs_session.json")

# 音源桥本机 HTTP 基址（server → bridge 反向代理用）
BRIDGE_URL = "http://127.0.0.1:%d" % BRIDGE_PORT

# 模板目录（server/templates）
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

# ---------------- 上传 / 请求限制 ----------------

MAX_UPLOAD = 100 * 1024 * 1024        # 单曲音频上限 100MB
MAX_PROFILE = 8 * 1024 * 1024         # 注册/改资料请求上限 8MB
MAX_COMMENT = 200                     # 评论最大字符
MAX_COMMENT_IMAGE = 8 * 1024 * 1024   # 单张评论图片上限 8MB
MAX_PACKAGE = 100 * 1024 * 1024       # 安装包（APK/IPA）上传上限 100MB（与 Nginx client_max_body_size 对齐）
MAX_NAME = 40                         # 昵称/歌手/标题最大长度

# ---------------- 鉴权 / 密码 / Token ----------------

TOKEN_TTL = 30 * 24 * 3600            # token 有效期 30 天
PBKDF2_ITER = 210000                  # 新密码哈希迭代次数
LEGACY_ITER = 120000                  # 旧哈希迭代次数（自动迁移）
AUTH_STRICT = _cfg_bool("MUSIC_AUTH_STRICT")
DEBUG = _cfg_bool("MUSIC_DEBUG")
# P2：Sentry 错误监控（可选）。留空=不启用；填入 DSN（环境变量 MUSIC_SENTRY_DSN 或此处）即生效，
# 线上 500/未捕获异常自动上报。依赖 sentry-sdk（pip install sentry-sdk），未安装时静默跳过。
SENTRY_DSN = os.environ.get("MUSIC_SENTRY_DSN", "").strip()
SOCKET_TIMEOUT = 60

# ---------------- 流媒体签名防盗链 ----------------

STREAM_SIGN_TTL = 1800                # 签名流 URL 有效期 30 分钟
SIGN_STRICT = _cfg_bool("MUSIC_SIGN_STRICT")
PUBLIC_BASE = _cfg_str(
    "public_url", "MUSIC_PUBLIC_URL",
    "https://www.dbmt999.asia/music").rstrip("/")
# 主域名（无 /music 前缀，安装程序设置；分享短链 OG:url 用）
PRIMARY_DOMAIN = _cfg_str("domain", "MUSIC_DOMAIN",
                          "www.dbmt999.asia").rstrip("/")

SIGN_SECRET = os.environ.get("MUSIC_SIGN_SECRET", "")
if not SIGN_SECRET:
    _s = (_USER_CFG.get("sign_secret") if isinstance(_USER_CFG.get("sign_secret"), str) else "")
    if _s:
        SIGN_SECRET = _s
if not SIGN_SECRET:
    _secret_file = _cfg_path("sign_secret", ".sign_secret")
    try:
        if os.path.exists(_secret_file):
            with open(_secret_file, encoding="utf-8") as f:
                _s = f.read().strip()
            if _s:
                SIGN_SECRET = _s
        if not SIGN_SECRET:
            SIGN_SECRET = secrets.token_hex(32)
            with open(_secret_file, "w", encoding="utf-8") as f:
                f.write(SIGN_SECRET)
    except OSError:
        SIGN_SECRET = secrets.token_hex(32)

try:
    os.makedirs(FILES, exist_ok=True)
except OSError:
    # 部署目录尚不可写（如未安装先做只读检查、非 root 探测）时不应阻断 import；
    # 真正写文件时再由对应逻辑暴露权限错误。
    pass

# ---------------- 日志 ----------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("music")

# ---------------- 采集入库密钥 ----------------

COLLECT_KEY = _cfg_str("collect_key", "COLLECT_KEY", "PLACEHOLDER_COLLECT_KEY")

# ---------------- 后台密码文件 ----------------

ADMIN_PWD_FILE = os.environ.get(
    "MUSIC_ADMIN_PWD_FILE",
    (_USER_CFG.get("admin_pwd_file") if isinstance(_USER_CFG.get("admin_pwd_file"), str) else "")
    or os.path.join(ROOT, "admin_pwd"))

# ---------------- 官网展示配置 ----------------

SITE_CONFIG_FILE = _cfg_path("site_config", "site_config.json")
_SITE_CONFIG_DEFAULTS = {
    "site_name": "流萤音乐",
    "ios_url": "",
    "android_url": "",
    "hero_subtitle": "为你精心挑选的音乐，流式播放不等待，逐字歌词跟着唱。",
    # 官网轮播图（后台动态管理，数量不固定）：[{name,title,link}]，name 为 files/banner_* 文件名
    "banners": [],
    # 极光离线推送开关（APP 被杀时是否推送系统通知）
    "jpush_enabled": False,
    # 关于页联系方式（App 内「关于」页从 /api/site-config 读取，后台可配，前端不再硬编码）
    "about_website": "https://www.dbmt999.asia",
    "about_email": "yosar027@outlook.com",
    "about_phone": "3065751692",
    "about_qq_group": "807910369",
    # 易支付（App 内开通会员）后台可配：开关 / 商户ID(pid) / 商户密钥(key) / 支付网关 / 回调与跳转地址
    "pay_enabled": False,
    "pay_merchant_id": "",
    "pay_merchant_key": "",
    "pay_gateway": "",
    "pay_notify_url": "",
    "pay_return_url": "",
    # 会员档位价格与时长（App 会员开通页展示 + 下单金额/天数均以后台配置为准，前端不写死）
    # 三档套餐：月卡/季卡/年卡 × VIP/SVIP（预置价按当前实际功能价值定，后台可改）
    "vip_month_price": 6,    "vip_month_days": 30,
    "vip_quarter_price": 16, "vip_quarter_days": 90,
    "vip_year_price": 48,    "vip_year_days": 365,
    "svip_month_price": 10,  "svip_month_days": 30,
    "svip_quarter_price": 26, "svip_quarter_days": 90,
    "svip_year_price": 68,   "svip_year_days": 365,
}

# ---------------- 全景音效（服务端 Demucs 分离 + 端上 HRTF） ----------------
# 2 轨（人声 vocals + 伴奏 accompaniment），opus 64kbps 压缩，按需/热门分离。
STEM_MODEL = "htdemucs"          # Demucs 模型
STEM_SEGMENT = 7                 # 分段长度（秒），小内存机器防 OOM
STEM_BITRATE = _cfg_int("stem_bitrate", "MUSIC_STEM_BITRATE", 64)   # opus 码率 kbps
STEM_SR = 44100                  # 输出采样率
# 全局同时只允许 1 个分离进程（CPU/内存受限，避免与在线服务争抢）
STEM_WORKER_BATCH = _cfg_int("stem_worker_batch", "MUSIC_STEM_BATCH", 3)   # 热门补齐每轮处理数
STEM_WORKER_IDLE = _cfg_int("stem_worker_idle", "MUSIC_STEM_IDLE", 1800)   # 补齐轮询间隔（秒）

# ---------------- AI 乐评配置 ----------------
# 接入参数（base/key/model）存于该文件（安装程序/运维写入，权限 600），
# 同时支持环境变量 AI_API_BASE / AI_API_KEY / AI_MODEL 兜底。
AI_CFG_FILE = _cfg_path("ai_config", "ai_config.json")
AI_API_BASE_DEFAULT = ("https://ws-5usvd1tklaq38lj3.cn-beijing.maas.aliyuncs.com"
                       "/compatible-mode/v1")
AI_MODEL_DEFAULT = "qwen-turbo"
AI_UID = "ai_liuying_comment"

# ---------------- AI 主动乐评（定时自动评论） ----------------
# 后端定时为歌曲生成并发布 AI 乐评。默认值可被「网站管理」页保存到 ai_comment_cfg 表动态覆盖（无需重启）。
AI_AUTO_INTERVAL = _cfg_int("ai_auto_interval", "MUSIC_AI_AUTO_INTERVAL", 3600)          # 自动评论周期（秒），默认 1 小时
AI_AUTO_COUNT = _cfg_int("ai_auto_count", "MUSIC_AI_AUTO_COUNT", 2)                      # 每轮评论歌曲数，默认 2 首
AI_AUTO_MAX_PER_TRACK = _cfg_int("ai_auto_max_per_track", "MUSIC_AI_AUTO_MAX_PER_TRACK", 2)  # 每首歌最多 AI 乐评条数
AI_AUTO_SKIP_DAYS = _cfg_int("ai_auto_skip_days", "MUSIC_AI_AUTO_SKIP_DAYS", 7)          # 近 N 天已自动评过的歌不再选（防集中）

# ---------------- 实时热词（AI 乐评热梗来源：百度热搜 + 贴吧热议，按歌曲信息匹配） ----------------
# AI 生成乐评前抓取当前网络热梗池，并用歌曲信息（歌名/歌手/曲风）匹配出相关的热梗注入上下文；
# 抓取失败时静默降级（无热梗，评论回到纯乐评）。
HOT_WORDS_ENDPOINT = _cfg_str("hot_words_endpoint", "MUSIC_HOT_WORDS_ENDPOINT",
                              "https://top.baidu.com/api/board?platform=wise&tab=realtime")
HOT_WORDS_ENDPOINT_2 = _cfg_str("hot_words_endpoint_2", "MUSIC_HOT_WORDS_ENDPOINT_2",
                                "https://tieba.baidu.com/hottopic/browse/topicList")
HOT_WORDS_TTL = _cfg_int("hot_words_ttl", "MUSIC_HOT_WORDS_TTL", 3600)       # 热词缓存（秒），默认 1 小时
HOT_WORDS_TIMEOUT = _cfg_int("hot_words_timeout", "MUSIC_HOT_WORDS_TIMEOUT", 8)
HOT_WORDS_LIMIT = _cfg_int("hot_words_limit", "MUSIC_HOT_WORDS_LIMIT", 20)   # 注入的最大热词条数
HOT_WORDS_MATCH = _cfg_int("hot_words_match", "MUSIC_HOT_WORDS_MATCH", 3)    # 按歌曲匹配出的热梗上限

# ---------------- AI 评论表情包（随机表情包 API → 下载本地 → 图片评论） ----------------
MEME_ENDPOINT = _cfg_str("meme_endpoint", "MUSIC_MEME_ENDPOINT", "https://meme-api.com/gimme")
MEME_PROB_PCT = _cfg_int("meme_prob", "MUSIC_MEME_PROB", 30)      # AI 评论附带表情包概率（百分比），默认 30%
MEME_TIMEOUT = _cfg_int("meme_timeout", "MUSIC_MEME_TIMEOUT", 10)
MEME_MAX_BYTES = _cfg_int("meme_max_bytes", "MUSIC_MEME_MAX_BYTES", 6 * 1024 * 1024)

# ---------------- 汽水音乐分享解析 UA ----------------

QISHUI_SHARE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                   "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1")

# ---------------- 封面缩略图参数 ----------------

COVER_SMALL_MAX_SIDE = 600            # 缩略图长边最大像素
COVER_SMALL_QUALITY = 82              # JPEG 质量
COVER_SMALL_MIN_SIZE = 150 * 1024     # 超过 150KB 才压缩（小图直接原样返回）

# ---------------- 个性化推荐参数 ----------------

REC_BUDGET = 20
REC_FRESH_DAYS = 14
REC_MAX_PER_ARTIST = 3
REC_MAX_PER_GENRE = 5
REC_MMR_LAMBDA = 0.7
REC_NOISE = float(os.environ.get("MUSIC_REC_NOISE", "1.2"))   # F4：扰动 0.6→1.2，让不同批次种子真正改变排序
REC_TITLE_SIM_THRESHOLD = 0.8
REC_BUCKET_SECONDS = int(os.environ.get("MUSIC_REC_BUCKET", str(6 * 3600)))
REC_SESSION_IDLE = int(os.environ.get("MUSIC_REC_IDLE", str(30 * 60)))
REC_EXPLORE_POOL = int(os.environ.get("MUSIC_REC_EXPLORE_POOL", "240"))   # F9：探索池=桶内 80%（8 桶每桶约 300 首）
REC_LIMITS = {
    "behavior": 40, "artist": 30, "genre": 30,
    "hot": 20, "fresh": 10, "explore": 30,
    # 2026-11-05 听感召回（audio_features 的 mood|tempo 桶）：单桶召回上限
    "audio_taste": 30,
}
# 防信息茧房：探索/新歌保底配额（2026-11-01 综合调研后新增）
# 纯权重竞争下行为/精准/歌手等"像你听过"的路会淹没新歌与随机探索——业界成熟做法是
# 显式配额保底（网易云 CCDF 可控类别多样性、抖音「一键破茧」、MMR/DPP 后处理重排）。
# 推荐结果中 fresh(新歌)+explore(随机探索) 合计占比下限，ai_comment_cfg 可用 rec_explore_min 覆盖。
REC_EXPLORE_MIN = float(os.environ.get("MUSIC_REC_EXPLORE_MIN", "0.15"))
REC_WEIGHTS = {
    # 2026-10-24 主辅重构：文本路（歌名/歌手/专辑/标签/常听歌手/场景/新歌）降为辅助，
    # 全部保留但权重下调补位；精准路（音频/协同/i2i/短期兴趣）升为主力（见下方 precise_ 组）。
    # 注意：精准路只对 VIP/SVIP（precise=True）启用；普通用户走本组粗略算法（文本+热度+探索）。
    "behavior": float(os.environ.get("MUSIC_REC_W_BEHAVIOR", "1.5")),
    # 2026-11-05：歌手权重上调、风格下调——规范化风格召回后单风格池可达数百首，
    # 与歌手路同权会让"同风格但不熟的歌手"淹没"常听歌手"（实测 artist 命中率被稀释）。
    "artist": float(os.environ.get("MUSIC_REC_W_ARTIST", "2.0")),
    "genre": float(os.environ.get("MUSIC_REC_W_GENRE", "1.0")),
    "hot": 0.4, "fresh": 1.5, "explore": float(os.environ.get("MUSIC_REC_W_EXPLORE", "1.0")),
    # 2026-11-05：听感桶召回（全用户可用，不依赖 genre/title），四路画像里的"听感"主力
    "audio_taste": float(os.environ.get("MUSIC_REC_W_AUDIO_TASTE", "1.8")),
    # 2026-11-05：LLM 语义标签（track_tags）心情/场景召回
    "tag_taste": float(os.environ.get("MUSIC_REC_W_TAG_TASTE", "1.3")),
    # 2026-11-05：多兴趣音频召回（MERT 加权 k-means，多口味用户）
    "audio_multi": float(os.environ.get("MUSIC_REC_W_AUDIO_MULTI", "1.5")),
    # 2026-11-05：时段画像（用户自己作息规律：深夜/清晨/午后/夜晚 各自爱听什么），温和乘性加权
    "time_taste": float(os.environ.get("MUSIC_REC_W_TIME", "0.8")),
}
REC_HOT_TOP = 60
# F3：推送去重窗口 3h→24h——长会话（实测最长 8.3h）中途不再重复推送已推过的歌
REC_PUSH_WINDOW = int(os.environ.get("MUSIC_REC_PUSH_WINDOW", str(24 * 3600)))
# 长期去重：解决"听来听去都是那几首"——
# 用户最近 N 天内听完成分 ≥ 阈值的歌，视为"已听过"，长期不再重复推荐（只推没听过的新内容）
REC_REPLAY_DAYS = int(os.environ.get("MUSIC_REC_REPLAY_DAYS", "30"))
REC_REPLAY_FRACTION_MIN = float(os.environ.get("MUSIC_REC_REPLAY_FRAC", "0.5"))

# ---------------- 天气×时段心情场景推荐参数（M2） ----------------
# 默认 Open-Meteo（免费、无需 key）；备选和风天气需用户自备 key 时替换 endpoint
WEATHER_ENDPOINT = _cfg_str("weather_endpoint", "MUSIC_WEATHER_ENDPOINT",
                            "https://api.open-meteo.com/v1/forecast")
WEATHER_TTL = int(os.environ.get("MUSIC_WEATHER_TTL", "1800"))     # 天气缓存 30 分钟
WEATHER_TIMEOUT = 10                                                # 出站请求超时 10s
# 场景路权重与融合系数（方案 §3.5：个性化×场景双向融合）
REC_WEIGHTS["scene"] = float(os.environ.get("MUSIC_REC_W_SCENE", "2.0"))
REC_LIMITS["scene"] = 25
REC_SCENE_BOOST = float(os.environ.get("MUSIC_REC_SCENE_BOOST", "1.5"))     # 场景加权倍数（近期爱听歌场景前移）
REC_SCENE_PROFILE = float(os.environ.get("MUSIC_REC_SCENE_PROFILE", "0.5")) # 应景歌吃个性化分的系数（吻合加权）

# ---------------- 精准推荐参数（音频特征 + 协同过滤，2026-10-24） ----------------
# 新增两条召回：①audio=音频内容特征相似（节奏/能量/音色/情绪像不像你常听的）
#               ②collab=全体用户行为协同过滤（听 A 的人也在听 B）
# 权重默认中等偏上，与文本路（歌名/歌手/专辑/标签）搭配融合，不替代任何现有路；
# ai_comment_cfg 可用 rec_w_audio / rec_w_collab 覆盖。
REC_WEIGHTS["audio"] = float(os.environ.get("MUSIC_REC_W_AUDIO", "4.0"))
REC_LIMITS["audio"] = 60
REC_WEIGHTS["collab"] = float(os.environ.get("MUSIC_REC_W_COLLAB", "3.0"))
REC_LIMITS["collab"] = 60
# i2i 关联召回（2026-10-24 优化）：常听歌 → 音频特征最相似的歌（item_sims 表，离线生成）
REC_WEIGHTS["i2i"] = float(os.environ.get("MUSIC_REC_W_I2I", "3.0"))
REC_LIMITS["i2i"] = 30
REC_I2I_SEED = 5           # 取用户常听 topN 首歌做种子
REC_I2I_PER = 20           # 每首种子取关联 topN
REC_AUDIO_TOP = 80        # 音频相似候选截断数
REC_COLLAB_TOP = 60       # 协同过滤候选截断数
REC_PREF_TOP = 8          # 用户偏好画像：取近期高分行为歌的最大数（特征加权均值）
REC_MAX_PRECISE = int(os.environ.get("MUSIC_REC_MAX_PRECISE", "14"))  # 每批推荐里「为你精推」最多 10 首（50% 配额）

# ---------------- 音频双数据（2026-11-01 用户口径重构） ----------------
# audio 路找相似：MERT 768 维语义余弦为主（打分） + librosa 20 维节奏/能量/音色做差异筛选。
# librosa 数据由 feature_extract.py 写入 raw_vec（20 维归一化）；MERT 由 embed_mert.py 写入 vec（768 维）。
REC_LIBROSA_DIM = 20
# 候选歌与用户 librosa 偏好的余弦低于该值 → 判定节奏/能量/音色差异过大，剔除不推。
# 0.30 为宽松初始值（只滤明显不搭的歌）；线上可按效果经 ai_comment_cfg rec_librosa_filter 调整。
REC_LIBROSA_FILTER = float(os.environ.get("MUSIC_REC_LIBROSA_FILTER", "0.30"))
# 行为画像时间窗（2026-11-01 用户口径）：用户行为数据保留 90 天以内——
# 近 3 天=最新段（×0.7）、3 天前~90 天=过去段（×0.3），90 天以前的行为不参与画像。
REC_PROFILE_WINDOW_DAYS = int(os.environ.get("MUSIC_REC_PROFILE_WINDOW", "90"))
# 2026-10-24 主辅重构新增：
REC_PRECISE_VIP_BOOST = float(os.environ.get("MUSIC_REC_PRECISE_VIP_BOOST", "1.3"))  # VIP/SVIP 精准路再上调系数

# ---------------- 邮箱验证码登录（SMTP） ----------------
# 配置优先级：环境变量 > config.json > 默认空（未配置时邮箱登录不可用）
SMTP_HOST = _cfg_str("smtp_host", "MUSIC_SMTP_HOST", "")               # SMTP 服务器，如 smtp.qq.com
SMTP_PORT = _cfg_int("smtp_port", "MUSIC_SMTP_PORT", 465)             # SMTP 端口，QQ邮箱默认 465（SSL）
SMTP_USER = _cfg_str("smtp_user", "MUSIC_SMTP_USER", "")             # 发件邮箱地址，如 xxx@qq.com
SMTP_PASS = _cfg_str("smtp_pass", "MUSIC_SMTP_PASS", "")             # SMTP 授权码（QQ邮箱开启SMTP后获取的授权码）
SMTP_FROM_NAME = _cfg_str("smtp_from_name", "MUSIC_SMTP_FROM_NAME", "流萤音乐")  # 发件人显示名称
EMAIL_CODE_EXPIRE = 300                                                 # 验证码有效期（秒），默认 5 分钟
EMAIL_CODE_RESEND_INTERVAL = 60                                         # 重发间隔（秒），默认 1 分钟
EMAIL_CODE_MAX_FAIL = 5                                                 # 连续校验失败次数上限（≥5 次作废，防爆破）
EMAIL_CODE_DAILY_LIMIT = 20                                             # 单个邮箱每日发送上限（防轰炸）
