import SwiftUI
import UIKit

// MARK: - 开源许可页

/// 开源许可：展示本 App 使用的开源组件及其许可证（AGPL-3.0 合规展示）。
/// 从设置页「关于」分组进入，沉浸式自定义头部。
/// 本页展示核心开源组件（Folia 歌词引擎及其渲染依赖），点击条目可打开对应仓库。
struct OpenSourceLicensesView: View {
    @Environment(\.dismiss) private var dismiss

    /// 开源组件条目：名称 / 许可证 / 用途说明 / 仓库链接
    private struct LicenseItem: Identifiable {
        let id: String
        let name: String
        let license: String
        let purpose: String
        let url: String
        let tint: Color
        let icon: String
    }

    private let items: [LicenseItem] = [
        LicenseItem(
            id: "folia",
            name: "Folia (folia-major)",
            license: "AGPL-3.0",
            purpose: "全屏歌词展示引擎：14 种歌词动画模式（tilt / diorama / partita / lumiere / pendolo / cadenza / monet / cappella / classic / still / fume / claddagh / sonnet / tempera）",
            url: "https://github.com/chthollyphile/folia-major",
            tint: Theme.accent,
            icon: "music.note.list"
        ),
        LicenseItem(
            id: "react",
            name: "React",
            license: "MIT",
            purpose: "歌词渲染层 UI 框架",
            url: "https://github.com/facebook/react",
            tint: Theme.sky,
            icon: "atom"
        ),
        LicenseItem(
            id: "pixijs",
            name: "PixiJS",
            license: "MIT",
            purpose: "WebGL 渲染引擎（流光 / 商籁 / 蛋彩等模式的 2D 加速渲染）",
            url: "https://github.com/pixijs/pixijs",
            tint: Theme.lilac,
            icon: "camera.filters"
        ),
        LicenseItem(
            id: "threejs",
            name: "three.js",
            license: "MIT",
            purpose: "3D 场景渲染（镜台模式的 3D 歌词空间与运镜）",
            url: "https://github.com/mrdoob/three.js",
            tint: Theme.peach,
            icon: "cube"
        ),
        LicenseItem(
            id: "framer-motion",
            name: "Framer Motion",
            license: "MIT",
            purpose: "动画驱动（歌词行过渡 / 逐字发光时序）",
            url: "https://github.com/framer/motion",
            tint: Theme.sky,
            icon: "waveform.path.ecg"
        ),
        LicenseItem(
            id: "vite",
            name: "Vite",
            license: "MIT",
            purpose: "构建工具（开发期，用于打包歌词渲染层静态资源）",
            url: "https://github.com/vitejs/vite",
            tint: Theme.lilac,
            icon: "hammer"
        ),
    ]

    var body: some View {
        ZStack {
            Theme.background.ignoresSafeArea()

            ScrollView {
                VStack(spacing: 20) {
                    header

                    // 说明卡片
                    VStack(alignment: .leading, spacing: 10) {
                        Text("本应用内嵌了以下开源软件。依据各开源协议的许可要求，我们在此提供完整的许可证信息与源代码获取方式。")
                            .font(.system(size: 13))
                            .foregroundColor(Theme.textSecondary)
                            .lineSpacing(4)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(16)
                    .background(Theme.card, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous)
                        .stroke(Theme.cardBorder, lineWidth: 1))

                    // 开源组件列表
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(Array(items.enumerated()), id: \.element.id) { idx, item in
                            Button {
                                open(item.url)
                            } label: {
                                licenseRow(item)
                            }
                            .buttonStyle(.plain)
                            if idx < items.count - 1 {
                                divider
                            }
                        }
                    }
                    .background(Theme.card, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous)
                        .stroke(Theme.cardBorder, lineWidth: 1))

                    // AGPL-3.0 特别说明（Folia 采用 AGPL，需向使用者提供源代码）
                    VStack(alignment: .leading, spacing: 10) {
                        Text("GNU AGPL-3.0 说明")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundColor(Theme.textPrimary)
                        Text("Folia（folia-major）采用 GNU Affero General Public License v3.0 许可。AGPL-3.0 要求：通过网络提供服务的软件，使用者也享有获取对应源代码的权利。Folia 完整源代码可在其 GitHub 仓库获取，本应用使用的歌词渲染层基于该仓库构建。")
                            .font(.system(size: 12.5))
                            .foregroundColor(Theme.textSecondary)
                            .lineSpacing(4)
                        Button {
                            open("https://github.com/chthollyphile/folia-major/blob/main/LICENSE")
                        } label: {
                            HStack(spacing: 5) {
                                Text("查看 AGPL-3.0 许可证全文")
                                    .font(.system(size: 13, weight: .semibold))
                                    .foregroundColor(Theme.accent)
                                Image(systemName: "arrow.up.right")
                                    .font(.system(size: 11, weight: .semibold))
                                    .foregroundColor(Theme.accent)
                            }
                            .padding(.top, 4)
                        }
                        .buttonStyle(.plain)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(16)
                    .background(Theme.card, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous)
                        .stroke(Theme.cardBorder, lineWidth: 1))
                }
                .padding(20)
                .padding(.bottom, 30)
            }
            .appBottomTabSpacing()
        }
        .navigationBarHidden(true)
        .onAppear { NavBack.enableSwipeBack() }
    }

    // MARK: - 头部

    private var header: some View {
        HStack(spacing: 12) {
            Button {
                NavBack.goBack(dismiss)
            } label: {
                ZStack {
                    Circle().fill(Theme.card)
                    Image(systemName: "chevron.left")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundColor(Theme.textPrimary)
                }
                .frame(width: 38, height: 38)
            }
            .buttonStyle(.plain)

            VStack(alignment: .leading, spacing: 2) {
                Text("开源许可")
                    .font(.system(size: 30, weight: .bold))
                    .foregroundColor(Theme.textPrimary)
                Text("本应用使用的开源组件与许可证")
                    .font(.system(size: 12))
                    .foregroundColor(Theme.textSecondary)
            }
            Spacer()
        }
    }

    // MARK: - 组件

    private var divider: some View {
        Rectangle()
            .fill(Theme.cardBorder)
            .frame(height: 0.5)
            .frame(maxWidth: .infinity)
            .padding(.leading, 56)
    }

    private func licenseRow(_ item: LicenseItem) -> some View {
        HStack(spacing: 12) {
            Image(systemName: item.icon)
                .font(.system(size: 14, weight: .semibold))
                .foregroundColor(item.tint)
                .frame(width: 28, height: 28)
                .background(item.tint.opacity(0.14), in: RoundedRectangle(cornerRadius: 8, style: .continuous))

            VStack(alignment: .leading, spacing: 3) {
                Text(item.name)
                    .font(.system(size: 15))
                    .foregroundColor(Theme.textPrimary)
                    .lineLimit(1)
                Text(item.purpose)
                    .font(.system(size: 11.5))
                    .foregroundColor(Theme.textTertiary)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
            }

            Spacer()

            Text(item.license)
                .font(.system(size: 12, weight: .semibold))
                .foregroundColor(item.tint)
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(item.tint.opacity(0.14), in: Capsule())

            Image(systemName: "chevron.right")
                .font(.system(size: 12, weight: .semibold))
                .foregroundColor(Theme.textTertiary.opacity(0.7))
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 13)
        .contentShape(Rectangle())
    }

    // MARK: - 动作

    private func open(_ urlString: String) {
        guard let url = URL(string: urlString) else { return }
        UIApplication.shared.open(url)
    }
}
