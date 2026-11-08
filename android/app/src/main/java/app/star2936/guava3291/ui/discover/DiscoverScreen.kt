package app.star2936.guava3291.ui.discover

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.outlined.WifiOff
import androidx.compose.material.icons.filled.AcUnit
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.DeviceThermostat
import androidx.compose.material.icons.filled.NightsStay
import androidx.compose.material.icons.filled.Umbrella
import androidx.compose.material.icons.filled.WbSunny
import androidx.compose.material.icons.filled.WbTwilight
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.star2936.guava3291.data.Api
import app.star2936.guava3291.data.BehaviorEvent
import app.star2936.guava3291.data.Track
import app.star2936.guava3291.state.AppPreloader
import app.star2936.guava3291.state.Library
import app.star2936.guava3291.state.NotificationBus
import app.star2936.guava3291.state.PlayerManager
import app.star2936.guava3291.state.WeatherContext
import app.star2936.guava3291.ui.components.CoverImage
import app.star2936.guava3291.ui.components.StatusPlaceholder
import app.star2936.guava3291.ui.components.TrackRow
import app.star2936.guava3291.ui.theme.Accent
import app.star2936.guava3291.ui.theme.Background
import app.star2936.guava3291.ui.theme.Card
import app.star2936.guava3291.ui.theme.TextPrimary
import app.star2936.guava3291.ui.theme.TextSecondary
import app.star2936.guava3291.ui.theme.TextTertiary
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import kotlinx.coroutines.launch

// 对应 iOS DiscoverView.swift：猜你喜欢 + 最新上架 + 全部歌曲

private enum class DiscoverState { Loading, Failed, Empty, Loaded }

