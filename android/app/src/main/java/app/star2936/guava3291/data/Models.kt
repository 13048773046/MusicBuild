package app.star2936.guava3291.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// 启动页广告（后端 /api/splash-ad 随机返回一张；img 为相对路径 /api/splash/{name}）
@Serializable
data class SplashAd(
    val name: String = "",
    val title: String = "",
    val link: String = "",
    val img: String = ""
) {
    val imageUrl: String?
        get() = if (img.isEmpty()) null else Api.BASE + img
}

// 对应 iOS Track.swift
@Serializable
data class Track(
    val id: String,
    val title: String,
    val artist: String? = null,
    val album: String? = null,
    val cover: String? = null,
    val size: Long? = null,
    val dur: Double? = null,
    @SerialName("durText") val durText: String? = null,
    val created: Double? = null,
    @SerialName("playCount") val playCount: Int? = null,
    @SerialName("likeCount") val likeCount: Int? = null,
    @SerialName("shareCount") val shareCount: Int? = null,
    @SerialName("commentCount") val commentCount: Int? = null,
    @SerialName("userId") val userId: String? = null,
    @SerialName("userName") val userName: String? = null,
    @SerialName("hasLyric") val hasLyric: Boolean? = null,
    @SerialName("isLiked") val isLiked: Boolean? = null,
    val reason: String? = null,
    // P1 归因闭环：推荐来源路代码（audio,behavior,collab,i2i,session,... 逗号拼接），
    // 播放 listen 上报时原样带回，后端按路统计完听率/跳过（仅推荐流返回该字段）
    @SerialName("recRoute") val recRoute: String? = null,
) {
    val coverURL: String?
        get() = if (cover.isNullOrEmpty()) null else Api.coverUrl(id)

    val displayAuthor: String
        get() = when {
            !userName.isNullOrEmpty() -> userName
            !artist.isNullOrEmpty() -> artist
            else -> "未知歌手"
        }
}

// 相似歌曲（2026-11-05）：后端 /api/similar 的 item 里额外带 similarity
@Serializable
data class SimilarScore(val similarity: Double = 0.0)

/** 相似歌曲结果项：歌曲 + 相似度（0~1，>=0.78 很像 / >=0.6 较像 / 其余 有点像） */
data class SimilarTrack(val track: Track, val score: Double) {
    val label: String
        get() = when {
            score >= 0.78 -> "很像"
            score >= 0.6 -> "较像"
            else -> "有点像"
        }
}

// 通用 {ok:true} 响应
@Serializable
data class OkResp(val ok: Boolean = false)

// 对应 iOS Auth.swift User
@Serializable
data class User(
    val id: String,
    val account: String,
    val name: String,
    val avatar: String? = null,
    val gender: String? = null,
    val age: Int? = null,
    val background: String? = null,
    val signature: String? = null,
    val ip: String? = null,
    val created: Double? = null,
    val token: String? = null,
    // 会员/认证/头衔（2026-10-19；旧数据缺省）
    val title: String? = null,
    val verified: String? = null,
    @SerialName("verifiedLabel") val verifiedLabel: String? = null,
    val member: String? = null,
    @SerialName("memberExpire") val memberExpire: Double? = null,
    @SerialName("svipExpire") val svipExpire: Double? = null,
) {
    val avatarURL: String get() = Api.avatarUrl(id)
    val backgroundURL: String?
        get() = if (background.isNullOrEmpty()) null else Api.backgroundUrl(id)
}

// 对应 API.Comment
@Serializable
data class Comment(
    val id: String,
    @SerialName("userId") val userId: String,
    @SerialName("userName") val userName: String,
    val content: String,
    val created: Double,
    @SerialName("commentImageURL") val commentImageURL: String? = null,
    @SerialName("isLiked") val isLiked: Boolean = false,
    @SerialName("likeCount") val likeCount: Int = 0,
    @SerialName("userOnline") val userOnline: Boolean = false,
    // 评论者头衔/认证/会员（2026-10-19；旧数据缺省）
    val title: String? = null,
    val verified: String? = null,
    @SerialName("verifiedLabel") val verifiedLabel: String? = null,
    val member: String? = null,
)

// 对应 API.MyComment
@Serializable
data class MyComment(
    @SerialName("trackId") val trackId: String,
    val content: String,
    val created: Double? = null,
    val title: String? = null,
    val artist: String? = null,
)

// 对应 API.PageResult
@Serializable
data class PageResult<T>(
    val list: List<T>,
    val total: Int,
    val hasMore: Boolean,
)

// 对应 API.Notification
@Serializable
data class Notification(
    val id: String,
    val type: String,
    @SerialName("trackId") val trackId: String? = null,
    @SerialName("trackTitle") val trackTitle: String? = null,
    @SerialName("fromUserId") val fromUserId: String? = null,
    @SerialName("fromUser") val fromUser: String? = null,
    val content: String? = null,
    @SerialName("commentId") val commentId: String? = null,
    val created: Double,
    val unread: Boolean = false,
    val mutual: Boolean = false,
)

