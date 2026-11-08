import SwiftUI
import WebKit
import UIKit

/// Folia「全貌版」全屏歌词：WKWebView 加载本地伺服的 Folia 网页渲染层。
///
/// - 数据链路：原生拉取歌词（API.fetchLyric，复用内存缓存）→ song 消息注入网页；
///   播放进度（0.1s 级 @Published currentTime）→ time 消息（限流 ~10Hz）驱动逐字/逐行。
/// - 双端控制：网页控制条事件（play/pause/next/previous/seek）回传原生执行；
///   原生控制（顶部/外部）通过 time 消息中的 state 字段 + seek 跳转同步网页。
/// - 模式：用户在网页侧切换 14 种 Folia 模式，模式名持久化（WebLyricModeStore）。
/// - 主题（背景优先级链）：自定义背景图（原生层，最优先）→ AI 生成配色（Folia major 移植，
///   AIThemeService 缓存即时命中；未命中先用封面主色，AI 生成完成走轻量 theme 消息换肤，
///   不重推 song 以免重置 Folia UI）→ 封面主色 → 黑色兜底。
///   清除自定义背景图后回落到 AI 配色/封面主色，与普通播放页保持一致。
struct FullScreenWebLyricView: View {
    let track: Track

    @EnvironmentObject private var player: PlayerManager
    @Environment(\.dismiss) private var dismiss
    /// 观察自定义背景服务：save()/clear() 后立即刷新背景图层与顶栏图标，
    /// 此前未观察 → 选完图要等别的 @State 变化才重绘（表现为"设了图没反应"）
    @ObservedObject private var bgService = PlayerBackgroundService.shared

    /// 进入全貌版时正在播放的曲目下标（用于跟随切歌）
    @State private var enteredIndex = -1
    /// 沉浸模式：轻点隐藏/显示顶栏
    @State private var chromeVisible = true
    /// 网页加载失败兜底提示
    @State private var loadFailed = false
    /// 时间推送限流
    @State private var lastTimePush: TimeInterval = 0
    /// 全屏播放页自定义背景：相册选图确认弹窗
    @State private var showBackgroundPicker = false
    /// 已确认选图后打开的相册（confirmationDialog → sheet 两级）
    @State private var showBackgroundGallery = false
    /// 真机诊断：网页侧 patch 状态 JSON（onReady 后拉取）
    @State private var diagText = ""
    /// 诊断浮层显隐（默认显示，截图取证后告知我移除）
    @State private var showDiag = true

    private let bridge: WebLyricBridge

