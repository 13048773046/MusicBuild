package app.star2936.guava3291

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.togetherWith

import android.Manifest
import android.media.AudioDeviceCallback
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Build
import android.os.Bundle
import android.content.Intent
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import app.star2936.guava3291.ui.theme.Accent
import app.star2936.guava3291.ui.theme.Card
import app.star2936.guava3291.ui.theme.TextPrimary
import app.star2936.guava3291.ui.theme.TextSecondary
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.media3.common.Player
import app.star2936.guava3291.state.AuthManager
import app.star2936.guava3291.state.Library
import app.star2936.guava3291.state.NotificationBus
import app.star2936.guava3291.state.PlayerManager
import app.star2936.guava3291.state.PlayerSession
import app.star2936.guava3291.state.PlaybackService
import app.star2936.guava3291.ui.main.MainScreen
import app.star2936.guava3291.ui.login.LoginScreen
import app.star2936.guava3291.ui.splash.SplashScreen
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var audioManager: AudioManager? = null
    private var audioDeviceCallback: AudioDeviceCallback? = null
    /// 分享 H5 深链待播歌曲：未登录时暂存，登录成功后自动播放
    private var pendingDeepLinkId: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        // App 崩溃/异常日志自动上报（启动补报上次未上行的日志）
        app.star2936.guava3291.data.LogReporter.init(this)
        // 启动心跳日志：验证上报链路 + 记录冷启动（崩溃排查基准点）
        app.star2936.guava3291.data.LogReporter.log("info", "launch", "App 冷启动 v${BuildConfig.VERSION_NAME}")

        // 分享 H5「去 APP 里面听」深链：liuyingmusic://track/{id}（冷启动）
        handleDeepLink(intent)

        // Android 13+ 锁屏控制通知权限
        if (Build.VERSION.SDK_INT >= 33) {
            val granted = checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == android.content.pm.PackageManager.PERMISSION_GRANTED
            if (!granted) {
                requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 100)
            }
        }

        // 耳机/蓝牙插拔：拔插后音频流恢复（对应 iOS 音频恢复加固）
        audioManager = getSystemService(AUDIO_SERVICE) as AudioManager
        audioDeviceCallback = object : AudioDeviceCallback() {
            override fun onAudioDevicesAdded(addedDevices: Array<out AudioDeviceInfo>?) { resumeAfterDeviceChange() }
            override fun onAudioDevicesRemoved(removedDevices: Array<out AudioDeviceInfo>?) { resumeAfterDeviceChange() }
        }
        audioManager?.registerAudioDeviceCallback(audioDeviceCallback, null)

        // 生命周期：前台重连 WS + 复位音量；后台保存播放会话
        lifecycle.addObserver(LifecycleEventObserver { _, event ->
            when (event) {
                Lifecycle.Event.ON_START -> {
                    NotificationBus.resetAndReconnect()
                    Library.load()
                    // 切前台强制复位音量（对应 iOS 打断完整恢复逻辑）
                    PlayerManager.player.volume = 1.0f
                    // 回前台同步未读数（对应 iOS didBecomeActive）
                    kotlinx.coroutines.GlobalScope.launch(kotlinx.coroutines.Dispatchers.IO) {
                        try {
                            val r = app.star2936.guava3291.data.Api.notifications()
                            NotificationBus.setUnread(r.unread)
                        } catch (_: Exception) {}
                    }
                }
                Lifecycle.Event.ON_STOP -> {
                    PlayerSession.saveToDisk()
                    NotificationBus.stop()
                }
                else -> {}
            }
        })

        setContent {
            UpdateCheckDialog()
            AppRoot()
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        // 分享 H5 深链（热启动：App 已在运行，singleTask 复用本 Activity）
        handleDeepLink(intent)
    }

    /// 解析 liuyingmusic://track/{id} 并播放；未登录暂存，登录成功后自动播放
    private fun handleDeepLink(intent: Intent?) {
        val data = intent?.data ?: return
        if (data.scheme?.equals("liuyingmusic", ignoreCase = true) != true || data.host != "track") return
        val tid = data.lastPathSegment ?: return
        if (tid.isEmpty()) return
        if (AuthManager.currentUserValue != null) {
            playDeepLinkTrack(tid)
        } else {
            pendingDeepLinkId = tid
            scope.launch {
                AuthManager.currentUser.collect { u ->
                    if (u != null && pendingDeepLinkId != null) {
                        val id = pendingDeepLinkId!!
                        pendingDeepLinkId = null
                        playDeepLinkTrack(id)
                    }
                }
            }
        }
    }

    /// 深链：单曲精确查询 → 播放并跳回首页播放页（与通知跳歌同一路由）
    private fun playDeepLinkTrack(tid: String) {
        scope.launch {
            try {
                val t = app.star2936.guava3291.data.Api.track(tid)
                app.star2936.guava3291.state.AppRouter.jumpToHome(play = t)
            } catch (_: Exception) {
                // 单曲拉取失败（如下架）：保持当前页面，不打扰用户
            }
        }
    }

    private fun resumeAfterDeviceChange() {
        // 拔插瞬间播放器常被系统打断：短暂延迟后若仍在播放目标则恢复
        scope.launch {
            kotlinx.coroutines.delay(400)
            val player = PlayerManager.player
            val shouldPlay = PlayerManager.isPlaying.value
            if (shouldPlay &&
                player.playbackState != Player.STATE_BUFFERING &&
                !player.playWhenReady) {
                player.play()
            }
        }
    }

    override fun onTrimMemory(level: Int) {
        super.onTrimMemory(level)
        PlayerManager.onLowMemory()
    }

    override fun onDestroy() {
        audioDeviceCallback?.let { audioManager?.unregisterAudioDeviceCallback(it) }
        super.onDestroy()
    }
}