@Serializable
data class NotificationsResult(
    val list: List<Notification>,
    val unread: Int,
    val hasMore: Boolean = false,
)

// 对应 API.OfficialMessage
@Serializable
data class OfficialMessage(
    val id: String,
    val sender: String? = null,
    val content: String,
    val created: Double = 0.0,
    val isNew: Boolean = false,
)

// 官方消息列表 + 未读条数（公共广播）
@Serializable
data class OfficialMsgResp(
    val list: List<OfficialMessage> = emptyList(),
    val unreadCount: Int = 0,
)

// 对应 MusicApp.swift Announcement
@Serializable
data class Announcement(
    val id: String,
    val title: String,
    val content: String,
    val version: String,
    @SerialName("showOnce") val showOnce: Int,
)

// 对应 MusicApp.swift UpdateNotice / API.UpdateCheck
@Serializable
data class UpdateCheck(
    val hasUpdate: Boolean = false,
    val id: String? = null,
    val title: String? = null,
    val content: String? = null,
    val version: String? = null,
    @SerialName("forceUpdate") val forceUpdate: Int? = null,
    @SerialName("downloadUrl") val downloadUrl: String? = null,
    @SerialName("downloadUrlIos") val downloadUrlIos: String? = null,
)

// 行为事件（对应 API.BehaviorEvent）
data class BehaviorEvent(
    @SerialName("trackId") val trackId: String,
    val event: String,
    val value: Double = 0.0,
    val meta: String? = null,
) {
    companion object {
        fun metaJSON(map: Map<String, Any>): String? {
            return try {
                val entries = map.map { (k, v) ->
                    k to when (v) {
                        is Double -> v
                        is Float -> v.toDouble()
                        is Int -> v.toDouble()
                        is Long -> v.toDouble()
                        is Boolean -> if (v) 1.0 else 0.0
                        else -> v.toString()
                    }
                }.toMap()
                kotlinx.serialization.json.JsonObject(
                    entries.map { (k, v) -> k to kotlinx.serialization.json.JsonPrimitive(v.toString()) }.toMap()
                ).toString()
            } catch (_: Exception) { null }
        }
    }
}

// ---------- 新增数据模型（歌单/历史/用户主页） ----------

// 对应 iOS Playlist
@Serializable
data class Playlist(
    val id: String,
    val name: String,
    val description: String? = null,
    @SerialName("userId") val userId: String? = null,
    @SerialName("trackCount") val trackCount: Int = 0,
    val cover: String? = null,
    val created: Double? = null,
)

// 对应 iOS HistoryTrack
@Serializable
data class HistoryTrack(
    val id: String,
    @SerialName("trackId") val trackId: String,
    val title: String,
    val artist: String? = null,
    val cover: String? = null,
    val created: Double? = null,
) {
    val coverURL: String?
        get() = if (cover.isNullOrEmpty()) null else Api.coverUrl(trackId)
}

// 对应 iOS UserProfile
@Serializable
data class UserProfile(
    @SerialName("userId") val id: String,
    val name: String,
    val avatar: String? = null,
    val signature: String? = null,
    val gender: String? = null,
    val age: Int? = null,
    val background: String? = null,
    @SerialName("city") val city: String = "",
    @SerialName("trackCount") val workCount: Int = 0,
    @SerialName("followerCount") val followerCount: Int = 0,
    @SerialName("followingCount") val followingCount: Int = 0,
    @SerialName("mutualCount") val mutualCount: Int = 0,
    @SerialName("likeCount") val likeCount: Int = 0,
    @SerialName("isFollowing") val isFollowing: Boolean = false,
    @SerialName("isOnline") val isOnline: Boolean = false,
    // 头衔/认证/会员（2026-10-19；旧数据缺省）
    val title: String? = null,
    val verified: String? = null,
    @SerialName("verifiedLabel") val verifiedLabel: String? = null,
    val member: String? = null,
    @SerialName("memberExpire") val memberExpire: Double? = null,
    @SerialName("svipExpire") val svipExpire: Double? = null,
) {
    val avatarURL: String get() = Api.avatarUrl(id)
    val backgroundURL: String?
        get() = if (background.isNullOrEmpty()) null else Api.backgroundUrl(id)
}

/// 易支付下单结果（pay/create）
@Serializable
data class PayCreateResult(
    val ok: Boolean = false,
    @SerialName("orderNo") val orderNo: String? = null,
    val amount: Double? = null,
    val plan: String? = null,
    @SerialName("payUrl") val payUrl: String? = null,
    val note: String? = null,
)

@Serializable
data class AppNotification(
    val id: String = "",
    val type: String = "",
    val content: String? = null,
    val fromUser: String? = null,
    val fromUserId: String? = null,
    val trackId: String? = null,
    val trackTitle: String? = null,
    val commentId: String? = null,
    val created: Double = 0.0,
    val unread: Boolean = false
)
