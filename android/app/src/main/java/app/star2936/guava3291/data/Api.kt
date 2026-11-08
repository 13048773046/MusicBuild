package app.star2936.guava3291.data

import app.star2936.guava3291.BuildConfig
import app.star2936.guava3291.security.Obfuscator
import app.star2936.guava3291.state.AuthManager
import app.star2936.guava3291.state.WeatherInfo
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.booleanOrNull
import okhttp3.CertificatePinner
import okhttp3.FormBody
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory
import retrofit2.Retrofit
import java.net.URLEncoder
import java.util.concurrent.TimeUnit

// 对应 iOS API.swift；敏感地址经 Obfuscator 运行时解密 + 证书固定防中间人
// 设备信息：平台 + 唯一 ID（ANDROID_ID），上报后端用于更新链接按平台分发
object DeviceInfo {
    @Volatile var appContext: android.content.Context? = null
    val deviceId: String by lazy {
        val ctx = appContext ?: return@lazy "unknown"
        val id = android.provider.Settings.Secure.getString(
            ctx.contentResolver, android.provider.Settings.Secure.ANDROID_ID) ?: ""
        if (id.isEmpty()) "unknown" else id
    }
    const val platform = "android"
}

object Api {
    // 运行时解密，dex 中无明文域名
    val BASE: String by lazy { Obfuscator.API_BASE }
    val WS_PREFIX: String by lazy { Obfuscator.WS_PREFIX }
    val SERVER_HOST: String by lazy { Obfuscator.SERVER_HOST }

