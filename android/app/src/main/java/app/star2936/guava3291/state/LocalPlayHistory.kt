package app.star2936.guava3291.state

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import app.star2936.guava3291.data.Api
import app.star2936.guava3291.data.Track
import kotlinx.coroutines.flow.first
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString

// P2-11：SharedPreferences -> DataStore（Preferences）。
// 行为契约不变：MRU 最近播放置顶、同曲去重置顶、JSON 上限 100 条、只存元数据不存音频。
// DataStore 保证原子写（避免多线程并发写损坏），读写均为挂起函数（协程友好）。
private val Context.dataStore by preferencesDataStore(name = "local_play_history")

object LocalPlayHistory {
    private val key = stringPreferencesKey("local_play_history_v1")
    private const val limit = 100

    private lateinit var appContext: Context

    @Serializable
    data class Item(
        val id: String,          // trackId 作为去重键
        val title: String,
        val artist: String,
        val cover: String,       // 封面 URL 字符串
        val created: Double,
    )

    fun init(context: Context) {
        appContext = context.applicationContext
    }

    /** 记录一次播放：已存在 → 移除旧记录置顶；不存在 → 插入头部；裁剪到上限 */
    suspend fun record(track: Track) {
        if (track.id.isEmpty()) return
        val list = load().toMutableList()
        list.removeAll { it.id == track.id }
        list.add(0, Item(
            id = track.id,
            title = track.title,
            artist = track.artist ?: "",
            cover = track.cover ?: "",
            created = System.currentTimeMillis() / 1000.0,
        ))
        if (list.size > limit) {
            list.subList(limit, list.size).clear()
        }
        save(list)
    }

    suspend fun load(): List<Item> {
        val raw = appContext.dataStore.data.first()[key] ?: return emptyList()
        return try {
            Api.json.decodeFromString<List<Item>>(raw)
        } catch (_: Exception) { emptyList() }
    }

    suspend fun clear() {
        appContext.dataStore.edit { it.remove(key) }
    }

    private suspend fun save(list: List<Item>) {
        try {
            appContext.dataStore.edit { it[key] = Api.json.encodeToString(list) }
        } catch (_: Exception) { }
    }
}