    init(track: Track) {
        self.track = track
        let bridge = WebLyricBridge()
        // 网页渲染层就绪：注入当前歌曲并开始推时间
        bridge.onReady = {
            FoliaWebSync.markReady()
            // 诊断面包屑：进入全屏 / 页面就绪 / 切模式 / 关闭 全链路落 app_logs，
            // 用于定位"选模式后模式条与歌曲一起消失"发生在哪一步。
            LogReporter.shared.log(level: "info", tag: "folia", message: "ready")
            // ★ 自定义背景状态必须独立于 pushSong 立即推送：
            //   pushSong 内部 await API.fetchLyric（网络，无超时保护，慢/挂起时可能延迟
            //   数十秒），若 background 消息等 pushSong 完成才推，用户会看到
            //   "自定义图 → AI 情绪背景持续覆盖"的完整窗口。这里在 ready 后毫秒级
            //   推送让位状态，网页渲染层第一时间透明化根容器/背景层。
            // ★ 重推：folia-patch.js 的 tryHookHost 通过轮询拦截 __foliaHost.receive，
            //   存在 ≤50ms 的 hook 窗口——若本条消息恰好落在 hook 前，会被 bundle 原版
            //   receive 处理、bgActive 置位失败。重推（幂等，bgActive=true 无副作用）
            //   保证至少一次落在 hook 之后。
            FoliaWebSync.pushBackground(to: FoliaWebViewRef.shared.webView)
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
                FoliaWebSync.pushBackground(to: FoliaWebViewRef.shared.webView)
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) {
                FoliaWebSync.pushBackground(to: FoliaWebViewRef.shared.webView)
            }
            Task { @MainActor in
                await FoliaWebSync.pushSong(fallback: track, to: FoliaWebViewRef.shared.webView)
            }
            FoliaWebSync.pushTime(to: FoliaWebViewRef.shared.webView)
            // 真机诊断：1.5s 后拉取网页侧 patch 状态（hook/bgActive/压制层），
            // 显示在左下角诊断浮层，截图即可取证失效环节。
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                FoliaWebViewRef.shared.webView?.evaluateJavaScript(
                    "window.__foliaDiag ? window.__foliaDiag() : '__NO_DIAG__'"
                ) { result, _ in
                    let s = result as? String ?? "__NO_DIAG__"
                    FoliaDiagRef.shared.text = s
                    FoliaDiagRef.shared.onUpdate?(s)
                }
            }
        }
        // 网页点歌词行 / 拖进度条 → 原生 seek
        bridge.onSeek = { t in
            PlayerManager.shared.seek(toSeconds: t)
        }
        // 网页侧切模式 → 持久化 + 让 Folia 模式背景显示（自定义背景让位）
        bridge.onModeChange = { mode in
            LogReporter.shared.log(level: "info", tag: "folia", message: "mode \(mode)")
            WebLyricModeStore.current = mode
            // ★ 用户主动切换 Folia 模式 → 解除自定义背景压制，让模式背景显示。
            //   自定义图「让位」是用户主动操作（非自动覆盖，不违反硬目标）；
            //   点顶栏背景按钮可随时恢复自定义背景显示。
            FoliaBgModeRef.shared.customBgActive = false
            DispatchQueue.main.async {
                FoliaWebSync.pushBackground(active: false, to: FoliaWebViewRef.shared.webView)
            }
            // 切模式后整体重跑补丁 + 强制控件可见（兜底"模式条/歌曲一起消失"）
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) {
                FoliaWebSync.resyncPatch(to: FoliaWebViewRef.shared.webView)
            }
            // 切模式后存活探针：重负载模式（WebGL/着色器）可能让 WebContent 进程被系统回收，
            // 页面变空白且无 didFail 回调 → 这里 1.2s 后校验 patch 是否还在，异常自动重载恢复
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
                FoliaWebSync.probeAliveAndRecover(FoliaWebViewRef.shared.webView)
            }
        }
        // 网页控制条 → 原生播放控制（双端可控）
        bridge.onControl = { action in
            let pm = PlayerManager.shared
            switch action {
            case "play": Haptics.lightTap(); pm.resumePlayback()   // P0-5：续播当前曲目（保留进度/已加载媒体），不走 playCurrent 重建
            case "pause": Haptics.lightTap(); pm.pausePlayback()
            case "next": Haptics.lightTap(); pm.next()
            case "previous": Haptics.lightTap(); pm.previous()
            default: break
            }
        }
        // 网页侧轻点舞台切换沉浸 → 原生顶栏同步隐藏/显示（网页控件由补丁 JS 自行隐藏）。
        // ★ 注意：此处处于 init 阶段，闭包不能捕获 self（self.bridge 尚未初始化完成），
        //   因此先转发到全局状态箱，View 在 onAppear 注册真实处理器（与 FoliaWebViewRef 同模式）。
        bridge.onImmersive = { visible in
            FoliaImmersiveRef.shared.onVisibleChange?(visible)
        }
        self.bridge = bridge
    }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            // 自定义背景图：原生层居中裁剪（scaledToFill = cover）铺满全屏 + 轻压暗，
            // 位于 WebView 之下；网页主题背景层在补丁 JS 中同步透明让位，背景图必然可见。
            if let bg = PlayerBackgroundService.shared.customImage {
                Image(uiImage: bg)
                    .resizable()
                    .scaledToFill()
                    .overlay(Color.black.opacity(0.25))  // 轻黑罩：保证歌词/控件对比度
                    .ignoresSafeArea()
                    .clipped()
            }

            // Web 渲染层（WKWebView）
            FoliaWebContainer(
                bridge: bridge,
                onLoadFailed: { loadFailed = true },
                onLoadRecovered: { loadFailed = false }
            )
            .ignoresSafeArea()

            if loadFailed {
                VStack(spacing: 12) {
                    Text("歌词引擎加载失败")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundColor(.white.opacity(0.8))
                    Text("请关闭后重试")
                        .font(.system(size: 13))
                        .foregroundColor(.white.opacity(0.5))
                    Button {
                        FoliaLocalServer.shared.restart()
                        loadFailed = false
                        FoliaWebViewRef.shared.webView?.load(
                            URLRequest(url: URL(string: "host.html?v=136", relativeTo: FoliaLocalServer.shared.baseURL)!)
                        )
                    } label: {
                        Text("重试")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundColor(.white)
                            .padding(.horizontal, 28)
                            .padding(.vertical, 10)
                            .background(Color.white.opacity(0.16), in: Capsule())
                    }
                    .buttonStyle(.plain)
                    .padding(.top, 4)
                }
            }

            // 顶栏（沉浸模式下隐藏）
            if chromeVisible {
                VStack {
                    HStack {
                        Button {
                            FullScreenOrientation.shared.isFullScreen = false
                            dismiss()
                        } label: {
                            Image(systemName: "chevron.down")
                                .font(.system(size: 16, weight: .semibold))
                                .foregroundColor(.white)
                                .frame(width: 36, height: 36)
                                .background(Color.white.opacity(0.14), in: Circle())
                        }
                        .buttonStyle(.plain)
                        Spacer()
                        Text("全貌版")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundColor(.white.opacity(0.85))
                        Spacer()
                        // 全屏播放页自定义背景：自定义背景显示态 → 相册选图/清除；
                        // Folia 模式显示态（用户切过模式）→ 点击恢复自定义背景显示
                        Button {
                            if FoliaBgModeRef.shared.customBgActive {
                                showBackgroundPicker = true
                            } else {
                                // 当前是 Folia 模式显示态：恢复自定义背景图（重新压制网页背景层）
                                FoliaBgModeRef.shared.customBgActive = true
                                FoliaWebSync.pushBackground(active: true, to: FoliaWebViewRef.shared.webView)
                            }
                        } label: {
                            Image(systemName: (FoliaBgModeRef.shared.customBgActive && PlayerBackgroundService.shared.hasCustom) ? "photo.fill" : "photo")
                                .font(.system(size: 15, weight: .semibold))
                                .foregroundColor(.white)
                                .frame(width: 36, height: 36)
                                .background(Color.white.opacity(0.14), in: Circle())
                        }
                        .buttonStyle(.plain)
                        .padding(.leading, 10)
                        Button {
                            // 原生收缩按钮：隐藏顶栏并同步网页控件进入沉浸（统一状态机）
                            withAnimation(.easeOut(duration: 0.18)) { chromeVisible = false }
                            FoliaWebPusher.pushImmersive(to: FoliaWebViewRef.shared.webView, hidden: true)
                        } label: {
                            Image(systemName: "arrow.up.left.and.arrow.down.right")
                                .font(.system(size: 15, weight: .semibold))
                                .foregroundColor(.white)
                                .frame(width: 36, height: 36)
                                .background(Color.white.opacity(0.14), in: Circle())
                        }
                        .buttonStyle(.plain)
                        .padding(.leading, 10)
                    }
                    Spacer()
                }
                .padding(.horizontal, 20)
                .padding(.top, 8)
                .transition(.move(edge: .top).combined(with: .opacity))
            }

            // 真机诊断浮层（左下角）：原生层状态 + 网页侧 patch 状态，
            // 打开全屏页自动显示，截图即可取证失效环节；点按可关闭。
            if showDiag {
                VStack(alignment: .leading, spacing: 3) {
                    Text("原生: img=\(PlayerBackgroundService.shared.hasCustom ? "1" : "0")")
                        .font(.system(size: 10, weight: .semibold))
                    Text(diagText.isEmpty ? "诊断加载中…" : diagText)
                        .font(.system(size: 9, design: .monospaced))
                        .lineLimit(4)
                        .truncationMode(.head)
                }
                .foregroundColor(.white)
                .padding(.horizontal, 8)
                .padding(.vertical, 6)
                .background(Color.black.opacity(0.65), in: RoundedRectangle(cornerRadius: 6))
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.leading, 12)
                .padding(.bottom, 40)
                .allowsHitTesting(false)
                .onTapGesture {
                    withAnimation(.easeOut(duration: 0.15)) { showDiag = false }
                }
            }
        }
        .statusBarHidden(true)
        .preferredColorScheme(.dark)
        .background(Color.black)
        .landscapeFullScreen()
        // 全屏播放页自定义背景：已设置时先弹确认（选图更换/清除），未设置直接打开相册
        .confirmationDialog("全屏播放背景", isPresented: $showBackgroundPicker, titleVisibility: .visible) {
            Button("从相册选择图片") { showBackgroundGallery = true }
            if PlayerBackgroundService.shared.hasCustom {
                Button("清除自定义背景", role: .destructive) {
                    PlayerBackgroundService.shared.clear()
                    // 轻量恢复：只通知网页恢复主题背景层（不重推 song，避免 Folia UI 重置）；
                    // 当前歌已有 AI 配色则一并换肤
                    FoliaWebSync.pushBackground(to: FoliaWebViewRef.shared.webView)
                    let pm = PlayerManager.shared
                    if pm.currentIndex >= 0, pm.currentIndex < pm.queue.count,
                       let theme = AIThemeService.shared.cachedTheme(for: pm.queue[pm.currentIndex].id) {
                        FoliaWebSync.pushTheme(theme: theme, to: FoliaWebViewRef.shared.webView)
                    }
                }
            }
            Button("取消", role: .cancel) {}
        }
        .sheet(isPresented: $showBackgroundGallery) {
            ImagePicker { data in
                if let img = UIImage(data: data) {
                    PlayerBackgroundService.shared.save(img)
                    // 用户明确选图 → 自定义背景优先（即使之前在 Folia 模式显示态）
                    FoliaBgModeRef.shared.customBgActive = true
                    FoliaWebSync.pushBackground(to: FoliaWebViewRef.shared.webView)
                }
            }
            .ignoresSafeArea()
        }
        .onAppear {
            LogReporter.shared.log(level: "info", tag: "folia", message: "open")
            enteredIndex = player.currentIndex
            FoliaLocalServer.shared.startIfNeeded()
            // 注册沉浸处理器：网页侧切换沉浸 → 同步顶栏（此时 View 已初始化，可安全捕获）。
            // WKScriptMessageHandler 回调线程不保证主线程，@State 写入必须切主线程，
            // 否则后台线程改 @State → SwiftUI 后台重建视图树 → Representable update
            // 触发 UIKit 主线程断言（"Call must be made on main thread" 崩溃）。
            FoliaImmersiveRef.shared.onVisibleChange = { visible in
                DispatchQueue.main.async {
                    withAnimation(.easeOut(duration: 0.2)) { chromeVisible = visible }
                }
            }
            // 进入页面时应用已保存的全屏自定义背景（若有）；
            // 每次进入全屏页默认自定义背景优先（用户上次切过的模式显示态不跨页面残留）
            FoliaBgModeRef.shared.customBgActive = true
            FoliaWebSync.pushBackground(to: FoliaWebViewRef.shared.webView)
            // 注册诊断回调：网页侧 patch 状态 → 浮层
            FoliaDiagRef.shared.onUpdate = { s in
                DispatchQueue.main.async {
                    diagText = s
                }
            }
            // 进入后 2.5s 存活探针：若页面已空白/进程已死（无 didFail 回调），自动重载恢复
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) {
                FoliaWebSync.probeAliveAndRecover(FoliaWebViewRef.shared.webView)
            }
        }
        .onDisappear {
            LogReporter.shared.log(level: "info", tag: "folia", message: "close")
            FullScreenOrientation.shared.isFullScreen = false
            FoliaWebSync.resetReady()
            FoliaImmersiveRef.shared.onVisibleChange = nil
            FoliaDiagRef.shared.onUpdate = nil
        }
        // 播放进度 → time 消息（限流 ~10Hz，P0-6：0.25s→0.1s 逐字跟手度提升；真机耗电异常可回退 0.15s）
        .onReceive(player.$currentTime) { _ in
            guard FoliaWebSync.webReady else { return }
            let now = ProcessInfo.processInfo.systemUptime
            guard now - lastTimePush >= 0.1 else { return }
            lastTimePush = now
            FoliaWebSync.pushTime(to: FoliaWebViewRef.shared.webView)
        }
        // 播放状态 → time 消息 state 字段
        .onReceive(player.$isPlaying) { _ in
            FoliaWebSync.pushState(to: FoliaWebViewRef.shared.webView)
        }
        // 队列内切歌 → 重新注入 song
        .onReceive(player.$currentIndex) { idx in
            guard idx != enteredIndex else { return }
            enteredIndex = idx
            // ★ 切歌立即推送背景让位（不等 pushSong 的 fetchLyric 网络完成）：
            //   pushSong 内部 await API.fetchLyric（无超时保护），慢/挂起时若只靠
            //   pushSong 末尾的 pushBackground，切歌瞬间会出现 AI/封面主题背景层
            //   重新覆盖自定义图的可视窗口。这里先行让位，杜绝窗口。
            FoliaWebSync.pushBackground(to: FoliaWebViewRef.shared.webView)
            Task { @MainActor in
                await FoliaWebSync.pushSong(fallback: track, to: FoliaWebViewRef.shared.webView)
            }
        }
        // AI 配色**新生成**完成 → 轻量 theme 消息换肤（不重推 song）。
        // 场景：进入全屏页/切歌时缓存未命中，pushSong 已用封面主色兜底推送；
        // 后台 fetchTheme（PlayerManager 切歌自动触发）生成成功后这里换成 AI 配色。
        // ★ 不重推 song 的原因：song 消息会重置 Folia 播放器 UI（React 全量重渲染），
        //   竞态下主题背景层残留（AI 情绪背景仍显示）+ 控件状态被重置（按钮消失）。
        //   theme 消息只改舞台背景层颜色，不动歌词/控件/播放状态。
        // 仅当生成结果属于当前播放曲目时才换肤，避免切歌竞态误换主题。
        .onReceive(AIThemeService.shared.$lastGeneratedTrackId) { trackId in
            guard let trackId else { return }
            let pm = PlayerManager.shared
            let playing = pm.currentIndex >= 0 && pm.currentIndex < pm.queue.count
                ? pm.queue[pm.currentIndex] : nil
            guard let playing, playing.id == trackId else { return }
            guard let theme = AIThemeService.shared.cachedTheme(for: trackId) else { return }
            // 有自定义背景图时自定义图最优先（背景层保持透明让位），无需换肤
            guard !PlayerBackgroundService.shared.hasCustom else { return }
            // 封面取色优先（2026-11-03 还原）：有封面的曲目保持封面主色，不被 AI 配色覆盖
            guard playing.coverURL == nil else { return }
            FoliaWebSync.pushTheme(theme: theme, to: FoliaWebViewRef.shared.webView)
        }
    }
}

