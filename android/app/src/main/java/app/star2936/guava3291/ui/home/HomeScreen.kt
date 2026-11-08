package app.star2936.guava3291.ui.home

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.asPaddingValues
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.pager.VerticalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.VolumeUp
import androidx.compose.material.icons.filled.Waves
import androidx.compose.material.icons.filled.AllInclusive
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.MusicNote
import androidx.compose.material.icons.filled.Album
import androidx.compose.material3.Icon
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.unit.IntOffset
import app.star2936.guava3291.R
import app.star2936.guava3291.data.Api
import app.star2936.guava3291.data.BehaviorEvent
import app.star2936.guava3291.data.Track
import app.star2936.guava3291.state.AuthManager
import app.star2936.guava3291.state.LyricsLinesCache
import app.star2936.guava3291.state.NetworkMonitor
import app.star2936.guava3291.state.PlayerManager
import app.star2936.guava3291.state.AppRouter
import app.star2936.guava3291.ui.components.CoverImage
import app.star2936.guava3291.ui.components.MarqueeText
import app.star2936.guava3291.ui.theme.Accent
import app.star2936.guava3291.ui.theme.Card
import app.star2936.guava3291.ui.theme.TextPrimary
import app.star2936.guava3291.ui.theme.TextSecondary
import app.star2936.guava3291.ui.theme.TextTertiary
import app.star2936.guava3291.util.KRCLine
import app.star2936.guava3291.util.parseLyrics
import app.star2936.guava3291.util.formatTime
import kotlin.math.min
import kotlin.math.roundToInt
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

// 对应 iOS VerticalPager.swift + LyricView.swift：播放页竖滑切歌 + 歌词渲染

@Composable
fun HomeScreen() {
    val queue by PlayerManager.queue.collectAsState()
    val currentIndex by PlayerManager.currentIndex.collectAsState()

    val initial = if (currentIndex in queue.indices) currentIndex else 0
    val pagerState = rememberPagerState(initialPage = initial) { queue.size }

    // 用户翻页完成 → 切歌（对标 iOS didFinishAnimating：滑动结束稳定后才回调）
    LaunchedEffect(pagerState.settledPage, queue.size) {
        if (pagerState.isScrollInProgress) return@LaunchedEffect
        if (pagerState.settledPage != currentIndex) {
            val t = queue.getOrNull(pagerState.settledPage)
            if (t != null) PlayerManager.play(t, queue)
        }
    }
    // 滑到倒数第 2 页时预加载下一批歌曲
    LaunchedEffect(pagerState.settledPage, queue.size) {
        if (pagerState.settledPage >= queue.size - 2 && queue.isNotEmpty()) {
            PlayerManager.preloadNextBatch()
        }
    }
    // 自动切歌（播完/通知栏切歌）→ Pager 平滑跟随翻页
    // 对标 iOS updateUIViewController：用户手势翻页期间禁止程序化回跳
    // 用户滑动切歌时 settledPage 已等于 currentIndex，不会触发这里翻页
    LaunchedEffect(currentIndex, queue.size) {
        if (pagerState.isScrollInProgress) return@LaunchedEffect
        if (currentIndex in queue.indices
            && currentIndex != pagerState.settledPage
            && currentIndex != pagerState.currentPage
        ) {
            pagerState.animateScrollToPage(currentIndex)
        }
    }

    VerticalPager(
        state = pagerState,
        modifier = Modifier.fillMaxSize(),
        key = { page -> queue.getOrNull(page)?.id ?: page },
    ) { page ->
        val track = queue.getOrNull(page)
        if (track != null) {
            key(track.id) {
                PlayerCard(track = track, active = page == currentIndex)
            }
        }
    }
}

