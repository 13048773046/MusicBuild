import AVFoundation
import QuartzCore   // CACurrentMediaTime（可靠单调时钟）

/// 引擎整曲下载的进度代理：把 URLSessionDownloadTask 的下载进度按 URL 转发给对应引擎实例。
/// （downloadTask 无 completion-handler 进度 API，必须走 delegate；共享会话共享一个代理）
final class EngineDownloadDelegate: NSObject, URLSessionDownloadDelegate {
    static let shared = EngineDownloadDelegate()
    /// url.absoluteString → 进度回调
    private var handlers: [String: (Double) -> Void] = [:]
    private let lock = NSLock()

    func register(url: String, handler: @escaping (Double) -> Void) {
        lock.lock(); handlers[url] = handler; lock.unlock()
    }
    func unregister(url: String) {
        lock.lock(); handlers[url] = nil; lock.unlock()
    }

    // MARK: URLSessionDownloadDelegate

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask,
                    didWriteData bytesWritten: Int64, totalBytesWritten: Int64,
                    totalBytesExpectedToWrite: Int64) {
        guard totalBytesExpectedToWrite > 0,
              let desc = downloadTask.taskDescription else { return }
        let fraction = Double(totalBytesWritten) / Double(totalBytesExpectedToWrite)
        lock.lock()
        let handler = handlers[desc]
        lock.unlock()
        DispatchQueue.main.async { handler?(min(max(fraction, 0), 1)) }
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask,
                    didFinishDownloadingTo location: URL) {}
}

/// 音效实时处理引擎（原音 + 7 种引擎音效）。
///
/// 管线：
/// ```
/// playerNode ──→ directMix ───────────────────────────────→ output   （原音直通）
/// playerNode ──→ eqLow  → delayLow → mixLow ─┐
/// playerNode ──→ eqMid  →──────────→ mixMid ──┼→ reverb → distortion → output
/// playerNode ──→ eqHigh → delayHigh → mixHigh┘
/// ```
/// 每种音效 = 一组参数（三分频 EQ 增益/分频点、左右延迟、混响预设、三路混音增益、失真染色），
/// 运行时切换参数即切换音效：不重建图、不重新下载整曲，进度与歌词保持连续。
///
/// 进度计算不依赖 AVAudioEngine 渲染时间线（playerNode.lastRenderTime /
/// playerTime(forNodeTime:) 在真机上采样时间域与文件采样率可能不一致、
/// sampleTime 可能无效，导致进度恒为 0 —— 进度条/歌词全部卡死的根源），
/// 改为「真实单调时钟 + 文件采样率」推算：play 时记录起点帧与时刻，
/// currentFrame = 起点帧 + 已播放秒数 × 文件采样率，与硬件采样率/引擎状态无关。
final class AudioEnginePlayer {

    /// 音效模式。与 UI 选项一一对应（PlayerManager 负责名称 → 模式的映射）
    enum Mode: String, CaseIterable {
        case original     // 原音
        case surround3D   // 3D 立体音效
        case surround360  // 360 环绕
        case panorama     // 全景音效（人声/主旋律前景居中突出，伴奏空间包围）
        case retro        // 复古
        case live         // 现场
        case pop          // 流行
        case rock         // 摇滚
    }

    // MARK: - 节点

    private let engine = AVAudioEngine()
    private let playerNode = AVAudioPlayerNode()
    /// 三分频路由：低通 250Hz（贝斯/底鼓）/ 带通 1.8k（人声主体）/ 高通 6kHz（镲片/空气感）
    private let eqLow = AVAudioUnitEQ(numberOfBands: 1)
    private let eqMid = AVAudioUnitEQ(numberOfBands: 1)
    private let eqHigh = AVAudioUnitEQ(numberOfBands: 1)
    private let mixLow = AVAudioMixerNode()
    private let mixMid = AVAudioMixerNode()
    private let mixHigh = AVAudioMixerNode()
    private let delayLow = AVAudioUnitDelay()
    private let delayHigh = AVAudioUnitDelay()
    private let reverb = AVAudioUnitReverb()
    /// 失真染色（复古磁带感 / 摇滚失真）；wetDryMix=0 时纯干直通
    private let distortion = AVAudioUnitDistortion()
    /// 直通 mixer（原音模式用）
    private let directMix = AVAudioMixerNode()

    // MARK: - 全景空间音频节点（HRTF）
    /// 全景专用三分频 EQ（与旧链独立，分频点可自由调整，不影响其余 6 种模式）
    private let eqLowP = AVAudioUnitEQ(numberOfBands: 1)
    private let eqMidP = AVAudioUnitEQ(numberOfBands: 1)
    private let eqHighP = AVAudioUnitEQ(numberOfBands: 1)
    /// 3 个虚拟点声源（AVAudioMixerNode 满足 AVAudioMixing）。
    /// 系统按各自 3D position + renderingAlgorithm=.HRTF 做双耳渲染，把声音定位到脑袋外。
    private let srcLow = AVAudioMixerNode()
    private let srcMid = AVAudioMixerNode()
    private let srcHigh = AVAudioMixerNode()
    /// 3D 环境混合器：内置距离衰减 + 环境混响，隐式听者。
    /// 硬约束：仅对「mono 连接格式」的输入做空间化，因此三条声源链全程以 mono 接入。
    /// 环境节点直接连 mainMixer，用自身 outputVolume 作为全景链总开关（与旧链互斥）。
    private let environmentNode = AVAudioEnvironmentNode()

    // MARK: - 多轨分离音轨节点（服务端 Demucs 2 轨：人声 + 伴奏）
    /// 人声 / 伴奏独立播放节点，用统一 hostTime 做 sample-accurate 同步
    private let playerVocals = AVAudioPlayerNode()
    private let playerAcc = AVAudioPlayerNode()
    /// 人声前景点声源（正前方，HRTF 贴耳突出）
    private let srcVocals = AVAudioMixerNode()
    /// 伴奏左后 / 右后点声源（HRTF 把伴奏包围到两侧与身后）
    private let srcAccL = AVAudioMixerNode()
    private let srcAccR = AVAudioMixerNode()

    // MARK: - 状态

    private var audioFile: AVAudioFile?
    private var fileFormat: AVAudioFormat?
    private var totalFrames: AVAudioFramePosition = 0
    private var isPlaying = false
    private var mode: Mode = .original
    /// 当前模式是否需要环境节点（仅全景 HRTF 需要）。
    /// 其余音效只走三分频链，不把环境节点接进图——避免无关节点导致 AUGraph 初始化失败 -10868。
    private var needsEnvironment: Bool { mode == .panorama }
    /// 播放起点帧与起点时刻（可靠时钟进度计算）
    private var playStartFrame: AVAudioFramePosition = 0
    private var playStartTime: TimeInterval = 0
    private var pausedFrame: AVAudioFramePosition = 0
    private var progressTimer: Timer?
    /// 当前下载任务与目标 URL：快速切歌时取消旧任务、丢弃旧回调，防止旧歌覆盖新歌
    private var currentTask: URLSessionDownloadTask?
    private var pendingURL: URL?
    /// prepare 是否就绪（已下载、引擎已启动、已 schedule）。供 PlayerManager 预取闸门读取。
    var isPrepared = false
    /// 引擎运行状态低频检查计数（progress timer 内每 ~1s 检查一次）。
    /// AVAudioEngine 长时间运行可能被系统异常停止且不触发配置变化通知 → 静默无声，
    /// 兜底：检测到未运行时自动 restartIfNeeded 续播。
    private var restoreCheckCounter = 0

