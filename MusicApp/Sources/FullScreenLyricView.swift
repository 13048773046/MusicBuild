import SwiftUI
import AVFoundation
import UIKit

/// 全屏沉浸式歌词（Folia 式 3D 版，横屏）
///
/// 视觉语言对齐 Folia 的 3D 歌词模式：
/// - 行间透视：歌词按「距当前句的句差」做 X 轴旋转 + 缩放 + 透明度衰减，
///   当前句正面居中，前后句向后上方/下方翻折，形成空间纵深；
/// - 视角转动（相机）：整个歌词场景随时间做缓慢的 Y/X 轴正弦微旋转（perspective），
///   背景粒子层以不同速度漂移形成视差，模拟镜头缓移；
/// - 逐字卡拉OK高亮保留：当前句按毫秒级字时间戳从左到右填充。
///
/// 说明：WebGL 粒子/滤镜级效果无法在 SwiftUI 像素级复刻，
/// 这里用 SwiftUI 3D 变换 + Canvas 粒子实现同款视觉语言，细节可继续迭代。
struct FullScreenLyricView: View {
    let track: Track
    let lines: [KRCLine]

    @EnvironmentObject private var player: PlayerManager
    @Environment(\.dismiss) private var dismiss

    /// 当前歌词展示模式（Folia 多模式，持久化记忆）
    @State private var selectedMode: LyricDisplayMode = LyricModeStore.current
    /// 模式选择面板
    @State private var showModePicker = false
    /// 「全貌版」Folia 网页版全屏歌词
    @State private var showWebLyric = false
    /// 沉浸模式：轻点歌词区隐藏/显示顶部歌名歌手与全部按钮；
    /// 隐藏后顶栏/底栏不占位，歌词区占满全屏（真全屏，无安全区留白）。
    @State private var chromeVisible = true

    /// 横屏安全区：刘海在左右两侧，顶部/底部为 0；所有控件避开刘海。
    /// 与播放页一致从 keyWindow 读取真实 inset（Environment safeAreaInsets 在部分
    /// Xcode/SDK 组合下 keyPath 推断失败，改用 UIKit 直读，iOS 16 全兼容）。
    private var safeArea: EdgeInsets {
        let i = (UIApplication.shared.connectedScenes.first { $0 is UIWindowScene } as? UIWindowScene)?
            .windows.first(where: { $0.isKeyWindow })?
            .safeAreaInsets ?? .zero
        return EdgeInsets(top: i.top, leading: i.left, bottom: i.bottom, trailing: i.right)
    }

    /// 背景：封面主色压暗（横屏深底白字沉浸）
    private var baseColor: Color {
        if let d = player.playerCardDominant {
            var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
            d.getRed(&r, green: &g, blue: &b, alpha: &a)
            return Color(red: r * 0.28, green: g * 0.28, blue: b * 0.28, opacity: a)
        }
        return Color(hex: 0x121214)
    }

    /// 前景色：素笺浅色背景用深墨，其余深色背景用白
    private var fg: Color {
        selectedMode == .paper ? Color(red: 0.16, green: 0.14, blue: 0.12) : .white
    }
    private var fgSoft: Color {
        selectedMode == .paper
            ? Color(red: 0.16, green: 0.14, blue: 0.12).opacity(0.58)
            : .white.opacity(0.65)
    }
    private var fgBtn: Color {
        selectedMode == .paper
            ? Color(red: 0.16, green: 0.14, blue: 0.12).opacity(0.92)
            : .white.opacity(0.9)
    }
    private var fgBtnBg: Color {
        selectedMode == .paper ? Color.black.opacity(0.10) : Color.white.opacity(0.14)
    }