/** 单曲播放卡片：封面 + 歌名 + 歌词预览 + 互动行 + 进度条（严格对齐 iOS VerticalPager 布局） */
@Composable
fun PlayerCard(track: Track, active: Boolean) {
    // 每个卡片独立提取自己的封面主色
    var cardDominant by remember(track.id) { mutableStateOf<androidx.compose.ui.graphics.Color?>(null) }
    LaunchedEffect(track.id) {
        val url = track.coverURL ?: return@LaunchedEffect
        val cached = app.star2936.guava3291.util.ColorPalette.colorFor(track.id, url)
        if (cached != null) {
            cardDominant = cached
        } else {
            app.star2936.guava3291.util.ColorPalette.load(track.id, url)?.let {
                cardDominant = it
            }
        }
    }
    if (active) {
        cardDominant?.let { PlayerManager.playerCardDominant.value = it }
    }
    val isPlaying by PlayerManager.isPlaying.collectAsState()
    val isBuffering by PlayerManager.isBuffering.collectAsState()
    val loadFailed by PlayerManager.loadFailed.collectAsState()
    val selectedEQ by PlayerManager.selectedEQ.collectAsState()
    val lyricsState = rememberLyrics(track.id)
    val lyrics = lyricsState.lines
    val progress = rememberProgressState(active)
    val duration by PlayerManager.duration.collectAsState()
    // 拖动进度条时的预览值（歌词预览跟随，对齐 iOS LiveLyricView scrubFraction）
    var scrubFraction by remember { mutableStateOf<Float?>(null) }
    var showTrackMenu by remember { mutableStateOf(false) }
    var showEQPanel by remember { mutableStateOf(false) }
    var showAddToPlaylist by remember { mutableStateOf(false) }
    var showFullLyrics by remember { mutableStateOf(false) }
    // 相似歌曲弹窗（2026-11-05）：跨歌手语义邻居
    var showSimilar by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    // 关注按钮状态（对标 iOS）：null=未查询；非空时按钮才显示。
    // 拉取失败默认「未关注」，保证有归属用户的歌曲一定看得到关注按钮。
    val currentUid = AuthManager.currentUser.value?.id
    var followed by remember(track.id) { mutableStateOf<Boolean?>(null) }
    LaunchedEffect(track.id, currentUid) {
        val uid = track.userId
        if (!uid.isNullOrEmpty() && uid != currentUid && currentUid != null) {
            followed = runCatching { Api.fetchUserProfile(uid).isFollowing }.getOrDefault(false)
        }
    }

    // 背景：主色（或深色兜底）整体动画淡入，同色相三档渐变由 gradientShades 实时生成
    val targetDominant = cardDominant ?: Color(0xFF1C1C1F)
    val animatedDominant by androidx.compose.animation.animateColorAsState(
        targetValue = targetDominant,
        animationSpec = androidx.compose.animation.core.tween(durationMillis = 600),
        label = "dominant"
    )
    // 同色相三档（顶部提亮 0.62 / 中部本色 0.50 / 底部同色相加深 0.30），色相饱和度保真
    val shades = app.star2936.guava3291.util.ColorPalette.gradientShades(animatedDominant)

    // 沉浸式：动态获取系统状态栏/导航栏高度
    val statusBarTop = WindowInsets.statusBars.asPaddingValues().calculateTopPadding()
    val navBarBottom = WindowInsets.navigationBars.asPaddingValues().calculateBottomPadding()
    // 顶部：状态栏 + APP TopBar(56dp) + 间距6dp
    val topPadding = statusBarTop + 56.dp + 6.dp
    // 底部：系统导航栏 + TabBar 高度（TabBar 现在浮在内容上面）
    val bottomPadding = navBarBottom + 64.dp

    BoxWithConstraints(
        Modifier
            .fillMaxSize()
    ) {
        // ===== 背景四层（对齐 iOS：同色相渐变 + 氛围光 + 轻黑罩 + 顶部可读性兜底）=====
        // 层 1：同色相三档渐变（顶部提亮空气感 → 中部本色 → 底部同色相加深）
        Box(
            Modifier
                .fillMaxSize()
                .background(
                    Brush.verticalGradient(
                        colorStops = arrayOf(
                            0f to shades.first,
                            0.35f to shades.second,
                            1f to shades.third
                        )
                    )
                )
        )
        // 层 2：顶部氛围光（白色 0.07 径向，模拟封面"透光"质感）
        Box(
            Modifier
                .fillMaxSize()
                .background(
                    Brush.radialGradient(
                        colors = listOf(Color.White.copy(alpha = 0.07f), Color.Transparent),
                        center = androidx.compose.ui.geometry.Offset(0.5f, 0f),
                        radius = 1600f
                    )
                )
        )
        // 层 3：底部轻黑罩（45% 前透明 → 62% 处黑 0.15 → 底部保持，只给歌词/控件轻微对比）
        Box(
            Modifier
                .fillMaxSize()
                .background(
                    Brush.verticalGradient(
                        colorStops = arrayOf(
                            0f to Color.Transparent,
                            0.45f to Color.Transparent,
                            0.62f to Color.Black.copy(alpha = 0.15f),
                            1f to Color.Black.copy(alpha = 0.15f)
                        )
                    )
                )
        )
        // 层 4：顶部 12% 黑微渐变（状态栏/全局 TopBar 区域白字在亮色背景下的可读性兜底）
        Box(
            Modifier
                .fillMaxSize()
                .background(
                    Brush.verticalGradient(
                        colorStops = arrayOf(
                            0f to Color.Black.copy(alpha = 0.22f),
                            0.12f to Color.Transparent
                        )
                    )
                )
        )
        // 音频频谱背景（可选，设置开关控制）
        val spectrumEnabled by app.star2936.guava3291.state.VisualizationSettings.audioSpectrumEnabled.collectAsState()
        if (spectrumEnabled && active) {
            // 光效 Canvas 只占底部 45%（原绘制区域即屏高 55% 以下），视觉不变、重绘面积减半
            AudioSpectrumBackground(
                tint = cardDominant ?: Accent,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(maxHeight * 0.45f)
                    .align(Alignment.BottomCenter)
            )
        }
        // 封面大小 = 屏宽-28dp，不超过可用高度减顶部/底部预留
        val screenW = androidx.compose.ui.platform.LocalConfiguration.current.screenWidthDp.dp
        val screenH = androidx.compose.ui.platform.LocalConfiguration.current.screenHeightDp.dp
        // 尺寸缩放因子：以 800dp 屏高为基准，小屏紧凑、大屏舒展
        val s = (screenH.value / 800f).coerceIn(0.85f, 1.15f)
        fun Float.dpS() = (this * s).dp
        fun Int.dpS() = (this * s).dp
        fun Float.spS() = (this * s).sp

        val bottomReserved = 260.dpS() + bottomPadding
        val coverSize = minOf(screenW - 28.dp, maxHeight - bottomReserved - topPadding - 16.dp)

        // 封面：居中在上半部分
        Box(
            Modifier
                .fillMaxWidth()
                .align(Alignment.TopCenter)
                .padding(top = topPadding + 16.dp),
            contentAlignment = Alignment.TopCenter
        ) {
            Box(
                Modifier
                    .size(coverSize)
                    .clip(RoundedCornerShape(20.dp))
            ) {
                CoverImage(track = track, modifier = Modifier.fillMaxSize(), cornerRadius = 20.dp)
            }
        }

        // 底部区：歌名/歌词/互动/进度条，紧贴 AppTabBar 上方
        Column(
            Modifier
                .fillMaxWidth()
                .align(Alignment.BottomCenter)
                .padding(horizontal = 14.dp)
                .padding(bottom = bottomPadding),
            horizontalAlignment = Alignment.Start
        ) {
            // 歌名行
            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column(Modifier.weight(1f)) {
                    MarqueeText(
                        text = track.title,
                        style = androidx.compose.ui.text.TextStyle(
                            color = TextPrimary, fontSize = 24f.spS(), fontWeight = FontWeight.Bold
                        )
                    )
                    Spacer(Modifier.height(4.dpS()))
                    // 作者行：发布者名称（点击跳主页）+ 关注按钮（对标 iOS，跟在作者名后面）
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            track.displayAuthor,
                            color = TextSecondary, fontSize = 15f.spS(), maxLines = 1,
                            modifier = Modifier
                                .weight(1f, fill = false)
                                .clickable { track.userId?.let { AppRouter.navigateToUserProfile(it) } }
                        )
                        // 关注按钮：已登录、有归属用户、且不是自己时才显示；
                        // 已关注=灰胶囊，未关注=鲜亮蓝渐变（对齐 iOS #3D7BFF→#2A6DF4）
                        val tuid = track.userId
                        if (currentUid != null && !tuid.isNullOrEmpty() && tuid != currentUid && followed != null) {
                            val isF = followed == true
                            Spacer(Modifier.width(10.dpS()))
                            Box(
                                Modifier
                                    .clip(androidx.compose.foundation.shape.RoundedCornerShape(percent = 50))
                                    .background(
                                        if (isF) Brush.verticalGradient(listOf(Color(0x29FFFFFF), Color(0x29FFFFFF)))
                                        else Brush.verticalGradient(
                                            listOf(Color(0xFF3D7BFF), Color(0xFF2A6DF4))
                                        )
                                    )
                                    .clickable {
                                        scope.launch {
                                            val before = followed
                                            followed = !(before == true)
                                            runCatching { Api.toggleFollow(tuid) }
                                                .onSuccess { followed = it }
                                                .onFailure { followed = before }
                                        }
                                    }
                                    .padding(horizontal = 14.dpS(), vertical = 5.dpS())
                            ) {
                                Text(
                                    if (isF) "已关注" else "关注",
                                    fontSize = 12f.spS(), fontWeight = FontWeight.SemiBold,
                                    color = if (isF) Color.White.copy(alpha = 0.65f) else Color.White
                                )
                            }
                        }
                    }
                }
            }

            Spacer(Modifier.height(12.dpS()))

            // 歌词预览（对齐 iOS LyricPreview：视口只露两行，第三行超出部分
            // 由 LiveLyricPreview 的 clipToBounds 直接裁掉，无遮罩层；
            // 滚动滑入时第三行叠在背景上，下方互动行自然承接）
            Box(
                Modifier
                    .fillMaxWidth()
                    .height(54.dpS())
                    .clip(RoundedCornerShape(0.dp))
                    .clickable { if (lyrics.isNotEmpty()) showFullLyrics = true }
            ) {
                if (loadFailed) {
                    LoadFailedView(track = track)
                } else if (lyrics.isEmpty()) {
                    NoLyricPlaceholder(artist = track.displayAuthor)
                } else {
                    LiveLyricPreview(
                        lines = lyrics,
                        // 拖动进度条时歌词跟随预览位置（对齐 iOS scrubFraction 联动）
                        progressSec = scrubFraction?.let { it * duration } ?: progress,
                    )
                }
            }

            Spacer(Modifier.height(12.dpS()))

            // 互动行 + 三点菜单
            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically
            ) {
                InteractionRow(track = track, modifier = Modifier.weight(1f))
                Box(
                    Modifier
                        .size(34.dpS())
                        .clip(RoundedCornerShape(10.dpS()))
                        .clickable { showTrackMenu = true },
                    contentAlignment = Alignment.Center
                ) {
                    Text("⋮", color = TextPrimary, fontSize = 20f.spS(), fontWeight = FontWeight.Bold)
                }
            }

            Spacer(Modifier.height(10.dpS()))

            // 进度条（根据设置开关：普通进度条 / 波形进度条）
            val waveformEnabled by app.star2936.guava3291.state.VisualizationSettings.waveformBarEnabled.collectAsState()
            if (waveformEnabled) {
                WaveformProgressBar(
                    scrubFraction = scrubFraction,
                    onScrubChange = { scrubFraction = it },
                    active = active,
                )
            } else {
                PlayerProgressBar(
                    scrubFraction = scrubFraction,
                    onScrubChange = { scrubFraction = it },
                    active = active,
                )
            }
        }
    }

    // 三点菜单弹窗
    if (showTrackMenu) {
        TrackMenuSheet(
            track = track,
            eqName = selectedEQ,
            onDismiss = { showTrackMenu = false },
            onEQ = { showEQPanel = true },
            onFullScreen = { showFullLyrics = true },
            onAddToPlaylist = { showAddToPlaylist = true },
            onSimilar = { showSimilar = true },
            onDislike = {
                // 不感兴趣：上报后立刻换下一首（后端会把这首歌从推荐里排除）
                scope.launch {
                    app.star2936.guava3291.data.Api.dislike(track.id, "track")
                    PlayerManager.next()
                }
            },
        )
    }

    // 相似歌曲弹窗
    if (showSimilar) {
        SimilarSongsSheet(
            seed = track,
            onDismiss = { showSimilar = false },
            onPick = { picked, list ->
                showSimilar = false
                PlayerManager.play(picked, list)
            },
        )
    }

    // 添加到歌单
    if (showAddToPlaylist) {
        AddToPlaylistSheet(track = track, onDismiss = { showAddToPlaylist = false })
    }

    // EQ 音效面板
    if (showEQPanel) {
        EQPanel(current = selectedEQ, onSelect = { name ->
            PlayerManager.applyEQ(name)
            showEQPanel = false
        }, onDismiss = { showEQPanel = false })
    }

    // 全屏歌词（独立横屏 Activity，避免主 Activity 配置变化递归崩溃）
    if (showFullLyrics) {
        val ctx = androidx.compose.ui.platform.LocalContext.current
        LaunchedEffect(Unit) {
            try {
                val intent = android.content.Intent(ctx, app.star2936.guava3291.FullScreenLyricActivity::class.java)
                intent.putExtra("trackId", track.id)
                // 叠加淡入转场（与全页面跳转统一：新页淡入叠加、旧页淡出，不滑动不缩放）
                val opts = android.app.ActivityOptions.makeCustomAnimation(
                    ctx,
                    app.star2936.guava3291.R.anim.fade_in,
                    app.star2936.guava3291.R.anim.fade_out
                )
                ctx.startActivity(intent, opts.toBundle())
            } catch (_: Exception) {
                // Activity 启动异常（组件未注册等）：兜底不闪退
            }
            showFullLyrics = false
        }
    }
}