    // MARK: 多轨状态
    /// 是否处于多轨分离模式（服务端 done 音轨）；false = 单文件链
    private var multiTrack = false
    /// 人声/伴奏解码后的 mono PCM buffer（标准 float32，硬件采样率），用 scheduleBuffer 调度
    private var vocalsBuffer: AVAudioPCMBuffer?
    private var accBuffer: AVAudioPCMBuffer?
    /// 多轨基准总帧数（取两轨较短者，避免某轨越界）
    private var multiTotalFrames: AVAudioFramePosition = 0
    /// 当前多轨曲目 id（缓存键用）
    private var multiTrackId: String?
    /// 多轨准备任务（stop 时取消，防止迟到回调）
    private var multiTask: Task<Void, Never>?

    /// 对外只读：当前是否处于多轨分离模式
    var isMultiTrack: Bool { multiTrack }

    /// 进度回调（每秒约 10 次）
    var onProgress: ((Double, Double) -> Void)?   // (currentSeconds, durationSeconds)
    var onCompletion: (() -> Void)?
    var onLoadingChanged: ((Bool) -> Void)?
    /// 加载/播放失败回调（下载失败、文件损坏等）：上层据此进入失败自愈（重试 UI + 自动重试）
    var onLoadFailed: (() -> Void)?

    /// 整曲下载用共享会话：避免每次切歌新建 URLSession（每个 session 自带连接池与资源，
    /// 频繁新建会造成资源浪费与潜在泄漏）。downloadTask 会把整曲下载到临时文件，缓存策略仅影响 URL 缓存命中。
    /// delegate 用于上报下载进度（「原音→引擎」无缝切换时 UI 显示加载百分比）。
    /// 弱网优化：首字节超时放宽到 60s（弱网/蜂窝下服务器响应慢，30s 会误杀）。
    private static let downloadSession: URLSession = {
        let config = URLSessionConfiguration.default
        config.requestCachePolicy = .returnCacheDataElseLoad
        config.timeoutIntervalForRequest = 60
        let session = URLSession(configuration: config,
                                 delegate: EngineDownloadDelegate.shared,
                                 delegateQueue: nil)
        return session
    }()

    /// 下载进度回调（0...1，主线程）
    var onDownloadProgress: ((Double) -> Void)?

    // MARK: - 配置

    /// graph 连接是否有效。系统音频配置变化（AVAudioEngineConfigurationChange：
    /// 输出硬件切换/采样率变化/蓝牙设备变更）会使已有节点连接失效——
    /// 此时 engine.start() 仍能成功，但 playerNode.play() 会抛不可捕获的
    /// NSException "player started when in a disconnected state"。
    /// 监听配置变化后置 false，下次播放前自动重建 graph。
    private var graphValid = true
    /// 图是否已建立（区别于 graphValid：graphBuilt 表示节点是否已 attach/connect 过）。
    /// 本类作为 PlayerManager 存储属性，在其 init 体 setActive 之前初始化，
    /// 此时会话未激活、硬件采样率为 0，不能建图，故延迟到首次播放前。
    private var graphBuilt = false

    init() {
        configureNodes()
        // 不在此建图：AVAudioSession 尚未激活（PlayerManager 存储属性先于其 init 体初始化），
        // 此时 outputNode 硬件采样率为 0，建出的连接无效——engine.start() 虽不抛错，
        // 但 playerNode 实际处于 disconnected，play 时抛不可捕获异常而闪退。
        // 延迟到 ensureGraphBuilt（首次 prepare/ensureEngineRunning，会话已激活）再建图。
    }

    /// 延迟建图入口：仅在首次调用时按当前真实硬件格式建立整张图。
    /// 调用时机均在 AVAudioSession 激活之后，outputNode 采样率有效。
    private func ensureGraphBuilt() {
        guard !graphBuilt else { return }
        buildGraph(includeEnvironment: needsEnvironment)
        graphBuilt = true
        graphValid = true
    }

    private func configureNodes() {
        // 低通：< 250Hz（贝斯/底鼓）
        if let band = eqLow.bands.first {
            band.filterType = .lowPass
            band.frequency = 250
            band.bandwidth = 0.5
            band.gain = 0
        }
        // 带通：250Hz – 6kHz（人声主体）
        if let band = eqMid.bands.first {
            band.filterType = .bandPass
            band.frequency = 1800
            band.bandwidth = 2.5
            band.gain = 0
        }
        // 高通：> 6kHz（镲片/空气感）
        if let band = eqHigh.bands.first {
            band.filterType = .highPass
            band.frequency = 6000
            band.bandwidth = 0.5
            band.gain = 0
        }
        // 延迟默认参数（apply(mode:) 会按音效覆盖）
        delayLow.delayTime = 0.018
        delayLow.feedback = 0
        delayLow.wetDryMix = 12
        delayHigh.delayTime = 0.012
        delayHigh.feedback = 0
        delayHigh.wetDryMix = 12
        // 混响默认（apply 覆盖）
        reverb.loadFactoryPreset(.smallRoom)
        reverb.wetDryMix = 8
        // 失真默认纯干直通（预设值在 apply(mode:) 按音效覆盖）
        distortion.loadFactoryPreset(.multiDecimated1)
        distortion.wetDryMix = 0
        // 分频混音默认（apply 覆盖）。三路 RMS 叠加 ≈ 1.0，响度与原音基本一致，
        // 避免「切音效瞬间音量猛增/时大时小」
        mixLow.outputVolume = 0.55
        mixMid.outputVolume = 0.6
        mixHigh.outputVolume = 0.55

        // MARK: 全景链 EQ（分频点与旧链一致，独立实例互不干扰）
        if let band = eqLowP.bands.first {
            band.filterType = .lowPass
            band.frequency = 250
            band.bandwidth = 0.5
            band.gain = 0
        }
        if let band = eqMidP.bands.first {
            band.filterType = .bandPass
            band.frequency = 1800
            band.bandwidth = 2.5
            band.gain = 0
        }
        if let band = eqHighP.bands.first {
            band.filterType = .highPass
            band.frequency = 6000
            band.bandwidth = 0.5
            band.gain = 0
        }

        // MARK: 环境节点：听者定位 + 内置环境混响
        // 听者固定在原点、朝向 -Z（正前方 = 负 Z），不做头部追踪
        environmentNode.listenerPosition = AVAudio3DPoint(x: 0, y: 0, z: 0)
        environmentNode.listenerAngularOrientation = AVAudio3DAngularOrientation(yaw: 0, pitch: 0, roll: 0)
        environmentNode.reverbParameters.enable = true
        environmentNode.reverbParameters.loadFactoryReverbPreset(.mediumHall)
        environmentNode.outputVolume = 1.0
        // 关闭距离衰减：3D 位置只决定方位，不引入额外电平变化，
        // 响度完全由各声源 outputVolume 精确控制（保证切换音效时响度一致）。
        // 注意：该枚举无 .none，用 exponential + rolloffFactor=0 等价于禁用衰减。
        let dist = environmentNode.distanceAttenuationParameters
        dist.distanceAttenuationModel = .exponential
        dist.rolloffFactor = 0
        // 声源默认输出关断（apply 的 .panorama 分支再开启），避免启动瞬间串音
        srcLow.outputVolume = 0
        srcMid.outputVolume = 0
        srcHigh.outputVolume = 0
        // 全景链总输出默认关断（环境节点直接连 mainMixer）
        environmentNode.outputVolume = 0
        // 多轨点声源默认关断
        srcVocals.outputVolume = 0
        srcAccL.outputVolume = 0
        srcAccR.outputVolume = 0
    }