@Composable
fun DiscoverScreen() {
    var state by remember { mutableStateOf(DiscoverState.Loading) }
    var latest by remember { mutableStateOf<List<Track>>(emptyList()) }
    var recommended by remember { mutableStateOf<List<Track>>(emptyList()) }
    var refreshing by remember { mutableStateOf(false) }
    // P1 曝光负信号：本批推荐「展示未点击」池（(trackId, route)，换批/点击时批量上报 expose）
    var exposePool by remember { mutableStateOf<List<Pair<String, String>>>(emptyList()) }
    val libraryTracks by Library.tracks.collectAsState()
    val scope = androidx.compose.runtime.rememberCoroutineScope()

    val weatherAvailable by WeatherContext.isAvailable.collectAsState()
    val weatherScene by WeatherContext.scene.collectAsState()
    val weatherLabel by WeatherContext.sceneLabel.collectAsState()
    val weatherTemp by WeatherContext.temperature.collectAsState()
    val weatherCity by WeatherContext.city.collectAsState()

    val permLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { _ ->
        scope.launch { WeatherContext.refresh() }
    }

    LaunchedEffect(Unit) {
        permLauncher.launch(arrayOf(
            android.Manifest.permission.ACCESS_FINE_LOCATION,
            android.Manifest.permission.ACCESS_COARSE_LOCATION
        ))
        WeatherContext.refresh()
    }

    suspend fun loadRecommendedInner(): List<Track> {
        // 天气×时段场景参数（对齐 iOS DiscoverView.loadRecommended：lat/lng/hour）
        return try {
            Api.recommend(
                source = "discover",
                lat = WeatherContext.lat,
                lng = WeatherContext.lng,
                hour = WeatherContext.hour,
            )
        } catch (_: Exception) { emptyList() }
    }

    /** P1 曝光上报（节流）：推荐列表展示过的歌，换批/点击时批量上报 expose（未点击=负信号）。每批 ≤6 首。 */
    fun flushExpose(excluding: String? = null) {
        var pool = exposePool
        if (excluding != null) pool = pool.filter { it.first != excluding }
        exposePool = emptyList()
        if (pool.isEmpty()) return
        val evs = pool.map { (id, route) ->
            BehaviorEvent(trackId = id, event = "expose", value = 1.0,
                meta = BehaviorEvent.metaJSON(mapOf("route" to route)))
        }
        scope.launch { Api.reportBehavior(evs) }
    }

    /** 新一批推荐就位：先 flush 上一批未点击，再入新曝光池。 */
    fun resetExpose(tracks: List<Track>) {
        flushExpose()
        exposePool = tracks.take(6).map { it.id to (it.recRoute ?: "") }
    }

    fun load() {
        if (!Library.isLoaded.value) {
            state = DiscoverState.Loading
            Library.load()
        }
        // 无论是否已加载，都 observe tracks 变化并更新 sections
        applySections(libraryTracks, recommended, latest, state, { state = it }, { recommended = it }, { latest = it }, scope)
    }

    // WS 事件：recommend → 重拉推荐；track_list → 重载曲库
    val lastDataEvent by NotificationBus.lastDataEvent.collectAsState()
    LaunchedEffect(lastDataEvent) {
        val ev = lastDataEvent ?: return@LaunchedEffect
        when (ev.event) {
            "recommend" -> scope.launch {
                val t = loadRecommendedInner()
                if (t.isNotEmpty()) { resetExpose(t); recommended = t }
            }
            "track_list" -> {
                Library.load(force = true)
                applySections(libraryTracks, recommended, latest, state, { state = it }, { recommended = it }, { latest = it }, scope)
            }
            else -> {}
        }
    }

    LaunchedEffect(Unit) {
        load()
        // 强制拉一次猜你喜欢
        val t = loadRecommendedInner()
        if (t.isNotEmpty()) { resetExpose(t); recommended = t }
    }

    // observe tracks 变化：首次空 → 加载完成后自动刷新 sections
    // 注意：key 不要带 recommended，否则"换一批"更新推荐后会触发 applySections 把新列表覆盖回旧的
    LaunchedEffect(libraryTracks) {
        if (libraryTracks.isNotEmpty()) {
            applySections(libraryTracks, recommended, latest, state, { state = it }, { recommended = it }, { latest = it }, scope)
        }
    }

    // 内容区
    when (state) {
        DiscoverState.Loading -> Column(
            Modifier.fillMaxSize(),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center
        ) {
            CircularProgressIndicator(color = Accent, strokeWidth = 2.dp)
            Spacer(Modifier.height(14.dp))
            Text("加载中…", color = TextSecondary, fontSize = 13.sp)
        }
        DiscoverState.Failed -> StatusPlaceholder(
            icon = Icons.Outlined.WifiOff,
            text = "加载失败\n请检查网络后重试",
            modifier = Modifier.fillMaxSize()
        )
        DiscoverState.Empty -> StatusPlaceholder(
            icon = Icons.Filled.AutoAwesome,
            text = "暂时没有可发现的内容\n去首页上传歌曲后，这里会有推荐",
            modifier = Modifier.fillMaxSize()
        )
        DiscoverState.Loaded -> DiscoverContent(
            latest = latest,
            recommended = recommended,
            allTracks = libraryTracks,
            weatherAvailable = weatherAvailable,
            weatherScene = weatherScene,
            weatherLabel = weatherLabel,
            weatherTemp = weatherTemp,
            weatherCity = weatherCity,
            onRefreshWeather = { scope.launch { WeatherContext.refresh(force = true) } },
            onPlayRecommended = { track ->
                Library.searchContext.value = null
                PlayerManager.noteRecommended(recommended.map { it.id })
                PlayerManager.finiteLoopList.value = false
                // P1 曝光负信号：用户已选择，本批其余未点击的歌上报 expose
                flushExpose(excluding = track.id)
                PlayerManager.playInNormalMode(track, recommended)
            },
            onPlayDiscover = { track -> Library.playFromDiscover(track) },
            onRefresh = {
                refreshing = true
                Library.load(force = true)
                scope.launch {
                    val t = loadRecommendedInner()
                    if (t.isNotEmpty()) { resetExpose(t); recommended = t }
                    applySections(libraryTracks, recommended, latest, state, { state = it }, { recommended = it }, { latest = it }, scope)
                    refreshing = false
                }
            },
            onRefreshRecommended = { scope.launch {
                val t = loadRecommendedInner()
                if (t.isNotEmpty()) { resetExpose(t); recommended = t }
            } },
            refreshing = refreshing,
        )
    }
}

private fun applySections(
    tracks: List<Track>,
    recommended: List<Track>,
    latest: List<Track>,
    currentState: DiscoverState,
    setState: (DiscoverState) -> Unit,
    setRecommended: (List<Track>) -> Unit,
    setLatest: (List<Track>) -> Unit,
    scope: kotlinx.coroutines.CoroutineScope,
) {
    if (tracks.isEmpty()) {
        setState(DiscoverState.Empty)
    } else {
        val sorted = tracks.sortedByDescending { it.created ?: 0.0 }
        setLatest(sorted.take(6))
        setState(DiscoverState.Loaded)
    }
    if (AppPreloader.discoverRecommendedReady.value) {
        setRecommended(AppPreloader.recommended.value)
    }
    // 推荐未就绪则异步拉取
    if (!AppPreloader.discoverRecommendedReady.value) {
        scope.launch {
            try { setRecommended(Api.recommend().take(6)) } catch (_: Exception) { }
        }
    }
}

