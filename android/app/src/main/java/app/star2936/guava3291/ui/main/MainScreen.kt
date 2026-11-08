package app.star2936.guava3291.ui.main

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.togetherWith
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.ScrollState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.asPaddingValues
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Explore
import androidx.compose.material.icons.outlined.Person
import androidx.compose.material.icons.outlined.PlayArrow
import androidx.compose.material.icons.outlined.Pause
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.rounded.PlayArrow
import androidx.compose.material.icons.rounded.Pause
import androidx.compose.material.icons.rounded.Person
import androidx.compose.material.icons.rounded.Explore
import androidx.compose.material.icons.rounded.Menu
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import app.star2936.guava3291.ui.login.LoginScreen
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import app.star2936.guava3291.state.AppRouter
import app.star2936.guava3291.ui.sidebar.SidebarContent
import app.star2936.guava3291.ui.home.HomeScreen
import app.star2936.guava3291.ui.settings.OpenSourceLicensesScreen
import app.star2936.guava3291.ui.discover.DiscoverScreen
import app.star2936.guava3291.ui.search.SearchScreen
import app.star2936.guava3291.ui.mine.MineScreen
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.zIndex
import app.star2936.guava3291.data.Announcement
import app.star2936.guava3291.data.Api
import app.star2936.guava3291.data.UpdateCheck
import app.star2936.guava3291.state.AppPreloader
import app.star2936.guava3291.state.AuthManager
import app.star2936.guava3291.state.Library
import app.star2936.guava3291.state.NotificationBus
import app.star2936.guava3291.state.PlayerManager
import app.star2936.guava3291.ui.components.AlbumArtPlaceholder
import app.star2936.guava3291.ui.components.CoverImage
import app.star2936.guava3291.ui.components.EqualizerBars
import app.star2936.guava3291.ui.discover.DiscoverScreen
import app.star2936.guava3291.ui.home.HomeScreen
import app.star2936.guava3291.ui.mine.MineScreen
import app.star2936.guava3291.ui.theme.Accent
import app.star2936.guava3291.ui.theme.Background
import app.star2936.guava3291.ui.theme.Card
import app.star2936.guava3291.ui.theme.CardBorder
import app.star2936.guava3291.ui.theme.TextSecondary
import app.star2936.guava3291.ui.theme.UpdateGreen
import app.star2936.guava3291.ui.theme.darkened

// 对应 iOS MusicApp.swift MainTabView：三页常驻 + 顶部栏 + 悬浮 TabBar + 侧边栏抽屉

/** 全屏子页面（对应 fullScreenCover / push） */
sealed class SubPage {
    object Search : SubPage()
    object Notifications : SubPage()
    object OfficialMessages : SubPage()
    object Settings : SubPage()
    object ProfileEdit : SubPage()
    object ChangePassword : SubPage()
    object About : SubPage()
    object Licenses : SubPage()
    object Upload : SubPage()
    object History : SubPage()
    object Playlists : SubPage()
    object Login : SubPage()
    data class PlaylistDetail(val playlistId: String, val playlistName: String) : SubPage()
    data class UserProfile(val userId: String) : SubPage()
    data class WebViewPage(val url: String, val title: String = "") : SubPage()
}