    var body: some View {
        ZStack {
            // 背景：素笺为浅色纸张（铺满），其余为主色渐变 + 视差粒子
            if selectedMode == .paper {
                PaperBackground()
            } else {
                background
            }

            VStack(spacing: 0) {
                // 顶部：歌名/歌手（左）+ 模式切换 + 关闭（右）；沉浸模式下整体隐藏
                if chromeVisible {
                    HStack(alignment: .center, spacing: 12) {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(track.title)
                                .font(.system(size: 16, weight: .bold))
                                .foregroundColor(fg)
                                .lineLimit(1)
                            Text(track.artist.flatMap { $0.isEmpty ? nil : $0 } ?? "未知歌手")
                                .font(.system(size: 12))
                                .foregroundColor(fgSoft)
                                .lineLimit(1)
                        }
                        Spacer(minLength: 8)
                        // 歌词展示模式切换（Folia 多模式）
                        Button {
                            withAnimation(.easeOut(duration: 0.18)) { showModePicker = true }
                        } label: {
                            Image(systemName: "square.grid.2x2")
                                .font(.system(size: 15, weight: .semibold))
                                .foregroundColor(fgBtn)
                                .frame(width: 36, height: 36)
                                .background(fgBtnBg, in: Circle())
                        }
                        .buttonStyle(.plain)
                        Button {
                            // 退出全屏：只复位方向授权并关闭；竖屏转回由 fullScreenCover
                            // onDismiss 兜底执行（避免 setValue 与 dismiss 同帧竞态导致按钮无响应）。
                            FullScreenOrientation.shared.isFullScreen = false
                            dismiss()
                        } label: {
                            Image(systemName: "xmark")
                                .font(.system(size: 15, weight: .semibold))
                                .foregroundColor(fgBtn)
                                .frame(width: 36, height: 36)
                                .background(fgBtnBg, in: Circle())
                        }
                        .buttonStyle(.plain)
                    }
                    // 横屏适配：灵动岛/刘海在屏幕左右两侧（safeArea.leading/trailing 为真实安全区，
                    // 机型不同数值不同：iPhone X 44 / 灵动岛 59），
                    // 顶栏固定 24/20pt 不足以避开，必须叠加安全区，否则按钮会被灵动岛遮挡。
                    .padding(.leading, safeArea.leading + 24)
                    .padding(.trailing, safeArea.trailing + 20)
                    .padding(.top, 8)
                    // 确保顶栏永远在最上层、按钮命中区域明确
                    .zIndex(10)
                    .allowsHitTesting(true)
                    .transition(.move(edge: .top).combined(with: .opacity))
                }

                Spacer(minLength: chromeVisible ? 6 : 0)

                // 中央：按歌词展示模式渲染；轻点任意处切换沉浸（隐藏/显示歌名与全部按钮）。
                // 沉浸隐藏后顶栏/底栏不再占位，本区占满全屏 = 真全屏（无安全区留白）。
                ZStack {
                    if lines.isEmpty {
                        VStack(spacing: 12) {
                            Text(track.artist.flatMap { $0.isEmpty ? nil : $0 } ?? "未知歌手")
                                .font(.system(size: 16, weight: .medium))
                                .foregroundColor(.white.opacity(0.55))
                            Text("该歌曲暂无歌词")
                                .font(.system(size: 20, weight: .medium))
                                .foregroundColor(.white.opacity(0.45))
                        }
                    } else {
                        switch selectedMode {
                        case .tilt:
                            TiltLyricView(lines: lines,
                                          progress: player.progress * player.duration)
                        case .diorama:
                            DioramaLyricView(lines: lines,
                                             progress: player.progress * player.duration,
                                             seed: track.id)
                        case .partita:
                            PartitaLyricView(lines: lines,
                                             progress: player.progress * player.duration)
                        case .lumiere:
                            LumiereLyricView(lines: lines,
                                             progress: player.progress * player.duration)
                        case .pendolo:
                            PendoloLyricView(lines: lines,
                                             progress: player.progress * player.duration)
                        case .cadenza:
                            CadenzaLyricView(lines: lines,
                                             progress: player.progress * player.duration)
                        case .monet:
                            MonetLyricView(lines: lines,
                                           progress: player.progress * player.duration,
                                           coverURL: track.coverURL?.absoluteString,
                                           title: track.title)
                        case .cappella:
                            CappellaLyricView(lines: lines,
                                              progress: player.progress * player.duration)
                        case .paper:
                            PaperDioramaLyricView(lines: lines,
                                                  progress: player.progress * player.duration,
                                                  seed: track.id)
                        }
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                // 歌词允许溢出屏幕，前后句自然跑出边界形成纵深感（各模式内部已做防溢出钳制）
                // 行式歌词模式：左右避让横屏刘海/灵动岛（灵动岛在屏幕左右两侧），
                // 长歌词行不会进入刘海区域被遮挡；素笺（词墙已钳制屏内）保持全屏。
                .padding(.horizontal, selectedMode == .paper ? 0 : max(safeArea.leading, 0) + 14)
                .contentShape(Rectangle())
                .onTapGesture {
                    // 模式选择面板打开时不触发沉浸切换（面板 overlay 优先命中，天然拦截）
                    guard !showModePicker else { return }
                    withAnimation(.easeOut(duration: 0.2)) { chromeVisible.toggle() }
                }
                // 相机运镜仅由镜台（Diorama）模式内部承载，其余模式保持平正排版

                // 底部：时间 + 进度；沉浸模式下隐藏
                if chromeVisible {
                    VStack(spacing: 8) {
                        HStack {
                            Text(fmt(player.progress * player.duration))
                                .font(.system(size: 12, weight: .medium))
                                .foregroundColor(fgSoft)
                                .monospacedDigit()
                            Spacer()
                            Text(fmt(player.duration))
                                .font(.system(size: 12, weight: .medium))
                                .foregroundColor(fgSoft)
                                .monospacedDigit()
                        }
                        // 细进度条：支持左右拖动控制歌曲进度
                        GeometryReader { g in
                            let w = g.size.width
                            ZStack(alignment: .leading) {
                                Capsule().fill(fg.opacity(0.16))
                                Capsule().fill(fg.opacity(0.85))
                                    .frame(width: max(4, w * min(max(player.progress, 0), 1)))
                            }
                            .frame(height: 3)
                            // 整行 24pt 高度都是命中区，条身垂直居中
                            .frame(maxHeight: .infinity, alignment: .center)
                            .contentShape(Rectangle())
                            .gesture(
                                DragGesture(minimumDistance: 0)
                                    .onChanged { v in
                                        let ratio = min(max(v.location.x / max(w, 1), 0), 1)
                                        player.seek(toFraction: ratio)
                                    }
                            )
                        }
                        .frame(height: 24)
                    }
                    // 底部时间+进度条：同样按横屏左右安全区避让（刘海/灵动岛在左右两侧）
                    .padding(.leading, safeArea.leading + 26)
                    .padding(.trailing, safeArea.trailing + 26)
                    .padding(.bottom, max(safeArea.bottom, 0) + 16)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                }
            }
        }
        // 沿用播放页的沉浸式状态栏方式：背景铺满、内容在安全区内。
        // 隐藏状态栏：横屏状态栏文字（时间/电量）不再叠加在顶部歌词/控件上（"被状态栏挡住"根因）。
        .statusBarHidden(true)
        .preferredColorScheme(selectedMode == .paper ? .light : .dark)
        .background(selectedMode == .paper
                    ? Color(red: 0.90, green: 0.89, blue: 0.86) : Color.black)
        // 歌词展示模式切换浮层（页面内 overlay，不依赖 sheet）
        .overlay {
            if showModePicker {
                LyricModePickerOverlay(selection: $selectedMode,
                                       isPresented: $showModePicker,
                                       onEnterFullView: {
                    // ★ 2026-11-03：不要在同一次 SwiftUI 更新里「关闭浮层 + present 全屏 cover」。
                    //   叠加全屏与横屏方向强制后，iOS 16/17 上这种同事务转场会偶发
                    //   present 出一个**空白/透明 cover**（用户看到的"选了模式后模式条和歌都不见了"），
                    //   并在转场 layout 中触发 UIKit 主线程断言崩溃。
                    //   等浮层 dismiss 的转场真正结束（0.35s）后再 present。
                    LogReporter.shared.log(level: "info", tag: "folia", message: "enter fullview requested")
                    let trackId = track.id
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) {
                        LogReporter.shared.log(level: "info", tag: "folia",
                                               message: "present fullview track=\(trackId)")
                        showWebLyric = true
                    }
                })
            }
        }
        // 全貌版：Folia 网页版全屏歌词（原生状态保持，退出后回到原生模式）
        .fullScreenCover(isPresented: $showWebLyric) {
            FullScreenWebLyricView(track: track)
                .environmentObject(player)
        }
        .animation(.easeOut(duration: 0.18), value: showModePicker)
        // 兜底：无论以何种方式退出全屏，都复位全局横屏授权（AppDelegate 恢复竖屏）
        .onDisappear {
            FullScreenOrientation.shared.isFullScreen = false
        }
    }

