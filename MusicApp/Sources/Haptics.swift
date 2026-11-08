import UIKit

/// P0-5：轻量触感反馈封装（UIImpactFeedbackGenerator 等，iOS 10+ 原生，零依赖）。
/// 说明：SwiftUI `.sensoryFeedback` 是 iOS 17+ API，本项目 min iOS 16 不可用；
/// CoreHaptics 对单次轻反馈过重，故用 UIKit 反馈发生器。
enum Haptics {
    private static let light = UIImpactFeedbackGenerator(style: .light)
    private static let medium = UIImpactFeedbackGenerator(style: .medium)
    private static let selection = UISelectionFeedbackGenerator()

    /// 播放/暂停、上/下一首等常规操作
    static func lightTap() {
        light.impactOccurred()
    }

    /// 点赞/红心、收藏等较重反馈
    static func mediumTap() {
        medium.impactOccurred()
    }

    /// 播放模式切换、列表勾选等轻选择反馈
    static func selectionChanged() {
        selection.selectionChanged()
    }
}