@Composable
private fun DiscoverContent(
    latest: List<Track>,
    recommended: List<Track>,
    allTracks: List<Track>,
    weatherAvailable: Boolean,
    weatherScene: String?,
    weatherLabel: String?,
    weatherTemp: Double?,
    weatherCity: String?,
    onRefreshWeather: () -> Unit,
    onPlayRecommended: (Track) -> Unit,
    onPlayDiscover: (Track) -> Unit,
    onRefresh: () -> Unit,
    onRefreshRecommended: () -> Unit,
    refreshing: Boolean,
) {
    val recommendSubtitle = remember(recommended) {
        val seen = mutableSetOf<String>()
        val unique = recommended.mapNotNull { it.reason }.filter { it.isNotEmpty() && seen.add(it) }
        if (unique.isEmpty()) "根据你的听歌行为推荐" else unique.take(3).joinToString(" · ")
    }

    Box(Modifier.fillMaxSize().statusBarsPadding()) {
        // 下拉刷新（对齐 iOS DiscoverView.refreshable：重拉曲库 + 推荐）
        @OptIn(ExperimentalMaterial3Api::class)
        PullToRefreshBox(
            isRefreshing = refreshing,
            onRefresh = onRefresh,
            modifier = Modifier.fillMaxSize(),
        ) {
            LazyColumn(
            Modifier.fillMaxSize(),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(top = 8.dp, bottom = 100.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            // 头部
            item {
                Row(
                    Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 20.dp)
                        .padding(top = 8.dp),
                    verticalAlignment = Alignment.Bottom
                ) {
                    Column {
                        Text("发现音乐", color = TextPrimary, fontSize = 30.sp, fontWeight = FontWeight.Bold)
                        Text("好听的不止一首", color = TextSecondary, fontSize = 14.sp)
                    }
                    Spacer(Modifier.weight(1f))
                    Box(
                        Modifier
                            .size(42.dp)
                            .clip(CircleShape)
                            .background(Accent)
                            .shadow(12.dp, CircleShape),
                        contentAlignment = Alignment.Center
                    ) {
                        Icon(Icons.Filled.AutoAwesome, contentDescription = null, tint = androidx.compose.ui.graphics.Color.White, modifier = Modifier.size(18.dp))
                    }
                }
            }

            // M2 今日心情卡片（默认显示，定位失败时用当前时段）
            item {
                val defaultScene = remember {
                    when (android.os.Build.VERSION.SDK_INT) { else -> "" }
                }
                WeatherCard(
                    scene = weatherScene ?: "clear_day_afternoon",
                    label = weatherLabel ?: "今日好歌",
                    temp = weatherTemp,
                    city = weatherCity,
                    onRefresh = onRefreshWeather
                )
            }

            // 猜你喜欢
            if (recommended.isNotEmpty()) {
                item {
                    Row(
                        Modifier.fillMaxWidth().padding(top = 4.dp),
                        verticalAlignment = Alignment.Bottom
                    ) {
                        Column(Modifier.padding(horizontal = 20.dp)) {
                            Text("猜你喜欢", color = TextPrimary, fontSize = 19.sp, fontWeight = FontWeight.Bold)
                            Text(recommendSubtitle, color = TextTertiary, fontSize = 12.sp)
                        }
                        Spacer(Modifier.weight(1f))
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            modifier = Modifier
                                .padding(end = 20.dp, bottom = 2.dp)
                                .clip(RoundedCornerShape(6.dp))
                                .clickable { onRefreshRecommended() }
                                .padding(4.dp)
                        ) {
                            Icon(Icons.Default.Refresh, contentDescription = null, tint = Accent, modifier = Modifier.size(12.dp))
                            Spacer(Modifier.width(4.dp))
                            Text("换一批", color = Accent, fontSize = 12.sp, fontWeight = FontWeight.Medium)
                        }
                    }
                }
                item {
                    // 推荐列表淡入淡出过渡，避免"换一批"时整列闪烁
                    val recommendAlpha by androidx.compose.animation.core.animateFloatAsState(
                        targetValue = if (recommended.isNotEmpty()) 1f else 0f,
                        animationSpec = androidx.compose.animation.core.tween(300),
                        label = "recommendFade"
                    )
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .horizontalScroll(rememberScrollState())
                            .padding(horizontal = 20.dp)
                            .graphicsLayer { alpha = recommendAlpha },
                        horizontalArrangement = Arrangement.spacedBy(14.dp)
                    ) {
                        recommended.forEach { track ->
                            RecommendCard(track = track, onClick = { onPlayRecommended(track) })
                        }
                    }
                }
            }

            // 最新上架
            if (latest.isNotEmpty()) {
                item {
                    Column(Modifier.padding(horizontal = 20.dp)) {
                        Text("最新上架", color = TextPrimary, fontSize = 19.sp, fontWeight = FontWeight.Bold)
                        Text("刚刚入库的新鲜音乐", color = TextTertiary, fontSize = 12.sp)
                    }
                }
                item {
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .horizontalScroll(rememberScrollState())
                            .padding(horizontal = 20.dp),
                        horizontalArrangement = Arrangement.spacedBy(14.dp)
                    ) {
                        latest.forEach { track ->
                            LatestCard(track = track, onClick = { onPlayDiscover(track) })
                        }
                    }
                }
            }

            // 全部歌曲
            item {
                Column {
                    Column(Modifier.padding(horizontal = 20.dp)) {
                        Text("全部歌曲", color = TextPrimary, fontSize = 19.sp, fontWeight = FontWeight.Bold)
                        Text("共 ${allTracks.size} 首", color = TextTertiary, fontSize = 12.sp)
                    }
                    Spacer(Modifier.height(10.dp))
                    allTracks.forEach { track ->
                        Box(
                            Modifier
                                .padding(horizontal = 16.dp, vertical = 2.dp)
                                .clip(RoundedCornerShape(12.dp))
                                .background(Card)
                                .clickable { onPlayDiscover(track) }
                        ) {
                            TrackRow(track = track, onClick = { onPlayDiscover(track) })
                        }
                    }
                }
            }
            }
        }
    }
}

