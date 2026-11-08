import SwiftUI
import CommonCrypto
import ImageIO
import CoreGraphics

// MARK: - 加载状态

enum LoadState: Equatable {
    case loading
    case loaded
    case empty
    case failed(String)
}

// MARK: - 封面组件（支持真实封面图，无封面时用柔和渐变占位）

struct AlbumArt: View {
    let size: CGFloat
    let title: String
    var coverURL: URL? = nil
    var radius: CGFloat? = nil

    var body: some View {
        Group {
            if let coverURL {
                CachedCoverImage(url: coverURL, title: title, placeholder: placeholder)
            } else {
                placeholder
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: radius ?? size * 0.22, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: radius ?? size * 0.22, style: .continuous)
                .stroke(Theme.cardBorder, lineWidth: 1)
        )
        // 封面柔和投影：在卡片上浮起
        .shadow(color: .black.opacity(0.3), radius: 8, x: 0, y: 3)
    }

    private var placeholder: some View {
        let (c1, c2) = paletteFrom(title)
        return ZStack {
            LinearGradient(
                colors: [c1, c2],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            Image(systemName: "music.note")
                .font(.system(size: size * 0.36, weight: .semibold))
                .foregroundColor(.white.opacity(0.9))
        }
    }

    private func paletteFrom(_ text: String) -> (Color, Color) {
        var hash = 0
        for scalar in text.unicodeScalars {
            hash = (hash &* 31 &+ Int(scalar.value)) & 0x7fffffff
        }
        return Theme.placeholderPalettes[hash % Theme.placeholderPalettes.count]
    }
}

// MARK: - 封面图缓存（内存 + 磁盘二级，P2：杀进程后图片不再重新下载）

final class CoverImageCache {
    static let shared = CoverImageCache()

    private let cache = NSCache<NSURL, UIImage>()
    private let diskDir: URL
    private let fm = FileManager.default
    /// 磁盘 IO 放后台队列，不阻塞主线程
    private let ioQueue = DispatchQueue(label: "cover.disk", qos: .utility)

    init() {
        let base = fm.urls(for: .cachesDirectory, in: .userDomainMask).first!
        diskDir = base.appendingPathComponent("cover-cache-v1", isDirectory: true)
        try? fm.createDirectory(at: diskDir, withIntermediateDirectories: true)
    }

    // MARK: 内存
    func image(for url: URL) -> UIImage? {
        cache.object(forKey: url as NSURL)
    }

    func store(_ image: UIImage, for url: URL) {
        cache.setObject(image, forKey: url as NSURL)
    }

    /// P0-2：ImageIO 按像素上限生成缩略图（官方最佳实践），
    /// 消除 UIImage(data:) 全分辨率解码的内存峰值；解码应在后台队列/async 上下文执行。
    static func downsample(data: Data, maxPixel: CGFloat) -> UIImage? {
        let srcOpts = [kCGImageSourceShouldCache: false] as CFDictionary
        guard let src = CGImageSourceCreateWithData(data as CFData, srcOpts) else {
            return UIImage(data: data)
        }
        let thumbOpts: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceShouldCacheImmediately: true,
            kCGImageSourceThumbnailMaxPixelSize: maxPixel,
        ]
        guard let cg = CGImageSourceCreateThumbnailAtIndex(src, 0, thumbOpts as CFDictionary) else {
            return UIImage(data: data)
        }
        return UIImage(cgImage: cg)
    }

    /// P0-2：统一封面解码入口——按渲染尺寸降采样 + 写内存 + 写磁盘（三条取图管线共用）。
    /// 调用方应在后台上下文执行。
    static func decodeAndCache(data: Data, for url: URL, maxPixel: CGFloat = 1200) -> UIImage? {
        guard let img = downsample(data: data, maxPixel: maxPixel) else { return nil }
        CoverImageCache.shared.store(img, for: url)
        CoverImageCache.shared.storeToDisk(img, for: url)
        return img
    }

    // MARK: 磁盘（异步读/写）
    /// 磁盘读：命中返回降采样 UIImage，未命中返回 nil。后台 IO。
    func diskImage(for url: URL, maxPixel: CGFloat = 1200) async -> UIImage? {
        let file = diskFile(for: url)
        guard fm.fileExists(atPath: file.path) else { return nil }
        return await withCheckedContinuation { cont in
            ioQueue.async {
                guard let data = try? Data(contentsOf: file) else {
                    cont.resume(returning: nil)
                    return
                }
                let img = CoverImageCache.downsample(data: data, maxPixel: maxPixel)
                cont.resume(returning: img)
            }
        }
    }

    /// 磁盘写：优先 JPEG 0.85（肉眼无差、省 5-8 倍空间），带透明通道的图保留 PNG。
    /// 超过 100MB 自动清空重建（封面图重下成本低，防止长期膨胀）。
    private var writesSinceCheck = 0
    private let diskMaxBytes: Int64 = 100 * 1024 * 1024