@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
fun MainScreen() {
    // 抽屉进度带平滑动画
    var drawerTarget by remember { mutableStateOf(0f) }
    val drawerProgress by animateFloatAsState(
        targetValue = drawerTarget,
        animationSpec = tween(durationMillis = 300, easing = FastOutSlowInEasing),
        label = "drawer"
    )
    var subPageStack by remember { mutableStateOf<List<SubPage>>(emptyList()) }
    val subPage = subPageStack.lastOrNull()
    fun pushPage(p: SubPage) { subPageStack = subPageStack + p }
    fun popPage() { subPageStack = subPageStack.dropLast(1) }
    // 设置页滚动位置提升到 MainScreen 持有：跳转子页面返回时不丢滚动位置（AnimatedContent 切换会销毁设置页组合，rememberScrollState 会重置）
    val settingsScrollState = androidx.compose.runtime.saveable.rememberSaveable(saver = ScrollState.Saver) { ScrollState(0) }
    var announcement by remember { mutableStateOf<Announcement?>(null) }
    var updateNotice by remember { mutableStateOf<UpdateCheck?>(null) }
    var uploadUri by remember { mutableStateOf<android.net.Uri?>(null) }

    val selectedTab by AppRouter.selectedTab.collectAsState()
    // 评论弹窗（通知点击定位）
    val showCommentSheet by AppRouter.showCommentSheet.collectAsState()
    val pendingCommentTrack by AppRouter.pendingCommentTrack.collectAsState()
    val highlightCommentId by AppRouter.highlightCommentId.collectAsState()

    // 启动拉取公告/更新（与 iOS fetchAnnouncement/fetchUpdate 一致）
    val ctx = LocalContext.current
    LaunchedEffect(Unit) {
        try { Api.reportDevice() } catch (_: Exception) { }
        // 启动即刷新未读红点（互动 + 官方，与 iOS 一致）
        try { NotificationBus.refreshUnread() } catch (_: Exception) { }
        try {
            val ann = Api.fetchAnnouncement()
            if (ann != null && ann.title.isNotEmpty() && ann.id.isNotEmpty()) {
                val shown = ctx.getSharedPreferences("announcement", android.content.Context.MODE_PRIVATE)
                    .getString("shown_id", null)
                if (ann.showOnce != 1 || shown != ann.id) announcement = ann
            }
        } catch (_: Exception) { }
        try {
            val upd = Api.checkUpdate()
            if (upd?.hasUpdate == true) updateNotice = upd
        } catch (_: Exception) { }
    }

    // 监听全局跳转用户主页
    val navigateToUser by AppRouter.navigateToUser.collectAsState()
    LaunchedEffect(navigateToUser) {
        val uid = navigateToUser ?: return@LaunchedEffect
        pushPage(SubPage.UserProfile(uid))
        AppRouter.navigateToUser.value = null
    }

    // 登录后建立 WebSocket；WS 数据事件驱动刷新（track_list/profile/stats）
    val lastDataEvent by NotificationBus.lastDataEvent.collectAsState()
    LaunchedEffect(lastDataEvent) {
        val ev = lastDataEvent ?: return@LaunchedEffect
        when (ev.event) {
            "track_list" -> Library.load(force = true)
            "profile" -> ev.user?.let { AuthManager.applyRemoteProfile(it) }
            "stats" -> ev.totalSeconds?.let { AppPreloader.mineStatsSeconds.value = it }
            else -> {}
        }
    }

    BackHandler(enabled = subPage != null) { popPage() }
    BackHandler(enabled = drawerProgress > 0.01f) { drawerTarget = 0f }

    Box(Modifier.fillMaxSize()) {
        val panelWidthDp = (LocalConfiguration.current.screenWidthDp * 0.85f).dp

        // ===== 侧边栏在最底层（从左滑入）=====
        SidebarDrawer(
            progress = drawerProgress,
            onClose = { drawerTarget = 0f },
        )

        // ===== 主内容：fillMaxSize 延伸到底部，TabBar 浮在上面 =====
        Column(
            Modifier
                .fillMaxSize()
                .offset(x = panelWidthDp * drawerProgress)
        ) {
            Box(Modifier.weight(1f)) {
                // 三页常驻：选中页 zIndex 置顶 + 透明度平滑过渡（tab 切换淡入淡出，避免硬切闪烁）
                val tabAlpha0 by animateFloatAsState(
                    targetValue = if (selectedTab == 0) 1f else 0f,
                    animationSpec = tween(300), label = "tab0")
                val tabAlpha1 by animateFloatAsState(
                    targetValue = if (selectedTab == 1) 1f else 0f,
                    animationSpec = tween(300), label = "tab1")
                val tabAlpha2 by animateFloatAsState(
                    targetValue = if (selectedTab == 2) 1f else 0f,
                    animationSpec = tween(300), label = "tab2")
                Box(Modifier.fillMaxSize().zIndex(if (selectedTab == 0) 1f else 0f).graphicsLayer { alpha = tabAlpha0 }) {
                    DiscoverScreen()
                }
                Box(Modifier.fillMaxSize().zIndex(if (selectedTab == 1) 1f else 0f).graphicsLayer { alpha = tabAlpha1 }) {
                    HomeScreen()
                }
                Box(Modifier.fillMaxSize().zIndex(if (selectedTab == 2) 1f else 0f).graphicsLayer { alpha = tabAlpha2 }) {
                    MineScreen(
                        onOpenUpload = { uri -> uploadUri = uri; pushPage(SubPage.Upload) },
                        onOpenNotifications = { pushPage(SubPage.Notifications) },
                        onOpenSettings = { pushPage(SubPage.Settings) },
                        onOpenHistory = { pushPage(SubPage.History) },
                        onOpenPlaylists = { pushPage(SubPage.Playlists) },
                        onEditProfile = { pushPage(SubPage.ProfileEdit) },
                    )
                }

                // 顶部栏（仅首页播放页显示）
                if (selectedTab == 1) {
                    GlobalTopBar(
                        onOpenSidebar = { drawerTarget = 1f },
                        onOpenSearch = { pushPage(SubPage.Search) },
                    )
                }
            }
        }

        // ===== TabBar 浮在内容上面（播放页时透明，背景延伸过来）=====
        Box(
            Modifier
                .align(Alignment.BottomCenter)
                .offset(x = panelWidthDp * drawerProgress)
                .zIndex(5f)
        ) {
            AppTabBar(
                selection = selectedTab,
                onSelect = { AppRouter.selectedTab.value = it },
            )
        }

        // 点击主内容区域关闭抽屉
        if (drawerProgress > 0.01f) {
            Box(
                Modifier
                    .fillMaxSize()
                    .offset(x = panelWidthDp * drawerProgress)
                    .clickable { drawerTarget = 0f }
                    .zIndex(7f)
            )
        }

        // ===== 全屏子页面（叠加淡入）=====
        // 实现说明：不再使用 AnimatedContent，改用手动 alpha 叠加层——
        // 新页面从 alpha=0 原地淡入盖在旧页面/主界面上，不滑动不缩放；
        // 返回时旧子页面 alpha 降到 0 后露出主界面。animateFloatAsState
        // 是确定性动画（与 tab 切换同机制），任何设备上必然播放，
        // 避免 AnimatedContent 内部行为导致的"淡入不生效"问题。
        // zIndex(10f) > TabBar(5f)：子页面必须盖住悬浮底部导航栏
        val subPageAlpha by animateFloatAsState(
            targetValue = if (subPage != null) 1f else 0f,
            animationSpec = tween(320, easing = FastOutSlowInEasing),
            label = "subPageAlpha"
        )
        // 退出动画期间保留最后渲染的子页面（alpha 尚未降到 0 时仍显示旧页，保证淡出可见）
        var renderedSubPage by remember { mutableStateOf<SubPage?>(null) }
        LaunchedEffect(subPage) { if (subPage != null) renderedSubPage = subPage }
        val renderPage = subPage ?: renderedSubPage
        if (renderPage != null && (subPage != null || subPageAlpha > 0.01f)) {
            Box(
                Modifier
                    .fillMaxSize()
                    .zIndex(10f)
                    .graphicsLayer { alpha = subPageAlpha }
            ) {
                SubPageHost(onBack = { popPage() }) {
                    when (renderPage) {
                        SubPage.Search -> SearchScreenCompat(onBack = { popPage() })
                        SubPage.Notifications -> NotificationsScreenCompat(
                            onBack = { popPage() },
                            onOpenOfficial = { pushPage(SubPage.OfficialMessages) })
                        SubPage.OfficialMessages -> OfficialMessagesScreenCompat(
                            onBack = { popPage() },
                            onOpenWebView = { url, title -> pushPage(SubPage.WebViewPage(url, title)) }
                        )
                        SubPage.Settings -> SettingsScreenCompat(
                            settingsScrollState = settingsScrollState,
                            onBack = { popPage() },
                            onOpenProfileEdit = { pushPage(SubPage.ProfileEdit) },
                            onOpenChangePassword = { pushPage(SubPage.ChangePassword) },
                            onOpenAbout = { pushPage(SubPage.About) },
                            onOpenLicenses = { pushPage(SubPage.Licenses) },
                            onOpenLogin = { pushPage(SubPage.Login) },
                            onOpenWebView = { url, title -> pushPage(SubPage.WebViewPage(url, title)) }
                        )
                        SubPage.Login -> LoginScreen(
                            onLoginSuccess = { popPage() },
                            onOpenWebView = { url, title -> pushPage(SubPage.WebViewPage(url, title)) }
                        )
                        SubPage.ProfileEdit -> ProfileEditScreenCompat(onBack = { popPage() })
                        SubPage.ChangePassword -> ChangePasswordScreenCompat(onBack = { popPage() })
                        SubPage.About -> AboutScreenCompat(onBack = { popPage() })
                        SubPage.Licenses -> OpenSourceLicensesScreen(onBack = { popPage() })
                        SubPage.Upload -> UploadScreenCompat(fileUri = uploadUri, onBack = { popPage() })
                        SubPage.History -> HistoryScreenCompat(onBack = { popPage() })
                        SubPage.Playlists -> PlaylistsScreenCompat(onBack = { popPage() },
                            onOpenPlaylist = { id, name -> pushPage(SubPage.PlaylistDetail(id, name)) })
                        is SubPage.PlaylistDetail -> PlaylistDetailScreenCompat(
                            playlistId = (renderPage as SubPage.PlaylistDetail).playlistId,
                            playlistName = (renderPage as SubPage.PlaylistDetail).playlistName,
                            onBack = { popPage() })
                        is SubPage.UserProfile -> UserProfileScreenCompat(
                            userId = (renderPage as SubPage.UserProfile).userId,
                            onBack = { popPage() })
                        is SubPage.WebViewPage -> app.star2936.guava3291.ui.web.WebViewScreenCompat(
                            url = (renderPage as SubPage.WebViewPage).url,
                            title = (renderPage as SubPage.WebViewPage).title,
                            onBack = { popPage() }
                        )
                    }
                }
            }
        }
    }

    // 公告/更新弹窗
    announcement?.let { AnnouncementSheetCompat(announcement = it, onDismiss = { announcement = null }) }
    updateNotice?.let { UpdateSheetCompat(update = it, onDismiss = { updateNotice = null }) }

    // 评论弹窗（对应 iOS CommentSheet presentation）
    val commentTrack = pendingCommentTrack
    if (showCommentSheet && commentTrack != null) {
        androidx.compose.material3.ModalBottomSheet(
            onDismissRequest = { AppRouter.showCommentSheet.value = false },
            containerColor = Background,
            sheetState = androidx.compose.material3.rememberModalBottomSheetState(skipPartiallyExpanded = true)
        ) {
            app.star2936.guava3291.ui.comment.CommentSheet(
                track = commentTrack,
                highlightCommentId = highlightCommentId,
                onDismiss = { AppRouter.showCommentSheet.value = false },
                onUserClick = { uid ->
                    // 先关评论弹窗，再 push 用户主页
                    AppRouter.showCommentSheet.value = false
                    pushPage(SubPage.UserProfile(uid))
                }
            )
        }
    }
}