// MARK: - WKWebView 容器

/// 弱引用当前 webView，供 bridge 回调（init 阶段配置，无法访问 @State）使用。
final class FoliaWebViewRef {
    static let shared = FoliaWebViewRef()
    weak var webView: WKWebView?
    private init() {}
}

/// 沉浸状态处理器中转：bridge.onImmersive 在 init 阶段配置（无法捕获 self），
/// 先转发到全局；FullScreenWebLyricView 在 onAppear 注册真实处理器（操作 @State chromeVisible），
/// onDisappear 注销，避免跨页面残留。
final class FoliaImmersiveRef {
    static let shared = FoliaImmersiveRef()
    var onVisibleChange: ((Bool) -> Void)?
    private init() {}
}

/// 真机诊断状态箱：onReady 后 evaluateJavaScript 拉取 patch 内部状态（hook/bgActive/
/// 压制层），经 onUpdate 转发到 View 的诊断浮层。反复被真机否决后加入，
/// 用于区分「patch 未执行 / hook 失败 / 消息丢失 / 压制不完整」四类失效。
final class FoliaDiagRef {
    static let shared = FoliaDiagRef()
    var text: String = ""
    var onUpdate: ((String) -> Void)?
    private init() {}
}

/// 自定义背景 / Folia 模式背景显示态：true = 自定义背景优先（patch 压制开启，
/// 硬目标）；false = 用户主动切过 Folia 模式，解除压制让模式背景显示。
/// onModeChange 在 init 阶段设置（闭包无法捕获 @State），经状态箱中转；
/// View 在 onAppear 重置为 true（每次进入全屏默认自定义背景优先）。
final class FoliaBgModeRef {
    static let shared = FoliaBgModeRef()
    var customBgActive = true
    private init() {}
}