    func storeToDisk(_ image: UIImage, for url: URL) {
        let file = diskFile(for: url)
        ioQueue.async { [weak self] in
            guard let self else { return }
            self.writesSinceCheck += 1
            if self.writesSinceCheck >= 20 {
                self.writesSinceCheck = 0
                if self.diskUsage() > self.diskMaxBytes {
                    try? self.fm.removeItem(at: self.diskDir)
                    try? self.fm.createDirectory(at: self.diskDir, withIntermediateDirectories: true)
                }
            }
            let data: Data?
            if imageHasAlpha(image) {
                data = image.pngData()
            } else {
                data = image.jpegData(compressionQuality: 0.85)
            }
            guard let data else { return }
            do {
                try data.write(to: file, options: .atomic)
            } catch {
                // 磁盘写失败仅影响下次冷启动，静默降级
            }
        }
    }

    private func diskUsage() -> Int64 {
        guard let enumerator = fm.enumerator(at: diskDir,
                                             includingPropertiesForKeys: [.fileSizeKey],
                                             options: []) else { return 0 }
        var total: Int64 = 0
        for case let url as URL in enumerator {
            if let size = (try? url.resourceValues(forKeys: [.fileSizeKey]))?.fileSize {
                total += Int64(size)
            }
        }
        return total
    }

    private func diskFile(for url: URL) -> URL {
        // URL 含 query（如 /track/x/cover?v=2），用完整字符串哈希做文件名
        let raw = url.absoluteString
        let digest = SHA256Digest.stringHash(raw)
        return diskDir.appendingPathComponent(digest + ".img")
    }

    /// 清理内存 + 磁盘（设置页「清理缓存」）
    func clear() {
        cache.removeAllObjects()
        ioQueue.async { [weak self] in
            guard let self else { return }
            try? self.fm.removeItem(at: self.diskDir)
            try? self.fm.createDirectory(at: self.diskDir, withIntermediateDirectories: true)
        }
    }
}

/// 简单 SHA256 hex（无 CryptoKit 额外依赖需求，iOS 16 原生可用）
private enum SHA256Digest {
    static func stringHash(_ s: String) -> String {
        let data = Data(s.utf8)
        let hash = data.withUnsafeBytes { ptr -> [UInt8] in
            var h = Array(repeating: UInt8(0), count: 32)
            _ = CC_SHA256(ptr.baseAddress, CC_LONG(data.count), &h)
            return h
        }
        return hash.map { String(format: "%02x", $0) }.joined()
    }
}

private func imageHasAlpha(_ image: UIImage) -> Bool {
    guard let cg = image.cgImage else { return false }
    let info = cg.alphaInfo
    return info == .first || info == .last || info == .premultipliedFirst
        || info == .premultipliedLast || info == .alphaOnly
}

// MARK: - 歌词内存缓存（配合预加载：切歌时歌词立即命中，无需等待网络）

/// 已解析歌词的内存缓存：trackId → [KRCLine]。预加载时提前拉取并解析入库，
/// 播放页切歌时先查缓存再回退网络，避免切到下一首歌词区还转圈。
/// NSCache 自动淘汰（内存压力/上限），线程安全无需自加锁。
final class LyricsCache {
    static let shared = LyricsCache()
    private let cache = NSCache<NSString, NSArray>()

    func lines(for id: String) -> [KRCLine]? {
        cache.object(forKey: id as NSString) as? [KRCLine]
    }

    func store(_ lines: [KRCLine], for id: String) {
        cache.setObject(lines as NSArray, forKey: id as NSString)
    }

    /// 清理全部歌词缓存（设置页「清理缓存」）
    func clear() {
        cache.removeAllObjects()
    }
}

/// 带内存缓存的封面图：首次下载后缓存，切页/列表重建不再重复请求
struct CachedCoverImage<Placeholder: View>: View {
    let url: URL
    let title: String
    let placeholder: Placeholder

    @State private var image: UIImage?

    var body: some View {
        Group {
            if let image {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFill()
            } else {
                placeholder
                    .task { await load() }
            }
        }
    }

    private func load() async {
        // 1. 内存命中
        if let cached = CoverImageCache.shared.image(for: url) {
            image = cached
            return
        }
        // 2. 磁盘命中（杀进程后秒出，弱网/无网也能显示历史封面）
        if let disk = await CoverImageCache.shared.diskImage(for: url) {
            CoverImageCache.shared.store(disk, for: url)
            image = disk
            return
        }
        // 3. 网络下载 → 后台降采样解码 → 写内存 + 写磁盘（P0-2）
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            // 解码放后台线程：CGImageSource 缩略图解码不阻塞主线程
            let img = await Task.detached(priority: .utility) {
                CoverImageCache.decodeAndCache(data: data, for: url)
            }.value
            if let img { image = img }
        } catch {
            // 下载失败保留占位图
        }
    }
}

// MARK: - 可暂停时间轴调度器

/// #18（审查优化）：可暂停的 TimelineSchedule——paused 时序列结束（不再请求新帧），
/// 装饰类动画（运镜/粒子/流光）在播放暂停时冻结，不再 60fps 空转，省电并降低无用 GPU 渲染。
/// 恢复播放后 entries 返回当前时刻，时间连续不跳变（歌词滚动类 TimelineView 不受影响）。
struct PausableTimelineSchedule: TimelineSchedule {
    var paused: Bool
    func entries(from startDate: Date, mode: TimelineScheduleMode) -> AnyIterator<Date> {
        var isPaused = paused
        return AnyIterator<Date> {
            if isPaused { return nil }
            return Date()
        }
    }
}

