import AVFoundation
import MediaPlayer
import UIKit
import Combine

// MARK: - 播放模式

enum PlayMode: CaseIterable {
    case listLoop   // 列表循环
    case singleLoop // 单曲循环
    case shuffle    // 随机播放

    var symbol: String {
        switch self {
        case .listLoop: return "repeat"
        case .singleLoop: return "repeat.1"
        case .shuffle: return "shuffle"
        }
    }

    var label: String {
        switch self {
        case .listLoop: return "列表循环"
        case .singleLoop: return "单曲循环"
        case .shuffle: return "随机播放"
        }
    }
}

/// 侧边栏「听歌模式」：默认模式（推荐流）/ 我的作品（作品列表循环）/ 我的喜欢（点赞列表循环）/ 新鲜模式（完全随机）
enum ListenMode: String, CaseIterable {
    case normal = "默认模式"
    case works = "我的作品"
    case liked = "我的喜欢"
    case fresh = "新鲜模式"

    var icon: String {
        switch self {
        case .normal: return "list.bullet"
        case .works: return "music.note.list"
        case .liked: return "heart"
        case .fresh: return "sparkles"
        }
    }
}

final class PlayerManager: NSObject, ObservableObject {
    static let shared = PlayerManager()

    let player = AVQueuePlayer()
    /// 引擎音效实时处理引擎（3D 立体/360 环绕/全景/复古/现场/流行/摇滚）
    let enginePlayer = AudioEnginePlayer()
    /// 当前是否在用引擎播放（任一引擎音效模式；歌词等组件据此决定取数源）
    var usingEngine = false

    @Published var queue: [Track] = []
    /// 队列内容级变化版本号：原位插入/替换时 +1，
    /// VerticalPager 据此识别「队列内容变了但队首/长度未变」的场景并强制重建页面，
    /// 避免复用旧索引的缓存页面（导致显示信息与播放音频不符）。
    @Published var queueRevision = 0
    @Published var currentIndex: Int = -1
    @Published var isPlaying = false
    @Published var progress: Double = 0
    @Published var currentTime: Double = 0
    @Published var duration: Double = 0
    @Published var playMode: PlayMode = .listLoop
    /// 首页播放页背景是否偏浅（供透明导航栏决定文字颜色）。
    /// 初始 false：切歌/加载中背景为深色渐变，配白色文字；封面主色就绪后按亮度更新。
    @Published var playerCardBackgroundLight: Bool = false
    /// 当前歌曲封面提取的主色（原始值未压暗）；nil = 加载中/无封面。
    /// 播放页背景 = 主色×0.4，侧边栏背景 = 主色×0.3（比播放页再暗一档）
    @Published var playerCardDominant: UIColor? = nil
    /// 已缓冲进度 0...1（取 currentItem.loadedTimeRanges 中包含当前播放点的一段）
    @Published var bufferProgress: Double = 0
    /// 是否正在缓冲（等待网络数据，用于进度条加载动画）
    @Published var isBuffering: Bool = false
    /// 当前音频加载失败（网络中断/资源损坏/流不可用），UI 展示重试入口
    @Published var loadFailed: Bool = false
    /// 全局音效选择（切歌不重置）
    @Published var selectedEQ: String = "原音"
    /// 侧边栏听歌模式：默认模式 / 我的喜欢 / 新鲜模式
    @Published var listenMode: ListenMode = .normal
    /// 当前正在播放的场景音乐名（nil = 非场景）。
    /// 场景是「临时电台」：不属于任何听歌模式、不写入模式会话、不自动续拉推荐/搜索流；
    /// 侧滑栏据此不高亮听歌模式、改为高亮对应场景卡片。
    @Published var sceneTag: String? = nil
    /// 侧边栏选中态：仅用户「通过侧边栏主动选择模式」时非 nil，侧滑栏据此显示高亮/对勾。
    /// 历史播放 / 歌单播放 / 发现页点歌等播放入口会清空它，侧滑栏不显示任何已选中状态。
    @Published var sidebarSelection: ListenMode? = nil
    /// 有限列表循环：历史播放 / 歌单播放等「固定列表」来源。
    /// true 时队列不自动续拉，播到列表末尾回第一首循环（playMode 为 listLoop 时）。
    @Published var finiteLoopList = false
    /// 记忆续播：每个模式「最近一次通过页面点播的歌曲 id」（发现页→normal、我的作品→works、我的喜欢→liked）。
    /// 侧滑栏重新点击该模式时，重新加载列表后定位到这首歌的「下一首」自动播放；无记忆则从头。
    private(set) var resumeByMode: [ListenMode: String] = [:]
    func noteResumeTrack(_ mode: ListenMode, _ id: String) { resumeByMode[mode] = id }
    /// 当前用户点赞歌曲 id 集合（懒加载缓存）：供播放页红心初始态兜底
    /// （历史/歌单等入口构造 Track 时 isLiked 为 nil，用本集合补齐点过赞的红心）
    @Published var likedIds: Set<String> = []
    private var likedIdsLoaded = false

