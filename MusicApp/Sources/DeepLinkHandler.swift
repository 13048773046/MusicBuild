import Foundation

/// 分享 H5「去 APP 里面听」深链处理：`liuyingmusic://track/{trackId}`
///
/// 打开 App 后：单曲精确查询（tracks/collected 都覆盖）→ 播放该歌曲 → 跳回首页播放页。
/// 与通知点击跳歌共用同一路由（AppRouter.jumpToHome），体验一致。
enum DeepLinkHandler {

    /// 解析深链 URL，返回 trackId；不是流萤深链返回 nil
    static func trackId(from url: URL) -> String? {
        guard let scheme = url.scheme?.lowercased(), scheme == "liuyingmusic" else { return nil }
        // liuyingmusic://track/{id} → host=track, lastPathComponent=id
        guard url.host?.lowercased() == "track" else { return nil }
        let id = url.lastPathComponent
        return id.isEmpty ? nil : id
    }

    /// 打开并播放指定歌曲（@MainActor，深链在 App 前台时调用）
    @MainActor
    static func play(trackId: String) {
        Task {
            do {
                let t = try await API.track(id: trackId)
                PlayerManager.shared.playNotificationTrack(t)
                AppRouter.shared.jumpToHome(play: t)
            } catch {
                // 单曲拉取失败（如歌曲已下架）：保持当前页面，不打扰用户
            }
        }
    }
}