// MARK: - 在线状态绿点

/// 头像左下角的在线绿点：鲜绿圆点 + 白描边，圆心略越过头像边缘（盖住描边一点），更醒目。
/// 用法：头像视图后 `.overlay { if online { OnlineDot(avatarSize: 72) } }`
struct OnlineDot: View {
    let avatarSize: CGFloat

    var body: some View {
        let dot = max(avatarSize * 0.22, 10)
        return Circle()
            .fill(Color(red: 0.16, green: 0.78, blue: 0.36))
            .frame(width: dot, height: dot)
            .overlay(Circle().stroke(.white, lineWidth: max(dot * 0.12, 1.5)))
            .shadow(color: .black.opacity(0.3), radius: 1.5, y: 0.5)
            .offset(x: avatarSize * 0.38, y: avatarSize * 0.38) // 右下角，盖住头像一点
    }
}

// MARK: - 圆形认证标（替换我的页/评论区的在线绿点）

/// 圆形蓝色认证标：鲜亮蓝渐变 + 白 V + 高光描边 + 阴影，质感与会员开通按钮一致。
/// 替代头像上的在线绿点位置（右下角）；与旧版方形蓝 V 不同，本组件为圆形。
struct CertBadge: View {
    let avatarSize: CGFloat

    var body: some View {
        let size = max(avatarSize * 0.26, 12)
        return ZStack {
            Circle()
                .fill(
                    LinearGradient(colors: [Color(hex: 0x3D7BFF), Color(hex: 0x2A6DF4)],
                                   startPoint: .top, endPoint: .bottom)
                )
                .overlay(
                    Circle().stroke(
                        LinearGradient(colors: [.white.opacity(0.6), .white.opacity(0.15)],
                                       startPoint: .top, endPoint: .bottom),
                        lineWidth: max(size * 0.09, 1)
                    )
                )
                .shadow(color: Color(hex: 0x2A6DF4).opacity(0.45), radius: max(size * 0.22, 2), y: 1)
            Text("V")
                .font(.system(size: size * 0.46, weight: .heavy))
                .foregroundColor(.white)
                .shadow(color: .black.opacity(0.2), radius: 0.5, y: 0.5)
        }
        .frame(width: size, height: size)
        .offset(x: avatarSize * 0.38, y: avatarSize * 0.38) // 右下角，盖住头像一点（与绿点同位）
    }
}

// MARK: - 播放中声波动画

struct EqualizerBars: View {
    var color: Color = Theme.accent
    @State private var animate = false

    var body: some View {
        HStack(spacing: 3) {
            ForEach(0..<3, id: \.self) { index in
                Capsule()
                    .fill(color)
                    .frame(width: 3, height: animate ? 14 : 6)
                    .animation(
                        .easeInOut(duration: 0.5)
                            .repeatForever(autoreverses: true)
                            .delay(Double(index) * 0.12),
                        value: animate
                    )
            }
        }
        .frame(height: 16)
        .onAppear { animate = true }
    }
}

// MARK: - 列表行（白色卡片行）

struct TrackRow: View, Equatable {
    // P0-1：不再订阅整个 PlayerManager（iOS 16 ObservableObject 对象级依赖，
    // 0.25s 高频进度刷新会连带重算所有读了 player 的行）——由父级传入静态 isCurrent/isPlaying
    let track: Track
    let isCurrent: Bool
    let isPlaying: Bool
    let onTap: () -> Void

    static func == (l: Self, r: Self) -> Bool {
        l.track.id == r.track.id && l.isCurrent == r.isCurrent && l.isPlaying == r.isPlaying
    }

    var body: some View {
        Button(action: onTap) {
            HStack(spacing: 12) {
                AlbumArt(size: 52, title: track.title, coverURL: track.coverURL, radius: 14)

                VStack(alignment: .leading, spacing: 3) {
                    Text(track.title)
                        .font(.system(size: 16, weight: .semibold))
                        .lineLimit(1)
                        .foregroundColor(isCurrent ? Theme.accent : Theme.textPrimary)
                    HStack(spacing: 4) {
                        Text(track.displayAuthor)
                            .font(.system(size: 13))
                            .foregroundColor(Theme.textSecondary)
                            .lineLimit(1)
                        if let pc = track.playCount, pc > 0 {
                            Text("· 播放 \(pc)")
                                .font(.system(size: 12))
                                .foregroundColor(Theme.textTertiary)
                                .layoutPriority(1) // 播放量数字优先完整显示，作者名先截断
                        }
                    }
                }

                Spacer()

                // 右侧状态：未播放=竖三点；当前播放=跳动三竖条；当前暂停=静止三竖条
                if isCurrent {
                    if isPlaying {
                        EqualizerBars()
                    } else {
                        // 暂停的当前歌曲：静止三根竖条
                        HStack(spacing: 2.5) {
                            Capsule().fill(Theme.accent).frame(width: 2.5, height: 10)
                            Capsule().fill(Theme.accent).frame(width: 2.5, height: 10)
                            Capsule().fill(Theme.accent).frame(width: 2.5, height: 10)
                        }
                        .frame(width: 20, height: 16)
                    }
                } else {
                    Image(systemName: "ellipsis")
                        .font(.system(size: 16, weight: .medium))
                        .foregroundColor(Theme.textTertiary)
                        .rotationEffect(.degrees(90))
                        .frame(width: 20, height: 20)
                }
            }
            .padding(.vertical, 8)
        }
        .buttonStyle(PressableButtonStyle())
    }
}

