import Foundation

struct Track: Codable, Identifiable, Hashable {
    let id: String
    let title: String
    let artist: String?
    let album: String?
    let cover: String?
    let size: Int?
    let dur: Double?
    let durText: String?
    let created: Double?
    let playCount: Int?
    let likeCount: Int?
    let shareCount: Int?
    let commentCount: Int?
    let userId: String?
    let userName: String?
    let hasLyric: Bool?
    var isLiked: Bool?
    /// 个性化推荐理由标签（后端多路召回返回：相似歌曲/常听歌手/同风格/热门/新歌/为你探索）
    let reason: String?
    /// P1 归因闭环：推荐来源路代码（audio,behavior,collab,i2i,session,... 逗号拼接）。
    /// 播放 listen 上报时原样带回，后端按路统计完听率/跳过（仅推荐流返回该字段）。
    /// ★ 2026-11-05：必须用 `var`（带默认值）而不是 `let`——`let` 带初始值时 Codable 合成
    ///   不会从 JSON 解码该字段，recRoute 恒为 nil，后端 route 归因全部为空（线上实测
    ///   listen.meta.route 8400 条空串）。改成 var 后 Codable 会 decodeIfPresent，
    ///   同时保留默认值以兼容各处 `Track(...)` 显式构造。
    var recRoute: String? = nil

    var coverURL: URL? {
        guard let cover, !cover.isEmpty else { return nil }
        return API.coverURL(id)
    }

    var lyricURL: URL? {
        guard hasLyric == true else { return nil }
        return API.lyricURL(id)
    }

    /// 作者名：优先用上传者昵称，其次 artist 字段
    var displayAuthor: String {
        if let userName, !userName.isEmpty { return userName }
        if let artist, !artist.isEmpty { return artist }
        return "未知歌手"
    }
}