struct FoliaWebContainer: UIViewRepresentable {
    let bridge: WebLyricBridge
    let onLoadFailed: () -> Void
    /// 自动重试成功后复位失败态（让错误页消失）
    let onLoadRecovered: () -> Void

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.userContentController.add(bridge, name: "folia")
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.isOpaque = false
        webView.backgroundColor = .clear
        webView.scrollView.backgroundColor = .clear
        webView.scrollView.isScrollEnabled = false
        webView.scrollView.bounces = false
        webView.navigationDelegate = context.coordinator
        FoliaWebViewRef.shared.webView = webView

        // 确保本地服务器就绪后再加载（startIfNeeded 同步等待 ready）
        FoliaLocalServer.shared.startIfNeeded()
        // URL 已带 ?v=127 版本参数强制绕过 WKWebView 持久化缓存（旧版 folia-patch.js 残留）。
        let url = URL(string: "host.html?v=136", relativeTo: FoliaLocalServer.shared.baseURL)!
        webView.load(URLRequest(url: url))
        return webView
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {
        LogReporter.shared.checkMain("Folia.updateUIView")
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(onLoadFailed: onLoadFailed, onLoadRecovered: onLoadRecovered)
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        let onLoadFailed: () -> Void
        let onLoadRecovered: () -> Void
        /// 连续重试计数：失败后自动重连，避免瞬时失败直接展示错误页
        private var retryCount = 0
        private let maxRetries = 3
        init(onLoadFailed: @escaping () -> Void, onLoadRecovered: @escaping () -> Void) {
            self.onLoadFailed = onLoadFailed
            self.onLoadRecovered = onLoadRecovered
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            // 本地服务器启动/端口就绪存在瞬时窗口，先自动重试几次再展示失败页
            // WKNavigationDelegate 回调线程不保证主线程——闭包捕获的 @State（loadFailed）
            // 必须在主线程修改，否则 SwiftUI 在后台线程更新 representable →
            // "Call must be made on main thread" 崩溃。
            DispatchQueue.main.async { [weak self, weak webView] in
                guard let self else { return }
                guard self.retryCount < self.maxRetries else {
                    self.onLoadFailed()
                    return
                }
                self.retryCount += 1
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [weak webView] in
                    guard let webView else { return }
                    // 重试前确保服务器已就绪（首次失败多为 listener 未 ready / 端口未取到）
                    FoliaLocalServer.shared.restart()
                    let url = URL(string: "host.html?v=136", relativeTo: FoliaLocalServer.shared.baseURL)!
                    webView.load(URLRequest(url: url))
                }
            }
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            DispatchQueue.main.async { [weak self] in
                self?.retryCount = 0
                self?.onLoadRecovered()
            }
        }

