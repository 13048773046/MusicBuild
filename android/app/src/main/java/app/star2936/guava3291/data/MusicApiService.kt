package app.star2936.guava3291.data

import app.star2936.guava3291.state.WeatherInfo
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import retrofit2.http.GET
import retrofit2.http.Body
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query

// P2-11：声明式 REST 接口（Retrofit）。
// 覆盖标准 GET JSON 接口；multipart/签名 URL/WebSocket 等动态结构保持 OkHttp 手工封装（Api.kt）。
// 认证头与错误归一（401/errBody→ApiException）由 Api.retrofitClient 拦截器统一处理。

@Serializable
data class MyStatsResp(val totalSeconds: Int = 0)

@Serializable
data class SearchHistoryResp(val history: List<String> = emptyList())

@Serializable
data class ShareUrlResp(val code: String = "")

/** 不感兴趣上报体（2026-11-05） */
@Serializable
data class DislikeRequest(val trackId: String, val kind: String = "track")

interface MusicApiService {

    // ---------- 歌曲 ----------
    @GET("tracks")
    suspend fun tracks(@Query("userId") userId: String?, @Query("q") keyword: String?): List<Track>

    @GET("track/{id}")
    suspend fun track(@Path("id") id: String): Track

    @GET("search")
    suspend fun search(@Query("q") keyword: String): List<Track>

    @GET("recommend")
    suspend fun recommend(
        @Query("userId") userId: String?,
        @Query("exclude") exclude: String?,
        @Query("fresh") fresh: Int?,
        @Query("source") source: String?,
        @Query("lat") lat: Double?,
        @Query("lng") lng: Double?,
        @Query("hour") hour: Int?
    ): List<Track>

    // 场景音乐 → 个性化分类歌单（2026-11-01）：类型严格筛选 + 用户行为画像排序
    // seed（2026-11-05）：客户端每次点场景带新 seed，后端按它做「分档窗口洗牌」，
    // 同一 seed 结果可复现（一次播放会话内不跳变），不同 seed 换一批歌
    @GET("radio/{tag}")
    suspend fun radio(
        @Path("tag") tag: String,
        @Query("userId") userId: String?,
        @Query("seed") seed: String?
    ): List<Track>

    // 每日推荐（2026-11-05）：按 (用户, 日期) 固定生成，当天稳定、隔天换一批
    @GET("daily")
    suspend fun daily(@Query("n") n: Int?): JsonObject

    // 相似歌曲（2026-11-05）：948 维语义向量的跨歌手"听起来像"邻居
    @GET("similar/{id}")
    suspend fun similar(@Path("id") id: String, @Query("n") n: Int?): JsonObject

    // 不感兴趣（2026-11-05）：kind = track / artist / genre / mood / scene
    @POST("dislike")
    suspend fun dislike(@Body body: DislikeRequest): JsonObject

    @GET("weather")
    suspend fun weather(
        @Query("lat") lat: Double,
        @Query("lng") lng: Double,
        @Query("hour") hour: Int?
    ): WeatherInfo

    @GET("site-config")
    suspend fun siteConfig(): JsonObject

    @GET("splash-ad")
    suspend fun splashAd(): JsonObject

    // ---------- 互动 ----------
    @GET("comments/{id}")
    suspend fun comments(@Path("id") id: String): List<Comment>

    @GET("likes")
    suspend fun likedTracks(@Query("userId") userId: String?): List<Track>

    @GET("share-url/{id}")
    suspend fun shareUrl(@Path("id") id: String): ShareUrlResp

    // ---------- 我的 ----------
    @GET("my-tracks")
    suspend fun myTracks(@Query("page") page: Int, @Query("pageSize") pageSize: Int): PageResult<Track>

    @GET("my-likes")
    suspend fun myLikes(@Query("page") page: Int, @Query("pageSize") pageSize: Int): PageResult<Track>

    @GET("my-comments")
    suspend fun myComments(@Query("page") page: Int, @Query("pageSize") pageSize: Int): PageResult<MyComment>

    @GET("my-stats")
    suspend fun myStats(): MyStatsResp

    // ---------- 更新 / 公告 ----------
    @GET("check-update")
    suspend fun checkUpdate(
        @Query("v") version: String,
        @Query("platform") platform: String,
        @Query("device_id") deviceId: String
    ): UpdateCheck

    @GET("announcement")
    suspend fun announcement(): Announcement?

    @GET("official-messages")
    suspend fun officialMessages(): OfficialMsgResp

    @GET("notifications")
    suspend fun notifications(@Query("page") page: Int, @Query("size") size: Int): NotificationsResult

    // ---------- 歌单 ----------
    @GET("playlists")
    suspend fun playlists(): List<Playlist>

    @GET("playlists/{id}/tracks")
    suspend fun playlistTracks(@Path("id") id: String): List<Track>

    // ---------- 历史 / 搜索历史 ----------
    @GET("history")
    suspend fun history(@Query("page") page: Int, @Query("pageSize") pageSize: Int): PageResult<HistoryTrack>

    @GET("search/history")
    suspend fun searchHistory(): SearchHistoryResp

    // ---------- 用户主页 ----------
    @GET("user/{id}/profile")
    suspend fun userProfile(@Path("id") id: String): UserProfile

    @GET("user/{id}/tracks")
    suspend fun userTracks(@Path("id") id: String): List<Track>

    // ---------- 会员开通（易支付下单） ----------
    @POST("pay/create")
    suspend fun payCreate(@Body body: Map<String, String>): PayCreateResult
}
