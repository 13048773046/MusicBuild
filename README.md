# 流萤音乐 Liuying Music

音乐 App 一体化仓库：Python 后端 + SQLite + NodeJS 音源桥 + **iOS (SwiftUI) 客户端** + **Android (Jetpack Compose) 客户端**。

内置管理后台 H5、音源采集控制台、个性化推荐、WebSocket、音频 206 流式播放与防盗链。

- 后端端口：`127.0.0.1:8788`（由 Nginx 反代对外）
- 音源桥端口：`127.0.0.1:8789`
- 最低要求：Python 3.8+、NodeJS 16+、Nginx（推荐）、SQLite（Python 内置）
- 最新版本：**iOS v1.1.25 (build 25)** / **Android v1.1.22 (vc23)**（2026-10-31）
- 仓库状态：**已设为私有**（10-24）；CI 在 macOS runner 自动打 iOS 未签名 IPA + 冒烟测试

> **完整交接文档见 [`docs/流萤音乐-完整交接文档-20261023.md`](docs/流萤音乐-完整交接文档-20261023.md)**（v18，含服务器/部署信息）
> **Android 客户端文档见 [`docs/android-client.md`](docs/android-client.md)**
> **项目介绍见 [`docs/项目介绍.md`](docs/项目介绍.md)**

### 最新进展（2026-10-31）

- **全屏 Folia 背景覆盖根因修复**（iOS v1.1.25，复测"打开瞬间显示自定义图 → 稍后被 AI 情绪覆盖"浏览器 DOM 实测）：上一版只透明化 html/body/#root + 舞台内背景层，**漏掉播放器根容器**（#root 直接子，`position:fixed; inset:0; background:#000` 整屏黑底）→ React 挂载后黑底盖住原生背景图。本轮 `folia-patch.js`：①新增 `findRootContainers()` 透明化根容器；②背景层匹配升级为 backgroundColor **或** backgroundImage（渐变/封面层）；③压制机制改 **class + !important CSS**（React 重渲染重设 inline background 也不生效，根治"渲染完成 → 背景复活"竞态）；④恢复路径改扫 `__foliaBgOff` class（被压制层透明后被旧逻辑跳过导致无法恢复）。控件条实测无条件渲染、真实环境恒可见——"控件消失"为沉浸交互或黑底覆盖下视觉混淆，非渲染缺陷。浏览器实测：设图后全层透明、30 条 time + 切歌不复活、清除背景零残留
- **全屏 Folia AI 配色换肤修复**（iOS v1.1.24）：AI 配色生成完成不再重推 song（song 会重置 Folia 播放器 UI → 主题背景层残留竞态 + 控件按钮消失），改走**轻量 theme 消息**只换舞台背景层颜色；背景让位兜底强化（透明化**所有**铺满全屏的背景层 + 不受 120ms 防抖限制 + 80/200/500ms 延迟重试覆盖 React 异步渲染）；清除自定义背景也改轻量恢复（不再重推 song）；控件可见性在 React 重渲染后无条件恢复
- **全屏 Folia 播放器接入 AI 配色背景**（iOS v1.1.23）：舞台主题背景优先级对齐普通播放页——**自定义背景图（相册选图，原生层）→ AI 生成配色（`/api/ai/generate-theme` 内存缓存即时命中）→ 封面主色 → 深色兜底**。切歌/进入全屏页时缓存未命中先用封面主色推送，后台 AI 生成完成后自动换肤（`AIThemeService.lastGeneratedTrackId` 驱动，仅当前播放曲目生效）；清除自定义背景后回落到 AI 配色
- **全屏 Folia 自定义背景图修复链**（`f37bbcd`+`e452ac1`）：背景图移入全屏 Folia（顶栏相册选图/清除）；根因 = Folia 网页自带主题背景层（theme.backgroundColor, opacity .8）铺满舞台盖住注入层 → 改原生层渲染（scaledToFill 居中裁剪 cover + 0.25 黑罩）+ WKWebView 透明（isOpaque=false）+ 补丁 JS 透明化网页主题背景让位（MutationObserver 兜底 React 重渲染）