    private func buildGraph(includeEnvironment: Bool = true, includeMultiTrack: Bool = true) {
        engine.attach(playerNode)
        engine.attach(eqLow)
        engine.attach(eqMid)
        engine.attach(eqHigh)
        engine.attach(mixLow)
        engine.attach(mixMid)
        engine.attach(mixHigh)
        engine.attach(delayLow)
        engine.attach(delayHigh)
        engine.attach(reverb)
        engine.attach(distortion)
        engine.attach(directMix)
        if includeEnvironment {
        // 全景链节点
        engine.attach(eqLowP)
        engine.attach(eqMidP)
        engine.attach(eqHighP)
        engine.attach(srcLow)
        engine.attach(srcMid)
        engine.attach(srcHigh)
        engine.attach(environmentNode)
        // 多轨点声源（mixer，6 输入之一，始终 attach）
        engine.attach(srcVocals)
        engine.attach(srcAccL)
        engine.attach(srcAccR)
        // 多轨播放器节点（仅多轨模式需要）
        if includeMultiTrack {
            engine.attach(playerVocals)
            engine.attach(playerAcc)
        }
        }

        let output = engine.outputNode
        let rawFormat = output.inputFormat(forBus: 0)
        let outHwFormat = output.outputFormat(forBus: 0)
        // 最终汇聚点：用引擎内置 mainMixerNode（系统自动连接 output），
        // 不再让多条链路直接连 outputNode——避免与隐式 mainMixer 图冲突。
        let mainMixer = engine.mainMixerNode
        // 硬件输出采样率优先（outputFormat 是物理硬件格式，最可靠），回退 inputFormat，再回退 48k。
        let hwSR = outHwFormat.sampleRate > 0 ? outHwFormat.sampleRate : rawFormat.sampleRate
        let safeSR = hwSR > 0 ? hwSR : 48000
        // 立体声连接格式（节点内部处理，nonInterleaved）
        guard let format = AVAudioFormat(commonFormat: .pcmFormatFloat32,
                                         sampleRate: safeSR,
                                         channels: 2,
                                         interleaved: false) else {
            print("[AudioEngine] 立体声格式创建失败")
            return
        }
        fileFormat = format
        // 单声道连接格式：环境节点只对 mono 输入做空间化
        guard let monoFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32,
                                            sampleRate: safeSR,
                                            channels: 1,
                                            interleaved: false) else {
            print("[AudioEngine] mono format 创建失败，全景链不可用")
            return
        }

        // 原音直通
        engine.connect(playerNode, to: directMix, format: format)
        engine.connect(directMix, to: mainMixer, format: format)

        // 三分频链
        engine.connect(playerNode, to: eqLow, format: format)
        engine.connect(eqLow, to: delayLow, format: format)
        engine.connect(delayLow, to: mixLow, format: format)

        engine.connect(playerNode, to: eqMid, format: format)
        engine.connect(eqMid, to: mixMid, format: format)

        engine.connect(playerNode, to: eqHigh, format: format)
        engine.connect(eqHigh, to: delayHigh, format: format)
        engine.connect(delayHigh, to: mixHigh, format: format)

        // 三路分频 → 混响 → 失真染色 → 主混音
        engine.connect(mixLow, to: reverb, format: format)
        engine.connect(mixMid, to: reverb, format: format)
        engine.connect(mixHigh, to: reverb, format: format)
        engine.connect(reverb, to: distortion, format: format)
        engine.connect(distortion, to: mainMixer, format: format)

        guard includeEnvironment else { return }

        // 全景 HRTF 链：
        // playerNode → 全景 EQ（立体声，EQ 输入输出保持同通道数）
        // → 点声源 mixer（由 mixer 做立体声→mono 下混，effect 单元不做跨通道转换）
        // → 环境节点（mono 才空间化）
        engine.connect(playerNode, to: eqLowP, format: format)
        engine.connect(eqLowP, to: srcLow, format: format)
        engine.connect(srcLow, to: environmentNode, format: monoFormat)

        engine.connect(playerNode, to: eqMidP, format: format)
        engine.connect(eqMidP, to: srcMid, format: format)
        engine.connect(srcMid, to: environmentNode, format: monoFormat)

        engine.connect(playerNode, to: eqHighP, format: format)
        engine.connect(eqHighP, to: srcHigh, format: format)
        engine.connect(srcHigh, to: environmentNode, format: monoFormat)

        // 环境节点连「内部主混音」必须用 nonInterleaved 处理格式（与其他内部节点一致）。
        // 注意：官方示例的 initStandardFormat(interleaved) 仅适用于环境节点直连「硬件 outputNode」；
        // 连到 mainMixer 这种内部节点时用 interleaved 会导致 AUGraph 初始化失败 -10868。
        engine.connect(environmentNode, to: mainMixer, format: format)