    val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }
    private val jsonMedia = "application/json; charset=utf-8".toMediaType()
    private val formMedia = "multipart/form-data".toMediaType()

    // 全局复用 OkHttp 客户端（连接池 + 超时 + 证书固定 + 抓包代理拦截，与 URLSession 行为对齐）
    val http: OkHttpClient by lazy {
        val builder = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS)
            .followRedirects(true)
        // 证书固定：host 已配置时才挂载（空 host 时 OkHttp 拒绝构造，会导致首次请求闪退）
        val host = Obfuscator.SERVER_HOST
        if (host.isNotBlank()) {
            val pinner = CertificatePinner.Builder()
                .add(host, "sha256/${Obfuscator.SERVER_SPKI_SHA256}")
                .build()
            builder.certificatePinner(pinner)
        }
        builder
            // 防抓包：命中抓包代理特征端口（Charles/Fiddler/mitmproxy）直接拒绝连接
            .addInterceptor { chain ->
                if (app.star2936.guava3291.security.NetworkGuard.isCaptureProxy()) {
                    throw java.io.IOException("网络环境异常，请求被拒绝")
                }
                chain.proceed(chain.request())
            }
            .build()
    }

    val httpLong: OkHttpClient by lazy {
        http.newBuilder().writeTimeout(180, TimeUnit.SECONDS).build()
    }

    // P2-11：Retrofit 声明式层（复用 http 证书固定/防抓包配置，叠加统一认证头 + 错误归一拦截器）
    private val retrofitClient: OkHttpClient by lazy {
        http.newBuilder()
            .addInterceptor { chain ->
                val token = AuthManager.currentUser.value?.token
                val req = if (token != null) {
                    chain.request().newBuilder().header("Authorization", "Bearer $token").build()
                } else chain.request()
                chain.proceed(req)
            }
            .addInterceptor { chain ->
                val resp = chain.proceed(chain.request())
                if (resp.code !in 200..299) {
                    val body = resp.body?.string().orEmpty()
                    if (resp.code == 401) AuthManager.handleUnauthorized()
                    throw ApiException(errBody(body) ?: "请求失败(${resp.code})")
                }
                resp
            }
            .build()
    }

    private val service: MusicApiService by lazy {
        val base = if (BASE.endsWith("/")) BASE else "$BASE/"
        Retrofit.Builder()
            .baseUrl(base)
            .client(retrofitClient)
            .addConverterFactory(json.asConverterFactory(jsonMedia))
            .build()
            .create(MusicApiService::class.java)
    }

    fun coverUrl(id: String) = "$BASE/cover/$id"
    fun lyricUrl(id: String) = "$BASE/lyric/$id"
    fun avatarUrl(id: String) = "$BASE/avatar/$id"
    fun backgroundUrl(id: String) = "$BASE/background/$id"
    fun streamUrl(id: String, low: Boolean = false): String =
        "$BASE/stream/$id" + if (low) "?br=96" else ""
    fun wsUrl(token: String): String? {
        val prefix = WS_PREFIX
        if (prefix.isBlank()) return null  // 地址为空时不拼非法 URL，调用方优雅降级
        return "$prefix${URLEncoder.encode(token, "UTF-8")}"
    }

    class ApiException(val msg: String) : Exception(msg)

    // ---------- 基础请求 ----------

    private fun authHeader(builder: Request.Builder) {
        AuthManager.currentUser.value?.token?.let { builder.header("Authorization", "Bearer $it") }
    }

    private fun checkAuth(code: Int) {
        if (code == 401) {
            AuthManager.handleUnauthorized()
            throw ApiException("登录已过期，请重新登录")
        }
    }

    private fun errBody(body: String): String? {
        return try {
            val obj = json.decodeFromString<Map<String, String>>(body)
            obj["err"]
        } catch (_: Exception) { null }
    }

    /** GET 请求，返回 200 body；非 2xx 抛 ApiException */
    private suspend fun get(path: String, authed: Boolean = false, timeoutMs: Long = 15000): String {
        return withContext(Dispatchers.IO) {
            val url = if (path.startsWith("http")) path else "$BASE/$path"
            val b = Request.Builder().url(url)
            if (authed) authHeader(b)
            val client = if (timeoutMs > 15000) httpLong else http
            val call = client.newCall(b.build())
            call.execute().use { resp ->
                val code = resp.code
                val body = resp.body?.string().orEmpty()
                if (authed) checkAuth(code)
                if (code in 200..299) body
                else throw ApiException(errBody(body) ?: "请求失败($code)")
            }
        }
    }

    /** POST 表单请求 */
    private suspend fun postForm(path: String, form: FormBody, authed: Boolean = false,
                                 timeoutMs: Long = 15000): String {
        return withContext(Dispatchers.IO) {
            val b = Request.Builder().url("$BASE/$path").post(form)
            if (authed) authHeader(b)
            val client = if (timeoutMs > 15000) httpLong else http
            val call = client.newCall(b.build())
            call.execute().use { resp ->
                val code = resp.code
                val body = resp.body?.string().orEmpty()
                if (authed) checkAuth(code)
                if (code in 200..299) body
                else throw ApiException(errBody(body) ?: "请求失败($code)")
            }
        }
    }

    // ---------- 歌曲列表 / 搜索 ----------

    // 关于页配置（后台网站管理可配）：返回联系方式字段，失败空 Map
    suspend fun siteConfig(): Map<String, String> = runCatching {
        val obj = service.siteConfig()
        mapOf(
            "about_website" to (obj["about_website"]?.jsonPrimitive?.contentOrNull ?: ""),
            "about_email" to (obj["about_email"]?.jsonPrimitive?.contentOrNull ?: ""),
            "about_phone" to (obj["about_phone"]?.jsonPrimitive?.contentOrNull ?: ""),
            "about_qq_group" to (obj["about_qq_group"]?.jsonPrimitive?.contentOrNull ?: "")
        )
    }.getOrElse { emptyMap() }

    /** 启动页广告：每次启动随机返回一张（无广告/失败返回 null，超时 3s 不阻塞启动）。
     *  必须 authed=true：后端按登录用户判断 SVIP 无广告特权，不带 token 时 SVIP 也会看到广告 */
    suspend fun splashAd(): SplashAd? {
        return try {
            val raw = get("splash-ad", authed = true, timeoutMs = 3000)
            val obj = json.parseToJsonElement(raw).jsonObject
            val ad = obj["ad"]?.jsonObject ?: return null
            SplashAd(
                name = ad["name"]?.jsonPrimitive?.contentOrNull ?: "",
                title = ad["title"]?.jsonPrimitive?.contentOrNull ?: "",
                link = ad["link"]?.jsonPrimitive?.contentOrNull ?: "",
                img = ad["img"]?.jsonPrimitive?.contentOrNull ?: ""
            )
        } catch (_: Exception) {
            null
        }
    }

    suspend fun tracks(userId: String? = null, keyword: String? = null): List<Track> =
        service.tracks(userId?.takeIf { it.isNotEmpty() }, keyword?.takeIf { it.isNotEmpty() })


    suspend fun track(id: String): Track =
        service.track(id)

    suspend fun search(keyword: String): List<Track> =
        service.search(keyword)

    // ---------- 签名播放 URL ----------

    /**
     * 签名流 URL（对齐 iOS API.signedStreamURL for:bitrate:）。
     * @param low 低码率档（br=96，蜂窝弱网秒开）；后端签名只校验 tid:exp，br 参数不影响验签。
     */
    suspend fun signedStreamUrl(id: String, low: Boolean = false): String {
        val signUrl = "$BASE/stream-url/$id"
        val streamBase = streamUrl(id, low)
        return withContext(Dispatchers.IO) {
            // 签名签发接口已做防护（登录态或分享 token）：登录用户附加 Bearer 才能拿到签名
            val builder = Request.Builder().url(signUrl)
            AuthManager.currentUser.value?.token?.let { builder.header("Authorization", "Bearer $it") }
            try {
                val call = http.newCall(builder.build())
                call.execute().use { resp ->
                    if (resp.code in 200..299) {
                        val body = resp.body?.string().orEmpty()
                        val obj = kotlinx.serialization.json.Json.parseToJsonElement(body).jsonObject
                        val exp = obj["exp"]?.jsonPrimitive?.contentOrNull ?: ""
                        val sig = obj["sig"]?.jsonPrimitive?.contentOrNull ?: ""
                        if (exp.isNotEmpty() && sig.isNotEmpty()) {
                            // 分隔符：low 时 streamBase 已含 ?br=96 → 追加用 &；非 low 时首参用 ?
                            val sep = if (low) "&" else "?"
                            return@withContext "$streamBase$sep" +
                                "exp=$exp&sig=${URLEncoder.encode(sig, "UTF-8")}"
                        }
                    }
                }
            } catch (_: Exception) { }
            streamBase   // 失败回退普通 URL（低码率回退 br=96，与 iOS 一致）
        }
    }

    /** 预生成低码率签名 URL（对齐 iOS API.prepareLowBitrate：预取阶段预热 low 档） */
    suspend fun prepareLowBitrate(id: String) {
        try { signedStreamUrl(id, low = true) } catch (_: Exception) { }
    }

    // ---------- 互动 ----------

    suspend fun reportPlay(trackId: String) {
        try {
            withContext(Dispatchers.IO) {
                val b = Request.Builder().url("$BASE/play/$trackId").post(FormBody.Builder().build())
                authHeader(b)
                http.newCall(b.build()).execute().close()
            }
        } catch (_: Exception) { }
    }

    suspend fun toggleLike(trackId: String, userId: String): Pair<Boolean, Int> {
        val form = FormBody.Builder().add("userId", userId).build()
        val body = postForm("like/$trackId", form, authed = true)
        val obj = kotlinx.serialization.json.Json.parseToJsonElement(body).jsonObject
        val liked = obj["liked"]?.jsonPrimitive?.booleanOrNull ?: false
        val count = obj["likeCount"]?.jsonPrimitive?.intOrNull ?: 0
        return liked to count
    }

    suspend fun reportShare(trackId: String): Int {
        return withContext(Dispatchers.IO) {
            try {
                val b = Request.Builder().url("$BASE/share/$trackId").post(FormBody.Builder().build())
                authHeader(b)
                val resp = http.newCall(b.build()).execute()
                val body = resp.body?.string().orEmpty()
                resp.close()
                if (resp.code == 401) AuthManager.handleUnauthorized()
                val obj = kotlinx.serialization.json.Json.parseToJsonElement(body).jsonObject
                obj["shareCount"]?.jsonPrimitive?.intOrNull ?: 0
            } catch (_: Exception) { 0 }
        }
    }

    suspend fun shareLink(id: String): String? {
        return try {
            val code = service.shareUrl(id).code
            if (code.isNotEmpty()) "https://${Obfuscator.SERVER_HOST}/s/$code/" else null
        } catch (_: Exception) { null }
    }


    // ---------- 行为上报 ----------

    suspend fun reportBehavior(events: List<BehaviorEvent>) {
        if (events.isEmpty()) return
        // 优先走 WebSocket（NotificationBus.sendBehavior 内部判断连接）
        if (NotificationBusRef.sendBehavior(events)) return
        try {
            withContext(Dispatchers.IO) {
                val payload = Json.encodeToString(mapOf("events" to events))
                val b = Request.Builder()
                    .url("$BASE/behavior")
                    .post(payload.toRequestBody(jsonMedia))
                authHeader(b)
                http.newCall(b.build()).execute().close()
            }
        } catch (_: Exception) { }
    }

    // ---------- 推荐 / 喜欢 ----------

    suspend fun recommend(userId: String? = null, excluding: List<String> = emptyList(),
                          fresh: Boolean = false, source: String? = null,
                          lat: Double? = null, lng: Double? = null, hour: Int? = null): List<Track> {
        val uid = userId ?: AuthManager.currentUser.value?.id
        return service.recommend(
            uid?.takeIf { it.isNotEmpty() },
            excluding.takeIf { it.isNotEmpty() }?.joinToString(","),
            if (fresh) 1 else null,
            source, lat, lng, hour)
    }

    /** 场景音乐 → 个性化分类歌单（类型严格筛选 + 用户行为画像排序）
     *  seed：每次点场景带新值 → 后端「分档窗口洗牌」，每次都换一批歌；
     *  同一 seed 结果完全一致（一次播放会话内不会中途跳变）。 */
    suspend fun radio(tag: String, userId: String? = null, seed: String? = null): List<Track> {
        val uid = userId ?: AuthManager.currentUser.value?.id
        return service.radio(tag, uid?.takeIf { it.isNotEmpty() }, seed?.takeIf { it.isNotEmpty() })
    }

    /** 每日推荐（2026-11-05）：当天稳定、隔天换一批。items 为与 /api/tracks 同构的列表。 */
    suspend fun daily(n: Int = 30): List<Track> {
        val obj = service.daily(n)
        val items = obj["items"] ?: return emptyList()
        return try {
            json.decodeFromJsonElement<List<Track>>(items)
        } catch (_: Exception) {
            emptyList()
        }
    }

    /** 相似歌曲（2026-11-05）：返回 (歌曲, 相似度 0~1)，跨歌手语义邻居。 */
    suspend fun similar(trackId: String, n: Int = 20): List<SimilarTrack> {
        val obj = service.similar(trackId, n)
        val items = obj["items"] ?: return emptyList()
        return try {
            val tracks = json.decodeFromJsonElement<List<Track>>(items)
            val scores = json.decodeFromJsonElement<List<SimilarScore>>(items)
            tracks.mapIndexed { i, t -> SimilarTrack(t, scores.getOrNull(i)?.similarity ?: 0.0) }
        } catch (_: Exception) {
            emptyList()
        }
    }

    /** 不感兴趣：后端直接排除该歌，并按粒度给同歌手/同风格/同情绪降权。 */
    suspend fun dislike(trackId: String, kind: String = "track"): Boolean {
        return try {
            val obj = service.dislike(DislikeRequest(trackId, kind))
            json.decodeFromJsonElement<OkResp>(obj).ok
        } catch (_: Exception) {
            false
        }
    }


    suspend fun fetchWeather(lat: Double, lng: Double, hour: Int? = null): WeatherInfo? {
        return try { service.weather(lat, lng, hour) } catch (_: Exception) { null }
    }


    suspend fun likedTracks(userId: String? = null): List<Track> {
        val uid = userId ?: AuthManager.currentUser.value?.id
        return service.likedTracks(uid?.takeIf { it.isNotEmpty() })
    }


    // ---------- 我的页分页 ----------

    suspend fun myTracks(page: Int, pageSize: Int = 20): PageResult<Track> =
        service.myTracks(page, pageSize)


    suspend fun myLikes(page: Int, pageSize: Int = 20): PageResult<Track> =
        service.myLikes(page, pageSize)


    suspend fun myComments(page: Int, pageSize: Int = 20): PageResult<MyComment> =
        service.myComments(page, pageSize)


    suspend fun myStats(): Int =
        service.myStats().totalSeconds


    // ---------- 评论 ----------

    suspend fun fetchComments(trackId: String): List<Comment> =
        service.comments(trackId)


    suspend fun postComment(trackId: String, userId: String, userName: String, content: String, images: List<String> = emptyList()) {
        val arr = images.joinToString(prefix = "[", postfix = "]") { "\"$it\"" }
        val contentJson = kotlinx.serialization.json.JsonPrimitive(content).toString()
        val json = """{"userId":"$userId","userName":"$userName","content":$contentJson,"images":$arr}"""
        withContext(Dispatchers.IO) {
            val b = Request.Builder().url("$BASE/comment/$trackId")
                .post(json.toRequestBody(jsonMedia))
            authHeader(b)
            http.newCall(b.build()).execute().use { resp ->
                if (!resp.isSuccessful) throw ApiException("评论失败(${resp.code})")
            }
        }
    }

    // ---------- 歌词（内存缓存 300 上限，与 iOS 一致） ----------

    @Volatile private var lyricCache: MutableMap<String, String> = mutableMapOf()
    private const val LyricCacheMax = 300

    suspend fun fetchLyric(id: String): String {
        lyricCache[id]?.let { return it }
        val text = withContext(Dispatchers.IO) {
            // 歌词接口已做防护（登录态或分享 token）：登录用户附加 Bearer 才能拉取
            val builder = Request.Builder().url(lyricUrl(id))
            AuthManager.currentUser.value?.token?.let { builder.header("Authorization", "Bearer $it") }
            http.newCall(builder.build()).execute().use { resp ->
                if (resp.code in 200..299) resp.body?.string().orEmpty() else ""
            }
        }
        synchronized(lyricCache) {
            if (lyricCache.size >= LyricCacheMax) lyricCache = mutableMapOf()
            lyricCache[id] = text
        }
        return text
    }

    fun clearLyricCache() { synchronized(lyricCache) { lyricCache = mutableMapOf() } }

    // ---------- 用户（multipart，对应 multipart/form-data） ----------

    private fun multipart(path: String, fields: Map<String, String>,
                          files: List<Triple<String, String, ByteArray>>,
                          authed: Boolean = false): String {
        val builder = MultipartBody.Builder().setType(MultipartBody.FORM)
        fields.forEach { (k, v) -> builder.addFormDataPart(k, v) }
        files.forEach { (name, filename, data) ->
            builder.addFormDataPart(name, filename, data.toRequestBody("image/jpeg".toMediaType()))
        }
        val body = builder.build()
        val b = Request.Builder().url("$BASE/$path").post(body)
        if (authed) authHeader(b)
        val resp = httpLong.newCall(b.build()).execute()
        val code = resp.code
        val bodyStr = resp.body?.string().orEmpty()
        resp.close()
        if (authed) checkAuth(code)
        if (code !in 200..299) throw ApiException(errBody(bodyStr) ?: "请求失败($code)")
        return bodyStr
    }

    suspend fun register(password: String, name: String, avatarData: ByteArray?,
                         gender: String, age: Int, signature: String,
                         backgroundData: ByteArray?): User {
        val files = mutableListOf<Triple<String, String, ByteArray>>()
        avatarData?.let { files += Triple("avatar", "avatar.jpg", it) }
        backgroundData?.let { files += Triple("background", "background.jpg", it) }
        val body = withContext(Dispatchers.IO) {
            multipart("register", mapOf(
                "password" to password, "name" to name, "gender" to gender,
                "age" to age.toString(), "signature" to signature
            ), files)
        }
        return json.decodeFromString(body)
    }

    suspend fun login(account: String, password: String): User {
        val body = withContext(Dispatchers.IO) {
            multipart("login", mapOf("account" to account, "password" to password), emptyList())
        }
        return json.decodeFromString(body)
    }

    // ===== 邮箱验证码登录 =====
    suspend fun sendEmailCode(email: String) {
        withContext(Dispatchers.IO) {
            multipart("send-email-code", mapOf("email" to email), emptyList())
        }
    }

    suspend fun loginEmail(email: String, code: String): User {
        val body = withContext(Dispatchers.IO) {
            multipart("login-email", mapOf("email" to email, "code" to code), emptyList())
        }
        return json.decodeFromString(body)
    }

    suspend fun bindEmail(email: String, code: String): User {
        val body = withContext(Dispatchers.IO) {
            multipart("bind-email", mapOf("email" to email, "code" to code), emptyList(), authed = true)
        }
        return json.decodeFromString(body)
    }

    suspend fun updateProfile(account: String, name: String, gender: String, age: Int,
                              signature: String, avatarData: ByteArray?, backgroundData: ByteArray?): User {
        val files = mutableListOf<Triple<String, String, ByteArray>>()
        avatarData?.let { files += Triple("avatar", "avatar.jpg", it) }
        backgroundData?.let { files += Triple("background", "background.jpg", it) }
        val body = withContext(Dispatchers.IO) {
            multipart("profile/update", mapOf(
                "account" to account, "name" to name, "gender" to gender,
                "age" to age.toString(), "signature" to signature
            ), files, authed = true)
        }
        return json.decodeFromString(body)
    }

    suspend fun changePassword(old: String, new: String) {
        withContext(Dispatchers.IO) {
            multipart("change-password", mapOf(
                "old_password" to old, "new_password" to new
            ), emptyList(), authed = true)
        }
    }

    // ---------- 上传 ----------

    suspend fun upload(data: ByteArray, filename: String, title: String, artist: String,
                       album: String, userId: String, coverData: ByteArray?) {
        withContext(Dispatchers.IO) {
            val builder = MultipartBody.Builder().setType(MultipartBody.FORM)
            builder.addFormDataPart("title", title)
            builder.addFormDataPart("artist", artist)
            builder.addFormDataPart("album", album)
            builder.addFormDataPart("userId", userId)
            builder.addFormDataPart("file", filename, data.toRequestBody("audio/mpeg".toMediaType()))
            coverData?.let { builder.addFormDataPart("cover", "cover.jpg", it.toRequestBody("image/jpeg".toMediaType())) }
            val b = Request.Builder().url("$BASE/upload").post(builder.build())
            authHeader(b)
            httpLong.newCall(b.build()).execute().use { resp ->
                if (resp.code == 401) AuthManager.handleUnauthorized()
                val body = resp.body?.string().orEmpty()
                if (resp.code !in 200..299) throw ApiException(errBody(body) ?: "上传失败(${resp.code})")
            }
        }
    }

    // ---------- 更新 / 公告 ----------

    suspend fun checkUpdate(): UpdateCheck? {
        return try {
            service.checkUpdate(BuildConfig.VERSION_NAME, DeviceInfo.platform, DeviceInfo.deviceId)
        } catch (_: Exception) { null }
    }


    /** 启动时上报设备平台与唯一 ID（后端记录活跃设备；check-update 亦顺带记录） */
    suspend fun reportDevice() {
        try {
            val body = "{\"device_id\":\"${DeviceInfo.deviceId}\",\"platform\":\"${DeviceInfo.platform}\",\"version\":\"${BuildConfig.VERSION_NAME}\"}"
            val b = Request.Builder().url("$BASE/device")
                .post(body.toRequestBody(jsonMedia))
            // 登录态下自动带 Authorization：后端据此关联设备到用户（后台用户管理可见设备类型）
            authHeader(b)
            http.newCall(b.build()).execute().use { }
        } catch (_: Exception) { }
    }

    suspend fun fetchAnnouncement(): Announcement? {
        return try { service.announcement() } catch (_: Exception) { null }
    }


    // ---------- 通知 ----------

    suspend fun officialMessages(): OfficialMsgResp =
        service.officialMessages()


    suspend fun notifications(page: Int = 1, size: Int = 30): NotificationsResult =
        service.notifications(page, size)


    suspend fun markNotificationsRead() {
        try {
            withContext(Dispatchers.IO) {
                val b = Request.Builder().url("$BASE/notifications/read").post(FormBody.Builder().build())
                authHeader(b)
                http.newCall(b.build()).execute().close()
            }
        } catch (_: Exception) { }
    }

    suspend fun markOfficialMessagesRead() {
        try {
            withContext(Dispatchers.IO) {
                val b = Request.Builder().url("$BASE/official-messages/read").post(FormBody.Builder().build())
                authHeader(b)
                http.newCall(b.build()).execute().close()
            }
        } catch (_: Exception) { }
    }

    suspend fun deleteNotification(id: String) {
        try {
            withContext(Dispatchers.IO) {
                val b = Request.Builder().url("$BASE/notifications/$id").delete()
                authHeader(b)
                http.newCall(b.build()).execute().close()
            }
        } catch (_: Exception) { }
    }

    // ---------- 歌单 ----------

    suspend fun fetchPlaylists(): List<Playlist> {
        return try { service.playlists() } catch (_: Exception) { emptyList() }
    }


    suspend fun createPlaylist(name: String, description: String = ""): Playlist {
        // 后端 /api/playlists 解析 JSON body（表单会导致 400 bad json，前端静默吞错=点击无反应）
        val jsonBody = """{"name":${JsonPrimitive(name).toString()},"description":${JsonPrimitive(description).toString()}}"""
        val body = withContext(Dispatchers.IO) {
            val b = Request.Builder().url("$BASE/playlists")
                .post(jsonBody.toRequestBody(jsonMedia))
            authHeader(b)
            http.newCall(b.build()).execute().use { resp ->
                val code = resp.code
                val respBody = resp.body?.string().orEmpty()
                checkAuth(code)
                if (code in 200..299) respBody
                else throw ApiException(errBody(respBody) ?: "创建歌单失败($code)")
            }
        }
        return try { json.decodeFromString(body) } catch (_: Exception) { Playlist(id = "", name = name, cover = null, trackCount = 0) }
    }

    suspend fun fetchPlaylistTracks(playlistId: String): List<Track> {
        return try { service.playlistTracks(playlistId) } catch (_: Exception) { emptyList() }
    }


    suspend fun addToPlaylist(playlistId: String, trackId: String, title: String,
                              artist: String, cover: String?) {
        val formBuilder = FormBody.Builder()
            .add("trackId", trackId).add("title", title).add("artist", artist)
        if (!cover.isNullOrEmpty()) formBuilder.add("cover", cover)
        postForm("playlists/$playlistId/tracks", formBuilder.build(), authed = true)
    }

    suspend fun removeFromPlaylist(playlistId: String, trackId: String) {
        withContext(Dispatchers.IO) {
            val b = Request.Builder().url("$BASE/playlists/$playlistId/tracks/$trackId").delete()
            authHeader(b)
            http.newCall(b.build()).execute().close()
        }
    }

    // ---------- 播放历史 ----------

    suspend fun recordHistory(trackId: String, title: String, artist: String, cover: String?) {
        try {
            val form = FormBody.Builder()
                .add("trackId", trackId).add("title", title).add("artist", artist)
            if (!cover.isNullOrEmpty()) form.add("cover", cover)
            postForm("history", form.build(), authed = true)
        } catch (_: Exception) { }
    }

    suspend fun fetchHistory(page: Int = 1, pageSize: Int = 50): PageResult<HistoryTrack> =
        service.history(page, pageSize)


    // ---------- 搜索历史 ----------

    suspend fun recordSearch(keyword: String) {
        try {
            val form = FormBody.Builder().add("keyword", keyword).build()
            postForm("search/history", form, authed = true)
        } catch (_: Exception) { }
    }

    suspend fun fetchSearchHistory(): List<String> {
        return try {
            service.searchHistory().history.filter { it.isNotEmpty() }
        } catch (_: Exception) { emptyList() }
    }


    suspend fun clearSearchHistory() {
        try {
            val b = Request.Builder().url("$BASE/search/history").delete()
            authHeader(b)
            http.newCall(b.build()).execute().close()
        } catch (_: Exception) { }
    }

    // ---------- 用户主页 ----------

    suspend fun fetchUserProfile(userId: String): UserProfile =
        service.userProfile(userId)

    // ---------- 会员开通（易支付下单） ----------

    /** 会员套餐价格/时长：后台「网站管理→支付设置」可配，前端不写死（对齐 iOS API.MemberPricing） */
    data class MemberPricing(
        val vipMonth: Pair<Double, Int> = 6.0 to 30,
        val vipQuarter: Pair<Double, Int> = 16.0 to 90,
        val vipYear: Pair<Double, Int> = 48.0 to 365,
        val svipMonth: Pair<Double, Int> = 10.0 to 30,
        val svipQuarter: Pair<Double, Int> = 26.0 to 90,
        val svipYear: Pair<Double, Int> = 68.0 to 365,
    ) {
        fun tier(plan: String, term: String): Pair<Double, Int> = when {
            plan == "svip" && term == "quarter" -> svipQuarter
            plan == "svip" && term == "year" -> svipYear
            plan == "svip" -> svipMonth
            term == "quarter" -> vipQuarter
            term == "year" -> vipYear
            else -> vipMonth
        }
        fun price(plan: String, term: String): Double = tier(plan, term).first
        fun days(plan: String, term: String): Int = tier(plan, term).second
    }

    suspend fun memberPricing(): MemberPricing {
        var p = MemberPricing()
        val cfg = try { siteConfig() } catch (_: Exception) { emptyMap() }
        fun num(k: String, d: Double): Double {
            val v = cfg[k]
            return v?.toDoubleOrNull()?.takeIf { it > 0 } ?: d
        }
        fun days(k: String, d: Int): Int {
            val v = cfg[k]
            return v?.toIntOrNull()?.takeIf { it > 0 } ?: d
        }
        return MemberPricing(
            vipMonth = num("vip_month_price", p.vipMonth.first) to days("vip_month_days", p.vipMonth.second),
            vipQuarter = num("vip_quarter_price", p.vipQuarter.first) to days("vip_quarter_days", p.vipQuarter.second),
            vipYear = num("vip_year_price", p.vipYear.first) to days("vip_year_days", p.vipYear.second),
            svipMonth = num("svip_month_price", p.svipMonth.first) to days("svip_month_days", p.svipMonth.second),
            svipQuarter = num("svip_quarter_price", p.svipQuarter.first) to days("svip_quarter_days", p.svipQuarter.second),
            svipYear = num("svip_year_price", p.svipYear.first) to days("svip_year_days", p.svipYear.second),
        )
    }

    suspend fun payCreate(plan: String, term: String = "month"): PayCreateResult =
        service.payCreate(mapOf("plan" to plan, "term" to term))


    suspend fun toggleFollow(userId: String): Boolean {
        val form = FormBody.Builder().build()
        val body = postForm("user/$userId/follow", form, authed = true)
        val obj = kotlinx.serialization.json.Json.parseToJsonElement(body).jsonObject
        return obj["isFollowing"]?.jsonPrimitive?.booleanOrNull ?: false
    }

    suspend fun fetchUserTracks(userId: String): List<Track> {
        return try { service.userTracks(userId) } catch (_: Exception) { emptyList() }
    }


    // ---------- 评论扩展（图片/点赞） ----------

    suspend fun uploadCommentImage(data: ByteArray, filename: String): String {
        val body = multipart("upload-comment-image", emptyMap(),
            listOf(Triple("image", filename, data)), authed = true)
        val obj = kotlinx.serialization.json.Json.parseToJsonElement(body).jsonObject
        return obj["url"]?.jsonPrimitive?.contentOrNull ?: ""
    }

    suspend fun toggleCommentLike(commentId: String): Pair<Boolean, Int> {
        val form = FormBody.Builder().build()
        val body = postForm("comments/$commentId/like", form, authed = true)
        val obj = kotlinx.serialization.json.Json.parseToJsonElement(body).jsonObject
        val liked = obj["liked"]?.jsonPrimitive?.booleanOrNull ?: false
        val count = obj["likeCount"]?.jsonPrimitive?.intOrNull ?: 0
        return liked to count
    }

    // ---------- 认证（2026-10-19 会员体系） ----------

    data class VerifyCategory(val id: String, val level1: String, val level2: String)

    suspend fun fetchVerifyCategories(): List<VerifyCategory> {
        return try {
            val body = get("verify/categories")
            val obj = kotlinx.serialization.json.Json.parseToJsonElement(body).jsonObject
            val list = obj["list"]?.jsonArray ?: return emptyList()
            list.mapNotNull { e ->
                val o = e.jsonObject
                val id = o["id"]?.jsonPrimitive?.contentOrNull ?: return@mapNotNull null
                VerifyCategory(
                    id = id,
                    level1 = o["level1"]?.jsonPrimitive?.contentOrNull ?: "",
                    level2 = o["level2"]?.jsonPrimitive?.contentOrNull ?: ""
                )
            }
        } catch (_: Exception) { emptyList() }
    }

    suspend fun submitVerifyApply(level1: String, level2: String, label: String, reason: String): Boolean {
        val form = FormBody.Builder()
            .add("level1", level1)
            .add("level2", level2)
            .add("label", label)
            .add("reason", reason)
            .build()
        return try {
            postForm("verify/apply", form, authed = true)
            true
        } catch (_: Exception) { false }
    }

}

// 供 Api.reportBehavior 引用（在 state 包中定义实现，避免循环依赖编译问题）
object NotificationBusRef {
    var sendBehavior: (List<BehaviorEvent>) -> Boolean = { false }
}