### 最新进展（2026-10-24）

- **iOS 双崩溃根因修复**（`c2ff12b`，crashfix2 IPA 已交付）：主线程崩溃 = WKWebView JS 回调在后台线程调 UIKit → 统一 `DispatchQueue.main.async` 分发；disconnected 崩溃 = AVAudioEngine 系统配置变化后 graph 连接失效 → 配置变化监听标记 graph 失效 + 播放前自动重建
- **Android 四项修复**（`c89227b`）：①启动预加载竞态（登录/切账号后"我的作品"不加载）；②播放页逐字歌词卡顿（每帧 new Paint+Typeface.create → 复用缓存）；③歌名跑马灯切歌不重启（`key(text)` 重建动画实例）；④全页面跳转改纯叠加淡入（去缩放）
- **Android 全跳转淡入覆盖**（`048b3e8`）：全屏歌词 Activity 进出、AppRoot 启动页/登录/主界面根级切换统一叠加淡入
- **Android v21 淡入强化**（`29df5f8` + 手动 alpha 层）：子页面跳转弃用 AnimatedContent 改手动 alpha 叠加层（`animateFloatAsState` 确定性播放，320ms），tab 切换 300ms；versionCode 21（版本名仍 1.1.20，覆盖安装必成功）；日志上报带 versionCode，服务器可精确确认用户安装版本
- **仓库已私有化**：10-24 已从公开改为私有，保护源码与服务器信息

### 此前里程碑（2026-10-18 ~ 10-23）

- **推荐算法 P0/P1 全量落地并部署**：音频特征（librosa 全量分析 + embedding）、用户听歌画像、协同过滤 ALS、多路召回（行为×场景×新鲜度×i2i×评论意图）+ 融合打分 + MMR 去重 + 3 小时短期兴趣窗口 + VIP 分级配额，前端行为采集三件套（播放/互动/评论意图）
- **全项目深度审查 23 项 + 全部修复**：后端 15 项（`bc3491f`，已部署线上，冒烟 236 PASS）、iOS 4 项（`480e59b`）、Android 3 项（`0857a1b`）
- **iOS P0 六项落地**（`673bdf3`+`8135948`）：列表行 Equatable 解耦、封面 ImageIO 降采样+后台解码、API DEBUG 日志、拔耳机自动暂停、原生 Haptics、歌词推送 10Hz
- **1.1.19 公测加固发布**：双端加固 + 强制更新；iOS 协议页改 App 内置浏览器（45dp 主色头部 + 居中网页标题）、登录页协议勾选同行圆形、Android 同步
- **分享 H5 歌词 + APP 卡片 + 深链跳歌**：顶部 APP 信息卡片（图标/名称/去 APP 听，按平台深链打开对应端并定位歌曲）、歌曲信息左对齐、未安装跳官网；资源接口防护（lyric/stream-url 要求 share token 或登录态）+ 客户端附加 token
- **合规与隐私**：设置页个人信息收集清单/应用权限清单入口（双端）+ 线上合规 H5（personal-info / app-permission / bind-email / user-agreement / privacy-policy）
- **日志/崩溃上报**：安卓全局崩溃落盘 + 自动上报服务器 app_logs 表（兼容三路径，管理端可查），iOS 崩溃同样入库
- **客户端安装包**：iOS IPA（未签名）v1.1.20 crashfix2（https://aka.doubaocdn.com/s/48jlYKP377）/ Android APK v1.1.20 vc21（https://aka.doubaocdn.com/s/XM5m7J9v3Y）

---

## 一、目录结构