        // 多轨播放器直连环境节点（AVAudioPlayerNode 本身 conform AVAudioMixing，
        // 可直接设 position/HRTF/reverbBlend，对齐官方 AVAEGamingExample，去掉 mixer 中转）
        if includeMultiTrack {
            // 人声 → 环境（正前景点声源）
            engine.connect(playerVocals, to: environmentNode, format: monoFormat)
            // 伴奏 → 环境（高 reverbBlend 做空间包围）
            engine.connect(playerAcc, to: environmentNode, format: monoFormat)
        }
    }

    // MARK: - 播放控制

    /// 预下载整曲并就绪（不启动播放、不上报进度 Timer）。
    /// 用途：原音 → 引擎音效的无缝接力——AVPlayer 继续出声，引擎在后台下载；
    /// 就绪后调用 playFrom(seconds:autoPlay:) 从当前进度接管，用户感知不到加载。
    /// 缓存共享：若该曲已在本机缓存（原音/引擎共用 AudioCache），直接读缓存免下载；
    /// 下载完成落盘到共享缓存，原音后续播放可直接用 file:// 本地秒开。
    /// 完成/失败回调均回主线程；下载进度经 onDownloadProgress 上报（0...1）。
    func prepare(url: URL, trackId: String, low: Bool = false, mode: Mode, completion: @escaping () -> Void) {
        stop()
        isPrepared = false
        onLoadingChanged?(true)
        pendingURL = url
        LogReporter.shared.log(level: "info", tag: "eq", message: "prepare 开始 track=\(trackId) low=\(low) mode=\(mode)")

        // 缓存优先：共享缓存命中 → 免下载直接读本地文件（弱网/离线也能切音效）。
        // ⚠ 契约：prepare 调用方必须保证主线程（PlayerManager 经 MainActor.run 调用）——
        // 缓存分支同步执行 engine 操作并同步回调 completion，省一次主线程跳转（秒切换）。
        if let cached = AudioCache.shared.existing(trackId: trackId, low: low) {
            do {
                let file = try AVAudioFile(forReading: cached)
                self.audioFile = file
                self.fileFormat = file.processingFormat
                self.totalFrames = file.length
                self.pausedFrame = 0
                self.apply(mode: mode)
                guard self.ensureEngineRunning() else {
                    LogReporter.shared.log(level: "error", tag: "eq",
                                           message: "prepare 缓存引擎启动失败 track=\(trackId)")
                    self.onLoadingChanged?(false)
                    self.onLoadFailed?()
                    return
                }
                self.schedule(from: 0)
                self.isPrepared = true
                self.onLoadingChanged?(false)
                LogReporter.shared.log(level: "info", tag: "eq", message: "prepare 缓存命中完成 track=\(trackId)")
                completion()
                return
            } catch {
                print("[AudioEngine] cache load error: \(error)")
                LogReporter.shared.log(level: "error", tag: "eq", message: "prepare 缓存读取失败 track=\(trackId) err=\(error)")
                // 缓存损坏：继续走网络下载（不中断用户操作）
            }
        }

        EngineDownloadDelegate.shared.register(url: url.absoluteString) { [weak self] f in
            self?.onDownloadProgress?(f)
        }

        let task = Self.downloadSession.downloadTask(with: url) { [weak self] tempURL, _, error in
            // 下载结果统一回主线程处理：AVAudioEngine 的 start/schedule/参数切换
            // 必须与主线程的 seek/stop/切音效/配置变化回调串行执行，
            // 后台线程直接操作引擎会与渲染线程并发 → 真机闪退（EXC_BAD_ACCESS / AVAudioEngineGraph）
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                // 切歌/切音效后旧任务迟到响应：直接丢弃，防止旧内容覆盖新状态
                guard self.pendingURL == url else {
                    EngineDownloadDelegate.shared.unregister(url: url.absoluteString)
                    return
                }
                self.pendingURL = nil
                EngineDownloadDelegate.shared.unregister(url: url.absoluteString)
                if let error = error {
                    print("[AudioEngine] download error: \(error)")
                    LogReporter.shared.log(level: "error", tag: "eq", message: "prepare 下载失败 track=\(trackId) err=\(error)")
                    self.onLoadingChanged?(false)
                    self.onLoadFailed?()   // 上层自愈：重试 UI + 有限自动重试
                    return
                }
                guard let tempURL = tempURL else {
                    LogReporter.shared.log(level: "error", tag: "eq", message: "prepare 下载无临时文件 track=\(trackId)")
                    self.onLoadingChanged?(false)
                    self.onLoadFailed?()
                    return
                }
                do {
                    // 落盘到共享缓存（downloadTask 完成后 tempURL 会被系统回收，必须先拷贝）。
                    // 原音/引擎共用 AudioCache：引擎下载的整曲，原音播放直接 file:// 秒开。
                    guard let permanent = AudioCache.shared.store(fromTemp: tempURL,
                                                                  trackId: trackId,
                                                                  low: low) else {
                        LogReporter.shared.log(level: "error", tag: "eq", message: "prepare 缓存落盘失败 track=\(trackId)")
                        self.onLoadingChanged?(false)
                        self.onLoadFailed?()
                        return
                    }

                    let file = try AVAudioFile(forReading: permanent)
                    self.audioFile = file
                    self.fileFormat = file.processingFormat
                    self.totalFrames = file.length
                    self.pausedFrame = 0
                    self.apply(mode: mode)
                    guard self.ensureEngineRunning() else {
                        LogReporter.shared.log(level: "error", tag: "eq",
                                               message: "prepare 引擎启动失败 track=\(trackId)")
                        self.onLoadingChanged?(false)
                        self.onLoadFailed?()
                        return
                    }
                    self.schedule(from: 0)
                    self.isPrepared = true
                    self.onLoadingChanged?(false)
                    LogReporter.shared.log(level: "info", tag: "eq", message: "prepare 下载完成引擎就绪 track=\(trackId)")
                    completion()
                } catch {
                    print("[AudioEngine] load error: \(error)")
                    LogReporter.shared.log(level: "error", tag: "eq", message: "prepare 引擎加载失败 track=\(trackId) err=\(error)")
                    self.onLoadingChanged?(false)
                    self.onLoadFailed?()
                }
            }
        }
        task.taskDescription = url.absoluteString
        currentTask = task
        task.resume()
    }

    /// 从 URL 加载并播放（先下载到临时文件 / 命中共享缓存则直接读缓存）。
    /// - startSeconds: 初始播放位置（无缝续播：跨播放器切换时从该秒数接续，0 = 从头）
    /// - autoPlay: 下载完成后是否立即播放（false = 就绪但保持暂停，pausedFrame 已定位，
    ///   之后 play() 会从该位置续播）
    func load(url: URL, trackId: String, low: Bool = false, mode: Mode, startSeconds: Double = 0, autoPlay: Bool = true) {
        prepare(url: url, trackId: trackId, low: low, mode: mode) { [weak self] in
            guard let self = self else { return }
            if autoPlay {
                self.playFrom(seconds: startSeconds, autoPlay: true)
            } else {
                // 暂停态：只定位，就绪后由 resumePlayback 从该位置起播
                self.seek(toSeconds: startSeconds)
            }
        }
    }

    /// 从指定位置播放（必须已 prepare 就绪）。
    /// - autoPlay: false 时只定位就绪（pausedFrame），由后续 play() 从该位置起播
    func playFrom(seconds: Double, autoPlay: Bool = true) {
        guard isPrepared, let format = fileFormat, totalFrames > 0 else { return }
        let target = AVAudioFramePosition(seconds * format.sampleRate)
        let clamped = max(0, min(target, totalFrames - 1))
        pausedFrame = clamped
        schedule(from: clamped)
        if autoPlay {
            // 崩溃防护：engine 未运行先启动，失败不播（同 play()）
            guard ensureEngineRunning() else { return }
            playerNode.play()
            isPlaying = true
            playStartFrame = clamped
            playStartTime = CACurrentMediaTime()
            startTimer()
        }
    }

    /// 引擎应恢复运行时调用（回前台 / 音频会话配置变化）：
    /// AVAudioEngine 可能被系统停止（后台优化/配置变更），引擎不再输出 → 无声。
    /// 这里检测到未运行就重启并重新挂载，从推算位置续播（进度不丢）。
    func restartIfNeeded() {
        guard isPlaying else { return }
        ensureGraphBuilt()
        // 配置变化后旧连接失效：先重建 graph 再恢复（防 disconnected 崩溃）
        if !graphValid {
            rebuildGraph()
        }
        guard !engine.isRunning else { return }
        do {
            // 重启前先复位主输出音量：系统/后台优化可能改变 mixer 增益，
            // 不复位会出现「重启后声音变小」。
            engine.mainMixerNode.outputVolume = 1.0
            let resume = currentFrame()   // 引擎停止期间进度时钟仍在走，以推算位置续播
            try engine.start()
            if multiTrack {
                let when = AVAudioTime(hostTime: futureHostTime(seconds: 0.1))
                scheduleMulti(from: resume, at: when)
                playerVocals.play()
                playerAcc.play()
                playStartFrame = resume
                playStartTime = CACurrentMediaTime() + 0.1
            } else {
                schedule(from: resume)
                playerNode.play()
                playStartFrame = resume
                playStartTime = CACurrentMediaTime()
            }
            startTimer()
            print("[AudioEngine] restarted at \(resume) frames")
        } catch {
            print("[AudioEngine] restart failed: \(error)")
        }
    }

    /// 恢复主输出音量（打断/闪避/后台降权后统一复位，防「声音变小」残留）
    func restoreVolume() {
        engine.mainMixerNode.outputVolume = 1.0
    }

    /// 调度世代号：每次 schedule/stop 递增，完成回调据此识别「正常播完」与「被 stop 打断」。
    /// AVAudioPlayerNode.stop() 会触发尚未播完 segment 的 completionHandler，
    /// 若不区分，拖动条 seek / 切歌时旧回调会误判为播放完成 → 误切下一首。
    private var scheduleGen = 0

    private func schedule(from frame: AVAudioFramePosition) {
        guard let file = audioFile, let format = fileFormat else { return }
        playerNode.stop()
        scheduleGen += 1
        let gen = scheduleGen
        let framesToPlay = totalFrames - frame
        guard framesToPlay > 0 else { return }
        playerNode.scheduleSegment(
            file,
            startingFrame: frame,
            frameCount: AVAudioFrameCount(framesToPlay),
            at: nil
        ) {
            DispatchQueue.main.async { [weak self] in
                guard let self = self, self.scheduleGen == gen, self.isPlaying else { return }
                self.onCompletion?()
            }
        }
    }

    /// 确保引擎运行：engine 未运行先 start；失败返回 false（调用方不得 play）。
    /// AVAudioPlayerNode 在 engine disconnected/未启动时 play 会抛不可捕获的
    /// NSException（"player started when in a disconnected state"）→ 必须前置拦截。
    @discardableResult
    private func ensureEngineRunning() -> Bool {
        // 延迟建图：首次播放时（会话已激活）才建立节点连接
        ensureGraphBuilt()
        // graph 失效时先重建连接（系统配置变化后旧连接不可用）
        if !graphValid {
            rebuildGraph()
        }
        if engine.isRunning { return true }
        do {
            try engine.start()
            return true
        } catch {
            // start 失败：旧连接必然已失效，标记待重建（下次播放前自动 rebuildGraph）
            graphValid = false
            LogReporter.shared.log(level: "error", tag: "eq",
                                   message: "engine.start 失败 err=\(error)")
            print("[AudioEngine] engine.start failed: \(error)")
            return false
        }
    }

    /// 系统音频配置变化后无条件重建 graph（无论是否在播）。
    /// 旧 graph 节点连接在配置变化后失效：若此时正播放必须立即重启恢复；
    /// 若在下载/暂停中，先重建好新连接，保证任何后续 start/play 都走有效图，
    /// 消除 "player started when in a disconnected state" 崩溃窗口。
    func rebuildGraphIfNeeded() {
        ensureGraphBuilt()
        if graphValid && engine.isRunning { return }
        rebuildGraph()
        guard isPlaying else { return }
        do {
            // 重启前先复位主输出音量：系统/后台优化可能改变 mixer 增益
            engine.mainMixerNode.outputVolume = 1.0
            let resume = currentFrame()
            try engine.start()
            if multiTrack {
                let when = AVAudioTime(hostTime: futureHostTime(seconds: 0.1))
                scheduleMulti(from: resume, at: when)
                playerVocals.play()
                playerAcc.play()
                playStartFrame = resume
                playStartTime = CACurrentMediaTime() + 0.1
            } else {
                schedule(from: resume)
                playerNode.play()
                playStartFrame = resume
                playStartTime = CACurrentMediaTime()
            }
            startTimer()
            print("[AudioEngine] config change: graph rebuilt, resumed at \(resume) frames")
        } catch {
            graphValid = false
            LogReporter.shared.log(level: "error", tag: "eq",
                                   message: "配置变化恢复失败 err=\(error)")
        }
    }

    /// 系统音频配置变化回调：旧 graph 连接可能已失效，标记待重建
    func markGraphInvalid() {
        graphValid = false
    }

    /// 重建引擎图（detach 全部节点 → 重新 attach + connect）。
    /// 仅在 graph 失效或首次播放前调用；audioFile/schedule 状态不受影响，
    /// 重建后 engine 需重新 start（由 ensureEngineRunning 完成）。
    /// 全部可挂载节点（detach 统一使用，避免列表遗漏）。
    private var allNodes: [AVAudioNode] {
        [playerNode, eqLow, eqMid, eqHigh,
         mixLow, mixMid, mixHigh,
         delayLow, delayHigh, reverb, distortion, directMix,
         eqLowP, eqMidP, eqHighP,
         srcLow, srcMid, srcHigh,
         environmentNode,
         playerVocals, playerAcc,
         srcVocals, srcAccL, srcAccR]
    }

    private func detachAllNodes() {
        for n in allNodes { engine.detach(n) }
    }

    private func rebuildGraph() {
        engine.stop()
        detachAllNodes()
        buildGraph(includeEnvironment: needsEnvironment)
        graphBuilt = true
        graphValid = true
        LogReporter.shared.log(level: "info", tag: "eq", message: "engine graph 已重建 env=\(needsEnvironment)")
    }

    func play() {
        guard !isPlaying else { return }
        // 崩溃防护：engine 处于 disconnected/未运行状态时直接 playerNode.play()
        // 会抛 NSException（AVFAudio "player started when in a disconnected state"），
        // Swift 无法 catch，只能前置拦截——未运行先 start，失败则不播（等恢复时机）。
        guard ensureEngineRunning() else { return }
        if multiTrack {
            let when = AVAudioTime(hostTime: futureHostTime(seconds: 0.1))
            scheduleMulti(from: pausedFrame, at: when)
            playerVocals.play()
            playerAcc.play()
            playStartFrame = pausedFrame
            playStartTime = CACurrentMediaTime() + 0.1
        } else {
            playerNode.play()
            // 记录播放起点（可靠时钟进度）
            playStartFrame = pausedFrame
            playStartTime = CACurrentMediaTime()
        }
        isPlaying = true
        startTimer()
    }

    func pause() {
        guard isPlaying else { return }
        pausedFrame = currentFrame()
        if multiTrack {
            playerVocals.pause()
            playerAcc.pause()
        } else {
            playerNode.pause()
        }
        isPlaying = false
        stopTimer()
    }

    func stop() {
        currentTask?.cancel()
        currentTask = nil
        multiTask?.cancel()
        multiTask = nil
        pendingURL = nil
        isPrepared = false
        playerNode.stop()
        scheduleGen += 1   // 失效所有未播完 segment 的完成回调
        // 多轨节点停止与状态清理
        playerVocals.stop()
        playerAcc.stop()
        multiGen += 1
        vocalsBuffer = nil
        accBuffer = nil
        multiTotalFrames = 0
        multiTrackId = nil
        multiTrack = false
        isPlaying = false
        pausedFrame = 0
        playStartFrame = 0
        stopTimer()
        // 释放已打开音频文件句柄与格式引用（切歌后旧文件不再被引用，
        // 避免文件句柄残留导致磁盘/内存资源占用）
        audioFile = nil
        fileFormat = nil
        totalFrames = 0
        if engine.isRunning {
            engine.pause()
        }
    }

    /// 进度 Timer：必须调度在主线程 RunLoop 上。
    /// load() 的下载完成回调在后台线程，若直接 Timer.scheduledTimer 会把 timer 挂到后台线程
    /// （其 RunLoop 不运行）——timer 永不触发 → onProgress 从不回调 → 进度/歌词/拖动条全卡死。
    /// 统一回主线程创建，回调即主线程，写 @Published 也安全。
    private func startTimer() {
        stopTimer()
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            // 期间可能已 stop 再 start（异步竞态）：以最新状态为准
            guard self.isPlaying else { return }
            guard self.progressTimer == nil else { return }
            self.progressTimer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
                guard let self = self else { return }
                // 播放完成兜底：scheduleSegment 的 completionHandler 在部分 iOS 版本/
                // 部分文件格式下播放到末尾不触发（AVAudioPlayerNode 已知问题），
                // 若完全依赖它，「播完自动切下一首」会失效。
                // 用进度时钟判定播到末尾（容差 1024 帧 ≈ 23ms），触发完成回调。
                // #19：0.1s → 0.25s（完成判定基于帧比较不受影响；进度/歌词秒级精度足够，
                // 同时减少 3 个 @Published 每 100ms 刷新引起的视图树大范围重组）。
                // 幂等：先置 isPlaying=false 再回调，无论 completionHandler 是否已触发，
                // 另一路径的 guard isPlaying 都会拦住，保证只切一次歌。
                // 多轨/单文件统一用当前链路的总帧数
                let endFrames = self.multiTrack ? self.multiTotalFrames : self.totalFrames
                if self.isPlaying, endFrames > 0,
                   self.currentFrame() >= endFrames - 1024 {
                    print("[AudioEngine] playback completed (timer fallback)")
                    self.isPlaying = false
                    self.pausedFrame = endFrames
                    self.stopTimer()
                    self.onCompletion?()
                    return
                }
                // 长时间播放兜底：AVAudioEngine 可能被系统异常停止且未触发配置变化通知
                // （长时间运行/后台优化/音频栈异常），每 ~1s 检查一次，未运行时自动重启续播，
                // 防止长时间播放后引擎静默停止导致无声。
                self.restoreCheckCounter += 1
                if self.restoreCheckCounter >= 10 {
                    self.restoreCheckCounter = 0
                    let activeFrames = self.multiTrack ? self.multiTotalFrames : self.totalFrames
                    if self.isPlaying, activeFrames > 0, !self.engine.isRunning {
                        self.restartIfNeeded()
                    }
                }
                self.onProgress?(self.currentSeconds(), self.durationSeconds())
            }
        }
    }

    private func stopTimer() {
        progressTimer?.invalidate()
        progressTimer = nil
    }

    func seek(toSeconds seconds: Double) {
        if multiTrack {
            guard let vf = vocalsBuffer, multiTotalFrames > 0 else { return }
            let target = AVAudioFramePosition(seconds * vf.format.sampleRate)
            let clamped = max(0, min(target, multiTotalFrames - 1))
            pausedFrame = clamped
            if isPlaying {
                guard ensureEngineRunning() else { return }
                let when = AVAudioTime(hostTime: futureHostTime(seconds: 0.1))
                scheduleMulti(from: clamped, at: when)
                playerVocals.play()
                playerAcc.play()
                playStartFrame = clamped
                playStartTime = CACurrentMediaTime() + 0.1
            } else {
                scheduleMulti(from: clamped, at: nil)
            }
            return
        }
        guard let format = fileFormat, totalFrames > 0 else { return }
        let sampleRate = format.sampleRate
        let targetFrame = AVAudioFramePosition(seconds * sampleRate)
        let clamped = max(0, min(targetFrame, totalFrames - 1))
        pausedFrame = clamped
        if isPlaying {
            schedule(from: clamped)
            // 崩溃防护：引擎未运行先启动（同 play()）
            guard ensureEngineRunning() else { return }
            playerNode.play()
            playStartFrame = clamped
            playStartTime = CACurrentMediaTime()
        } else {
            // 暂停态：也必须重新挂载目标段，否则 play() 时仍播上一次调度的位置（从旧位置起播）
            schedule(from: clamped)
        }
    }

    /// 运行时切换音效参数（引擎模式之间切换用，不重载音频、进度连续）
    func setMode(_ mode: Mode) {
        self.mode = mode
        apply(mode: mode)
    }

    /// 每种音效的参数表：先全部关断，再按模式开启对应组合。
    /// 参数设计原则：三路分频混音叠加后 RMS ≈ 1.0，保证各音效响度一致、切换不突兀。
    ///
    /// 线程安全：调用方必须保证在主线程（setMode/prepare 已统一主线程串行）。
    /// 渲染安全：引擎运行中修改 AVAudioUnit 配置（reverb/distortion 的 loadFactoryPreset、
    /// EQ 分频点等）在部分 iOS 版本会触发渲染线程与配置变更并发崩溃 → 切换前先
    /// engine.pause() 暂停渲染，改完再 start() 恢复；pause 保留 playerNode 已调度 segment
    /// 与进度，恢复后从原位置继续输出（无感切换）。
    private func apply(mode: Mode) {
        let previousNeedsEnv = needsEnvironment
        self.mode = mode
        let wasRunning = engine.isRunning
        if wasRunning {
            engine.pause()
        }
        // 环境节点需求变化（全景 ↔ 其他模式）：图拓扑必须重建，
        // 否则会缺环境节点，或残留无关节点导致 AUGraph 初始化失败 -10868。
        if graphBuilt && previousNeedsEnv != needsEnvironment {
            rebuildGraph()
        }
        // 全关
        directMix.outputVolume = 0
        mixLow.outputVolume = 0
        mixMid.outputVolume = 0
        mixHigh.outputVolume = 0
        // 旧链声像复位（切走时回中，避免声像残留）
        mixLow.pan = 0
        mixMid.pan = 0
        mixHigh.pan = 0
        // 全景环境链关断（与旧链互斥）
        environmentNode.outputVolume = 0
        srcLow.outputVolume = 0
        srcMid.outputVolume = 0
        srcHigh.outputVolume = 0
        reverb.wetDryMix = 0
        distortion.wetDryMix = 0
        delayLow.feedback = 0
        delayLow.wetDryMix = 0
        delayHigh.feedback = 0
        delayHigh.wetDryMix = 0
        // 分频点复位（复古会下调高频分频点）
        eqHigh.bands.first?.frequency = 6000

        switch mode {
        case .original:
            // 原音：仅直通
            directMix.outputVolume = 1.0

        case .surround3D:
            // 3D 立体音效：三分频 + 左右微延迟相位差 + 小房间自然反射
            mixLow.outputVolume = 0.55
            mixMid.outputVolume = 0.6
            mixHigh.outputVolume = 0.55
            delayLow.delayTime = 0.018   // 低频略偏左
            delayLow.feedback = 0
            delayLow.wetDryMix = 12
            delayHigh.delayTime = 0.012  // 高频略偏右
            delayHigh.feedback = 0
            delayHigh.wetDryMix = 12
            reverb.loadFactoryPreset(.smallRoom)
            reverb.wetDryMix = 8

        case .surround360:
            // 360 环绕：左右延迟差拉大（声像更宽）+ 中厅反射 → 环绕流动感
            mixLow.outputVolume = 0.5
            mixMid.outputVolume = 0.55
            mixHigh.outputVolume = 0.5
            delayLow.delayTime = 0.030   // 左路更晚 → 偏右
            delayLow.feedback = 0
            delayLow.wetDryMix = 20
            delayHigh.delayTime = 0.006  // 右路更早 → 偏左
            delayHigh.feedback = 0
            delayHigh.wetDryMix = 20
            reverb.loadFactoryPreset(.mediumHall)
            reverb.wetDryMix = 18

        case .panorama:
            // 全景音效（对标汽水音乐「全景音效」）：真正的 HRTF 双耳渲染，消除头中效应——
            // 把声音从「脑袋里」定位到「脑袋外的空间」。
            // 人声/主旋律作为「前景点声源」定位在正前方、最近、最突出；
            // 低频/高频伴奏作为左右对称的点声源，配合环境混响把空间包裹到四周。
            // 距离衰减已关闭，位置只决定方位，响度由 outputVolume 精确控制。
            environmentNode.outputVolume = 1.0   // 打开全景总输出

            // 人声（中频）：正前方居中、距离最近 → 前景 C 位
            srcMid.position = AVAudio3DPoint(x: 0, y: 0, z: -1.6)
            srcMid.renderingAlgorithm = .HRTF
            srcMid.reverbBlend = 12      // 人声混响少，保持贴耳、清晰
            srcMid.outputVolume = 0.85

            // 低频（贝斯/底鼓）：左前，节奏根基
            srcLow.position = AVAudio3DPoint(x: -1.0, y: 0, z: -1.1)
            srcLow.renderingAlgorithm = .HRTF
            srcLow.reverbBlend = 25
            srcLow.outputVolume = 0.6

            // 高频（镲片/空气感）：右前，与低频左右对称
            srcHigh.position = AVAudio3DPoint(x: 1.0, y: 0, z: -1.1)
            srcHigh.renderingAlgorithm = .HRTF
            srcHigh.reverbBlend = 35      // 高频混响略多，空气感与空间包围
            srcHigh.outputVolume = 0.6

        case .retro:
            // 复古：高频路衰减（磁带高切）+ 原音混底保清晰 + 磁带感失真 + 轻微单声道回声
            directMix.outputVolume = 0.25
            mixLow.outputVolume = 0.5
            mixMid.outputVolume = 0.5
            mixHigh.outputVolume = 0.35
            eqHigh.bands.first?.frequency = 4500   // 高切：高频分频点下调
            delayLow.delayTime = 0.085
            delayLow.feedback = 0.35
            delayLow.wetDryMix = 18
            delayHigh.delayTime = 0.06
            delayHigh.feedback = 0.3
            delayHigh.wetDryMix = 16
            distortion.loadFactoryPreset(.multiDecimated1)   // 采样降级：Lo-Fi 磁带质感
            distortion.wetDryMix = 18

        case .live:
            // 现场：大厅混响 + 原音干湿混合（更自然的现场距离感）
            directMix.outputVolume = 0.35
            mixLow.outputVolume = 0.5
            mixMid.outputVolume = 0.55
            mixHigh.outputVolume = 0.5
            reverb.loadFactoryPreset(.largeHall)
            reverb.wetDryMix = 26

        case .pop:
            // 流行：人声路（中频）更突出 + 轻厅堂润色
            directMix.outputVolume = 0.2
            mixLow.outputVolume = 0.45
            mixMid.outputVolume = 0.7
            mixHigh.outputVolume = 0.5
            reverb.loadFactoryPreset(.mediumHall)
            reverb.wetDryMix = 10

        case .rock:
            // 摇滚：V 形 EQ（低频/高频更响、中频稍收）+ 轻微失真染色 + 紧凑混响
            directMix.outputVolume = 0.15
            mixLow.outputVolume = 0.65
            mixMid.outputVolume = 0.45
            mixHigh.outputVolume = 0.6
            delayLow.delayTime = 0.014
            delayLow.feedback = 0
            delayLow.wetDryMix = 10
            delayHigh.delayTime = 0.010
            delayHigh.feedback = 0
            delayHigh.wetDryMix = 10
            reverb.loadFactoryPreset(.mediumRoom)
            reverb.wetDryMix = 12
            distortion.loadFactoryPreset(.multiDistortedSquared)
            distortion.wetDryMix = 12
        }

        // 恢复渲染（参数已全部就位）：playerNode 已调度 segment 保留，从原位置继续输出
        if wasRunning {
            // 配置变化后旧连接失效：先重建再启动，防 disconnected 崩溃
            if !graphValid {
                rebuildGraph()
            }
            do {
                try engine.start()
            } catch {
                // 不静默：start 失败会导致后续 playerNode.play() 抛 disconnected 崩溃
                graphValid = false
                LogReporter.shared.log(level: "error", tag: "eq",
                                       message: "apply 引擎恢复失败 mode=\(mode) err=\(error)")
                print("[AudioEngine] apply engine restart failed: \(error)")
            }
        }
    }

    // MARK: - 多轨分离音轨播放（人声前景 + 伴奏 HRTF 包围）

    /// 配置多轨点声源的 3D 方位与 HRTF 参数（距离衰减已关闭，位置只决定方位）。
    private func configureMultiTrackSources() {
        // 本函数在 ensureEngineRunning 之前调用，需先确保节点已 attach/connect
        ensureGraphBuilt()
        // 链路互斥：多轨走 environment → mainMixer，关闭单文件点声源与直通，避免串音
        environmentNode.outputVolume = 1
        directMix.outputVolume = 0
        srcLow.outputVolume = 0
        srcMid.outputVolume = 0
        srcHigh.outputVolume = 0

        // 人声：正前方、距离最近、混响少 → 前景 C 位，贴耳清晰
        playerVocals.position = AVAudio3DPoint(x: 0, y: 0, z: -1.5)
        playerVocals.renderingAlgorithm = .HRTF
        playerVocals.reverbBlend = 10
        playerVocals.volume = 1.0

        // 伴奏：稍靠后、高混响，靠环境混响把伴奏空间化包围，电平略低于人声以突出前景
        playerAcc.position = AVAudio3DPoint(x: 0, y: 0, z: 0.9)
        playerAcc.renderingAlgorithm = .HRTF
        playerAcc.reverbBlend = 38
        playerAcc.volume = 0.85
    }

    /// 读取音频文件并转成 mono float32（目标采样率）的 PCM buffer。
    /// 用 commonFormat 初始化器强制解码（容器/编码不支持会明确抛错，而非静默无声），
    /// 再用 AVAudioConverter 完成立体声→mono 下混与采样率转换。
    private static func loadMonoBuffer(url: URL, targetSampleRate: Double) throws -> AVAudioPCMBuffer {
        // 强制标准 float32 nonInterleaved 解码（对齐官方 AVAEGamingExample 的文件打开方式）
        let file = try AVAudioFile(forReading: url,
                                   commonFormat: .pcmFormatFloat32,
                                   interleaved: false)
        let srcFrames = AVAudioFrameCount(file.length)
        guard srcFrames > 0 else {
            throw NSError(domain: "AudioEngine", code: -1,
                          userInfo: [NSLocalizedDescriptionKey: "空音频文件"])
        }
        guard let inBuffer = AVAudioPCMBuffer(pcmFormat: file.processingFormat,
                                              frameCapacity: srcFrames) else {
            throw NSError(domain: "AudioEngine", code: -2)
        }
        try file.read(into: inBuffer)

        // 目标 mono 格式
        guard let outFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32,
                                            sampleRate: targetSampleRate,
                                            channels: 1, interleaved: false) else {
            throw NSError(domain: "AudioEngine", code: -3)
        }
        // 输入已是 mono 且采样率一致：直接返回
        if file.processingFormat.channelCount == 1,
           file.processingFormat.sampleRate == targetSampleRate {
            return inBuffer
        }
        guard let converter = AVAudioConverter(from: file.processingFormat, to: outFormat) else {
            throw NSError(domain: "AudioEngine", code: -4,
                          userInfo: [NSLocalizedDescriptionKey: "无法创建音频转换器"])
        }
        let ratio = targetSampleRate / file.processingFormat.sampleRate
        let capacity = AVAudioFrameCount((Double(srcFrames) * ratio).rounded(.up)) + 1
        guard let outBuffer = AVAudioPCMBuffer(pcmFormat: outFormat, frameCapacity: capacity) else {
            throw NSError(domain: "AudioEngine", code: -5)
        }
        var pulled = false
        var convError: NSError?
        let status = converter.convert(to: outBuffer, error: &convError) { _, outStatus in
            if pulled {
                outStatus.pointee = .noDataNow
                return nil
            }
            pulled = true
            outStatus.pointee = .haveData
            return inBuffer
        }
        if let convError { throw convError }
        guard status != .error else {
            throw NSError(domain: "AudioEngine", code: -6,
                          userInfo: [NSLocalizedDescriptionKey: "音频转换失败"])
        }
        guard outBuffer.frameLength > 0 else {
            throw NSError(domain: "AudioEngine", code: -7,
                          userInfo: [NSLocalizedDescriptionKey: "转换后无音频数据"])
        }
        return outBuffer
    }

    /// 下载单个分离音轨（缓存优先），返回本地文件 URL；失败返回 nil。
    private static func fetchStem(url: URL, trackId: String,
                                  kind: AudioCache.StemKind) async -> URL? {
        if let cached = AudioCache.shared.stemExisting(trackId: trackId, kind: kind) {
            return cached
        }
        do {
            let (tempURL, _) = try await downloadSession.download(from: url)
            return AudioCache.shared.storeStem(fromTemp: tempURL, trackId: trackId, kind: kind)
        } catch {
            print("[AudioEngine] fetchStem fail \(kind): \(error)")
            return nil
        }
    }

    /// 多轨准备：读取/下载人声 + 伴奏 opus，配置 HRTF 多轨链并预调度（不自动播放）。
    func prepareMultiTrack(vocalsURL: URL, accURL: URL, trackId: String,
                           mode: Mode, completion: @escaping () -> Void) {
        stop()
        isPrepared = false
        multiTrack = true
        multiTrackId = trackId
        // 记录建图时是否已含环境节点，再切换到全景模式；
        // 若已建的是非全景图（无环境节点），必须重建，否则多轨节点不在图中。
        let hadEnvironment = needsEnvironment
        self.mode = .panorama
        if graphBuilt && !hadEnvironment {
            rebuildGraph()
        }
        onLoadingChanged?(true)

        multiTask = Task { @MainActor in
            let vLocal = await Self.fetchStem(url: vocalsURL, trackId: trackId, kind: .vocals)
            guard self.multiTrackId == trackId, !Task.isCancelled else { return }
            let aLocal = await Self.fetchStem(url: accURL, trackId: trackId, kind: .acc)
            guard self.multiTrackId == trackId, !Task.isCancelled else { return }
            guard let vLocal, let aLocal else {
                self.onLoadingChanged?(false); self.onLoadFailed?(); return
            }
            do {
                // 先确保图已建、引擎运行，拿到硬件采样率（fileFormat 在 buildGraph 后已设置）
                self.ensureGraphBuilt()
                guard self.ensureEngineRunning() else {
                    self.onLoadingChanged?(false); self.onLoadFailed?(); return
                }
                let targetSR = self.fileFormat?.sampleRate ?? 48000
                // 解码并下混为 mono PCM buffer（不支持会在此明确抛错，不再静默）
                let vBuf = try Self.loadMonoBuffer(url: vLocal, targetSampleRate: targetSR)
                let aBuf = try Self.loadMonoBuffer(url: aLocal, targetSampleRate: targetSR)
                self.vocalsBuffer = vBuf
                self.accBuffer = aBuf
                self.multiTotalFrames = AVAudioFramePosition(min(vBuf.frameLength, aBuf.frameLength))
                self.configureMultiTrackSources()
                self.scheduleMulti(from: 0, at: nil)
                self.isPrepared = true
                self.onLoadingChanged?(false)
                completion()
            } catch {
                print("[AudioEngine] multi prepare error: \(error)")
                self.onLoadingChanged?(false); self.onLoadFailed?()
            }
        }
    }

    /// 多轨加载（带起始位置与自动播放开关）。
    func loadMultiTrack(vocalsURL: URL, accURL: URL, trackId: String,
                        mode: Mode, startSeconds: Double = 0, autoPlay: Bool = true) {
        prepareMultiTrack(vocalsURL: vocalsURL, accURL: accURL, trackId: trackId,
                          mode: mode) { [weak self] in
            guard let self else { return }
            if autoPlay {
                self.playMultiFrom(seconds: startSeconds, autoPlay: true)
            } else {
                // 暂停态：从目标位置挂载但不播放
                self.seek(toSeconds: startSeconds)
            }
        }
    }

    // MARK: - 多轨同步调度与播放

    /// 从完整 mono buffer 中切出从 start 开始、长度 count 的子 buffer（共享同一格式）。
    private func sliceBuffer(_ src: AVAudioPCMBuffer, start: AVAudioFramePosition,
                             count: AVAudioFrameCount) -> AVAudioPCMBuffer? {
        guard let out = AVAudioPCMBuffer(pcmFormat: src.format, frameCapacity: count),
              let srcData = src.floatChannelData, let dstData = out.floatChannelData else { return nil }
        let offset = Int(start)
        for ch in 0..<Int(src.format.channelCount) {
            dstData[ch].update(from: srcData[ch].advanced(by: offset), count: Int(count))
        }
        out.frameLength = count
        return out
    }

    /// 同步调度两条音轨（从同一帧开始），统一 hostTime 保证 sample-accurate。
    private func scheduleMulti(from frame: AVAudioFramePosition, at when: AVAudioTime?) {
        guard let vBuf = vocalsBuffer, let aBuf = accBuffer, multiTotalFrames > 0 else { return }
        playerVocals.stop()
        playerAcc.stop()
        multiGen += 1
        let gen = multiGen
        let count = AVAudioFrameCount(multiTotalFrames - frame)
        guard count > 0,
              let vSlice = sliceBuffer(vBuf, start: frame, count: count),
              let aSlice = sliceBuffer(aBuf, start: frame, count: count) else { return }
        playerVocals.scheduleBuffer(vSlice, at: when, options: []) {
            DispatchQueue.main.async { [weak self] in
                guard let self, self.multiGen == gen, self.isPlaying else { return }
                self.onCompletion?()
            }
        }
        playerAcc.scheduleBuffer(aSlice, at: when, options: [])
    }

    /// 多轨世代号（同 scheduleGen，区分完成/打断）
    private var multiGen = 0

    /// 从指定位置起播（统一 hostTime 同步两个播放节点）。
    func playMultiFrom(seconds: Double, autoPlay: Bool = true) {
        guard isPrepared, multiTotalFrames > 0, let vBuf = vocalsBuffer else { return }
        let target = AVAudioFramePosition(seconds * vBuf.format.sampleRate)
        let clamped = max(0, min(target, multiTotalFrames - 1))
        pausedFrame = clamped
        guard ensureEngineRunning() else { return }
        if autoPlay {
            // sample-accurate：两个 buffer 指定同一未来渲染时间，再 play() 启动节点。
            let hostTime = futureHostTime(seconds: 0.15)
            let when = AVAudioTime(hostTime: hostTime)
            scheduleMulti(from: clamped, at: when)
            playerVocals.play()
            playerAcc.play()
            isPlaying = true
            playStartFrame = clamped
            playStartTime = CACurrentMediaTime() + 0.15
            startTimer()
        } else {
            scheduleMulti(from: clamped, at: nil)
        }
    }

    /// 计算距现在 delay 秒后的 hostTime（mach 绝对时间），用于多节点同步启动。
    private func futureHostTime(seconds: Double) -> UInt64 {
        var tb = mach_timebase_info_data_t()
        if mach_timebase_info(&tb) != KERN_SUCCESS { return mach_absolute_time() }
        let ticks = UInt64(seconds * 1_000_000_000 *
                           Double(tb.numer) / Double(tb.denom))
        return mach_absolute_time() + ticks
    }

    // MARK: - 进度（可靠时钟，与引擎渲染时间线解耦）

    private func currentFrame() -> AVAudioFramePosition {
        if multiTrack {
            guard let vf = vocalsBuffer else { return pausedFrame }
            let sr = vf.format.sampleRate
            guard isPlaying, sr > 0 else { return pausedFrame }
            let elapsed = CACurrentMediaTime() - playStartTime
            guard elapsed >= 0, elapsed.isFinite else { return playStartFrame }
            let frames = playStartFrame + AVAudioFramePosition(elapsed * sr)
            return min(frames, max(multiTotalFrames - 1, 0))
        }
        guard isPlaying, let format = fileFormat, format.sampleRate > 0 else { return pausedFrame }
        let elapsed = CACurrentMediaTime() - playStartTime
        guard elapsed >= 0, elapsed.isFinite else { return playStartFrame }
        let frames = playStartFrame + AVAudioFramePosition(elapsed * format.sampleRate)
        return min(frames, max(totalFrames - 1, 0))
    }

    func currentSeconds() -> Double {
        if multiTrack {
            guard let vf = vocalsBuffer else { return 0 }
            let sr = vf.format.sampleRate
            return sr > 0 ? Double(currentFrame()) / sr : 0
        }
        guard let format = fileFormat, format.sampleRate > 0 else { return 0 }
        return Double(currentFrame()) / format.sampleRate
    }

    func durationSeconds() -> Double {
        if multiTrack {
            guard let vf = vocalsBuffer else { return 0 }
            let sr = vf.format.sampleRate
            return sr > 0 ? Double(multiTotalFrames) / sr : 0
        }
        guard let format = fileFormat, format.sampleRate > 0 else { return 0 }
        return Double(totalFrames) / format.sampleRate
    }
}