/** 全屏子页面宿主：统一深色背景 */
@Composable
fun SubPageHost(onBack: () -> Unit, content: @Composable () -> Unit) {
    Box(Modifier.fillMaxSize().background(Background).zIndex(20f)) { content() }
}

// 子页面转发（由各自模块实现，避免编译顺序问题）
@Composable
fun SearchScreenCompat(onBack: () -> Unit) { app.star2936.guava3291.ui.search.SearchScreen(onBack = onBack) }
@Composable
fun NotificationsScreenCompat(onBack: () -> Unit, onOpenOfficial: () -> Unit) { app.star2936.guava3291.ui.notifications.NotificationsScreenCompat(onBack = onBack, onOpenOfficial = onOpenOfficial) }
@Composable
fun OfficialMessagesScreenCompat(onBack: () -> Unit, onOpenWebView: (String, String) -> Unit = { _, _ -> }) { app.star2936.guava3291.ui.notifications.OfficialMessagesScreenCompat(onBack = onBack, onOpenWebView = onOpenWebView) }
@Composable
fun SettingsScreenCompat(settingsScrollState: ScrollState, onBack: () -> Unit, onOpenProfileEdit: () -> Unit, onOpenChangePassword: () -> Unit, onOpenAbout: () -> Unit, onOpenLicenses: () -> Unit, onOpenLogin: () -> Unit, onOpenWebView: (String, String) -> Unit = { _, _ -> }) { app.star2936.guava3291.ui.settings.SettingsScreenCompat(settingsScrollState = settingsScrollState, onBack = onBack, onOpenProfileEdit = onOpenProfileEdit, onOpenChangePassword = onOpenChangePassword, onOpenAbout = onOpenAbout, onOpenLicenses = onOpenLicenses, onOpenLogin = onOpenLogin, onOpenWebView = onOpenWebView) }
@Composable
fun ProfileEditScreenCompat(onBack: () -> Unit) { app.star2936.guava3291.ui.settings.ProfileEditScreenCompat(onBack = onBack) }
@Composable
fun ChangePasswordScreenCompat(onBack: () -> Unit) { app.star2936.guava3291.ui.settings.ChangePasswordScreenCompat(onBack = onBack) }
@Composable
fun AboutScreenCompat(onBack: () -> Unit) { app.star2936.guava3291.ui.settings.AboutScreenCompat(onBack = onBack) }
@Composable
fun UploadScreenCompat(fileUri: android.net.Uri?, onBack: () -> Unit) { app.star2936.guava3291.ui.upload.UploadScreenCompat(fileUri = fileUri, onBack = onBack) }
@Composable
fun HistoryScreenCompat(onBack: () -> Unit) { app.star2936.guava3291.ui.history.HistoryScreen(onBack = onBack) }
@Composable
fun PlaylistsScreenCompat(onBack: () -> Unit, onOpenPlaylist: (String, String) -> Unit) { app.star2936.guava3291.ui.playlists.PlaylistsScreen(onBack = onBack, onOpenPlaylist = onOpenPlaylist) }
@Composable
fun PlaylistDetailScreenCompat(playlistId: String, playlistName: String, onBack: () -> Unit) { app.star2936.guava3291.ui.playlists.PlaylistDetailScreen(playlistId = playlistId, playlistName = playlistName, onBack = onBack) }
@Composable
fun UserProfileScreenCompat(userId: String, onBack: () -> Unit) { app.star2936.guava3291.ui.profile.UserProfileScreen(userId = userId, onBack = onBack) }