```
.
├── server.py                 # 兼容入口（薄包装，实际逻辑在 server/ 包；python3 server.py 启动）
├── server/                   # 规范化后端包
│   ├── config.py             # 统一配置（config.json + 环境变量 + 默认值），所有路径在此派生
│   ├── database.py           # SQLite 连接 + 幂等建表/增量迁移
│   ├── common.py             # 鉴权/密码/Token/限流/流签名/短码/文件安全/通知等
│   ├── ws.py                 # WebSocket（RFC6455）握手/帧/推送
│   ├── recommend.py          # 个性化推荐（多路召回+融合+MMR+去重）
│   ├── qishui.py             # 汽水音乐分享解析
│   ├── media.py              # 封面压缩/上传魔数校验/音频时长
│   ├── handler.py            # HTTP 路由分发主体
│   ├── main.py               # 服务入口
│   └── templates/            # 从代码剥离的 H5 页面
│       ├── admin.html        #   管理后台
│       ├── collect.html      #   音源采集控制台（含汽水音乐登录）
│       ├── share_player.html #   单歌分享播放页
│       └── share_collect.html#   汽水分享链接采集页
├── source-bridge/            # NodeJS 音源桥
│   ├── bridge.js             #   HTTP 服务（长青音源 + 汽水 + 兜底聚合）
│   ├── collect_v3.py         #   采集脚本（榜单 + 搜索补量，路径已参数化）
│   ├── package.json
│   └── source.js             #   ⚠ 长青音源脚本（外部音源文件，见“音源桥说明”）
├── install/                  # 自动安装程序
│   ├── index.html            #   H5 安装向导（5 步）
│   ├── install_api.py        #   安装向导 HTTP 服务（127.0.0.1:8899，一次性令牌，装完自锁）
│   ├── installer.py          #   安装核心（检测/备份/配置/建库/服务/自检/回滚）
│   ├── backup.py             #   备份与回滚（可单独命令行使用）
│   └── templates/            #   systemd / nginx 配置模板
├── install.sh                # 一键安装入口
├── web/index.html            # 官网落地页（Nginx 静态根）
├── config.json.example       # 配置文件样例（安装程序会自动生成 config.json）
├── requirements.txt          # Python 依赖（Pillow / mutagen）
├── tests/                    # 冒烟测试与专项验证
├── MusicApp/                 # iOS SwiftUI 客户端
├── android/                  # Android Jetpack Compose 客户端
│   └── app/src/main/java/app/star2936/guava3291/
│       ├── state/            # PlayerManager / AuthManager / NotificationBus / AppState
│       ├── data/             # Api / Models
│       └── ui/              # 主框架/播放页/发现/我的/通知/侧边栏
├── docs/                     # 交接文档（最新版）+ 项目介绍 + Android 技术文档
└── .github/workflows/        # CI：冒烟测试 + 未签名 IPA 打包
```

---

## 二、全新服务器安装（推荐：H5 向导）

1. **下载源码**：`git clone` 或下载 Release 压缩包。
2. **配置域名**：在 DNS 控制台把域名（如 `music.example.com`）解析到本服务器 IP。
3. **上传并解压**到该域名的站点目录，例如 `/www/wwwroot/music/`（宝塔站点目录亦可）。
4. **执行安装入口**（root）：

   ```bash
   cd /www/wwwroot/music
   chmod +x install.sh
   ./install.sh music.example.com
   ```

5. **浏览器打开** `http://music.example.com/install/`，按 5 步向导操作：
   环境检测 → 站点配置（域名/端口）→ 后台密码（可随机生成）→ 确认 → 自动安装。
6. **安装完成页**给出并可一键复制：

   | 入口 | 地址 |
   |---|---|
   | 官网 | `https://<域名>/` |
   | 接口健康检查 | `https://<域名>/api/health` |
   | 管理后台 | `https://<域名>/music/admin.html` |
   | 采集控制台（含汽水音乐登录） | `https://<域名>/music/collect.html` |

   后台密码仅在完成页展示一次（选择“保留现有密码”除外），同时写入 `admin_pwd`。

> 暂不配置域名时，可执行 `./install.sh`，用 `http://服务器IP/install/` 访问。
> 安装向导仅在安装期可用，**成功后自动锁定并关闭**；正式 Nginx 配置会移除 `/install` 入口。

### 本地演练（不写系统配置）

```bash
./install.sh --local --dry-run        # 前台启动向导：http://127.0.0.1:8899/install/
# 或纯命令行演练：
python3 install/installer.py --root /tmp/test_music --domain t.example.com --gen-password --dry-run
```