// 状态桥接：播放页常用状态的 remember 包装
// 高频进度（4Hz）只在前台可见页订阅；离屏页保持最后已知值、不再逐帧重组
@Composable
private fun rememberProgressState(active: Boolean): Double {
    val progress by androidx.compose.runtime.produceState(
        initialValue = PlayerManager.progress.value,
        key1 = active,
    ) {
        if (active) PlayerManager.progress.collect { value = it }
    }
    val duration by PlayerManager.duration.collectAsState()
    return progress * duration
}

// 歌词加载（内存缓存）
@Composable
private fun rememberLyrics(trackId: String): LyricsState {
    var lines by remember(trackId) { mutableStateOf(LyricsLinesCache.map[trackId] ?: emptyList()) }
    LaunchedEffect(trackId) {
        val cached = LyricsLinesCache.map[trackId]
        if (cached != null) { lines = cached; return@LaunchedEffect }
        try {
            val text = Api.fetchLyric(trackId)
            val parsed = parseLyrics(text)
            if (parsed.isNotEmpty()) {
                LyricsLinesCache.map[trackId] = parsed
                lines = parsed
            }
        } catch (_: Exception) { }
    }
    return LyricsState(lines)
}

data class LyricsState(val lines: List<KRCLine>)

// 加载失败重试（对应 iOS 失败重试胶囊；断网时文案区分「网络未连接」）
@Composable
private fun LoadFailedView(track: Track) {
    val offline by NetworkMonitor.isOffline.collectAsState()
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(if (offline) "网络未连接" else "加载失败", color = TextSecondary, fontSize = 14.sp)
        if (offline) {
            Spacer(Modifier.height(4.dp))
            Text("网络恢复后自动续播", color = TextTertiary, fontSize = 12.sp)
        }
        Spacer(Modifier.height(10.dp))
        Box(
            Modifier
                .clip(RoundedCornerShape(16.dp))
                .background(Card)
                .clickable { PlayerManager.reloadCurrent() }
                .padding(horizontal = 18.dp, vertical = 8.dp)
        ) {
            Text("重试", color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Medium)
        }
    }
}

