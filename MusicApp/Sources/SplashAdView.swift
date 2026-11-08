import SwiftUI
import UIKit

/// 启动页广告模型（后端 /api/splash-ad 随机返回一张）
struct SplashAd: Identifiable {
    var id: String { name }
    let name: String
    let title: String
    let link: String
    let imgPath: String

    /// 广告图完整 URL（imgPath 为相对路径 /api/splash/{name}）
    var imageURL: URL? {
        guard !imgPath.isEmpty else { return nil }
        // 兼容绝对 URL：后端若直接返回 http(s) 完整地址则原样使用
        if imgPath.hasPrefix("http://") || imgPath.hasPrefix("https://") {
            return URL(string: imgPath)
        }
        // 相对路径：去掉前导 "/" 再基于 base 拼接，保留 base 的 /music/api 路径前缀。
        // 注意：直接 URL(string: "/api/...", relativeTo: base) 会以根路径覆盖 base 路径，
        // 丢掉 /music 得到 https://host/api/... 导致 404（2026-10-22 修复）
        let rel = imgPath.hasPrefix("/") ? String(imgPath.dropFirst()) : imgPath
        return URL(string: rel, relativeTo: API.base)
    }
}

/// 启动页广告视图：
/// - 广告图占满全屏（scaledToFill 裁切，可点击跳 link）
/// - 底部紧凑品牌区（点击提示 + AppLogo + 名称 + 标语，自适应高度、渐变衔接，不再固定占屏 25%）
/// - 右上角 5 秒倒计时跳过按钮，读到 0 或点击立即进入主界面
struct SplashAdView: View {
    let ad: SplashAd
    let onFinish: () -> Void
    @State private var countdown = 5
    /// 广告图：走 CoverImageCache 内存+磁盘二级缓存（首次下载后二次启动秒显，
    /// 不再每次启动重新下载数 MB 大图与音频流抢带宽——2026-10-19 修复）
    @State private var adImage: UIImage?

    private let timer = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    var body: some View {
        ZStack {
            Theme.background.ignoresSafeArea()

            // 广告图：占满上部（scaledToFill 裁切，底部被品牌区渐变遮罩衔接）
            GeometryReader { geo in
                Group {
                    if let img = adImage {
                        Image(uiImage: img).resizable().scaledToFill()
                    } else {
                        ZStack {
                            Color.black.opacity(0.35)
                            ProgressView().tint(.white.opacity(0.6))
                        }
                        .task { await loadAdImage() }
                    }
                }
                .frame(width: geo.size.width, height: geo.size.height)
                .clipped()
                .contentShape(Rectangle())
                .onTapGesture {
                    if !ad.link.isEmpty, let url = URL(string: ad.link) {
                        UIApplication.shared.open(url)
                    }
                }
            }

            // 底部品牌区：紧凑自适应高度（点击提示 + Logo + 名称 + 标语），
            // 不再固定占屏 25%，消除大片留白，广告图展示面积更大
            VStack(spacing: 0) {
                Spacer()
                VStack(spacing: 12) {
                    // 点击提示（仅广告配置了跳转链接时显示，提示用户广告可点）
                    if !ad.link.isEmpty {
                        HStack(spacing: 5) {
                            Image(systemName: "hand.tap.fill")
                                .font(.system(size: 11, weight: .semibold))
                            Text("点击了解更多")
                                .font(.system(size: 12, weight: .semibold))
                            Image(systemName: "chevron.right")
                                .font(.system(size: 10, weight: .bold))
                        }
                        .foregroundColor(.white)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 6)
                        .background(Color.black.opacity(0.35))
                        .clipShape(Capsule())
                    }

                    HStack(spacing: 12) {
                        Image("AppLogo")
                            .resizable()
                            .scaledToFit()
                            .frame(width: 44, height: 44)
                            .clipShape(RoundedRectangle(cornerRadius: 11, style: .continuous))
                        VStack(alignment: .leading, spacing: 3) {
                            Text("流萤音乐")
                                .font(.system(size: 21, weight: .bold))
                                .foregroundColor(Theme.textPrimary)
                            Text("让好音乐如流萤般点亮夜晚")
                                .font(.system(size: 12))
                                .foregroundColor(Theme.textSecondary)
                        }
                        Spacer(minLength: 0)
                    }
                    .padding(.horizontal, 24)
                }
                .frame(maxWidth: .infinity)
                .padding(.top, 18)
                .padding(.bottom, 14)
                .background(
                    LinearGradient(
                        colors: [Theme.background.opacity(0), Theme.background.opacity(0.9), Theme.background],
                        startPoint: .top, endPoint: .bottom
                    )
                )
            }

            // 右上角倒计时跳过按钮
            VStack {
                HStack {
                    Spacer()
                    Button(action: { onFinish() }) {
                        Text("\(countdown)s 跳过")
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundColor(.white)
                            .padding(.horizontal, 14)
                            .padding(.vertical, 7)
                            .background(Color.black.opacity(0.45))
                            .clipShape(Capsule())
                    }
                    .buttonStyle(.plain)
                }
                Spacer()
            }
            .padding(.top, 8)
            .padding(.horizontal, 16)
        }
        .onReceive(timer) { _ in
            if countdown > 1 {
                countdown -= 1
            } else {
                onFinish()
            }
        }
    }

    /// 广告图加载：内存 → 磁盘 → 网络（与封面同款三级缓存，写入内存+磁盘）
    private func loadAdImage() async {
        guard let url = ad.imageURL else { return }
        if let mem = CoverImageCache.shared.image(for: url) {
            adImage = mem
            return
        }
        if let disk = await CoverImageCache.shared.diskImage(for: url) {
            CoverImageCache.shared.store(disk, for: url)
            adImage = disk
            return
        }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            if let img = UIImage(data: data) {
                CoverImageCache.shared.store(img, for: url)
                CoverImageCache.shared.storeToDisk(img, for: url)
                adImage = img
            }
        } catch {
            // 下载失败保留加载占位，倒计时照常走完
        }
    }
}
