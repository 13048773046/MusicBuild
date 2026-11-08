# -*- coding: utf-8 -*-
"""P2-11：Api.kt 标准 GET JSON 方法委托 Retrofit（MusicApiService）。
保留 POST/multipart/签名/WebSocket/特殊超时方法原实现。调用点签名不变。"""
import re

SRC = "android/app/src/main/java/app/star2936/guava3291/data/Api.kt"
src = open(SRC, encoding="utf-8").read()

# 方法名 -> 新方法体（含完整签名，缩进 4 空格）
REPL = {
"tracks": '''    suspend fun tracks(userId: String? = null, keyword: String? = null): List<Track> =
        service.tracks(userId?.takeIf { it.isNotEmpty() }, keyword?.takeIf { it.isNotEmpty() })
''',
"track": '''    suspend fun track(id: String): Track =
        service.track(id)
''',
"search": '''    suspend fun search(keyword: String): List<Track> =
        service.search(keyword)
''',
"recommend": '''    suspend fun recommend(userId: String? = null, excluding: List<String> = emptyList(),
                          fresh: Boolean = false, source: String? = null,
                          lat: Double? = null, lng: Double? = null, hour: Int? = null): List<Track> {
        val uid = userId ?: AuthManager.currentUser.value?.id
        return service.recommend(
            uid?.takeIf { it.isNotEmpty() },
            excluding.takeIf { it.isNotEmpty() }?.joinToString(","),
            if (fresh) 1 else null,
            source, lat, lng, hour)
    }
''',
"fetchWeather": '''    suspend fun fetchWeather(lat: Double, lng: Double, hour: Int? = null): WeatherInfo? {
        return try { service.weather(lat, lng, hour) } catch (_: Exception) { null }
    }
''',
"likedTracks": '''    suspend fun likedTracks(userId: String? = null): List<Track> {
        val uid = userId ?: AuthManager.currentUser.value?.id
        return service.likedTracks(uid?.takeIf { it.isNotEmpty() })
    }
''',
"myTracks": '''    suspend fun myTracks(page: Int, pageSize: Int = 20): PageResult<Track> =
        service.myTracks(page, pageSize)
''',
"myLikes": '''    suspend fun myLikes(page: Int, pageSize: Int = 20): PageResult<Track> =
        service.myLikes(page, pageSize)
''',
"myComments": '''    suspend fun myComments(page: Int, pageSize: Int = 20): PageResult<MyComment> =
        service.myComments(page, pageSize)
''',
"myStats": '''    suspend fun myStats(): Int =
        service.myStats().totalSeconds
''',
"fetchComments": '''    suspend fun fetchComments(trackId: String): List<Comment> =
        service.comments(trackId)
''',
"checkUpdate": '''    suspend fun checkUpdate(): UpdateCheck? {
        return try {
            service.checkUpdate(BuildConfig.VERSION_NAME, DeviceInfo.platform, DeviceInfo.deviceId)
        } catch (_: Exception) { null }
    }
''',
"fetchAnnouncement": '''    suspend fun fetchAnnouncement(): Announcement? {
        return try { service.announcement() } catch (_: Exception) { null }
    }
''',
"officialMessages": '''    suspend fun officialMessages(): OfficialMsgResp =
        service.officialMessages()
''',
"notifications": '''    suspend fun notifications(page: Int = 1, size: Int = 30): NotificationsResult =
        service.notifications(page, size)
''',
"fetchPlaylists": '''    suspend fun fetchPlaylists(): List<Playlist> {
        return try { service.playlists() } catch (_: Exception) { emptyList() }
    }
''',
"fetchPlaylistTracks": '''    suspend fun fetchPlaylistTracks(playlistId: String): List<Track> {
        return try { service.playlistTracks(playlistId) } catch (_: Exception) { emptyList() }
    }
''',
"fetchHistory": '''    suspend fun fetchHistory(page: Int = 1, pageSize: Int = 50): PageResult<HistoryTrack> =
        service.history(page, pageSize)
''',
"fetchSearchHistory": '''    suspend fun fetchSearchHistory(): List<String> {
        return try {
            service.searchHistory().history.filter { it.isNotEmpty() }
        } catch (_: Exception) { emptyList() }
    }
''',
"fetchUserProfile": '''    suspend fun fetchUserProfile(userId: String): UserProfile =
        service.userProfile(userId)
''',
"fetchUserTracks": '''    suspend fun fetchUserTracks(userId: String): List<Track> {
        return try { service.userTracks(userId) } catch (_: Exception) { emptyList() }
    }
''',
"shareLink": '''    suspend fun shareLink(id: String): String? {
        return try {
            val code = service.shareUrl(id).code
            if (code.isNotEmpty()) "https://${Obfuscator.SERVER_HOST}/s/$code/" else null
        } catch (_: Exception) { null }
    }
''',
}

# 方法块正则：签名行(可能多行参数) -> 方法体尾（下一 4 空格 `}` 或单行 = 表达式）
def method_block(name):
    # 匹配 `    suspend fun name(` 到方法结束
    return re.compile(
        r"(    suspend fun %s\(.*?\n)    \}" % re.escape(name), re.S)

def expr_block(name):
    # 匹配 `    suspend fun name(...) : Type = 表达式` 整行
    return re.compile(r"    suspend fun %s\([^)]*\)[^\n]*\n" % re.escape(name))

done, skipped = [], []
for name, new in REPL.items():
    m = method_block(name)
    mm = m.search(src)
    if mm:
        src = src[:mm.start()] + new + src[mm.end():]
        done.append(name)
        continue
    e = expr_block(name)
    ee = e.search(src)
    if ee:
        # 单行表达式方法：替换整行为新模板（新模板可能多行，含 = 或 { }）
        src = src[:ee.start()] + new + src[ee.end():]
        done.append(name)
        continue
    skipped.append(name)

open(SRC, "w", encoding="utf-8").write(src)
print("替换成功:", done)
print("跳过(未匹配):", skipped)