// 进度条（对齐 iOS PlayerProgressBar 完整交互：点击平滑 seek / 长按放大预览 / 拖动预览 / 防闪回保持）
// 移植自 iOS Components.swift PlayerProgressBar（2026-10-25）：
// - 手势状态机：none → pressed →（位移>8px）quickDrag /（0.3s 长按）longPress →（长按后位移）longDrag
// - 快速点击：easeOutCubic 0.35s 平滑滑动到位后才 seek；动画完成保持视觉位置
//   （holdTarget），等播放器真实进度追上（|diff|<0.005）再无缝交接，消除 seek 异步闪回
// - 动画可打断：动画中新指令 → 从当前视觉位置平滑到新目标（无跳变）
// - 长按（0.3s）：轨道加粗 3→5dp、拖头放大 10→15dp（对齐 iOS pt），松手回弹不跳转
// - 拖动中放大（同长按反馈）；松手 seek + holdTarget 防闪回
// - holdTarget 1.5s 超时兜底强制释放（seek 异常不永久停在目标位）
@Composable
private fun PlayerProgressBar(
    scrubFraction: Float?,
    onScrubChange: (Float?) -> Unit,
    active: Boolean,
) {
    // 高频状态（progress/currentTime 4Hz）只在前台可见页订阅；离屏页保持最后值，不再逐帧重组
    val progress by androidx.compose.runtime.produceState(
        initialValue = PlayerManager.progress.value,
        key1 = active,
    ) {
        if (active) PlayerManager.progress.collect { value = it }
    }
    val currentTime by androidx.compose.runtime.produceState(
        initialValue = PlayerManager.currentTime.value,
        key1 = active,
    ) {
        if (active) PlayerManager.currentTime.collect { value = it }
    }
    val bufferProgress by PlayerManager.bufferProgress.collectAsState()
    val isBuffering by PlayerManager.isBuffering.collectAsState()
    val duration by PlayerManager.duration.collectAsState()
    val isPlaying by PlayerManager.isPlaying.collectAsState()

    // —— 平滑点击动画 + 防闪回保持（内部状态，对应 iOS @State）——
    val scope = rememberCoroutineScope()
    val anim = remember { Animatable(0f) }
    var animTarget by remember { mutableStateOf<Float?>(null) }
    var holdTarget by remember { mutableStateOf<Float?>(null) }
    var smoothJob by remember { mutableStateOf<kotlinx.coroutines.Job?>(null) }

    // —— 手势状态机（对应 iOS GestureMode）——
    // 0 none / 1 pressed / 2 quickDrag / 3 longPress / 4 longDrag
    var gestureMode by remember { mutableIntStateOf(0) }
    var pressStartX by remember { mutableFloatStateOf(0f) }
    var relativeStart by remember { mutableFloatStateOf(0f) }
    var relativeStartX by remember { mutableFloatStateOf(0f) }
    // 长按放大（对齐 iOS 视觉：轨道 3→5dp、拖头 10→15dp；iOS pt 与 dp 等比）
    var longPressActive by remember { mutableStateOf(false) }
    val trackHeight by androidx.compose.animation.core.animateDpAsState(
        if (longPressActive) 5.dp else 3.dp,
        animationSpec = androidx.compose.animation.core.spring(dampingRatio = 0.7f, stiffness = 400f),
        label = "trackH"
    )
    val thumbSize by androidx.compose.animation.core.animateDpAsState(
        if (longPressActive) 15.dp else 10.dp,
        animationSpec = androidx.compose.animation.core.spring(dampingRatio = 0.7f, stiffness = 400f),
        label = "thumbS"
    )

    // 当前视觉位置：拖动预览 > 到位保持 > 动画插值 > 实时进度（对齐 iOS currentVisual）
    val shown = (scrubFraction
        ?: holdTarget
        ?: if (animTarget != null) anim.value else progress.toFloat()).coerceIn(0f, 1f)
    val onSeekState by androidx.compose.runtime.rememberUpdatedState({ f: Float ->
        PlayerManager.seekToFraction(f.toDouble())
    })
    // 轨道像素宽度（手势状态机/缓冲滑块共享；上提到本层，避免手势回调作用域外引用）
    var widthPx by remember { mutableStateOf(0) }

    // holdTarget 释放：播放器真实进度追上（|diff|<0.005）→ 无缝交接实时进度（防闪回）
    androidx.compose.runtime.LaunchedEffect(progress, holdTarget) {
        val h = holdTarget ?: return@LaunchedEffect
        if (kotlin.math.abs(progress - h) < 0.005f) holdTarget = null
    }
    // holdTarget 超时兜底：seek 异常时 1.5s 强制释放，避免视觉永久停在目标位
    androidx.compose.runtime.LaunchedEffect(holdTarget) {
        val h = holdTarget ?: return@LaunchedEffect
        delay(1500)
        if (holdTarget == h) holdTarget = null
    }

    // ===== 手势事件 → 状态机（iOS PlayerProgressBar 完整交互，2026-10-25） =====
    // pointerInput（受限 scope）只做事件转发；状态机/长按协程/平滑动画全部在
    // composable 层执行（可读写最新状态），事件回调经 rememberUpdatedState 传递最新闭包。
    val onScrubChangeState by androidx.compose.runtime.rememberUpdatedState(onScrubChange)
    var pressed by remember { mutableStateOf(false) }
    var longPressJob by remember { mutableStateOf<kotlinx.coroutines.Job?>(null) }

    /** 长按检测：0.3s 未松开且未快速滑动 → 进入长按放大预览 */
    fun armLongPress(downX: Float) {
        longPressJob?.cancel()
        longPressJob = scope.launch {
            delay(300)
            if (pressed && gestureMode == 1) {
                gestureMode = 3            // longPress
                longPressActive = true
                relativeStart = shown
                relativeStartX = downX
            }
        }
    }

    /** 快速点击平滑 seek：easeOutCubic 0.35s 滑到目标，动画完成才 seek；防闪回保持 */
    fun startSmoothSeekTo(f: Float) {
        smoothJob?.cancel()
        smoothJob = scope.launch {
            // 从当前视觉位置平滑动画（from 取当前插值，旧动画打断后无跳变）
            val from = shown
            anim.snapTo(from)
            animTarget = f
            anim.animateTo(
                f,
                animationSpec = androidx.compose.animation.core.tween(
                    durationMillis = 350,
                    easing = androidx.compose.animation.core.Easing { t ->
                        1f - (1f - t) * (1f - t) * (1f - t)
                    }
                )
            )
            // 双保险：动画期间再次被新指令接管 → 放弃本次 seek
            if (animTarget == f) {
                PlayerManager.seekToFraction(f.toDouble())
                holdTarget = f
                animTarget = null
            }
        }
    }

    val onDownState by androidx.compose.runtime.rememberUpdatedState({ x: Float ->
        gestureMode = 1                 // pressed
        pressStartX = x
        pressed = true
        armLongPress(x)
    })

    val onMoveState by androidx.compose.runtime.rememberUpdatedState({ x: Float, densityPx: Float ->
        if (!pressed) return@rememberUpdatedState
        val f = (x / widthPx.toFloat()).coerceIn(0f, 1f)
        when (gestureMode) {
            1 -> { // pressed：位移超过 8dp → 未长按直接滑动（跳到手指位置，打断平滑动画）
                if (kotlin.math.abs(x - pressStartX) > densityPx) {
                    gestureMode = 2    // quickDrag
                    animTarget = null
                    onScrubChangeState(f)
                    longPressJob?.cancel()
                    longPressActive = true   // 拖动中放大（同长按反馈）
                }
            }
            2 -> onScrubChangeState(f)
            3 -> { // longPress：开始滑动 → 相对跟手（不跳）
                if (kotlin.math.abs(x - pressStartX) > densityPx) {
                    gestureMode = 4    // longDrag
                    animTarget = null
                    onScrubChangeState((relativeStart + (x - relativeStartX) / widthPx.toFloat()).coerceIn(0f, 1f))
                    longPressJob?.cancel()
                }
            }
            4 -> onScrubChangeState((relativeStart + (x - relativeStartX) / widthPx.toFloat()).coerceIn(0f, 1f))
        }
    })

    val onUpState by androidx.compose.runtime.rememberUpdatedState({ x: Float, dtMs: Long ->
        pressed = false
        longPressJob?.cancel()
        longPressJob = null
        val f = (x / widthPx.toFloat()).coerceIn(0f, 1f)
        when (gestureMode) {
            3 -> longPressActive = false   // 长按未滑动：仅放大预览，松手回弹，不跳转
            4 -> { // 长按后滑动：seek + 保持视觉（防闪回）
                onSeekState(f)
                holdTarget = f
                onScrubChangeState(null)
                longPressActive = false
            }
            2 -> { // 快速滑动：seek + 保持视觉（防闪回）
                onSeekState(f)
                holdTarget = f
                onScrubChangeState(null)
                longPressActive = false
            }
            1 -> { // 快速点击：平滑滑到目标，动画完成才 seek
                if (dtMs < 350) {
                    onScrubChangeState(null)
                    startSmoothSeekTo(f)
                }
                // dt >= 0.35 兜底（长按定时器异常未触发）：不跳转
            }
        }
        gestureMode = 0
    })

    Column(Modifier.fillMaxWidth()) {
        Box(
            Modifier.fillMaxWidth().height(28.dp)
        ) {
            Box(Modifier.fillMaxSize().onSizeChanged { widthPx = it.width })

            // 总轨道（长按加粗：4 → 6dp，宽度不变）
            Box(
                Modifier.fillMaxWidth().height(trackHeight).align(Alignment.CenterStart)
                    .clip(RoundedCornerShape(percent = 50))
                    .background(Color.White.copy(alpha = 0.18f))
            )
            // 缓冲段：限制在播放进度内（min(buffer, shown)，不超出播放进度）
            Box(
                Modifier.fillMaxWidth(minOf(bufferProgress.toFloat().coerceIn(0f, 1f), shown)).height(trackHeight).align(Alignment.CenterStart)
                    .clip(RoundedCornerShape(percent = 50))
                    .background(Color.White.copy(alpha = 0.38f))
            )
            // 已播段
            Box(
                Modifier.fillMaxWidth(shown).height(trackHeight).align(Alignment.CenterStart)
                    .clip(RoundedCornerShape(percent = 50))
                    .background(Color.White)
            )
            // 缓存加载滑块：生长→移动→收缩→消失 循环（对齐 iOS：拖动/动画/保持期间不显示）
            val density = androidx.compose.ui.platform.LocalDensity.current
            if (isBuffering && scrubFraction == null && animTarget == null && holdTarget == null) {
                // 三阶段插值（与 iOS 一致）：
                // 0~0.3 生长：左边缘固定最左，宽度 0 → sweepW
                // 0.3~0.7 移动：宽度保持 sweepW，左边缘从左滑到右
                // 0.7~1.0 收缩：右边缘固定最右，宽度 sweepW → 0
                val sweepTransition = androidx.compose.animation.core.rememberInfiniteTransition(label = "bufferingSweep")
                val sweepPhase by sweepTransition.animateFloat(
                    initialValue = 0f,
                    targetValue = 1f,
                    animationSpec = androidx.compose.animation.core.infiniteRepeatable(
                        animation = androidx.compose.animation.core.tween(800, easing = androidx.compose.animation.core.LinearEasing),
                        repeatMode = androidx.compose.animation.core.RepeatMode.Restart
                    ),
                    label = "sweepPhase"
                )
                val sweepW = maxOf((widthPx * 0.25f).toInt(), 8)
                val (sw, sx) = when {
                    sweepPhase < 0.3f -> {
                        val f = sweepPhase / 0.3f
                        Pair(sweepW * f, 0f)
                    }
                    sweepPhase < 0.7f -> {
                        val f = (sweepPhase - 0.3f) / 0.4f
                        Pair(sweepW.toFloat(), f * (widthPx - sweepW))
                    }
                    else -> {
                        val f = (sweepPhase - 0.7f) / 0.3f
                        val w = sweepW * (1 - f)
                        Pair(w, widthPx - w)
                    }
                }
                Box(
                    Modifier.height(trackHeight).width(with(density) { sw.toDp() }).align(Alignment.CenterStart)
                        .offset { IntOffset(sx.roundToInt().coerceIn(0, maxOf(0, widthPx - sw.toInt())), 0) }
                        .clip(RoundedCornerShape(percent = 50))
                        .background(Color.White)
                )
            }
            // 拖头（长按放大：10 → 15dp，对齐 iOS 圆点常显）
            val thumbPx = with(density) { thumbSize.roundToPx() }
            Box(
                Modifier.size(thumbSize).align(Alignment.CenterStart)
                    .offset {
                        val x = (shown * (widthPx - thumbPx)).toInt().coerceIn(0, maxOf(0, widthPx - thumbPx))
                        androidx.compose.ui.unit.IntOffset(x, 0)
                    }
                    .clip(androidx.compose.foundation.shape.CircleShape)
                    .background(Color.White)
            )
            // 手势（扩大点击热区）：iOS 状态机完整移植。
            // 受限 scope 内只做事件转发（down/move/up + 事件自带时间戳），
            // 状态机决策全部在 composable 层回调里执行（见 onDownState/onMoveState/onUpState）。
            Box(
                Modifier.fillMaxSize()
                    .pointerInput(Unit) {
                        val densityPx = with(density) { 8.dp.toPx() }
                        awaitEachGesture {
                            val down = awaitFirstDown(requireUnconsumed = false)
                            onDownState(down.position.x)
                            val downUptime = down.uptimeMillis
                            var upX = down.position.x
                            var upUptime = downUptime
                            while (true) {
                                val event = awaitPointerEvent()
                                val change = event.changes.firstOrNull() ?: break
                                if (change.pressed) {
                                    onMoveState(change.position.x, densityPx)
                                    change.consume()
                                } else {
                                    upX = change.position.x
                                    upUptime = change.uptimeMillis
                                    break
                                }
                            }
                            onUpState(upX, upUptime - downUptime)
                        }
                    }
            )
        }
        // 时间行
        Row(Modifier.fillMaxWidth().padding(top = 4.dp), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(
                if (isBuffering) "缓冲中…"
                else if (scrubFraction != null) formatTime(duration * scrubFraction!!)
                else formatTime(currentTime),
                color = TextTertiary, fontSize = 12.sp, fontFamily = FontFamily.Monospace
            )
            Text(formatTime(duration), color = TextTertiary, fontSize = 12.sp, fontFamily = FontFamily.Monospace)
        }
    }
}