/// 系统分享 sheet 桥接（纯文本分享：文本+链接换行独立，QQ/微信识别 URL 后
/// 自动抓取 og 渲染链接卡片——免 appid、免 SDK，标准站外卡片路径）
struct ShareSheet: UIViewControllerRepresentable {
    let text: String
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [text], applicationActivities: nil)
    }
    func updateUIViewController(_ vc: UIActivityViewController, context: Context) {}
}

/// 分享载荷：歌曲名@流萤音乐 + 换行独立短链（QQ 渲染卡片）
struct SharePayload: Identifiable {
    let id = UUID()
    let text: String
}

// MARK: - 通用时间格式化

/// 按压反馈按钮样式：按下轻微缩放+变暗，松手恢复（替代 plain 无反馈）
struct PressableButtonStyle: ButtonStyle {
    var scale: CGFloat = 0.97
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? scale : 1.0)
            .opacity(configuration.isPressed ? 0.85 : 1.0)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

func formatTime(_ seconds: Double) -> String {
    guard seconds.isFinite, seconds >= 0 else { return "0:00" }
    let total = Int(seconds)
    return "\(total / 60):\(String(format: "%02d", total % 60))"
}

// MARK: - 空态 / 错误态通用视图

struct StatusPlaceholder: View {
    let icon: String
    let title: String
    let message: String
    var retry: (() -> Void)? = nil

