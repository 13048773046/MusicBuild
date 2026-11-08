package app.star2936.guava3291.ui.sidebar

import androidx.compose.foundation.Image
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
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.DirectionsBus
import androidx.compose.material.icons.filled.DirectionsCar
import androidx.compose.material.icons.filled.DirectionsRun
import androidx.compose.material.icons.filled.Eco
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.MusicNote
import androidx.compose.material.icons.filled.Nightlight
import androidx.compose.material.icons.filled.NightsStay
import androidx.compose.material.icons.filled.SentimentSatisfied
import androidx.compose.material.icons.filled.Speaker
import androidx.compose.material.icons.filled.WaterDrop
import androidx.compose.material.icons.filled.WbSunny
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.star2936.guava3291.state.AuthManager
import app.star2936.guava3291.state.ListenMode
import app.star2936.guava3291.state.PlayerManager
import kotlinx.coroutines.launch
import app.star2936.guava3291.ui.theme.Accent

// 对应 iOS SidebarView.swift：听歌模式 + 场景音乐宫格 + 品牌区

private data class SceneItem(val icon: ImageVector, val title: String)

private val scenes = listOf(
    SceneItem(Icons.Filled.DirectionsBus, "通勤必听"),
    SceneItem(Icons.Filled.DirectionsCar, "驾车"),
    SceneItem(Icons.Filled.WbSunny, "起床"),
    SceneItem(Icons.Filled.Nightlight, "深夜 EMO"),
    SceneItem(Icons.Filled.Speaker, "DJ 模式"),
    SceneItem(Icons.Filled.NightsStay, "助眠模式"),
    SceneItem(Icons.Filled.DirectionsRun, "动感健身"),
    SceneItem(Icons.Filled.MusicNote, "抖音漫游"),
    SceneItem(Icons.Filled.WaterDrop, "洗澡"),
    SceneItem(Icons.Filled.Eco, "Chill 放松"),
    SceneItem(Icons.Filled.SentimentSatisfied, "快乐时光"),
    SceneItem(Icons.Filled.Mic, "电音"),
)