@Composable
fun AppRoot() {
    val user by AuthManager.currentUser.collectAsState()
    var showSplash by androidx.compose.runtime.remember { androidx.compose.runtime.mutableStateOf(true) }
    // 启动页广告（每次启动随机一张；null=无广告）
    var splashAd by androidx.compose.runtime.remember { androidx.compose.runtime.mutableStateOf<app.star2936.guava3291.data.SplashAd?>(null) }
    var adRequestDone by androidx.compose.runtime.remember { androidx.compose.runtime.mutableStateOf(false) }
    // 切换账号时的加载
    var showSwitchLoading by androidx.compose.runtime.remember { androidx.compose.runtime.mutableStateOf(false) }
    var prevUid by androidx.compose.runtime.remember { androidx.compose.runtime.mutableStateOf(user?.id) }

    // 断网恢复自动续播（对应 iOS NetworkMonitor 恢复回调）：
    // 离线期间播放失败 → 网络恢复后自动重试当前曲，无需用户手动点重试
    var wasOffline by androidx.compose.runtime.remember {
        androidx.compose.runtime.mutableStateOf(app.star2936.guava3291.state.NetworkMonitor.isOffline.value)
    }
    val offline by app.star2936.guava3291.state.NetworkMonitor.isOffline.collectAsState()
    androidx.compose.runtime.LaunchedEffect(offline) {
        if (wasOffline && !offline) {
            if (app.star2936.guava3291.state.PlayerManager.loadFailed.value) {
                app.star2936.guava3291.state.PlayerManager.reloadCurrent()
            }
        }
        wasOffline = offline
    }

    androidx.compose.runtime.LaunchedEffect(user?.id) {
        // 启动页最短展示 1.2s：数据就绪也至少停留 1.2s（避免闪跳）；超时 6s 兜底（对齐 iOS）
        val minShowMs = 1200L
        val maxWaitMs = 6000L
        if (user != null && prevUid != null && user?.id != prevUid) {
            // 切换账号：显示加载并预加载（force：上一次预加载可能在跑或已就绪，必须重载新账号数据）
            showSwitchLoading = true
            app.star2936.guava3291.state.AppPreloader.preload(force = true)
            val switchStart = System.currentTimeMillis()
            while (System.currentTimeMillis() - switchStart < maxWaitMs) {
                if ((System.currentTimeMillis() - switchStart >= minShowMs) &&
                    app.star2936.guava3291.state.AppPreloader.discoverRecommendedReady.value &&
                    app.star2936.guava3291.state.AppPreloader.mineLoaded.value
                ) break
                kotlinx.coroutines.delay(100)
            }
            showSwitchLoading = false
        }
        prevUid = user?.id
        if (user != null) {
            app.star2936.guava3291.state.AppPreloader.preload()
            val start = System.currentTimeMillis()
            while (System.currentTimeMillis() - start < maxWaitMs) {
                if ((System.currentTimeMillis() - start >= minShowMs) &&
                    app.star2936.guava3291.state.AppPreloader.discoverRecommendedReady.value &&
                    app.star2936.guava3291.state.AppPreloader.mineLoaded.value
                ) break
                kotlinx.coroutines.delay(100)
            }
            // 数据就绪后拉取启动页广告（随机一张，3s 超时；无广告直接放行）
            val ad = try { app.star2936.guava3291.data.Api.splashAd() } catch (_: Exception) { null }
            splashAd = ad
            adRequestDone = true
            if (ad == null) showSplash = false
        } else {
            kotlinx.coroutines.delay(1200)
            showSplash = false
        }
    }

    // 根页面切换（启动页/广告页/登录/主界面）统一叠加淡入：新页淡入叠加、旧页淡出，不滑动不缩放
    AnimatedContent(
        targetState = when {
            showSplash && adRequestDone && splashAd != null -> 4
            showSplash -> 0
            showSwitchLoading -> 1
            user == null -> 2
            else -> 3
        },
        transitionSpec = {
            fadeIn(
                animationSpec = tween(350, easing = androidx.compose.animation.core.EaseOutCubic)
            ).togetherWith(
                fadeOut(
                    animationSpec = tween(300, easing = androidx.compose.animation.core.EaseInCubic)
                )
            )
        },
        label = "appRoot"
    ) { state ->
        when (state) {
            0 -> SplashScreen()
            1 -> SplashScreen()
            2 -> LoginScreen()
            4 -> splashAd?.let { ad ->
                app.star2936.guava3291.ui.splash.SplashAdScreen(ad = ad) {
                    showSplash = false
                }
            } ?: SplashScreen()
            else -> MainScreen()
        }
    }
}

