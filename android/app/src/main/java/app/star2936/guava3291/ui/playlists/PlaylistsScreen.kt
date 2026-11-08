package app.star2936.guava3291.ui.playlists

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.star2936.guava3291.data.Api
import app.star2936.guava3291.data.Playlist
import app.star2936.guava3291.ui.components.StatusPlaceholder
import app.star2936.guava3291.ui.theme.Accent
import app.star2936.guava3291.ui.theme.Background
import app.star2936.guava3291.ui.theme.Card
import app.star2936.guava3291.ui.theme.CardBorder
import app.star2936.guava3291.ui.theme.TextPrimary
import app.star2936.guava3291.ui.theme.TextSecondary
import kotlinx.coroutines.launch

/** 歌单列表页（对应 iOS PlaylistsView） */
@Composable
fun PlaylistsScreen(onBack: () -> Unit, onOpenPlaylist: (String, String) -> Unit) {
    var playlists by remember { mutableStateOf<List<Playlist>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var showCreateDialog by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    suspend fun load() {
        playlists = Api.fetchPlaylists()
        loading = false
    }
    LaunchedEffect(Unit) { load() }

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
            Text("我的歌单", fontSize = 18.sp, fontWeight = FontWeight.Bold, color = TextPrimary, modifier = Modifier.weight(1f))
            IconButton(onClick = { showCreateDialog = true }) {
                Icon(Icons.Default.Add, contentDescription = "新建歌单", tint = Accent)
            }
        }

        if (loading) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                androidx.compose.material3.CircularProgressIndicator(color = TextSecondary)
            }
        } else if (playlists.isEmpty()) {
            StatusPlaceholder(text = "还没有歌单，点击右上角新建", modifier = Modifier.fillMaxSize())
        } else {
            LazyColumn(Modifier.fillMaxSize()) {
                items(playlists) { pl ->
                    Row(
                        Modifier.fillMaxWidth().clickable { onOpenPlaylist(pl.id, pl.name) }
                            .padding(horizontal = 16.dp, vertical = 10.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        if (!pl.cover.isNullOrEmpty()) {
                            coil.compose.AsyncImage(
                                model = pl.cover,
                                contentDescription = null,
                                modifier = Modifier.size(56.dp).clip(RoundedCornerShape(10.dp)),
                                contentScale = androidx.compose.ui.layout.ContentScale.Crop
                            )
                        } else {
                            Box(
                                Modifier.size(56.dp).clip(RoundedCornerShape(10.dp)).background(Card),
                                contentAlignment = Alignment.Center
                            ) {
                                Text(pl.name.take(1), color = Accent, fontSize = 20.sp)
                            }
                        }
                        Spacer(Modifier.width(14.dp))
                        Column(Modifier.weight(1f)) {
                            Text(pl.name, color = TextPrimary, fontSize = 16.sp, fontWeight = FontWeight.Medium, maxLines = 1)
                            // 描述（对齐 iOS PlaylistsView 显示 pl.description）
                            if (!pl.description.isNullOrEmpty()) {
                                Text(pl.description, color = TextSecondary, fontSize = 12.sp, maxLines = 1)
                            }
                            Text("${pl.trackCount} 首", color = TextSecondary, fontSize = 12.sp)
                        }
                    }
                }
            }
        }
    }

    if (showCreateDialog) {
        var name by remember { mutableStateOf("") }
        var description by remember { mutableStateOf("") }
        val ctx = androidx.compose.ui.platform.LocalContext.current
        AlertDialog(
            onDismissRequest = { showCreateDialog = false },
            title = { Text("新建歌单", color = TextPrimary) },
            text = {
                Column {
                    OutlinedTextField(
                        value = name,
                        onValueChange = { name = it },
                        label = { Text("歌单名称", color = TextSecondary) },
                        singleLine = true,
                        textStyle = androidx.compose.ui.text.TextStyle(color = TextPrimary),
                        colors = androidx.compose.material3.OutlinedTextFieldDefaults.colors(
                            focusedTextColor = TextPrimary,
                            unfocusedTextColor = TextPrimary,
                            focusedBorderColor = Accent,
                            unfocusedBorderColor = CardBorder,
                            focusedLabelColor = Accent,
                            unfocusedLabelColor = TextSecondary,
                            cursorColor = Accent,
                        )
                    )
                    Spacer(Modifier.height(10.dp))
                    OutlinedTextField(
                        value = description,
                        onValueChange = { description = it },
                        label = { Text("描述（可选）", color = TextSecondary) },
                        singleLine = true,
                        textStyle = androidx.compose.ui.text.TextStyle(color = TextPrimary),
                        colors = androidx.compose.material3.OutlinedTextFieldDefaults.colors(
                            focusedTextColor = TextPrimary,
                            unfocusedTextColor = TextPrimary,
                            focusedBorderColor = Accent,
                            unfocusedBorderColor = CardBorder,
                            focusedLabelColor = Accent,
                            unfocusedLabelColor = TextSecondary,
                            cursorColor = Accent,
                        )
                    )
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    if (name.isNotBlank()) {
                        scope.launch {
                            try {
                                Api.createPlaylist(name.trim(), description.trim())
                                showCreateDialog = false
                                load()
                            } catch (e: Exception) {
                                android.widget.Toast.makeText(ctx, "创建失败：${e.message ?: "未知错误"}", android.widget.Toast.LENGTH_SHORT).show()
                            }
                        }
                    }
                }) { Text("创建", color = Accent) }
            },
            dismissButton = { TextButton(onClick = { showCreateDialog = false }) { Text("取消", color = TextSecondary) } },
            containerColor = Card,
        )
    }
}