@Composable
fun SidebarContent(onClose: () -> Unit) {
    val listenMode by PlayerManager.listenMode.collectAsState()
    // 场景上下文：正在播放场景音乐时不高亮任何听歌模式，改高亮对应场景卡片
    val sceneTag by PlayerManager.sceneTag.collectAsState()
    // 侧滑栏选中态：仅侧滑栏主动选择才高亮（对标 iOS sidebarSelection）
    val sidebarSel by PlayerManager.sidebarSelection.collectAsState()
    val user by AuthManager.currentUser.collectAsState()
    // 用户是否有作品（没有则不显示"我的作品"按钮）
    var hasWorks by remember { mutableStateOf<Boolean?>(null) }
    LaunchedEffect(user?.id) {
        val uid = user?.id ?: run { hasWorks = false; return@LaunchedEffect }
        try {
            val r = app.star2936.guava3291.data.Api.myTracks(page = 1, pageSize = 1)
            hasWorks = r.list.isNotEmpty()
        } catch (_: Exception) {
            hasWorks = false
        }
    }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 0.dp)
    ) {
        // 顶部品牌区
        Row(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp)
                .padding(top = 30.dp, bottom = 26.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            androidx.compose.foundation.Image(
                painter = androidx.compose.ui.res.painterResource(app.star2936.guava3291.R.drawable.app_logo),
                contentDescription = null,
                modifier = Modifier.size(40.dp).clip(RoundedCornerShape(10.dp))
            )
            Spacer(Modifier.width(12.dp))
            Column {
                Text("流萤音乐", color = Color.White, fontSize = 20.sp, fontWeight = FontWeight.Black)
                Text(
                    user?.let { if (!it.signature.isNullOrEmpty()) it.signature else if (it.name.isNotEmpty()) it.name else "已登录" } ?: "登录后推荐更精准",
                    color = Color.White.copy(alpha = 0.5f), fontSize = 12.sp
                )
            }
        }

        // 听歌模式
        Text("听歌模式", color = Color.White.copy(alpha = 0.45f), fontSize = 13.sp, fontWeight = FontWeight.SemiBold,
            modifier = Modifier.padding(horizontal = 22.dp).padding(bottom = 10.dp))
        ListenMode.entries.forEach { mode ->
            // 用户没有作品时不显示"我的作品"按钮
            if (mode == ListenMode.Works && hasWorks == false) return@forEach
            SidebarModeButton(
                title = mode.label,
                icon = modeIcon(mode),
                selected = sceneTag == null &&
                    (sidebarSel == mode || (sidebarSel == null && listenMode == mode)),
                onClick = {
                    PlayerManager.sidebarSelection.value = mode
                    PlayerManager.applyListenMode(mode)
                    onClose()
                }
            )
        }
        Spacer(Modifier.height(22.dp))

        // 场景/每日推荐共用的加载态与协程作用域（需在两者之前声明）
        var sceneLoading by remember { mutableStateOf<String?>(null) }
        val scope = rememberCoroutineScope()

        // 每日推荐（2026-11-05）：后端按 (用户, 日期) 固定生成 30 首，
        // 当天多次进入结果一致、隔天换一批。做成整行大卡（比宫格更醒目），
        // 点击走场景播放入口（有限列表循环 + 场景高亮），与场景卡行为一致。
        DailyCard(
            loading = sceneLoading == "每日推荐",
            active = sceneTag == "每日推荐",
            onClick = {
                if (sceneLoading == null) {
                    sceneLoading = "每日推荐"
                    scope.launch {
                        try {
                            val tracks = app.star2936.guava3291.data.Api.daily(30)
                            val first = tracks.firstOrNull()
                            if (first != null) {
                                PlayerManager.playScene("每日推荐", first, tracks)
                                onClose()
                            }
                        } catch (_: Exception) {
                            // 弱网/失败保持现状，下次点击重试
                        } finally {
                            sceneLoading = null
                        }
                    }
                }
            }
        )

        Spacer(Modifier.height(22.dp))

        // 场景音乐
        Text("场景音乐", color = Color.White.copy(alpha = 0.45f), fontSize = 13.sp, fontWeight = FontWeight.SemiBold,
            modifier = Modifier.padding(horizontal = 22.dp).padding(bottom = 10.dp))
        LazyVerticalGrid(
            columns = GridCells.Fixed(3),
            modifier = Modifier
                .fillMaxWidth()
                .height(92.dp * ((scenes.size + 2) / 3)),  // 精确行数：12 项=4 行，避免多算一行空白
            horizontalArrangement = androidx.compose.foundation.layout.Arrangement.spacedBy(8.dp),
            verticalArrangement = androidx.compose.foundation.layout.Arrangement.spacedBy(22.dp),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp),
        ) {
            items(scenes) { item ->
                // 场景卡片点击：拉该场景个性化分类歌单（类型严格 + 按用户数据排序），
                // 取第一首直接播放、整单入队（切歌在队列内连续放同类型歌），随后收起侧边栏
                SceneGridItem(
                    icon = item.icon,
                    title = item.title,
                    loading = sceneLoading == item.title,
                    active = sceneTag == item.title,
                    onClick = {
                        if (sceneLoading != null) return@SceneGridItem   // 防重复点击
                        sceneLoading = item.title
                        scope.launch {
                            try {
                                // 每次点场景生成新 seed：后端按 seed 做分档窗口洗牌，
                                // 所以每次拉到的歌不同（同一次播放会话内队列固定、不跳变）
                                val seed = "${System.currentTimeMillis()}-${(1000..9999).random()}"
                                val tracks = app.star2936.guava3291.data.Api.radio(item.title, null, seed)
                                val first = tracks.firstOrNull()
                                if (first != null) {
                                    // 场景专用入口：保存当前模式会话 + 场景上下文置位 +
                                    // 有限列表循环 + 清搜索流，避免污染模式会话/续拉推荐
                                    PlayerManager.playScene(item.title, first, tracks)
                                    onClose()
                                }
                            } catch (_: Exception) {
                                // 拉取失败保持现状（可能弱网）；不做打断式弹窗，下一次点击重试即可
                            } finally {
                                sceneLoading = null
                            }
                        }
                    }
                )
            }
        }
        Spacer(Modifier.height(12.dp))
    }
}

private fun modeIcon(mode: ListenMode): ImageVector = when (mode) {
    ListenMode.Normal -> Icons.Filled.AutoAwesome
    ListenMode.Works -> Icons.Filled.MusicNote
    ListenMode.Liked -> Icons.Filled.Favorite
    ListenMode.Fresh -> Icons.Filled.Bolt
}