@Composable
private fun WeatherCard(scene: String, label: String, temp: Double?, city: String?, onRefresh: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 20.dp).clip(RoundedCornerShape(16.dp)).background(Card).padding(14.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(
            Modifier.size(48.dp).clip(RoundedCornerShape(14.dp)).background(Accent.copy(alpha = 0.12f)),
            contentAlignment = Alignment.Center
        ) {
            // 图标对齐 iOS SF Symbols 语义（Material 图标替代 emoji）
            Icon(weatherIcon(scene), contentDescription = null, tint = Accent, modifier = Modifier.size(22.dp))
        }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text("今日心情 · $label", color = TextPrimary, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(3.dp))
            Row {
                temp?.let { Text("${it.toInt()}°C", color = TextSecondary, fontSize = 12.sp) }
                if (!city.isNullOrEmpty()) Text(" · $city", color = TextSecondary, fontSize = 12.sp)
            }
        }
        Icon(
            Icons.Default.Refresh, contentDescription = null, tint = Accent,
            modifier = Modifier.size(16.dp).clickable { onRefresh() }
        )
    }
}

private fun weatherIcon(scene: String): androidx.compose.ui.graphics.vector.ImageVector = when (scene) {
    "rain" -> Icons.Filled.Umbrella
    "snow" -> Icons.Filled.AcUnit
    "hot" -> Icons.Filled.WbSunny
    "cold" -> Icons.Filled.DeviceThermostat
    "clear_day_morning", "clear_day_afternoon" -> Icons.Filled.WbSunny
    "clear_day_evening" -> Icons.Filled.WbTwilight
    "night" -> Icons.Filled.NightsStay
    "cloudy" -> Icons.Filled.Cloud
    else -> Icons.Filled.AutoAwesome
}

@Composable
private fun RecommendCard(track: Track, onClick: () -> Unit) {
    Column(
        Modifier
            .width(190.dp)
            .clickable(onClick = onClick)
    ) {
        Box {
            CoverImage(track = track, modifier = Modifier.size(190.dp), cornerRadius = 18.dp)
            Box(
                Modifier
                    .align(Alignment.TopEnd)
                    .padding(8.dp)
                    .clip(androidx.compose.foundation.shape.RoundedCornerShape(percent = 50))
                    .background(Accent)
                    .padding(horizontal = 8.dp, vertical = 4.dp)
            ) {
                Text(track.reason ?: "推荐", color = androidx.compose.ui.graphics.Color.White, fontSize = 10.sp, fontWeight = FontWeight.Bold)
            }
        }
        Spacer(Modifier.height(10.dp))
        Text(track.title, color = TextPrimary, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, maxLines = 1)
        Text(track.displayAuthor, color = TextSecondary, fontSize = 12.sp, maxLines = 1)
    }
}

@Composable
private fun LatestCard(track: Track, onClick: () -> Unit) {
    Column(
        Modifier
            .width(150.dp)
            .clickable(onClick = onClick)
    ) {
        CoverImage(track = track, modifier = Modifier.size(150.dp), cornerRadius = 16.dp)
        Spacer(Modifier.height(8.dp))
        Text(track.title, color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, maxLines = 1)
        Text(track.displayAuthor, color = TextSecondary, fontSize = 12.sp, maxLines = 1)
    }
}