    var body: some View {
        VStack(spacing: 16) {
            ZStack {
                Circle()
                    .fill(Theme.accentSoft)
                    .frame(width: 88, height: 88)
                Image(systemName: icon)
                    .font(.system(size: 36))
                    .foregroundColor(Theme.accent)
            }
            Text(title)
                .font(.system(size: 18, weight: .bold))
                .foregroundColor(Theme.textPrimary)
            Text(message)
                .font(.system(size: 14))
                .foregroundColor(Theme.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 36)
            if let retry {
                Button(action: retry) {
                    Text("重试")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundColor(.white)
                        .padding(.horizontal, 28)
                        .padding(.vertical, 10)
                        .background(Theme.accent, in: Capsule())
                }
                .buttonStyle(.plain)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

// MARK: - 多功能进度条（已播放段 + 已缓冲段 + 缓冲加载动画 + 拖动预览松手 seek）

/// 同一根进度条表达三层信息：
/// - 淡色轨道：整段歌曲时长
/// - 中浅段（buffer）：已经缓冲到的位置（来自 AVPlayerItem.loadedTimeRanges）
/// - 纯色段（progress）：当前播放位置
/// - 拖头：10pt 小圆点
/// 交互（2026-10-24）：
/// - 快速点击：滑块从当前位置平滑滑动到目标位置（easeOutCubic 0.35s），动画完成才 seek；
///   动画到位后视觉保持停在目标位置，等播放器真实进度追上（|progress-target|<0.005）再无缝交给实时进度，
///   消除 seek 异步期间（AVPlayer 跳转 + 0.1s 周期刷新）的闪回
/// - 动画打断：动画进行中再次按下，立即取消旧动画，新指令优先；
///   若动画中再次点击，从当前视觉位置平滑到新目标（无跳变）
/// - 长按（按住 0.3s）：轨道加粗 3→5pt、拖头放大 10→15pt（宽度不变），松手回弹，不跳转
/// - 拖动：跟随手指预览，松手直接 seek
struct PlayerProgressBar: View {
    var progress: Double        // 当前播放位置 0...1
    var buffer: Double          // 已缓冲位置 0...1
    var isBuffering: Bool       // 是否正在缓冲（显示加载动画）
    var tint: Color = .white
    /// 拖动中的预览位置（由父视图持有，时间标签同步显示预览时间）
    @Binding var scrubFraction: Double?
    var onSeek: (Double) -> Void

    // —— 平滑点击动画（TimelineView 系统逐帧驱动，天然可打断）——
    @State private var animFrom: Double = 0
    @State private var animTarget: Double? = nil
    @State private var animStart: Date = .now
    /// 动画完成后的保持位置：等播放器真实进度追上再释放（消除 seek 异步闪回）
    @State private var holdTarget: Double? = nil
    private let animDuration: TimeInterval = 0.35

    // —— 长按加粗放大 / 手势状态 ——
    /// 手势阶段机：none → pressed →（快速位移）quickDrag /（0.3s 长按）longPress →（长按后位移）longDrag
    private enum GestureMode {
        case none
        case pressed          // 已按下，尚未判定
        case quickDrag        // 未长按直接滑动：跳到手指位置（保持现状）
        case longPress        // 长按已触发，未滑动：放大预览
        case longDrag         // 长按后滑动：从当前位置相对跟手（不跳）
    }
    @State private var gestureMode: GestureMode = .none
    @State private var pressStartX: CGFloat = 0
    @State private var pressedAt: Date = .now
    @State private var longPressActive = false
    @State private var pressTimerTask: Task<Void, Never>?
    /// 长按后相对拖动的起点：长按触发瞬间的滑块位置 + 手指 x
    @State private var relativeStart: Double = 0
    @State private var relativeStartX: CGFloat = 0
    @State private var currentTouchX: CGFloat = 0

    /// 长按放大参数（用户确认：轨道加粗 3→5pt、拖头放大 10→15pt，宽度不变）
    private var trackHeight: CGFloat { longPressActive ? 5 : 3 }
    private var thumbSize: CGFloat { longPressActive ? 15 : 10 }

    /// 当前视觉位置（实时计算，供手势/动画起点用）：
    /// 拖动预览 > 到位保持 > 平滑动画插值 > 实时进度
    private var currentVisual: Double {
        if let hold = holdTarget { return hold }
        if let target = animTarget {
            let t = min(Date.now.timeIntervalSince(animStart) / animDuration, 1)
            let e = 1 - pow(1 - t, 3)
            return animFrom + (target - animFrom) * e
        }
        return progress
    }

    /// 指定时刻的视觉位置（TimelineView 每帧驱动用，纯计算不写状态）
    private func visualFraction(at date: Date) -> Double {
        if let hold = holdTarget { return hold }
        if let target = animTarget {
            let t = min(date.timeIntervalSince(animStart) / animDuration, 1)
            let e = 1 - pow(1 - t, 3)
            return animFrom + (target - animFrom) * e
        }
        return progress
    }

    /// 开始平滑滑动到目标：从当前视觉位置出发。
    /// 动画进行中被再次点击 → from 取当前动画插值 → 从当前位置平滑到新目标（动画打断、无跳变）
    private func startSmoothSeek(to target: Double) {
        animFrom = currentVisual
        animTarget = target
        animStart = Date.now
    }

    private func clamped(_ f: Double) -> Double { min(max(f, 0), 1) }
    private func pointX(_ f: Double, width: CGFloat) -> CGFloat {
        CGFloat(clamped(f)) * width
    }

    var body: some View {
        // 逐帧驱动只在「平滑动画进行中」启用——无动画时用静态渲染
        // （播放进度由父视图 0.1s 更新驱动重组、拖动手势事件驱动重组），
        // 消除无条件 60fps 重组（播放页常驻，无条件 TimelineView 是打开侧滑栏卡顿的根源之一）
        Group {
            if animTarget != nil {
                TimelineView(.animation(minimumInterval: 1.0 / 60.0)) { context in
                    // 当前帧视觉位置：拖动预览 > 到位保持 > 动画插值 > 实时进度
                    progressContent(shown: scrubFraction ?? visualFraction(at: context.date))
                }
            } else {
                progressContent(shown: scrubFraction ?? visualFraction(at: Date.now))
            }
        }
        .frame(height: 26)
        // 动画完成回调：等插值走完（同一 animStart/时长，视觉此刻已平滑到 target）
        // → seek + 保持视觉（holdTarget），等播放器真实进度追上再无缝交接，消除闪回。
        // 打断处理（关键）：动画进行中点击新位置 → animTarget 变化 → 本任务被取消，
        // Task.sleep 必须用 do/catch 捕获取消并 return——若用 try? 吞掉取消，
        // 旧任务会继续执行 onSeek(旧目标) 并覆盖 holdTarget，新指令被旧动画顶掉。
        .task(id: animTarget) {
            guard let target = animTarget else { return }
            let elapsed = Date.now.timeIntervalSince(animStart)
            if elapsed < animDuration {
                do {
                    try await Task.sleep(nanoseconds: UInt64((animDuration - elapsed) * 1_000_000_000))
                } catch {
                    return  // 被新指令打断：退出，新任务接管（绝不执行旧目标的 seek）
                }
            }
            // 双保险：sleep 期间若再次被新指令接管（animTarget 已变），放弃本次回调
            guard animTarget == target else { return }
            onSeek(target)
            holdTarget = target
            animTarget = nil
        }
        // progress 到位检测：播放器真实进度追上目标 → 无缝交给实时进度（消除闪回）
        .onChange(of: progress) { newP in
            if let hold = holdTarget, abs(newP - hold) < 0.005 {
                holdTarget = nil
            }
        }
        // 超时兜底：seek 异常（网络差/加载慢）时 1.5s 强制释放，避免视觉永久停在目标位置。
        // 取消处理同动画回调：连续 seek 时旧兜底任务被取消必须退出（try? 吞掉取消会
        // 误把新目标 holdTarget 提前清掉 → 视觉闪回）；释放前再校验目标未被替换。
        .task(id: holdTarget) {
            guard let hold = holdTarget else { return }
            do {
                try await Task.sleep(nanoseconds: 1_500_000_000)
            } catch {
                return  // 已被新 seek 接管：不越权释放
            }
            if holdTarget == hold {
                holdTarget = nil
            }
        }
    }

    /// 进度条内容（轨道/缓冲/已播/扫线/拖头 + 手势）。shown 由调用方决定驱动方式
    @ViewBuilder
    private func progressContent(shown: Double) -> some View {
        GeometryReader { geo in
            let width = geo.size.width
            ZStack(alignment: .leading) {
                // 总轨道（长按加粗：3 → 5pt，宽度不变）
                Capsule()
                    .fill(tint.opacity(0.18))
                    .frame(height: trackHeight)
                // 已缓冲段：限制在播放进度内（不超出播放进度，min(buffer, shown)）
                Capsule()
                    .fill(tint.opacity(0.38))
                    .frame(width: pointX(min(buffer, shown), width: width), height: trackHeight)
                // 已播放段
                Capsule()
                    .fill(tint)
                    .frame(width: pointX(shown, width: width), height: trackHeight)

                // 缓冲中：滑块生长-移动-收缩循环（平滑动画/保持/拖动预览期间不显示）
                if isBuffering, scrubFraction == nil, animTarget == nil, holdTarget == nil {
                    LoadingSweep(tint: tint)
                }

                // 拖头（长按放大：10 → 15pt，宽度不变）
                Circle().fill(tint).frame(width: thumbSize, height: thumbSize)
                    .offset(x: pointX(shown, width: width) - thumbSize / 2)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { g in
                        let f = clamped(g.location.x / max(width, 1))
                        currentTouchX = g.location.x
                        if gestureMode == .none {
                            // 按下：不打断动画（高频操作优化——动画从当前视觉位置自然衔接），
                            // 仅记录按下状态并启动长按检测
                            gestureMode = .pressed
                            pressStartX = g.location.x
                            pressedAt = Date.now
                            // 长按检测：0.3s 未松开且未快速滑动 → 放大预览 + 进入长按模式
                            pressTimerTask?.cancel()
                            pressTimerTask = Task {
                                try? await Task.sleep(nanoseconds: 300_000_000)
                                guard !Task.isCancelled, gestureMode == .pressed else { return }
                                // 长按触发：记录相对拖动起点（当前滑块位置 & 当前手指 x）
                                relativeStart = currentVisual
                                relativeStartX = currentTouchX
                                gestureMode = .longPress
                                withAnimation(.spring(response: 0.25, dampingFraction: 0.7)) {
                                    longPressActive = true
                                }
                            }
                        }
                            switch gestureMode {
                            case .pressed:
                                // 位移超过 8pt → 未长按直接滑动：跳到手指位置（保持现状）
                                if abs(g.location.x - pressStartX) > 8 {
                                    gestureMode = .quickDrag
                                    animTarget = nil   // 新指令优先：打断平滑动画
                                    scrubFraction = f
                                    // 拖动中放大（同长按）：轨道加粗 5pt + 拖头 15pt，交互反馈一致
                                    withAnimation(.spring(response: 0.25, dampingFraction: 0.7)) {
                                        longPressActive = true
                                    }
                                }
                            case .quickDrag:
                                // 快速滑动：持续跳到手指位置
                                scrubFraction = f
                            case .longPress:
                                // 长按后开始滑动 → 相对跟手：滑块从长按起点随手指位移增量移动，不跳
                                if abs(g.location.x - pressStartX) > 8 {
                                    gestureMode = .longDrag
                                    animTarget = nil
                                    scrubFraction = clamped(relativeStart + Double((g.location.x - relativeStartX) / max(width, 1)))
                                }
                            case .longDrag:
                                // 长按后滑动：持续相对跟手
                                scrubFraction = clamped(relativeStart + Double((g.location.x - relativeStartX) / max(width, 1)))
                            case .none:
                                break
                            }
                        }
                        .onEnded { g in
                            let f = clamped(g.location.x / max(width, 1))
                            pressTimerTask?.cancel()
                            let dt = Date.now.timeIntervalSince(pressedAt)
                            switch gestureMode {
                            case .longPress:
                                // 长按未滑动：仅放大预览，松手回弹，不跳转
                                withAnimation(.spring(response: 0.25, dampingFraction: 0.7)) {
                                    longPressActive = false
                                }
                            case .longDrag:
                                // 长按后滑动：保持视觉在最终位置，等播放器真实进度追上再交接（消除闪回）
                                let finalPos = scrubFraction ?? f
                                onSeek(finalPos)
                                holdTarget = finalPos
                                scrubFraction = nil
                                withAnimation(.spring(response: 0.25, dampingFraction: 0.7)) {
                                    longPressActive = false
                                }
                            case .quickDrag:
                                // 快速滑动：保持视觉在松手位置，等播放器真实进度追上再交接（消除闪回）
                                onSeek(f)
                                holdTarget = f
                                scrubFraction = nil
                                // 松手恢复默认尺寸（拖动中已放大）
                                withAnimation(.spring(response: 0.25, dampingFraction: 0.7)) {
                                    longPressActive = false
                                }
                            case .pressed:
                                // 快速点击：滑块从当前位置平滑滑到目标，动画完成才 seek
                                if dt < 0.35 {
                                    scrubFraction = nil
                                    startSmoothSeek(to: f)
                                }
                                // dt >= 0.35 兜底（长按定时器异常未触发）：不跳转
                            case .none:
                                break
                            }
                            gestureMode = .none
                        }
                )
            }
        }
}

/// 缓冲加载动画：不确定进度滑块——「生长 → 移动 → 收缩 → 消失」循环（2026-10-24）。
/// 高度限到 3pt，与轨道线重合，不浮在进度条空白区。
/// 需求：缓存开始/中途重新缓存时显示，缓存成功开始播放即消失（由 isBuffering 控制）。
/// 形态：从轨道最左端冒出（长度 0 慢慢长到固定值）→ 以固定长度滑到最右端 →
/// 右端固定、长度慢慢缩回 0 消失 → 再从左端冒出，循环往复，直到加载完成。
/// 速度：单个完整周期 0.8s（生长 0.3 / 移动 0.4 / 收缩 0.3）。
/// 实现要点：必须用 TimelineView 按帧驱动——SwiftUI 的 withAnimation(.repeatForever)
/// 只对 animatable 属性做「首尾」线性插值、不会在动画期间重算 body 里的条件分支；
/// 三阶段宽度/位置依赖逐帧重算，若用 withAnimation 则宽度恒为首尾插值的 0 → 滑块不可见。
private struct LoadingSweep: View {
    let tint: Color
    /// 单周期秒数
    private let cycle: TimeInterval = 0.8

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 60.0)) { context in
            // phase 0...1：按墙钟时间推进并循环（60fps 逐帧驱动，取消即停止）
            let t = context.date.timeIntervalSinceReferenceDate
            let phase = CGFloat((t / cycle).truncatingRemainder(dividingBy: 1))
            GeometryReader { geo in
                let totalW = geo.size.width
                let sweepW = totalW * 0.25   // 固定长度：轨道 25%
                // 三阶段插值（每帧重算，宽度/位置真实连续）：
                // 0.0~0.3 生长：左边缘固定在最左，长度 0 → sweepW
                // 0.3~0.7 移动：长度保持 sweepW，左边缘从左滑到最右
                // 0.7~1.0 收缩：右边缘固定在最右，长度 sweepW → 0
                let (w, x): (CGFloat, CGFloat) = {
                    if phase < 0.3 {
                        let f = phase / 0.3
                        return (sweepW * f, 0)
                    } else if phase < 0.7 {
                        let f = (phase - 0.3) / 0.4
                        return (sweepW, f * (totalW - sweepW))
                    } else {
                        let f = (phase - 0.7) / 0.3
                        let w = sweepW * (1 - f)
                        return (w, totalW - w)
                    }
                }()
                Capsule()
                    .fill(tint)
                    .frame(width: w, height: 3)
                    .offset(x: x)
            }
            .frame(height: 3)   // 关键：限高 3pt，ZStack 垂直居中后正好压在轨道线上
        }
    }
}

// MARK: - 跑马灯文字（长标题自动水平无缝滚动，短标题静止）

struct MarqueeText: View {
    let text: String
    let font: Font
    let color: Color
    /// 滚动速度（pt/秒）
    var speed: CGFloat = 50
    /// 每次滚动前停顿秒数
    var pause: TimeInterval = 1.2
    /// 是否允许滚动：非当前播放页传 false，离屏页不再跑 repeatForever 动画，减少卡顿
    var active: Bool = true