// ===== 全局顶部栏（仅首页显示，颜色随封面亮度）=====
@Composable
fun GlobalTopBar(onOpenSidebar: () -> Unit, onOpenSearch: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .statusBarsPadding()
            .padding(horizontal = 20.dp)
            .height(52.dp)
            .zIndex(2f),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Row(
            Modifier
                .clip(RoundedCornerShape(12.dp))
                .clickable { onOpenSidebar() }
                .padding(vertical = 8.dp, horizontal = 4.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Icon(
                Icons.Rounded.Menu,
                contentDescription = "菜单",
                tint = Color.White,
                modifier = Modifier.size(22.dp)
            )
            Spacer(Modifier.width(10.dp))
            Text("流萤音乐", color = Color.White, fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
        }
        Spacer(Modifier.weight(1f))
        Icon(
            Icons.Outlined.Search,
            contentDescription = "搜索",
            tint = Color.White,
            modifier = Modifier
                .clip(RoundedCornerShape(12.dp))
                .clickable { onOpenSearch() }
                .padding(10.dp)
                .size(20.dp)
        )
    }
}

// ===== 悬浮底部 TabBar（对应 AppTabBar）=====
@Composable
fun AppTabBar(selection: Int, onSelect: (Int) -> Unit) {
    val playerIsPlaying by PlayerManager.isPlaying.collectAsState()
    val backgroundLight by PlayerManager.playerCardBackgroundLight.collectAsState()
    val unread by NotificationBus.unreadCount.collectAsState()
    val current = PlayerManager.current

    fun tabColor(selected: Boolean): Color {
        if (selection == 1) {
            if (selected) return Accent
            return if (backgroundLight) TextSecondary else Color.White.copy(alpha = 0.72f)
        }
        return if (selected) Accent else TextSecondary
    }

    // TabBar 背景色：播放页透明，其他页深色，带过渡动画
    val tabBarBg by androidx.compose.animation.animateColorAsState(
        targetValue = if (selection == 1) Color.Transparent else Card.copy(alpha = 0.98f),
        animationSpec = tween(300),
        label = "tabBarBg"
    )

    Box(
        Modifier
            .fillMaxWidth()
            .background(tabBarBg)
            .zIndex(2f)
    ) {
        if (selection != 1) {
            // 顶部 0.5 边框（对应 overlay cardBorder）
            Box(
                Modifier
                    .fillMaxWidth()
                    .height(0.5.dp)
                    .background(CardBorder)
                    .align(Alignment.TopCenter)
            )
        }
        Row(Modifier.padding(top = 8.dp).padding(bottom = androidx.compose.foundation.layout.WindowInsets.navigationBars.asPaddingValues().calculateBottomPadding() + 6.dp), verticalAlignment = Alignment.CenterVertically) {
            // 发现
            TabButton(
                icon = { Icon(Icons.Rounded.Explore, contentDescription = null, tint = tabColor(selection == 0), modifier = Modifier.size(22.dp)) },
                label = "发现",
                color = tabColor(selection == 0),
                modifier = Modifier.weight(1f),
                onClick = { onSelect(0) }
            )
            // 中央播放控制
            Box(Modifier.weight(1f), contentAlignment = Alignment.Center) {
                if (selection == 1) {
                    Box(
                        Modifier
                            .size(48.dp)
                            .clip(CircleShape)
                            .background(Accent)
                            .clickable { PlayerManager.toggle() },
                        contentAlignment = Alignment.Center
                    ) {
                        Icon(
                            if (playerIsPlaying) Icons.Rounded.Pause else Icons.Rounded.PlayArrow,
                            contentDescription = null,
                            tint = Color.White,
                            modifier = Modifier.size(22.dp)
                        )
                    }
                } else {
                    val track = current
                    Box(
                        Modifier
                            .size(46.dp)
                            .clip(RoundedCornerShape(14.dp))
                            .clickable { onSelect(1) }
                    ) {
                        if (track != null) {
                            CoverImage(track = track, modifier = Modifier.fillMaxSize(), cornerRadius = 14.dp)
                        } else {
                            AlbumArtPlaceholder("音乐", Modifier.fillMaxSize())
                        }
                        Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.35f)))
                        if (playerIsPlaying) {
                            EqualizerBars(modifier = Modifier.align(Alignment.Center))
                        } else {
                            Icon(Icons.Rounded.Pause, contentDescription = null, tint = Color.White, modifier = Modifier.align(Alignment.Center).size(12.dp))
                        }
                    }
                }
            }
            // 我的（未读红点）
            Box(Modifier.weight(1f)) {
                TabButton(
                    icon = {
                        Box {
                            Icon(Icons.Rounded.Person, contentDescription = null, tint = tabColor(selection == 2), modifier = Modifier.size(22.dp))
                            if (unread > 0) {
                                Box(
                                    Modifier
                                        .size(9.dp)
                                        .clip(CircleShape)
                                        .background(Color.Red)
                                        .align(Alignment.TopEnd)
                                        .offset(x = 4.dp, y = (-2).dp)
                                )
                            }
                        }
                    },
                    label = "我的",
                    color = tabColor(selection == 2),
                    modifier = Modifier.fillMaxWidth(),
                    onClick = { onSelect(2) }
                )
            }
        }
    }
}