    /// 懒加载当前用户点赞列表到 likedIds（幂等；未登录不加载）
    func ensureLikedIdsLoaded() {
        guard !likedIdsLoaded, let uid = AuthManager.shared.currentUser?.id else { return }
        likedIdsLoaded = true
        Task {
            if let list = try? await API.likedTracks(userId: uid) {
                let ids = Set(list.map { $0.id })
                await MainActor.run { likedIds = ids }
            }
        }
    }
    /// 点赞成功：增量更新缓存
    func noteLiked(id: String) { likedIds.insert(id) }
    /// 取消点赞：增量更新缓存
    func noteUnliked(id: String) { likedIds.remove(id) }
    /// 切到「我的喜欢/新鲜模式」前记住的循环模式，切回默认模式时恢复
    var savedNormalMode: PlayMode = .listLoop

    // MARK: - 原音 → 引擎 无缝接力（下载期间原音继续出声）

    /// 引擎预加载切换任务：取消旧任务防迟到接管
    var engineSwitchTask: Task<Void, Never>?
    /// 是否正在为「原音 → 引擎」后台预下载（UI 显示加载百分比）
    @Published var engineSwitchInProgress: Bool = false
    /// 引擎整曲下载进度 0...1（engineSwitchInProgress 期间有效）
    @Published var engineDownloadFraction: Double = 0
    /// 音效操作的短暂提示（加载成功/失败/已生效），nil 不显示；自动延时消失。
    @Published var eqToast: String?
    private var eqToastWorkItem: DispatchWorkItem?
    /// 弹出音效提示（2 秒后自动消失；连续调用以最新为准）。
    func showEQToast(_ text: String) {
        eqToastWorkItem?.cancel()
        eqToast = text
        let work = DispatchWorkItem { [weak self] in self?.eqToast = nil }
        eqToastWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.0, execute: work)
    }
    /// 弱网播放 UI：当前是否离线（失败视图文案区分「网络未连接」与「加载失败」）
    var networkOffline: Bool { NetworkMonitor.shared.isOffline }
    /// 当前播放曲目的码率档（弱网=low 96k，Wi-Fi=原码率；供引擎下载/缓存键一致使用）
    var activeBitrate: API.Bitrate = .high
    /// 当前码率档决策：蜂窝弱网 → 低码率；Wi-Fi → 原码率（离线由共享缓存兜底）
    var effectiveBitrate: API.Bitrate {
        NetworkMonitor.shared.isCellular ? .low : .high
    }
    /// 已从低码率升级到原码率重试的曲目（low 失败 → high 兜底，避免同一首反复卡 low）
    var bitrateUpgraded: Set<String> = []
    /// 加载期间（duration==0）的待应用拖动位置：就绪后立即跳到该处
    var pendingSeekFraction: Double?

    var pendingArtwork: UIImage?
    /// 已构建好的锁屏 artwork 对象（切歌时重建一次，进度刷新不再重复创建，降低 0.1s 刷新开销）
    var currentArtwork: MPMediaItemArtwork?
    /// 锁屏信息最近一次刷新时间（updateNowPlaying 节流用）
    var lastNowPlayingUpdate: TimeInterval = 0
    var timeObserver: Any?
    var endObserver: NSObjectProtocol?
    var failObserver: NSObjectProtocol?

    // MARK: - 播放池健壮性（企业级防护）

    /// 切歌/重载的加载任务：快速连续切歌时取消旧任务，
    /// 避免旧签名 URL 请求继续占用带宽、迟到响应覆盖新歌状态。
    var loadTask: Task<Void, Never>?
    /// 已做过「自动重试一次」的歌曲 id：防止同一首歌无限自动重试；
    /// 切到新歌时重置。手动重试按钮不受影响。
    var autoRetriedTrackId: String?
    /// 连续播放失败计数：失败自动切下一首时累加，切到新歌或手动播放时重置；
    /// 达到上限（3 首）则停止自动切歌，避免无限循环。
    var consecutiveFailCount: Int = 0
    /// 连续失败自动切歌上限
    let maxConsecutiveFails = 3
    /// 缓冲卡死检测：连续缓冲超过阈值且播放点无进展 → 判定加载失败走自愈
    var bufferingStallStart: Date?
    var stallProgressAtStart: Double = 0
    /// 初始加载（还没拿到时长 duration=0）卡死检测用的播放点基准：
    /// 用于「新歌首包挂起」场景——此时 progress 无意义，只有 currentTime 停在 0
    var stallTimeAtStart: Double = 0
    /// 缓冲卡死判定阈值（秒）：按网络动态——离线不判定（等网络恢复），
    /// 蜂窝网放宽到 25s（弱网下首包慢，过早判定会频繁跳歌），Wi-Fi 15s。
    var effectiveStallTimeout: TimeInterval {
        if NetworkMonitor.shared.isOffline { return .infinity }
        return NetworkMonitor.shared.isCellular ? 25 : 15
    }

    // MARK: - 安全播放收口（崩溃防护）

    /// 统一安全播放入口：AVPlayer.play() 前确保音频会话激活。
    /// 崩溃场景：拔耳机/蓝牙断连/来电结束时，AVAudioSession 可能处于
    /// inactive/disconnected 状态，此时直接 player.play() 会抛 ObjC 层的
    /// NSException "player started when in a disconnected state"（Swift 无法 catch），
    /// 表现为 signal 6 崩溃。所有原音播放路径统一走这里。
    /// 会话激活失败（仍断连）时放弃本次播放请求，等待 route change / 打断结束
    /// 通知再恢复——宁可短暂无声也不崩溃。
    func safePlay() {
        if usingEngine {
            enginePlayer.play()
            return
        }
        guard player.currentItem != nil else { return }
        // 重新激活会话：拔耳机后系统可能已把会话停用，激活成功才能安全 play
        do {
            try AVAudioSession.sharedInstance().setActive(true)
        } catch {
            LogReporter.shared.log(level: "error", tag: "audio",
                                   message: "safePlay 会话激活失败: \(error.localizedDescription)")
            return
        }
        player.play()
    }

    /// 会话就绪重试：路由变化/打断结束后统一调用，
    /// 先激活会话再恢复播放（与 safePlay 同源防护）。
    func safeResumePlayback() {
        try? AVAudioSession.sharedInstance().setActive(true)
        if loadFailed {
            reloadCurrent()
            return
        }
        if usingEngine {
            enginePlayer.play()
        } else if player.currentItem != nil {
            player.play()
        }
        isPlaying = true
        updateNowPlaying(force: true)
    }

    // MARK: - 下一首预加载（只预热签名 URL + 连接头部，不整首下载）

    /// 预加载窗口：当前歌之后预加载的歌曲数。按网络动态降级——
    /// 离线 0（不预取，避免无用请求）；蜂窝 2（弱网优先当前曲带宽，避免预取抢带宽）；
    /// Wi-Fi 4（正常全量预取，切歌秒开）。
    /// 预取 ahead 数量：固定 3 首（Wi-Fi 与蜂窝一致）。
    /// 弱网保护由「当前曲就绪后才启动预取」+「只取每首开头一小部分」承担，不靠减少数量。
    var prefetchAheadCount: Int {
        if NetworkMonitor.shared.isOffline { return 0 }
        return 3
    }
    /// 签名播放 URL 缓存：trackId → URL。
    /// 切歌时优先复用，省去再向后端签发的网络往返，显著缩短切歌等待（“秒开”）；用掉即移除。
    var signedURLPool: [String: URL] = [:]
    /// 签名 URL 缓存的 FIFO 顺序（超限时淘汰最旧）
    var urlPoolOrder: [String] = []
    /// 签名 URL 缓存上限（超出淘汰最旧，防止无限堆积）
    let urlPoolLimit = 16
    /// 进行中的预加载任务（切歌/队列变化时取消，防错加载）
    var prefetchTask: Task<Void, Never>?
    /// 预取启动轮询（当前曲就绪探测）
    var prefetchReadyTimer: Timer?

    // MARK: - 个性化推荐流（汽水式无限听歌流）

    /// 已推荐过的歌曲 id（推荐流无限续推时传给后端 exclude，防止重复推荐同一批）
    var recommendedIds: Set<String> = []
    /// 推荐流每次续拉最大排除量（防止 exclude 参数无限膨胀）
    let recommendedExcludeCap = 500


    // MARK: - 收听行为追踪（个性化推荐数据源）

    /// 单曲收听状态：按 trackId 归属，切歌/重播时更新
    struct ListenState {
        var trackId: String = ""
        var seconds: Double = 0      // 本次会话累计收听秒数（真实出声才累计）
        var reported: Double = 0     // 已上报基线（每次只上报差值，避免重复计数）
        var completedPlays: Int = 0  // 完整听完整首的次数（同一首歌听了几遍）
        var seekCount: Int = 0       // P1 负信号：本曲累计手动拖动次数（快进/快退）
        var route: String = ""       // P1 归因：当前曲推荐来源路（audio,behavior,...），listen 上报带回
    }
    var listen = ListenState()
    var listenTimer: Timer?
    /// 网络恢复自动续播订阅（NetworkMonitor 状态变化）
    var networkCancellable: AnyCancellable?
    /// 是否「播放中被网络打断」（用于网络恢复自动续播；用户手动暂停会清除）
    var interruptedWhilePlaying = false

    /// 收听结算触发原因：switchTrack 用于快速切走的“跳过”负向信号判定
    enum ListenFlushReason { case switchTrack, pause, complete, heartbeat, background }

    override init() {
        super.init()
        let session = AVAudioSession.sharedInstance()
        try? session.setCategory(.playback, mode: .default)
        try? session.setActive(true)

        // 显式固定播放音量：App 不提供音量调节，确保任何情况下输出增益恒为 1.0，
        // 避免被异常状态（如流中断重建、模式切换残留）影响导致忽大忽小
        player.volume = 1.0

        // 关闭 AVQueuePlayer 自动前进（默认 .advance）：
        // 播完统一由 didPlayToEndTime → next() 唯一驱动前进。若保持系统自动前进，
        // 系统内部前进与代码 advanceToNextItem 存在时序竞争，偶发「自动切歌跳到下下首」
        // （实际播放下下首、界面索引仍是下一首，音频与信息错位一位）。
        player.actionAtItemEnd = .pause

        setupRemoteCommands()
        setupInterruptionHandling()
        setupEngineRestoreHandling()
        setupRouteChangeHandling()   // P0-4：拔耳机/蓝牙断连自动暂停，防隐私外放

        // P1-3（顺带落地）：iOS 17+ 输出设备断开时系统自动打断（min iOS 16 需上方监听兜底）
        if #available(iOS 17.0, *) {
            try? AVAudioSession.sharedInstance().setPrefersInterruptionOnRouteDisconnect(true)
        }

        // 弱网播放：网络恢复自动续播——断网导致加载失败/缓冲中断的歌曲，
        // 网络一恢复立即自动重载当前曲（保持进度），用户无感续播。
        // 仅处理"正在听却被网络打断"的场景：loadFailed 且当前曲未变；
        // 用户手动暂停/切走不触发。
        networkCancellable = NetworkMonitor.shared.$isOffline
            .dropFirst()   // 跳过初始值，只响应变化
            .receive(on: DispatchQueue.main)
            .sink { [weak self] offline in
                guard let self, !offline else { return }   // 仅网络恢复时
                // 只在「播放中被网络打断」的场景自动续播：用户手动暂停/切走不打扰
                guard self.interruptedWhilePlaying,
                      self.loadFailed, self.current != nil else { return }
                self.interruptedWhilePlaying = false
                self.loadFailed = false
                self.isBuffering = false
                self.playCurrent(flushReason: .heartbeat,
                                 resumeSeconds: self.currentTime,
                                 autoResume: true,
                                 preserveProgress: true)
            }

        timeObserver = player.addPeriodicTimeObserver(
            forInterval: CMTime(value: 1, timescale: 10),   // 0.1s：进度条等 UI 平滑（歌词另有逐帧实时源）
            queue: .main
        ) { [weak self] time in
            guard let self = self else { return }
            // 引擎音效模式（3D 立体/360 环绕/全景/复古/现场/流行/摇滚）：AVPlayer 无 currentItem，
            // 其 periodic observer 会把 currentTime/progress/duration/isBuffering 全部写回 0，
            // 覆盖 enginePlayer.onProgress 的进度数据 → 进度条/歌词/拖动条卡死。必须整体隔离。
            guard !self.usingEngine else { return }
            let sec = CMTimeGetSeconds(time)
            self.currentTime = sec.isFinite ? sec : 0
            let d = CMTimeGetSeconds(self.player.currentItem?.duration ?? .zero)
            if d.isFinite, d > 0 {
                self.duration = d
                self.progress = min(max(self.currentTime / d, 0), 1)
                // 加载期间拖动待应用：AVPlayer 拿到真实时长后立即跳过去
                if let pf = self.pendingSeekFraction {
                    self.pendingSeekFraction = nil
                    let target = CMTime(seconds: pf * d, preferredTimescale: 600)
                    self.player.seek(to: target)
                    self.currentTime = pf * d
                    self.progress = pf
                }
            }
            // 缓冲状态：播放点数据未到达时（卡顿/加载中）显示加载动画
            // ⚠ 修复：切歌重建窗口期（旧 item 已 replaceCurrentItem(nil)、新 URL 尚未就绪），
            //   currentItem==nil 且 timeControlStatus==.paused —— 此前无条件按 .paused 把
            //   isBuffering 写回 false：切歌瞬间看不到加载动画，且缓冲卡死自愈被绕过。
            //   无 currentItem 时保持 playCurrent 设置的加载态（isPlaying 且未失败 = 加载中）。
            if self.player.currentItem != nil {
                self.isBuffering = (self.player.timeControlStatus == .waitingToPlayAtSpecifiedRate)
            } else {
                self.isBuffering = (self.isPlaying && !self.loadFailed)
            }
            // 播放正常（非缓冲且在播放）：重置连续失败计数
            if !self.isBuffering && self.isPlaying {
                self.consecutiveFailCount = 0
            }
            // 已缓冲进度：当前已加载区间的终点 / 总时长
            if let item = self.player.currentItem, self.duration > 0,
               let range = item.loadedTimeRanges.first?.timeRangeValue {
                let bufferEnd = CMTimeGetSeconds(range.start) + CMTimeGetSeconds(range.duration)
                self.bufferProgress = min(max(bufferEnd / self.duration, 0), 1)
            }
            // 初始加载失败检测（404/网络断时 AVPlayerItem.status 变为 .failed，
            // 不触发 .AVPlayerItemFailedToPlayToEndTime 通知，只能轮询状态）
            if self.player.currentItem?.status == .failed {
                self.handlePlaybackFailure()
            }
            // 缓冲卡死检测：已有 duration 却长时间无进展 → 分级自愈（先同曲重载、再失败跳歌）。
            // 2026-10-19 修复：初始加载（duration=0，首包挂起）同样纳入检测——
            // 此前 duration>0 条件把「新歌加载中永久转圈」排除在自愈外（广告倒计时自然结束
            // 后进入播放页即踩中：播放器一直 waitingToPlayAtSpecifiedRate 且无时长，永不恢复）。
            // 离线时 effectiveStallTimeout = ∞，不判定——等网络恢复后 AVPlayer 自动续缓冲。
            if self.isBuffering {
                if self.bufferingStallStart == nil {
                    self.bufferingStallStart = Date()
                    self.stallProgressAtStart = self.progress
                    self.stallTimeAtStart = self.currentTime
                } else if let start = self.bufferingStallStart,
                          Date().timeIntervalSince(start) > self.effectiveStallTimeout {
                    // 已有时长：按进度无进展判定；初始加载（无时长）：按播放点仍为 0 判定
                    let stalled = self.duration > 0
                        ? (self.progress - self.stallProgressAtStart < 0.01)
                        : (self.currentTime - self.stallTimeAtStart < 0.01)
                    if stalled {
                        self.bufferingStallStart = nil
                        self.handleBufferingStall()
                    }
                }
            } else {
                self.bufferingStallStart = nil
            }
            self.updateNowPlaying()
        }

        endObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime,
            object: nil,
            queue: .main
        ) { [weak self] note in
            guard let self else { return }
            // 只处理「当前正在播放的 item」的播完通知：actionAtItemEnd=.pause 下，
            // 播完的 item 仍停留在 currentItem，直到这里 advance 才移除。
            // 旧/残留 item 的迟到通知（用户已手动切走、队列被替换重建等）若也触发
            // 一次 next()，会再跳一首（播放与索引错位），必须按对象身份过滤。
            if let item = note.object as? AVPlayerItem, item !== self.player.currentItem {
                return
            }
            // 完整听完一首：累计“听了几遍”，并结算收听时长
            if self.listen.trackId == self.current?.id {
                self.listen.completedPlays += 1
            }
            self.flushListen(.complete)
            self.next()
        }

        // 音频流加载失败（网络中断/资源损坏/服务器返回错误）：统一走自愈路径
        // （UI 展示重试 + 有限自动重试一次），避免 App 静默卡死
        // ⚠ 修复：必须按 item 身份过滤——切歌/替换 currentItem 时，旧 item 的加载流
        //   被中断（尤其弱网加载中切歌），系统会给旧 item 发失败通知。
        //   此前不过滤会误触发 handlePlaybackFailure：新歌被误判加载失败 →
        //   isPlaying 被置 false（界面"暂停"）+ 1.5s 后误重载再中断 → 新歌
        //   「既不播放也不加载」甚至跳到下下首（切歌瞬间弱网掉链的根因）。
        failObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemFailedToPlayToEndTime,
            object: nil,
            queue: .main
        ) { [weak self] note in
            guard let self else { return }
            if let item = note.object as? AVPlayerItem, item !== self.player.currentItem {
                return   // 旧 item 的迟到失败通知（已被切歌/替换中断）：丢弃
            }
            self.handlePlaybackFailure()
        }

        // 内存压力：清空图片/主色/歌词/URL 会话缓存，防止长滑播放后内存膨胀被系统杀进程
        NotificationCenter.default.addObserver(
            forName: UIApplication.didReceiveMemoryWarningNotification,
            object: nil,
            queue: .main
        ) { _ in
            CoverImageCache.shared.clear()
            LyricsCache.shared.clear()
            CoverPalette.clearCache()
            API.clearLyricCache()
            URLCache.shared.removeAllCachedResponses()
        }

        // 收听心跳：每秒累计真实播放秒数，每 30s 自动上报一次（防 App 被杀丢失数据）
        listenTimer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            self?.listenTick()
        }
        // 退后台/切应用前结算一次收听时长
        NotificationCenter.default.addObserver(
            forName: UIApplication.willResignActiveNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            self?.flushListen(.background)
        }
        NotificationCenter.default.addObserver(
            forName: UIApplication.didEnterBackgroundNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            self?.flushListen(.background)
            // 保存播放会话：杀 App 重开后，短时间间隔接着「上次那首的下一首」继续播
            self?.saveSessionToDisk()
            // 保持音频会话激活：防止后台期间会话被降权/停用导致音量被压低或无声
            try? AVAudioSession.sharedInstance().setActive(true)
        }
    }

    // MARK: - 播放会话持久化（杀 App 重开：短时间接着上次的下一首，长时间重新来）

    /// 短时间窗口：距上次保存 < 此值（默认 5 分钟）恢复会话、接着上次那首的下一首；
    /// 超过（如隔 10 分钟）则丢弃旧会话，重新进入带随机性的新鲜推荐流
    static let sessionResumeWindow: TimeInterval = 300
    static let sessionKey = "PlayerManager.savedSession.v1"
    /// 保存窗口：当前歌之前保留 N 首、之后保留 M 首（限制 UserDefaults 体积）
    static let sessionKeepBefore = 30
    static let sessionKeepAfter = 90

    struct SavedSession: Codable {
        var queue: [Track]
        var currentIndex: Int
        var listenMode: String
        var playModeIndex: Int
        var savedNormalModeIndex: Int
        var searchContext: String?
        var wasPlaying: Bool
        var savedAt: TimeInterval
        /// 场景音乐上下文（2026-11-05）：退出 App 前正在听的场景名（nil = 非场景）。
        /// 冷启动恢复时据此还原场景态，避免"重开 App 场景模式没内容/不高亮"。
        var sceneTag: String? = nil
    }

    /// 把当前播放会话（队列 + 位置 + 模式 + 进度态）写入本地。
    /// 只在进入后台时保存一次：切歌后杀 App 必经后台回调，状态即最新，避免频繁写盘。
    deinit {
        listenTimer?.invalidate()
        loadTask?.cancel()
        if let timeObserver {
            player.removeTimeObserver(timeObserver)
        }
    }

    /// 切换音效：直接应用（EQBSheet 调用入口）。
    /// - 引擎音效之间切换：只换引擎参数，不重新下载整曲，进度/歌词连续；
    /// - 原音 → 引擎：AVPlayer 继续出声（无感），引擎后台下载就绪后从当前进度无缝接管；
    /// - 引擎 → 原音：重载当前歌曲（引擎需下载整曲 / AVPlayer 走流）。
    /// - 加载失败状态下切换 = 重试加载。
    var current: Track? {
        guard queue.indices.contains(currentIndex) else { return nil }
        return queue[currentIndex]
    }
    static var trackIdAssocKey: UInt8 = 0
    var sessions: [ListenMode: ModeSession] = [:]
    func seek(toFraction fraction: Double) {
        // P1 负信号：手动拖动计入本曲 seek 次数（listen 上报时带 seekCount，后端判定快进/快退）
        listen.seekCount += 1
        if duration > 0 {
            if usingEngine {
                enginePlayer.seek(toSeconds: fraction * duration)
            } else {
                let target = CMTime(seconds: fraction * duration, preferredTimescale: 600)
                player.seek(to: target)
            }
        } else {
            // 加载中（duration 未知/为 0）：记住目标位置，就绪后立即跳过去，
            // 否则拖动会被静默丢弃、滑块弹回原位（原音→引擎下载期间可拖动）
            pendingSeekFraction = fraction
        }
    }

    /// 无封面时的兜底锁屏封面：只生成一次并缓存，避免每 0.5s 进度刷新重复渲染
    static let fallbackArtwork: MPMediaItemArtwork = {
        let size = CGSize(width: 600, height: 600)
        let renderer = UIGraphicsImageRenderer(size: size)
        let image = renderer.image { ctx in
            let colors = [UIColor.systemPurple.cgColor, UIColor.systemBlue.cgColor] as CFArray
            if let gradient = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(),
                                         colors: colors,
                                         locations: [0, 1]) {
                ctx.cgContext.drawLinearGradient(
                    gradient,
                    start: .zero,
                    end: CGPoint(x: size.width, y: size.height),
                    options: []
                )
            }
            let config = UIImage.SymbolConfiguration(pointSize: 180, weight: .light)
            if let symbol = UIImage(systemName: "music.note", withConfiguration: config) {
                symbol.draw(in: CGRect(x: size.width / 2 - 90,
                                       y: size.height / 2 - 90,
                                       width: 180,
                                       height: 180),
                            blendMode: .normal,
                            alpha: 0.85)
            }
        }
        return MPMediaItemArtwork(boundsSize: size) { _ in image }
    }()
}