// 互动行：点赞 / 评论 / 分享
@Composable
private fun InteractionRow(track: Track, modifier: Modifier = Modifier) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var liked by remember(track.id) { mutableStateOf(track.isLiked == true) }
    var likeCount by remember(track.id) { mutableStateOf(track.likeCount ?: 0) }
    var shareCount by remember(track.id) { mutableStateOf(track.shareCount ?: 0) }

    // 点赞红心兜底（对标 iOS）：isLiked 为 nil 的入口（历史/歌单等）用「我的喜欢」集合补齐，
    // 保证点过赞的歌一定显示红心
    LaunchedEffect(track.id) {
        if (track.isLiked == null) {
            PlayerManager.ensureLikedIdsLoaded()
            val ids = PlayerManager.likedIds.first()
            if (ids != null) {
                liked = ids.contains(track.id)
                if (liked && likeCount == 0) likeCount = 1
            }
        }
    }

    fun openComments() {
        scope.launch {
            Api.reportBehavior(listOf(BehaviorEvent(trackId = track.id, event = "comment_open")))
        }
        AppRouter.pendingCommentTrack.value = track
        AppRouter.showCommentSheet.value = true
    }

    fun share() {
        scope.launch {
            val link = Api.shareLink(track.id)
            val text = "《${track.title}》@流萤音乐" + (link?.let { "\n$it" } ?: "")
            val intent = android.content.Intent(android.content.Intent.ACTION_SEND).apply {
                type = "text/plain"
                putExtra(android.content.Intent.EXTRA_TEXT, text)
            }
            runCatching {
                ctx.startActivity(android.content.Intent.createChooser(intent, "分享歌曲"))
            }
            Api.reportBehavior(listOf(BehaviorEvent(trackId = track.id, event = "share")))
            val count = Api.reportShare(track.id)
            if (count > 0) shareCount = count
        }
    }

    Row(
        modifier,
        horizontalArrangement = Arrangement.spacedBy(30.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        // 点赞
        Box {
            Icon(
                painter = androidx.compose.ui.res.painterResource(R.drawable.ic_heart_fill),
                contentDescription = "点赞",
                tint = if (liked) Color(0xFFFF3B30) else TextPrimary,
                modifier = Modifier
                    .size(26.dp)
                    .clickable {
                        val uid = AuthManager.currentUserValue?.id ?: return@clickable
                        val wasLiked = liked
                        liked = !liked
                        likeCount = (likeCount + (if (liked) 1 else -1)).coerceAtLeast(0)
                        scope.launch {
                            try {
                                val (newLiked, newCount) = Api.toggleLike(track.id, uid)
                                liked = newLiked
                                likeCount = newCount
                                if (newLiked) PlayerManager.noteLiked(track.id) else PlayerManager.noteUnliked(track.id)
                                Api.reportBehavior(listOf(BehaviorEvent(trackId = track.id, event = "like")))
                            } catch (_: Exception) {
                                liked = wasLiked
                            }
                        }
                    }
            )
            if (likeCount > 0) {
                Text(
                    if (likeCount > 99) "99+" else "$likeCount",
                    color = TextPrimary,
                    fontSize = 10.sp,
                    modifier = Modifier.align(Alignment.TopEnd).offset(x = 8.dp, y = (-4).dp)
                )
            }
        }
        // 评论
        Box {
            Icon(
                painter = androidx.compose.ui.res.painterResource(R.drawable.ic_bubble_right_fill),
                contentDescription = "评论",
                tint = TextPrimary,
                modifier = Modifier
                    .size(26.dp)
                    .clickable { openComments() }
            )
            if ((track.commentCount ?: 0) > 0) {
                Text(
                    if (track.commentCount!! > 99) "99+" else "${track.commentCount}",
                    color = TextPrimary,
                    fontSize = 10.sp,
                    modifier = Modifier.align(Alignment.TopEnd).offset(x = 8.dp, y = (-4).dp)
                )
            }
        }
        // 分享
        Box {
            Icon(
                painter = androidx.compose.ui.res.painterResource(R.drawable.ic_turn_up_forward_fill),
                contentDescription = "分享",
                tint = TextPrimary,
                modifier = Modifier
                    .size(26.dp)
                    .clickable { share() }
            )
            if (shareCount > 0) {
                Text(
                    if (shareCount > 99) "99+" else "$shareCount",
                    color = TextPrimary,
                    fontSize = 10.sp,
                    modifier = Modifier.align(Alignment.TopEnd).offset(x = 8.dp, y = (-4).dp)
                )
            }
        }
    }
}