@Composable
fun TabButton(icon: @Composable () -> Unit, label: String, color: Color, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Column(
        modifier = modifier
            .clip(RoundedCornerShape(8.dp))
            .clickable(onClick = onClick)
            .padding(vertical = 7.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Box(Modifier.height(24.dp), contentAlignment = Alignment.Center) { icon() }
        Spacer(Modifier.height(3.dp))
        Text(label, color = color, fontSize = 11.sp, fontWeight = FontWeight.Medium)
    }
}

// ===== 侧边栏抽屉（85% 宽，offset 跟随进度）=====
@Composable
fun SidebarDrawer(progress: Float, onClose: () -> Unit) {
    val density = LocalDensity.current
    val panelWidthDp = (LocalConfiguration.current.screenWidthDp * 0.85f).dp
    val panelWidthPx = with(density) { panelWidthDp.toPx() }
    val dominant by PlayerManager.playerCardDominant.collectAsState()

    // 面板背景：v17.1 同色相三档加深（0.38/0.30/0.22，色相/饱和度完全保真）。
    // 替代旧的 RGB 乘法压暗（darkened 会把颜色压得浑浊发灰）——侧滑栏与播放页
    // 背景同一套色相语言，比播放页（0.62/0.50/0.30）暗一档保证白色文字可读。
    val shades = dominant?.let { app.star2936.guava3291.util.ColorPalette.darkenedShades(it) }
    Box(
        Modifier
            .fillMaxHeight()
            .width(panelWidthDp)
            .offset(x = (progress * panelWidthPx - panelWidthPx).dp)
            .background(
                if (shades != null) Brush.verticalGradient(listOf(shades.first, shades.second, shades.third))
                else Brush.linearGradient(listOf(Card, Card.copy(alpha = 0.85f)))
            )
            .zIndex(5f)
    ) {
        SidebarContent(onClose = onClose)
    }
}

// ===== 公告弹窗（半屏，对应 AnnouncementSheet）=====
@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
fun AnnouncementSheetCompat(announcement: Announcement, onDismiss: () -> Unit) {
    val ctx = LocalContext.current
    androidx.compose.material3.ModalBottomSheet(
        onDismissRequest = onDismiss,
        containerColor = Color(0xFF1A1A1F),
        shape = RoundedCornerShape(topStart = 24.dp, topEnd = 24.dp)
    ) {
        Column(Modifier.padding(24.dp).verticalScroll(rememberScrollState())) {
            Text(announcement.title, color = Color.White, fontSize = 22.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(14.dp))
            Text(announcement.content, color = Color.White.copy(alpha = 0.85f), fontSize = 15.sp, lineHeight = 24.sp)
            if (announcement.version.isNotEmpty()) {
                Spacer(Modifier.height(10.dp))
                Text("版本：${announcement.version}", color = Color.White.copy(alpha = 0.5f), fontSize = 13.sp)
            }
            Spacer(Modifier.height(24.dp))
            Box(
                Modifier
                    .fillMaxWidth()
                    .height(50.dp)
                    .clip(RoundedCornerShape(16.dp))
                    .background(Accent)
                    .clickable {
                        if (announcement.showOnce == 1) {
                            ctx.getSharedPreferences("announcement", android.content.Context.MODE_PRIVATE)
                                .edit().putString("shown_id", announcement.id).apply()
                        }
                        onDismiss()
                    },
                contentAlignment = Alignment.Center
            ) {
                Text("我知道了", color = Color.White, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
            }
            Spacer(Modifier.height(24.dp))
        }
    }
}

// ===== 更新弹窗（半屏，对应 UpdateSheet）=====
@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
fun UpdateSheetCompat(update: UpdateCheck, onDismiss: () -> Unit) {
    val ctx = LocalContext.current
    val force = update.forceUpdate == 1
    androidx.compose.material3.ModalBottomSheet(
        // 强制更新时，点击外面/按返回键都不关闭弹窗
        onDismissRequest = { if (!force) onDismiss() },
        containerColor = Color(0xFF1A1A1F),
        shape = RoundedCornerShape(topStart = 24.dp, topEnd = 24.dp)
    ) {
        Column(Modifier.padding(24.dp).verticalScroll(rememberScrollState())) {
            Text(update.title ?: "发现新版本", color = Color.White, fontSize = 22.sp, fontWeight = FontWeight.Bold)
            if (!update.version.isNullOrEmpty()) {
                Spacer(Modifier.height(8.dp))
                Text("新版本：${update.version}", color = UpdateGreen, fontSize = 14.sp)
            }
            Spacer(Modifier.height(14.dp))
            Text(update.content ?: "", color = Color.White.copy(alpha = 0.85f), fontSize = 15.sp, lineHeight = 24.sp)
            Spacer(Modifier.height(24.dp))
            Box(
                Modifier
                    .fillMaxWidth()
                    .height(50.dp)
                    .clip(RoundedCornerShape(16.dp))
                    .background(Accent)
                    .clickable {
                        update.downloadUrl?.let { url ->
                            try {
                                val intent = android.content.Intent(android.content.Intent.ACTION_VIEW, android.net.Uri.parse(url))
                                ctx.startActivity(intent)
                            } catch (_: Exception) { }
                        }
                        if (!force) onDismiss()
                    },
                contentAlignment = Alignment.Center
            ) {
                Text("去更新", color = Color.White, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
            }
            if (!force) {
                Spacer(Modifier.height(8.dp))
                Box(
                    Modifier
                        .fillMaxWidth()
                        .padding(vertical = 12.dp)
                        .clickable { onDismiss() },
                    contentAlignment = Alignment.Center
                ) {
                    Text("以后再说", color = Color.White.copy(alpha = 0.6f), fontSize = 15.sp)
                }
            }
            Spacer(Modifier.height(24.dp))
        }
    }
}
