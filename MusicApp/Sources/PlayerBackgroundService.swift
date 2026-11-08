import SwiftUI

/// 全屏 Folia 播放器自定义背景图服务（相册选图 · 全局一张 · 最优先）
///
/// 优先级链（FullScreenWebLyricView 推送给 Folia 网页）：
/// 自定义背景图 → AI 生成配色主题（Folia major 移植）→ 封面主色主题背景 → 黑色兜底
///
/// 持久化：UserDefaults 存 JPEG Data（长边压缩到 1600px 内，单张约 0.3~1MB，
/// 避免大图撑爆 UserDefaults 导致写入失败）。
@MainActor
final class PlayerBackgroundService: ObservableObject {
    static let shared = PlayerBackgroundService()

    @Published var customImage: UIImage?

    private let storageKey = "player_custom_background"
    private let maxSide: CGFloat = 1600

    private init() {
        // 启动时从磁盘恢复
        if let data = UserDefaults.standard.data(forKey: storageKey),
           let img = UIImage(data: data) {
            customImage = img
        }
    }

    /// 是否有自定义背景
    var hasCustom: Bool { customImage != nil }

    /// 保存新背景图（自动压缩 + 持久化）
    func save(_ image: UIImage) {
        let compressed = Self.compress(image, maxSide: maxSide)
        customImage = compressed
        if let data = compressed.jpegData(compressionQuality: 0.85) {
            UserDefaults.standard.set(data, forKey: storageKey)
        }
    }

    /// 清除自定义背景（回落到 AI 配色/封面取色）
    func clear() {
        customImage = nil
        UserDefaults.standard.removeObject(forKey: storageKey)
    }

    /// 等比压缩：长边不超过 maxSide，保视觉质量（背景图不需要原图精度）
    static func compress(_ image: UIImage, maxSide: CGFloat) -> UIImage {
        let longest = max(image.size.width, image.size.height)
        guard longest > maxSide else { return image }
        let scale = maxSide / longest
        let newSize = CGSize(width: image.size.width * scale,
                             height: image.size.height * scale)
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = 1.0
        let renderer = UIGraphicsImageRenderer(size: newSize, format: format)
        return renderer.image { _ in
            image.draw(in: CGRect(origin: .zero, size: newSize))
        }
    }
}