// EQ 面板（对应 iOS 音效选择面板；2026-10-22 对齐 iOS 网格卡片样式：2 列 + 图标 + 选中描边）
// 注：命名避开 TrackMenuSheet.kt 的包级 eqOptions（List<String>），防止同包顶层声明冲突
private val homeEqOptions: List<Pair<String, androidx.compose.ui.graphics.vector.ImageVector>> = listOf(
    "原音" to Icons.AutoMirrored.Filled.VolumeUp,
    "3D 立体音效" to Icons.Filled.Waves,
    "360 环绕" to Icons.Filled.AllInclusive,
    "复古" to Icons.Filled.History,
    "现场" to Icons.Filled.Mic,
    "流行" to Icons.Filled.MusicNote,
    "摇滚" to Icons.Filled.Album,
)

@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
private fun EQPanel(current: String, onSelect: (String) -> Unit, onDismiss: () -> Unit) {
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        containerColor = Color(0xFF1C1C1E),
    ) {
        Column(Modifier.padding(20.dp)) {
            Text("音效", color = TextPrimary, fontSize = 18.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(14.dp))
            homeEqOptions.chunked(2).forEach { rowItems ->
                Row(
                    Modifier.fillMaxWidth().padding(bottom = 10.dp),
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    rowItems.forEach { item ->
                        val name = item.first
                        val icon = item.second
                        val selected = name == current
                        Box(
                            Modifier
                                .weight(1f)
                                .clip(RoundedCornerShape(14.dp))
                                .background(if (selected) Accent.copy(alpha = 0.14f) else Card)
                                .clickable { onSelect(name) }
                                .padding(vertical = 14.dp),
                            contentAlignment = Alignment.Center,
                        ) {
                            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                Box(
                                    Modifier.size(30.dp).clip(androidx.compose.foundation.shape.CircleShape)
                                        .background(if (selected) Accent.copy(alpha = 0.22f) else Color.White.copy(alpha = 0.08f)),
                                    contentAlignment = Alignment.Center,
                                ) {
                                    Icon(icon, contentDescription = name, tint = if (selected) Accent else TextPrimary, modifier = Modifier.size(16.dp))
                                }
                                Spacer(Modifier.height(8.dp))
                                Text(
                                    name,
                                    color = if (selected) Accent else TextPrimary,
                                    fontSize = 13.sp,
                                    fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                                )
                            }
                        }
                    }
                    if (rowItems.size == 1) Spacer(Modifier.weight(1f))
                }
            }
            Spacer(Modifier.height(10.dp))
        }
    }
}