/**
 * 每日推荐大卡（2026-11-05）：整行横向卡片，比场景宫格更醒目。
 * 渐变底 + 星星图标 + 副标题说明，点下去直接开播当天 30 首。
 */
@Composable
private fun DailyCard(loading: Boolean, active: Boolean, onClick: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 20.dp)
            .clip(RoundedCornerShape(18.dp))
            .background(
                Brush.horizontalGradient(
                    listOf(
                        Accent.copy(alpha = if (active) 0.30f else 0.18f),
                        Accent.copy(alpha = if (active) 0.10f else 0.04f),
                    )
                )
            )
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier
                .size(44.dp)
                .clip(RoundedCornerShape(13.dp))
                .background(Accent.copy(alpha = 0.22f)),
            contentAlignment = Alignment.Center,
        ) {
            if (loading) {
                CircularProgressIndicator(
                    color = Accent,
                    strokeWidth = 2.dp,
                    modifier = Modifier.size(20.dp),
                )
            } else {
                Icon(
                    Icons.Filled.AutoAwesome,
                    contentDescription = null,
                    tint = Accent,
                    modifier = Modifier.size(22.dp),
                )
            }
        }
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f)) {
            Text(
                "每日推荐",
                fontSize = 16.sp,
                fontWeight = FontWeight.Bold,
                color = Color.White,
            )
            Spacer(Modifier.height(3.dp))
            Text(
                "为你挑的 30 首 · 每天换一批",
                fontSize = 12.sp,
                color = Color.White.copy(alpha = 0.55f),
            )
        }
        Icon(
            Icons.Filled.Bolt,
            contentDescription = null,
            tint = Accent.copy(alpha = if (active) 1f else 0.7f),
            modifier = Modifier.size(20.dp),
        )
    }
}

@Composable
private fun SidebarModeButton(title: String, icon: ImageVector, selected: Boolean, onClick: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 20.dp)
            .padding(vertical = 5.dp)
            .clip(RoundedCornerShape(16.dp))
            .background(if (selected) Accent.copy(alpha = 0.16f) else Color.White.copy(alpha = 0.05f))
            .clickable(onClick = onClick)
            .padding(horizontal = 14.dp)
            .height(58.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(
            Modifier
                .size(38.dp)
                .clip(RoundedCornerShape(11.dp))
                .background(if (selected) Accent else Color.White.copy(alpha = 0.1f)),
            contentAlignment = Alignment.Center
        ) {
            Icon(
                icon, contentDescription = null,
                tint = if (selected) Color(0xFF121214) else Color.White.copy(alpha = 0.85f),
                modifier = Modifier.size(16.dp)
            )
        }
        Spacer(Modifier.width(14.dp))
        Text(title, color = Color.White, fontSize = 16.sp, fontWeight = if (selected) FontWeight.Bold else FontWeight.SemiBold)
        Spacer(Modifier.weight(1f))
        if (selected) {
            Text("✓", color = Accent, fontSize = 13.sp, fontWeight = FontWeight.Bold)
        }
    }
}

@Composable
private fun SceneGridItem(icon: ImageVector, title: String, loading: Boolean = false,
                          active: Boolean = false, onClick: (() -> Unit)? = null) {
    Column(
        Modifier
            .fillMaxWidth()
            .clickable(enabled = onClick != null, onClick = { onClick?.invoke() }),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Box(
            Modifier
                .size(46.dp)
                .clip(RoundedCornerShape(14.dp))
                .background(if (active) Accent else Color.White.copy(alpha = 0.08f)),
            contentAlignment = Alignment.Center
        ) {
            if (loading) {
                CircularProgressIndicator(
                    modifier = Modifier.size(20.dp),
                    color = Color.White.copy(alpha = 0.92f),
                    strokeWidth = 2.dp
                )
            } else {
                Icon(
                    icon, contentDescription = null,
                    tint = if (active) Color(0xFF121214) else Color.White.copy(alpha = 0.92f),
                    modifier = Modifier.size(20.dp)
                )
            }
        }
        Spacer(Modifier.height(9.dp))
        Text(
            title,
            color = if (active) Accent else Color.White.copy(alpha = 0.75f),
            fontSize = 12.sp,
            fontWeight = if (active) FontWeight.SemiBold else FontWeight.Medium
        )
    }
}