    @State private var textWidth: CGFloat = 0      // 单份文字宽（溢出判定 + 滚动距离）
    @State private var containerWidth: CGFloat = 0
    @State private var offset: CGFloat = 0

    /// 原字号是否溢出容器 → 溢出即跑马灯滚动（不再缩小字号，需求：过长直接滚动）
    private var overflow: Bool { textWidth > containerWidth + 4 && containerWidth > 0 }
    /// 是否滚动：原字号溢出即滚动（去掉了"缩小后放得下就静止"的分支）
    private var shouldScroll: Bool {
        overflow && containerWidth > 0
    }

    /// 滚动判定键：文字 / 激活状态 / 文字宽度 / 容器宽度 任一变化都会让
    /// `.task(id:)` 取消旧任务并重新评估启动 —— 状态驱动，彻底规避
    /// onAppear / onChange 先后顺序不可靠导致的「切歌后跑马灯不启动」。
    private var scrollKey: String {
        "\(text)|\(active)|\(textWidth)|\(containerWidth)"
    }

    var body: some View {
        GeometryReader { geo in
            // 双份相同文字无缝衔接（spacing 0）：第一份滚出左端时第二份恰好补位，
            // 循环回跳点两帧画面完全相同 → 永不见空档/闪烁
            HStack(spacing: 0) {
                Text(text).font(font).foregroundColor(color).fixedSize()
                if shouldScroll {
                    Text(text).font(font).foregroundColor(color).fixedSize()
                }
            }
            .overlay(alignment: .leading) {
                // 文字测量：溢出判定 + 滚动距离（恒为原字号）。
                // 测量值用 onAppear + onChange 双重更新：文字/字号变化时 .id(text)
                // 强制重建，onAppear 重测；同款文字宽度再变也可靠更新。
                Text(text).font(font).fixedSize().hidden()
                    .id(text)
                    .background(
                        GeometryReader { orig in
                            Color.clear
                                .onAppear { textWidth = orig.size.width }
                                .onChange(of: orig.size.width) { textWidth = $0 }
                        }
                    )
            }
            .offset(x: offset)
            .onAppear { containerWidth = geo.size.width }
            .onChange(of: geo.size.width) { containerWidth = $0 }
            // 状态驱动启动：任何相关量变化都自动取消旧任务、重新评估，
            // 停顿由 Task.sleep 承担（取消即中断，无残留延迟任务）
            .task(id: scrollKey) {
                guard active, shouldScroll else { return }
                withAnimation(nil) { offset = 0 }   // 归位并打断旧动画（若有）
                do {
                    try await Task.sleep(nanoseconds: UInt64(pause * 1_000_000_000))
                } catch {
                    return                          // 任务被取消（状态再变）：退出等待新任务
                }
                guard !Task.isCancelled, active, shouldScroll else { return }
                withAnimation(
                    .linear(duration: Double(textWidth / speed))
                        .repeatForever(autoreverses: false)
                ) {
                    offset = -textWidth              // spacing 0：滚动一份文字宽即一个无缝周期
                }
            }
        }
        .frame(height: 30)
        .clipped()
    }
}

// MARK: - 会员 / 认证 / 头衔徽章（2026-10-19 会员体系）

/// 会员胶囊：SVIP 金色 / VIP 绿色（与会员开通页按钮同款配色），渐变 + 皇冠图标 + 高光描边 + 阴影。
/// 开了 SVIP 只显示 SVIP。徽章高度固定 20，不随文字挤压变形。
struct MemberBadge: View {
    let member: String?
    var body: some View {
        if let m = member, m == "svip" {
            capsule(text: "SVIP", colors: [Color(hex: 0xB98A2E), Color(hex: 0xE3C25B)])
        } else if let m = member, m == "vip" {
            capsule(text: "VIP", colors: [Color(hex: 0x0F9D58), Color(hex: 0x2ECC8F)])
        }
    }