        /// WebContent 进程被系统杀掉（重负载 Folia 模式 WebGL/内存峰值、iOS 内存回收）：
        /// WKWebView 会留下一个空白页且**不会**走 didFail/didFinish，表现为
        /// 「切模式后模式条、歌词、歌曲信息全部消失」。这里自动重载 host.html，
        /// ready 后 bridge.onReady 会重新注入 song/background/time，用户无感恢复。
        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
            DispatchQueue.main.async { [weak self, weak webView] in
                guard let self, let webView else { return }
                LogReporter.shared.log(level: "error", tag: "folia",
                                       message: "webContentProcessDidTerminate -> reload host")
                FoliaWebSync.resetReady()
                self.retryCount += 1
                guard self.retryCount <= self.maxRetries else {
                    self.onLoadFailed()
                    return
                }
                let url = URL(string: "host.html?v=136", relativeTo: FoliaLocalServer.shared.baseURL)!
                webView.load(URLRequest(url: url))
            }
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            // 主文档加载成功但子资源失败时，也走自动重试（同一通道）
            DispatchQueue.main.async { [weak self, weak webView] in
                guard let self else { return }
                guard self.retryCount < self.maxRetries else {
                    self.onLoadFailed()
                    return
                }
                self.retryCount += 1
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [weak webView] in
                    guard let webView else { return }
                    FoliaLocalServer.shared.restart()
                    let url = URL(string: "host.html?v=136", relativeTo: FoliaLocalServer.shared.baseURL)!
                    webView.load(URLRequest(url: url))
                }
            }
        }
    }
}