@Composable
fun UpdateCheckDialog() {
    var update by remember { mutableStateOf<app.star2936.guava3291.data.UpdateCheck?>(null) }
    val ctx = LocalContext.current
    LaunchedEffect(Unit) {
        kotlinx.coroutines.delay(1500)
        val u = try { app.star2936.guava3291.data.Api.checkUpdate() } catch (_: Exception) { null }
        if (u != null && u.hasUpdate) update = u
    }
    update?.let { u ->
        AlertDialog(
            onDismissRequest = { if (u.forceUpdate != 1) update = null },
            confirmButton = {
                TextButton(onClick = {
                    try {
                        val url = u.downloadUrl ?: ""
                        if (url.isNotEmpty()) {
                            val intent = Intent(Intent.ACTION_VIEW, android.net.Uri.parse(url))
                            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                            ctx.startActivity(intent)
                        }
                    } catch (_: Exception) {}
                    if (u.forceUpdate != 1) update = null
                }) { Text("去更新", color = Accent) }
            },
            dismissButton = if (u.forceUpdate != 1) {
                { TextButton(onClick = { update = null }) { Text("以后再说", color = TextSecondary) } }
            } else null,
            title = { Text(u.title ?: "发现新版本", color = TextPrimary) },
            text = {
                Column {
                    if (!u.version.isNullOrEmpty()) {
                        Text("新版本：${u.version}", color = Color(0xFF5CD68A))
                        Spacer(Modifier.height(8.dp))
                    }
                    Text(u.content ?: "", color = TextSecondary)
                }
            },
            containerColor = Card
        )
    }
}
