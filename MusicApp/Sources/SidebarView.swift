import SwiftUI
import UIKit

// MARK: - 播放页左侧抽屉侧边栏（参照汽水音乐风格）

/// 播放页右滑 / 点左上角音符打开的侧边栏：
/// 顶部品牌区（Logo + 登录态）→「听歌模式」三个模式大按钮 →「场景音乐」网格 → 底部版本。
/// 背景跟随当前歌曲封面主色（比播放页更暗一档），深色极简，无阴影渐变，与 App 整体主题一致。
struct SidebarView: View {
    @EnvironmentObject private var player: PlayerManager
    @EnvironmentObject private var auth: AuthManager
    /// 预加载器：启动页阶段已并行拉取「我的作品」总数（worksTotal），侧滑栏据此显示/隐藏"我的作品"模式
    @ObservedObject private var preloader = AppPreloader.shared
    /// 点遮罩 / 左滑关闭
    var onClose: () -> Void

    /// 场景音乐拉取中防重复点击（显示 spinner 的卡片标题）
    @State private var sceneLoading: String?

    /// 侧边栏宽度（约屏宽 85%）
    private let panelWidth = UIScreen.main.bounds.width * 0.85

    /// 全机型适配：侧边栏整体延伸到状态栏/Home 条（ignoresSafeArea），
    /// 顶部品牌区与底部版本信息必须按真实安全区避让——
    /// 状态栏高度随机型变化（iPhone X 44 / 灵动岛 59），写死数值会遮挡或下沉。
    private static var windowTopInset: CGFloat {
        (UIApplication.shared.connectedScenes.first { $0 is UIWindowScene } as? UIWindowScene)?
            .windows.first(where: { $0.isKeyWindow })?
            .safeAreaInsets.top ?? 0
    }
    private static var windowBottomInset: CGFloat {
        (UIApplication.shared.connectedScenes.first { $0 is UIWindowScene } as? UIWindowScene)?
            .windows.first(where: { $0.isKeyWindow })?
            .safeAreaInsets.bottom ?? 0
    }

    var body: some View {
        HStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 0) {
                // 整个侧边栏内容一起滚动（品牌区 + 模式按钮 + 场景网格）：
                // 内容超出面板高度时可整体上滑查看，不再只有网格单独滚动
                ScrollView(showsIndicators: false) {
                    VStack(alignment: .leading, spacing: 0) {
                        brandHeader
                        sectionTitle("听歌模式")
                        modeButtons
                        // 场景音乐标题与「新鲜模式」按钮拉开间距，避免紧贴
                        sectionTitle("场景音乐", topPadding: 18)
                        grid
                    }
                    // 滚动到底部留白：真实底部安全区 + 呼吸余量（无 Home 条机型 0）
                    .padding(.bottom, max(Self.windowBottomInset, 0) + 18)
                }
            }
            .frame(width: panelWidth)
            .background(panelBackground)