// MARK: - 推送逻辑（Native → Web）

/// 向 Folia 网页渲染层推送消息的全部封装（桥接协议与 host/bridge.ts 一一对应）。
enum FoliaWebSync {
    /// 网页渲染层是否已就绪（收到 ready 消息）
    static private(set) var webReady = false
    static func markReady() { webReady = true }
    static func resetReady() { webReady = false }

    /// 页面存活探针（切模式后调用）：WebContent 进程被杀时 evaluateJavaScript 会报错，
    /// 但 WKWebView 不会走 didFail/didFinish，界面就停在空白页（模式条/歌词全消失）。
    /// 探测失败即重置 ready 并重载 host.html——ready 后 bridge.onReady 会重推 song/背景/时间。
    @MainActor
    static func probeAliveAndRecover(_ webView: WKWebView?) {
        guard let webView else { return }
        webView.evaluateJavaScript("window.__foliaPatchLoaded === true") { result, error in
            if error == nil, (result as? Bool) == true { return }
            LogReporter.shared.log(
                level: "error", tag: "folia",
                message: "probe failed after mode change -> reload host (err=\(error?.localizedDescription ?? "nil"))")
            resetReady()
            let url = URL(string: "host.html?v=136", relativeTo: FoliaLocalServer.shared.baseURL)!
            webView.load(URLRequest(url: url))
        }
    }

    /// 切模式后重跑补丁：重放背景让位/主题、重新定位控件条、强制退出沉浸并显示控件条/模式条。
    /// 兜底「选模式后模式条与歌曲一起消失」——即使某条 DOM 路径把控件隐藏了，这里也会拉回来。
    @MainActor
    static func resyncPatch(to webView: WKWebView?) {
        guard let webView else { return }
        webView.evaluateJavaScript(
            "window.__foliaPatch && window.__foliaPatch.resync && window.__foliaPatch.resync()",
            completionHandler: nil)
    }