    // MARK: - 背景层：主色渐变 + 双层视差粒子

    private var background: some View {
        ZStack {
            LinearGradient(
                colors: [baseColor, baseColor.opacity(0.8), Color.black.opacity(0.6)],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            .ignoresSafeArea()

            // 中央径向光晕：歌词区一束柔光（Folia 光感层次）
            RadialGradient(
                colors: [Color.white.opacity(0.13), Color.white.opacity(0.035), .clear],
                center: .init(x: 0.5, y: 0.44),
                startRadius: 0, endRadius: 460
            )
            .ignoresSafeArea()

            // 三层粒子（远/中/近，漂移速度差 = 视差，越近越大越快越亮）
            ParticleLayer(count: 12, sizeRange: 30...72, drift: 4.5, opacity: 0.07)  // 远：大而慢
            ParticleLayer(count: 18, sizeRange: 12...26, drift: 10.0, opacity: 0.11) // 中
            ParticleLayer(count: 26, sizeRange: 4...12, drift: 22.0, opacity: 0.15)  // 近：小而快
        }
        .ignoresSafeArea()
    }

    private func fmt(_ s: Double) -> String {
        let t = max(Int(s), 0)
        return String(format: "%d:%02d", t / 60, t % 60)
    }
}

// MARK: - 相机运动：整个歌词场景缓慢视角转动 + 镜头推拉平移

/// 模拟"运镜对准歌词"（Folia diorama CameraRig 概念）：Y/X 双频正弦微旋转（扫视）
/// + 镜头推拉呼吸（接近/退后）+ 横向纵向游移（平移），三种运动叠加出相机缓缓
/// 运动过去、最终正对当前句的沉浸感。幅度克制，不干扰阅读。
/// 目前仅镜台（Diorama）模式承载。
struct CameraMotion: ViewModifier {
    func body(content: Content) -> some View {
        // #18（审查优化）：播放暂停时冻结运镜（纯装饰不 60fps 空转），恢复后时间连续
        TimelineView(PausableTimelineSchedule(paused: !PlayerManager.shared.isPlaying)) { ctx in
            let t = ctx.date.timeIntervalSinceReferenceDate
            // 扫视（旋转）：主频大弧度 + 次频小抖动
            let camY = sin(t * 0.11) * 3.2 + sin(t * 0.23 + 1.7) * 0.8   // 左右缓转 + 微抖
            let camX = sin(t * 0.083 + 1.3) * 2.0 + sin(t * 0.17) * 0.5  // 俯仰缓转 + 微抖
            // 推拉（zoom 呼吸）：镜头缓慢接近再退回，当前句始终正对屏幕
            let zoom = 1.0 + 0.015 * sin(t * 0.13 + 0.6)
            // 平移（游移）：横向纵向轻微位移，镜头"走过去"的体感
            let panX = CGFloat(sin(t * 0.09 + 2.1) * 10.0)
            let panY = CGFloat(sin(t * 0.07 + 0.4) * 6.0)
            content
                .scaleEffect(zoom)
                .rotation3DEffect(.degrees(camY), axis: (x: 0, y: 1, z: 0), perspective: 0.5)
                .rotation3DEffect(.degrees(camX), axis: (x: 1, y: 0, z: 0), perspective: 0.5)
                .offset(x: panX, y: panY)
        }
    }
}

// MARK: - 视差粒子光斑层

/// Canvas 绘制确定性伪随机光斑：位置由 sin-hash 决定（不跳动），
/// 随时间缓慢漂移（drift 越大越"近"、视差越明显）+ 每颗粒子独立呼吸（透明度正弦微变），
/// 多层叠加出镜头移动感与灵动光感。
private struct ParticleLayer: View {
    let count: Int
    let sizeRange: ClosedRange<CGFloat>
    let drift: CGFloat
    let opacity: Double