---

## 三、覆盖升级现有服务器（先备份再做）

安装程序检测到已有部署（存在 `tracks.db` / `files/` / `admin_pwd`）时自动进入**覆盖升级**：

1. **强制先完整备份**：数据库（WAL checkpoint）、`files/`、全部配置、音源桥、systemd/nginx；
   逐项 sha256 校验通过后才替换。
2. **默认保留**现有后台密码（向导勾选）、`.sign_secret`（已分享短链/签名流不失效）、
   以及 `source-bridge/source.js` 等音源文件。
3. 数据库只做幂等增量迁移，**不删数据、不重建表**。
4. **任一步失败自动回滚**到安装前快照；备份保留最近 3 份。

手动备份 / 回滚：

```bash
python3 install/backup.py backup                 # 立即完整备份
python3 install/backup.py verify                 # 校验最新备份
python3 install/backup.py restore --yes          # 回滚到最新备份
python3 install/backup.py restore --backup-dir /www/wwwroot/music/backup/20261016_210000 --yes
```

备份产物：`backup/<时间戳>/full_backup.tar.gz` 与 `manifest.json`（含逐项 sha256），
以及安装报告 `backup/install_report.json`。

---

## 四、配置说明（config.json）

安装程序自动生成；优先级：**环境变量 > config.json > 内置默认值**。无 config.json 也能运行。
常用环境变量：`MUSIC_ROOT`、`MUSIC_PORT`、`MUSIC_CONFIG`、`MUSIC_DEBUG`、
`MUSIC_AUTH_STRICT`、`MUSIC_SIGN_STRICT`、`MUSIC_SIGN_SECRET`、`MUSIC_PUBLIC_URL`、`BRIDGE_PORT`。

> 所有历史硬编码绝对路径已消除，统一由 `config.py` 派生，`collect_v3.py` 同样读取该配置。

---

## 五、音源桥说明

- `bridge.js` 启动**强依赖** `source-bridge/source.js`（落雪音乐源格式脚本）。
  该文件为**外部音源文件、不随仓库提供**；缺失时音源桥无法启动，但**不影响后端与后台安装**，
  完成页会给出明确提示。
  - **覆盖升级**：自动从备份恢复 `source.js`、`download_collected.py`。
  - **全新安装**：请将可用的 `source.js` 放入 `source-bridge/` 后
    `systemctl restart source-bridge.service`。
- 汽水音乐解析依赖 `ly-music-source`（npm 自动安装），支持扫码/短信/Cookie 登录与 session 持久化。

---

## 六、运维常用命令

```bash
systemctl restart music.service            # 重启后端
journalctl -u music.service -f             # 后端实时日志
systemctl restart source-bridge.service    # 重启音源桥
journalctl -u source-bridge.service -f     # 音源桥日志
nginx -t && nginx -s reload                # 校验并重载 Nginx
```

---

## 七、测试与 CI

```bash
python3 tests/server_smoke_test.py                 # 冒烟测试（默认端口 18788，临时数据目录）
python3 tests/verify_push_dedup.py                 # 推荐 3 小时推送去重
python3 tests/verify_recommend_newmix.py           # 推荐新歌混入
```

- 当前冒烟 **236 项全部通过**（2026-10-23 审查修复后基线；后端 15 项修复/部署/验证见交接文档 7.5 节），对 iOS 客户端 API 行为零变化。
- CI（`.github/workflows/build.yml`）：推送 main → Ubuntu 跑冒烟测试 → macOS 构建未签名 IPA。
  因根目录保留了 `server.py` 薄入口，CI 与既有调用方式无需改动。

---

## 八、兼容性红线（重构承诺）

- iOS 客户端零改动；所有 `/api/*`、`/s/*`、`/music/*` 路径、参数、返回结构保持不变。
- 现有 `tracks.db` 直接沿用，仅幂等增量迁移。
- 端口、systemd 服务名、Nginx 反代路径与线上一致。