    /// song：注入歌曲/歌词/主题（进入全貌版 + 切歌时调用）
    @MainActor
    static func pushSong(fallback: Track, to webView: WKWebView?) async {
        guard let webView else { return }
        let pm = PlayerManager.shared
        let track: Track = {
            if pm.currentIndex >= 0, pm.currentIndex < pm.queue.count {
                return pm.queue[pm.currentIndex]
            }
            return fallback
        }()

        // 歌词：API.fetchLyric 自带内存缓存（切回同一首歌秒出，不重复请求）。
        // ★ 格式适配：后端汽水采集输出 KRC 逐字（[mm:ss.xx]<起始ms,持续ms>字...），
        //   iOS 原生 parseLyrics 自动识别 KRC+LRC 解析为结构化行（KRCLine 含逐字 words）。
        //   这里把结构化行统一序列化为 Folia 的 enhanced-lrc 逐字格式（[mm:ss.xx]<mm:ss.xx>字...），
        //   Folia 渲染层按逐字时间驱动卡拉OK高亮，歌词跟人声逐字走；
        //   非逐字整句行不带标签，Folia 自动按行时长均分逐字。
        let rawLyric = (try? await API.fetchLyric(track.id)) ?? ""
        let lyric = foliaEnhancedLRC(from: parseLyrics(rawLyric),
                                     title: track.title,
                                     artist: track.artist ?? "")

        // 主题优先级（2026-11-03 按用户要求还原封面取色优先）：
        // 封面主色 → AI 生成配色（仅无封面/取色失败时兜底）→ 网页默认深色。
        var theme: [String: String] = [:]
        if let cover = track.coverURL {
            let color = await CoverPalette.color(for: track.id, from: cover)
            theme = themeDict(from: color)
        } else if let ai = AIThemeService.shared.cachedTheme(for: track.id) {
            theme = foliaThemeDict(from: ai)
        }

        FoliaWebPusher.push(to: webView, [
            "type": "song",
            "payload": [
                "id": track.id,
                "title": track.title,
                "artist": track.artist ?? "",
                "cover": track.coverURL?.absoluteString ?? "",
                "duration": pm.duration,
                "lyric": lyric,
                "theme": theme,
            ],
        ])
        // 歌曲注入后立即同步一次时间（网页端 currentTime 复位 0）
        await MainActor.run { pushTime(to: webView) }
        // 自定义背景随后推送（切歌后 React 舞台重渲染可能清掉背景层，重推保证恢复）
        await MainActor.run { pushBackground(to: webView) }
    }

    /// background：通知 Folia 网页开关主题背景透明让位。
    /// 背景图本身由原生层（SwiftUI Image）直接渲染；网页仅需在存在自定义背景时
    /// 透明化主题背景层让原生图透出。payload.image 非空 = 开启让位，空串 = 恢复。
    /// - Parameter active：显式指定让位开关；缺省（nil）跟随 FoliaBgModeRef 状态箱
    ///   （用户最后选择：自定义背景优先 / Folia 模式优先）。onModeChange 与背景按钮
    ///   显式传值切换显示态；切歌/onReady 等场合缺省即可保持一致。
    @MainActor
    static func pushBackground(active: Bool? = nil, to webView: WKWebView?) {
        guard let webView else { return }
        let on = active ?? FoliaBgModeRef.shared.customBgActive
        FoliaWebPusher.push(to: webView, [
            "type": "background",
            "payload": ["image": on && PlayerBackgroundService.shared.hasCustom ? "1" : ""],
        ])
    }

    /// theme：AI 配色生成完成后的轻量换肤——只改舞台主题背景层颜色。
    /// 不重推 song（song 会重置 Folia 播放器 UI：React 全量重渲染 → 背景层残留竞态
    /// + 控件状态重置），theme 消息由 folia-patch.js 直接改背景层 DOM，不动歌词/控件。
    /// 仅在无自定义背景图时调用（有自定义图时背景层保持透明让位，自定义图最优先）。
    @MainActor
    static func pushTheme(theme: PlayerTheme, to webView: WKWebView?) {
        guard let webView else { return }
        FoliaWebPusher.push(to: webView, [
            "type": "theme",
            "payload": [
                "backgroundColor": theme.backgroundColor,
                "primaryColor": theme.primaryColor,
                "accentColor": theme.accentColor,
                "secondaryColor": theme.secondaryColor,
            ],
        ])
    }

    /// time：播放进度 + 播放状态（主线程，evaluateJavaScript 必须在主线程）
    @MainActor
    static func pushTime(to webView: WKWebView?) {
        guard let webView else { return }
        let pm = PlayerManager.shared
        FoliaWebPusher.push(to: webView, [
            "type": "time",
            "payload": [
                "t": pm.currentTime,
                "state": pm.isPlaying ? "playing" : "paused",
            ],
        ])
    }

    /// 播放状态单独推送（isPlaying 变化时）
    @MainActor
    static func pushState(to webView: WKWebView?) {
        pushTime(to: webView)
    }

    /// 原生模式恢复：通知网页切换模式（可选，默认网页记忆自己的模式）
    @MainActor
    static func pushMode(to webView: WKWebView?, mode: String) {
        guard let webView else { return }
        FoliaWebPusher.push(to: webView, [
            "type": "mode",
            "payload": ["mode": mode],
        ])
    }

    // MARK: - 主题映射