    var body: some View {
        // #18：播放暂停时冻结粒子层（纯装饰不空转），恢复后时间连续
        TimelineView(PausableTimelineSchedule(paused: !PlayerManager.shared.isPlaying)) { ctx in
            Canvas { out, size in
                let t = ctx.date.timeIntervalSinceReferenceDate
                let w = size.width, h = size.height
                for i in 0..<count {
                    // 确定性伪随机（sin-hash），每帧位置稳定
                    let r1 = fract(sin(Double(i) * 12.9898) * 43758.5453)
                    let r2 = fract(sin(Double(i) * 78.233) * 12543.123)
                    let r3 = fract(sin(Double(i) * 39.19) * 2999.991)
                    let baseX = r1 * w
                    let baseY = r2 * h
                    // 漂移：横向缓慢往复 + 纵向极缓下落，伪"镜头移动"
                    let dx = drift * (sin(t * 0.14 + r1 * 6.28))
                    let dy = drift * 0.35 * sin(t * 0.09 + r2 * 6.28)
                    let px = baseX + dx
                    let py = baseY + dy
                    // 呼吸：每颗粒子亮度独立缓慢起伏（不同相位）
                    let breathe = 0.7 + 0.3 * sin(t * 0.6 + r3 * 6.28)
                    let d = sizeRange.lowerBound + r3 * (sizeRange.upperBound - sizeRange.lowerBound)
                    let path = Path(ellipseIn: CGRect(x: px - d / 2, y: py - d / 2, width: d, height: d))
                    out.fill(path, with: .color(.white.opacity(opacity * breathe)))
                }
            }
            .allowsHitTesting(false)
        }
    }

    private func fract(_ x: Double) -> Double { x - floor(x) }
}
