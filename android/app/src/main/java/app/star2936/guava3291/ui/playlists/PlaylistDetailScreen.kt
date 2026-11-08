package app.star2936.guava3291.ui.playlists

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.star2936.guava3291.data.Api
import app.star2936.guava3291.data.Track
import app.star2936.guava3291.state.PlayerManager
import app.star2936.guava3291.ui.components.StatusPlaceholder
import app.star2936.guava3291.ui.theme.Accent
import app.star2936.guava3291.ui.theme.Background
import app.star2936.guava3291.ui.theme.Card
import app.star2936.guava3291.ui.theme.TextPrimary
import app.star2936.guava3291.ui.theme.TextSecondary
import kotlinx.coroutines.launch

/** 歌单详情页（对应 iOS PlaylistDetailView；补齐：每行可移除歌曲） */
@Composable
fun PlaylistDetailScreen(playlistId: String, playlistName: String, onBack: () -> Unit) {
    var tracks by remember { mutableStateOf<List<Track>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    val scope = rememberCoroutineScope()
    val context = androidx.compose.ui.platform.LocalContext.current

    LaunchedEffect(playlistId) {
        tracks = Api.fetchPlaylistTracks(playlistId)
        loading = false
    }

    /// 从歌单移除歌曲（对齐 iOS PlaylistsView.removeTrack：垃圾桶按钮 + 移除后刷新）
    fun removeTrack(t: Track) {
        scope.launch {
            try {
                Api.removeFromPlaylist(playlistId, t.id)
                tracks = tracks - t
                android.widget.Toast.makeText(context, "已移除", android.widget.Toast.LENGTH_SHORT).show()
            } catch (e: Exception) {
                android.widget.Toast.makeText(context, "移除失败：${e.message ?: "未知错误"}", android.widget.Toast.LENGTH_SHORT).show()
            }
        }
    }

    Column(
        Modifier
            .fillMaxSize()
            .background(Background)
            .statusBarsPadding()
            .navigationBarsPadding()
    ) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            // 返回键统一样式：圆底 chevron（与全 App 一致，对齐 iOS NavBack）
            Box(
                Modifier.size(38.dp).clip(CircleShape).background(Card).clickable { onBack() },
                contentAlignment = Alignment.Center
            ) { Icon(Icons.Filled.ArrowBack, null, tint = TextPrimary, modifier = Modifier.size(18.dp)) }
            Spacer(Modifier.width(12.dp))
            Column {
                Text(playlistName, fontSize = 18.sp, fontWeight = FontWeight.Bold, color = TextPrimary)
                Text("${tracks.size} 首", fontSize = 12.sp, color = TextSecondary)
            }
        }

        when {
            loading -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                androidx.compose.material3.CircularProgressIndicator(color = TextSecondary)
            }
            tracks.isEmpty() -> StatusPlaceholder(text = "歌单为空", modifier = Modifier.fillMaxSize())
            else -> LazyColumn(Modifier.fillMaxSize()) {
                items(tracks, key = { it.id }) { t ->
                    Row(
                        Modifier.fillMaxWidth()
                            .clickable { PlayerManager.applyFiniteList(t, tracks) }
                            .padding(horizontal = 16.dp, vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        // 封面
                        Box(Modifier.size(44.dp).clip(RoundedCornerShape(9.dp)).background(Card), contentAlignment = Alignment.Center) {
                            if (!t.cover.isNullOrEmpty()) {
                                coil.compose.AsyncImage(model = t.cover, contentDescription = null,
                                    modifier = Modifier.fillMaxSize(), contentScale = androidx.compose.ui.layout.ContentScale.Crop)
                            } else {
                                Text(t.title.take(1), color = Accent, fontSize = 18.sp)
                            }
                        }
                        Spacer(Modifier.width(12.dp))
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                            Text(t.title, color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Medium, maxLines = 1)
                            Text(t.displayAuthor ?: "未知歌手", color = TextSecondary, fontSize = 12.sp, maxLines = 1)
                        }
                        // 移除歌曲（对齐 iOS 垃圾桶按钮）
                        Icon(
                            Icons.Outlined.Delete,
                            contentDescription = "移除",
                            tint = Color(0xFFFF6B6B).copy(alpha = 0.7f),
                            modifier = Modifier
                                .clip(RoundedCornerShape(8.dp))
                                .clickable { removeTrack(t) }
                                .padding(6.dp)
                                .size(16.dp)
                        )
                    }
                }
            }
        }
    }
}