    /// AI 配色主题（PlayerTheme，与 Folia Theme 四色同构）→ Folia Theme 字典。
    /// 直接映射四色，无需压暗——AI 生成的背景色本身即为适合舞台的深色情绪配色。
    private static func foliaThemeDict(from theme: PlayerTheme) -> [String: String] {
        [
            "backgroundColor": theme.backgroundColor,
            "primaryColor": theme.primaryColor,
            "accentColor": theme.accentColor,
            "secondaryColor": theme.secondaryColor,
        ]
    }

    /// 封面色 → Folia Theme 字典：accent=封面主色，background=主色压暗(~28%)，primary=白
    private static func themeDict(from color: UIColor) -> [String: String] {
        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        color.getRed(&r, green: &g, blue: &b, alpha: &a)
        func hex(_ v: CGFloat) -> Int { Int(min(max(v * 255, 0), 255)) }
        let acc = String(format: "#%02X%02X%02X", hex(r), hex(g), hex(b))
        let bg = String(format: "#%02X%02X%02X", hex(r * 0.28), hex(g * 0.28), hex(b * 0.28))
        return [
            "backgroundColor": bg,
            "primaryColor": "#f5f5f7",
            "accentColor": acc,
            "secondaryColor": "#c8ccd8",
        ]
    }

    // MARK: - 歌词格式适配

    /// 结构化歌词行 → Folia enhanced-lrc 逐字格式（卡拉OK逐字高亮）。
    ///
    /// Folia 解析入口（stageLayers av）按 format 分派，host 已改为 enhanced-lrc：
    /// `[mm:ss.xx]<mm:ss.xx>字<mm:ss.xx>字...`（逐字标签为绝对时间，字的结束=下一字标签/行结束）。
    /// 解析结果 words 带独立 startTime，渲染层按逐字时间推进卡拉OK高亮，歌词跟人声逐字走；
    /// 无逐字标签的整句行 Folia 自动按行时长均分逐字。
    ///
    /// 转换规则：
    /// - 行时间戳：mm:ss.xx（百分秒，与标准 LRC 一致）
    /// - 逐字行（isKaraoke 且有逐字 words）：`<mm:ss.xx>` 绝对开始时间依次拼接
    /// - 整句行（标准 LRC/元信息行）：直接输出 `[mm:ss.xx]整句文本`
    /// - 元信息行过滤：标题/作者/作词/作曲/…（避免污染歌词区）
    static func foliaEnhancedLRC(from lines: [KRCLine], title: String, artist: String) -> String {
        func stamp(_ t: Double) -> String {
            let mm = Int(t) / 60
            let ss = Int(t) % 60
            let cs = Int((t - Double(Int(t))) * 100)   // 百分秒（与 Folia mm:ss.xx 兼容）
            return String(format: "%02d:%02d.%02d", mm, ss, cs)
        }
        var out: [String] = []
        out.reserveCapacity(lines.count)
        for line in lines {
            let t = line.start
            guard t >= 0 else { continue }
            let text = line.words.map(\.text).joined()
            guard !text.isEmpty else { continue }
            // 标题/作者行（[ti:]/[ar:] 已被 parseLyrics 转成纯文本，直接与歌曲元数据比对）
            if text == title || (!artist.isEmpty && text == artist) { continue }
            // 兜底：形如 "[ti:xxx]" / "[ar:xxx]" 的标签行（行时间戳后跟标签时 parseLyrics 保留原文）
            if text.hasPrefix("[") && text.hasSuffix("]") {
                let inner = String(text.dropFirst().dropLast())
                if let colon = inner.firstIndex(of: ":"),
                   inner[..<colon].allSatisfy({ $0.isLetter }) {
                    continue
                }
            }
            // 元信息内容行：作词/作曲/演唱/…（非唱词，不输出）
            let metaKeywords = ["作词", "作曲", "编曲", "演唱", "原唱", "翻唱", "制作人",
                                "录音", "混音", "母带", "和声", "监制", "企划", "发行", "出品",
                                "专辑", "版权", "词曲", "翻译"]
            if metaKeywords.contains(where: { text.contains($0) }) { continue }

            var body = ""
            if line.isKaraoke, line.words.count > 1 {
                for word in line.words {
                    body += "<\(stamp(word.start))>\(word.text)"
                }
            } else {
                body = text // 整句行：Folia 自动按行时长均分逐字
            }
            out.append("[\(stamp(t))]\(body)")
        }
        return out.joined(separator: "\n")
    }
}

// MARK: - 网页侧模式持久化

/// 全貌版模式（Folia 14 模式）独立于原生 9 模式存储，互不干扰。
enum WebLyricModeStore {
    private static let key = "folia_web_lyric_mode"
    static var current: String {
        get { UserDefaults.standard.string(forKey: key) ?? "classic" }
        set { UserDefaults.standard.set(newValue, forKey: key) }
    }
}