            // 右侧填充，把遮罩露出来
            Color.clear
        }
        .ignoresSafeArea()
        // 开合手势统一由 MainTabView 的全局跟手手势控制（拖动侧边栏区域同样跟手），
        // 这里不再挂独立手势，避免与全局手势冲突导致卡顿
    }

    // MARK: - 背景色：跟随当前歌曲封面主色（比播放页再暗一档）

    /// 纯色背景（2026-11-05 用户口径：不要渐变，纯色；与播放页同色源，只是再暗一档）。
    /// 色源优先级与播放页保持一致：封面主色 → AI 配色 → 深灰兜底；
    /// 得到基色后整体压暗到 0.72（比播放页暗一档，仍能看出同一色相）。
    private var panelBackground: Color {
        if let ui = panelBaseColor {
            var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
            ui.getRed(&r, green: &g, blue: &b, alpha: &a)
            let factor: CGFloat = 0.72
            return Color(red: r * factor, green: g * factor, blue: b * factor)
        }
        return Color(red: 0.09, green: 0.09, blue: 0.10)
    }

    /// 播放页同款基色：封面提取主色优先，无封面时用 AI 生成配色（与 VerticalPager 一致）
    private var panelBaseColor: UIColor? {
        if let d = player.playerCardDominant { return d }
        if let theme = AIThemeService.shared.currentTheme { return UIColor(theme.bgColor) }
        return nil
    }

    // MARK: - 顶部品牌区

    private var brandHeader: some View {
        HStack(spacing: 12) {
            Image("AppLogo")
                .resizable()
                .frame(width: 40, height: 40)
                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))

            VStack(alignment: .leading, spacing: 3) {
                Text("流萤音乐")
                    .font(.system(size: 20, weight: .heavy))
                    .foregroundColor(.white)
                Text(subtitle)
                    .font(.system(size: 12))
                    .foregroundColor(.white.opacity(0.5))
            }

            Spacer(minLength: 0)
        }
        .padding(.horizontal, 20)
        // 顶部按真实状态栏高度避让（灵动岛 59 / 刘海 44 / 无刘海 20-24），
        // 品牌区在任何机型都不被状态栏/灵动岛遮挡
        .padding(.top, max(Self.windowTopInset, 0) + 12)
        .padding(.bottom, 26)
    }

    private var subtitle: String {
        if let u = auth.currentUser {
            if let sig = u.signature, !sig.isEmpty { return sig }
            return u.name.isEmpty ? "已登录" : u.name
        }
        return "登录后推荐更精准"
    }

    // MARK: - 分区小标题

    private func sectionTitle(_ title: String, topPadding: CGFloat = 0) -> some View {
        Text(title)
            .font(.system(size: 13, weight: .semibold))
            .foregroundColor(.white.opacity(0.45))
            .padding(.horizontal, 22)
            .padding(.top, topPadding)
            .padding(.bottom, 10)
    }

    // MARK: - 三个模式大按钮

    private var modeButtons: some View {
        VStack(spacing: 10) {
            // 无上传作品的用户不显示「我的作品」模式（预加载阶段 worksTotal 已就绪；上传后 MineView 同步刷新实时出现）
            ForEach(ListenMode.allCases.filter { $0 != .works || preloader.worksTotal > 0 }, id: \.self) { mode in
                SidebarModeButton(
                    title: mode.rawValue,
                    icon: mode.icon,
                    // 选中态 = 当前实际听歌模式（listenMode），而非仅侧滑栏主动选择：
                    // ①首次打开默认选中「默认模式」（listenMode 初始 .normal）
                    // ②发现页点歌 → 推荐流 listenMode=.normal → 选中「默认模式」
                    // ③我的页点作品/喜欢 → listenMode=.works/.liked → 选中对应模式
                    // ④侧滑栏主动切换 → applyListenMode 同步 listenMode → 即时生效
                    // ⑤正在播放场景音乐时不高亮任何听歌模式（场景不是听歌模式）
                    selected: player.listenMode == mode && player.sceneTag == nil
                ) {
                    Haptics.selectionChanged()   // P0-5：模式切换轻选择反馈
                    withAnimation(.easeInOut(duration: 0.2)) {
                        player.applyListenMode(mode)
                    }
                    onClose()
                }
            }
        }
        .padding(.horizontal, 20)
    }

    // MARK: - 场景音乐网格

    /// 场景卡片点击：拉该场景的个性化分类歌单（类型严格 + 按用户数据排序），
    /// 取第一首直接播放、整单入队（切歌在队列内连续放同类型歌），随后收起侧边栏。
    private func playScene(_ tag: String) {
        guard sceneLoading == nil else { return }   // 防重复点击
        Haptics.selectionChanged()
        sceneLoading = tag
        Task {
            defer { sceneLoading = nil }
            do {
                // 每次点场景生成新 seed：后端按 seed 做分档窗口洗牌，
                // 所以每次拉到的顺序都不一样（同一次播放会话内队列固定，不会中途跳变）
                let seed = "\(Int(Date().timeIntervalSince1970 * 1000))-\(UUID().uuidString.prefix(8))"
                let tracks = try await API.radio(tag: tag, seed: seed)
                guard let first = tracks.first else { return }
                // 走场景专用入口：保存当前模式会话、场景上下文置位（侧滑栏高亮场景卡、
                // 不高亮听歌模式）、有限列表循环、清搜索流，避免污染模式会话/续拉推荐
                player.playScene(tag, track: first, in: tracks)
                onClose()
            } catch {
                // 拉取失败保持现状（可能弱网）；不做打断式弹窗，下一次点击重试即可
                LogReporter.shared.log(level: "info", tag: "radio", message: "scene fail \(tag) \(error.localizedDescription)")
            }
        }
    }

    /// 每日推荐（2026-11-05）：后端按 (用户, 日期) 固定生成 30 首，当天多次进入结果一致、隔天换一批。
    /// 播放走场景专用入口（有限列表循环 + 侧栏高亮），与场景卡行为一致。
    private func playDaily() {
        guard sceneLoading == nil else { return }
        Haptics.selectionChanged()
        sceneLoading = "每日推荐"
        Task {
            defer { sceneLoading = nil }
            do {
                let tracks = try await API.daily(30)
                guard let first = tracks.first else { return }
                player.playScene("每日推荐", track: first, in: tracks)
                onClose()
            } catch {
                LogReporter.shared.log(level: "info", tag: "daily",
                                       message: "daily fail \(error.localizedDescription)")
            }
        }
    }

    private var grid: some View {
        // 外层 ScrollView 已承载整体滚动，这里直接用网格（不再内嵌 ScrollView）
        LazyVGrid(
            columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 3),
            spacing: 22
        ) {
            gridItem(icon: "sparkles", title: "每日推荐") { playDaily() }
            gridItem(icon: "bus", title: "通勤必听") { playScene("通勤必听") }
            gridItem(icon: "car", title: "驾车") { playScene("驾车") }
            gridItem(icon: "sun.max", title: "起床") { playScene("起床") }
            gridItem(icon: "moon.zzz", title: "深夜 EMO") { playScene("深夜 EMO") }
            gridItem(icon: "hifispeaker", title: "DJ 模式") { playScene("DJ 模式") }
            gridItem(icon: "moon.stars", title: "助眠模式") { playScene("助眠模式") }
            gridItem(icon: "figure.run", title: "动感健身") { playScene("动感健身") }
            gridItem(icon: "music.note", title: "抖音漫游") { playScene("抖音漫游") }
            gridItem(icon: "drop.fill", title: "洗澡") { playScene("洗澡") }
            gridItem(icon: "leaf", title: "Chill 放松") { playScene("Chill 放松") }
            gridItem(icon: "face.smiling", title: "快乐时光") { playScene("快乐时光") }
            gridItem(icon: "music.mic", title: "电音") { playScene("电音") }
        }
        .padding(.horizontal, 16)
        .padding(.top, 2)
        .padding(.bottom, 8)
    }

    private func gridItem(icon: String, title: String, action: @escaping () -> Void) -> some View {
        let loading = sceneLoading == title
        let active = player.sceneTag == title
        return Button(action: action) {
            VStack(spacing: 9) {
                Group {
                    if loading {
                        ProgressView()
                            .tint(.white.opacity(0.92))
                    } else {
                        Image(systemName: icon)
                            .font(.system(size: 20, weight: .medium))
                            .foregroundColor(active ? Color(hex: 0x121214) : .white.opacity(0.92))
                    }
                }
                .frame(width: 46, height: 46)
                .background(
                    active ? Theme.accent : Color.white.opacity(0.08),
                    in: RoundedRectangle(cornerRadius: 14, style: .continuous)
                )
                Text(title)
                    .font(.system(size: 12, weight: active ? .semibold : .medium))
                    .foregroundColor(active ? Theme.accent : .white.opacity(0.75))
            }
            .frame(maxWidth: .infinity)
        }
        .buttonStyle(.plain)
    }
}

// MARK: - 模式大按钮

/// 选中态：薄荷绿图标底 + 整行淡绿底 + 右侧对勾；未选中：半透明白图标底 + 弱化整行底
private struct SidebarModeButton: View {
    let title: String
    let icon: String
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 14) {
                Image(systemName: icon)
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundColor(selected ? Color(hex: 0x121214) : .white.opacity(0.85))
                    .frame(width: 38, height: 38)
                    .background(
                        selected ? Theme.accent : Color.white.opacity(0.1),
                        in: RoundedRectangle(cornerRadius: 11, style: .continuous)
                    )

                Text(title)
                    .font(.system(size: 16, weight: selected ? .bold : .semibold))
                    .foregroundColor(.white)

                Spacer(minLength: 0)

                if selected {
                    Image(systemName: "checkmark")
                        .font(.system(size: 13, weight: .bold))
                        .foregroundColor(Theme.accent)
                }
            }
            .padding(.horizontal, 14)
            .frame(height: 58)
            .background(
                selected ? Theme.accent.opacity(0.16) : Color.white.opacity(0.05),
                in: RoundedRectangle(cornerRadius: 16, style: .continuous)
            )
        }
        .buttonStyle(.plain)
    }
}