    private func capsule(text: String, colors: [Color]) -> some View {
        HStack(spacing: 3) {
            Image(systemName: "crown.fill")
                .font(.system(size: 8, weight: .bold))
            Text(text)
                .font(.system(size: 10, weight: .heavy))
        }
        .foregroundColor(.white)
        .padding(.horizontal, 6)
        .frame(height: 20)
        .background(
            LinearGradient(colors: colors, startPoint: .top, endPoint: .bottom),
            in: Capsule()
        )
        .overlay(
            Capsule()
                .stroke(
                    LinearGradient(colors: [.white.opacity(0.55), .white.opacity(0.1)],
                                   startPoint: .top, endPoint: .bottom),
                    lineWidth: 0.6
                )
        )
        .shadow(color: colors[1].opacity(0.35), radius: 3, y: 1)
    }
}

/// 官方认证蓝 V 标（固定样式，如抖音/快手蓝 V；认证类型非空即显示）
struct VerifyBadge: View {
    let verified: String?
    var body: some View {
        if let v = verified, !v.isEmpty {
            ZStack {
                RoundedRectangle(cornerRadius: 4)
                    .fill(LinearGradient(colors: [Color(hex: 0x2A6DF4), Color(hex: 0x5B8DEF)],
                                         startPoint: .top, endPoint: .bottom))
                    .frame(width: 16, height: 16)
                Text("V")
                    .font(.system(size: 10, weight: .heavy))
                    .foregroundColor(.white)
            }
        }
    }
}

/// 头衔胶囊（浅色半透明背景 + 文字；空头衔不显示）
struct TitleCapsule: View {
    let title: String?
    var body: some View {
        if let t = title, !t.isEmpty {
            Text(t)
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(Theme.accent)
                .lineLimit(1) // 长头衔在胶囊内截断，防止撑宽挤坏同行布局
                .padding(.horizontal, 6).padding(.vertical, 1.5)
                .background(Theme.accent.opacity(0.12))
                .clipShape(Capsule())
        }
    }
}

/// 会员到期时间文本（VIP/SVIP 各自到期，取较晚者展示"已开通至"）
struct MemberExpireText: View {
    let memberExpire: Double?
    let svipExpire: Double?
    var body: some View {
        let expire = max(memberExpire ?? 0, svipExpire ?? 0)
        if expire > 0 {
            Text("会员有效期至 " + Self.fmt(expire))
                .font(.system(size: 12))
                .foregroundColor(.secondary)
                .lineLimit(1)           // 窄屏同行防溢出（meta 行）
                .minimumScaleFactor(0.8)
        }
    }
    private static func fmt(_ ts: Double) -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: Date(timeIntervalSince1970: ts))
    }
}
