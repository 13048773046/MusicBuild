package app.star2936.guava3291.ui.home

// Folia「全貌版」全屏歌词（对应 iOS FullScreenWebLyricView + FoliaLocalServer + WebLyricBridge）
// 实现方式：WebViewAssetLoader 以 https://appassets.androidplatform.net 伺服本地 folia/ 网页包
// （ES Module 结构要求真实 origin，file:// 会触发 CORS 失败 —— 与 iOS 用本地 HTTP 服务同理）。
// 桥接协议与 iOS 完全一致：
//   Web → Native：window.webkit.messageHandlers.folia.postMessage({type, payload})（经 JS shim 转发到 AndroidFoliaBridge）
//   Native → Web：window.__foliaHost.receive(json)（song / time / mode 三类消息）

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.content.pm.ActivityInfo
import android.content.SharedPreferences
import android.graphics.Color as AColor
import android.view.View
import android.view.ViewGroup
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Fullscreen
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material3.Icon
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.webkit.WebViewAssetLoader
import app.star2936.guava3291.data.Api
import app.star2936.guava3291.data.Track
import app.star2936.guava3291.state.PlayerManager
import app.star2936.guava3291.util.ColorPalette
import app.star2936.guava3291.util.foliaEnhancedLRC
import app.star2936.guava3291.util.parseLyrics
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import androidx.compose.runtime.snapshotFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

private const val FOLIA_URL = "https://appassets.androidplatform.net/assets/folia/host.html"
private const val MODE_KEY = "folia_web_lyric_mode"

/** Web → Native 桥接对象（addJavascriptInterface 注入为 window.AndroidFoliaBridge） */
class AndroidFoliaBridge(private val onMessage: (String) -> Unit) {
    @android.webkit.JavascriptInterface
    fun postMessage(json: String) = onMessage(json)
}

private tailrec fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.findActivity()
    else -> null
}

/** Folia 全貌版全屏歌词页（横屏 + 沉浸 + 双端控制 + 模式持久化） */
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun FullScreenFoliaLyricScreen(track: Track, onDismiss: () -> Unit) {
    val context = LocalContext.current
    val activity = remember(context) { context.findActivity() }
    val scope = rememberCoroutineScope()

    var chromeVisible by remember { mutableStateOf(true) }
    var loadFailed by remember { mutableStateOf(false) }
    var webReady by remember { mutableStateOf(false) }
    // #22：首屏加载骨架——folia 引擎 1900+ JS 首屏需数百 ms~秒，弱机白屏前给出占位提示
    var isLoading by remember { mutableStateOf(true) }
    var enteredTrackId by remember { mutableStateOf(track.id) }
    var webKey by remember { mutableIntStateOf(0) }
    val webViewRef = remember { mutableStateOf<WebView?>(null) }
    val prefs: SharedPreferences = remember { context.getSharedPreferences("folia_prefs", Context.MODE_PRIVATE) }
    // 销毁标记：防止 WebView destroy 后仍有排队任务 evaluateJavascript（竞态崩溃）
    val disposed = remember { java.util.concurrent.atomic.AtomicBoolean(false) }

    // ---------- Native → Web 推送 ----------
    fun pushToWeb(dict: JSONObject) {
        val wv = webViewRef.value ?: return
        if (disposed.get()) return
        wv.post {
            if (disposed.get()) return@post
            wv.evaluateJavascript(
                "window.__foliaHost && window.__foliaHost.receive(${dict.toString()})",
                null,
            )
        }
    }

    fun pushTime() {
        val pm = PlayerManager
        pushToWeb(
            JSONObject().put("type", "time").put(
                "payload",
                JSONObject().put("t", pm.currentTime.value).put(
                    "state",
                    if (pm.isPlaying.value) "playing" else "paused",
                ),
            ),
        )
    }

    fun themeFrom(c: Color): JSONObject {
        fun hex(v: Float): Int = (v.coerceIn(0f, 1f) * 255f).toInt()
        val acc = String.format("#%02X%02X%02X", hex(c.red), hex(c.green), hex(c.blue))
        val bg = String.format("#%02X%02X%02X", hex(c.red * 0.28f), hex(c.green * 0.28f), hex(c.blue * 0.28f))
        return JSONObject()
            .put("backgroundColor", bg)
            .put("primaryColor", "#f5f5f7")
            .put("accentColor", acc)
            .put("secondaryColor", "#c8ccd8")
    }

    fun pushSong() {
        val pm = PlayerManager
        val cur = pm.queue.value.getOrNull(pm.currentIndex.value) ?: track
        scope.launch {
            // 歌词：复用内存缓存，KRC/LRC 解析后转 Folia enhanced-lrc 逐字格式
            val raw = try { Api.fetchLyric(cur.id) } catch (_: Exception) { "" }
            val lines = parseLyrics(raw)
            val lyric = foliaEnhancedLRC(lines, cur.title, cur.artist ?: "")
            // 主题：封面主色（直方图主色）→ Folia Theme
            var theme = JSONObject()
            cur.coverURL?.let { url ->
                val color = ColorPalette.load(cur.id, url)
                if (color != null) theme = themeFrom(color)
            }
            val payload = JSONObject()
                .put("id", cur.id)
                .put("title", cur.title)
                .put("artist", cur.artist ?: "")
                .put("cover", cur.coverURL ?: "")
                .put("duration", pm.duration.value)
                .put("lyric", lyric)
                .put("theme", theme)
            pushToWeb(JSONObject().put("type", "song").put("payload", payload))
            pushTime()
        }
    }

    // ---------- Web → Native 分发（主线程） ----------
    fun handleWebMessage(json: String) {
        val body = try { JSONObject(json) } catch (_: Exception) { return }
        when (body.optString("type")) {
            "ready" -> {
                webReady = true
                pushSong()
            }
            "seek" -> {
                val t = body.optJSONObject("payload")?.optDouble("t", 0.0) ?: 0.0
                if (t > 0) PlayerManager.seekToSeconds(t)
            }
            "mode" -> {
                val m = body.optJSONObject("payload")?.optString("mode")
                if (!m.isNullOrEmpty()) prefs.edit().putString(MODE_KEY, m).apply()
            }
            "control" -> {
                when (body.optJSONObject("payload")?.optString("action")) {
                    // 播放按钮 = 续播当前曲目（保留进度/已加载媒体），绝不能再走
                    // playCurrent()——那是重建入口（清空+重载），会把歌从头重播/闪一下
                    "play" -> PlayerManager.resumePlayback()
                    "pause" -> PlayerManager.pausePlayback()
                    "next" -> PlayerManager.next()
                    "previous" -> PlayerManager.previous()
                }
            }
            "immersive" -> {
                val visible = body.optJSONObject("payload")?.optBoolean("visible") ?: true
                chromeVisible = visible
            }
        }
    }

    // 主线程桥接入口
    val mainHandler = remember { android.os.Handler(android.os.Looper.getMainLooper()) }
    fun bridgePost(json: String) {
        if (android.os.Looper.myLooper() == android.os.Looper.getMainLooper()) handleWebMessage(json)
        else mainHandler.post { handleWebMessage(json) }
    }

    // JS shim：把 iOS 版 folia-patch / host 调用的 webkit.messageHandlers.folia 转发到 AndroidFoliaBridge
    fun injectShim(wv: WebView) {
        wv.evaluateJavascript(
            """(function () {
                if (window.__foliaShimLoaded) return;
                window.__foliaShimLoaded = true;
                window.webkit = window.webkit || {};
                window.webkit.messageHandlers = window.webkit.messageHandlers || {};
                window.webkit.messageHandlers.folia = window.webkit.messageHandlers.folia || {
                  postMessage: function (msg) {
                    try { if (typeof msg !== 'string') msg = JSON.stringify(msg); } catch (e) {}
                    if (window.AndroidFoliaBridge && window.AndroidFoliaBridge.postMessage) {
                      window.AndroidFoliaBridge.postMessage(msg);
                    }
                  }
                };
            })();""",
            null,
        )
    }

    // ---------- 沉浸系统栏（横屏已在 Manifest 里设置，无需代码动态切换） ----------
    DisposableEffect(Unit) {
        val window = activity?.window
        val decor = window?.decorView
        if (window != null && decor != null) {
            WindowCompat.setDecorFitsSystemWindows(window, false)
            val controller = WindowInsetsControllerCompat(window, decor)
            controller.hide(WindowInsetsCompat.Type.systemBars())
            controller.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
        onDispose {
            disposed.set(true)
            if (window != null && decor != null) {
                WindowInsetsControllerCompat(window, decor).show(WindowInsetsCompat.Type.systemBars())
            }
            webViewRef.value?.let { wv ->
                (wv.parent as? ViewGroup)?.removeView(wv)
                wv.stopLoading()
                wv.destroy()
            }
            webViewRef.value = null
        }
    }

    // ---------- 数据驱动 ----------
    // 播放进度 → time 消息（限流 ~4Hz，对齐 iOS）
    LaunchedEffect(Unit) {
        while (true) {
            if (webReady) pushTime()
            delay(250)
        }
    }
    // 播放状态变化 → time 消息 state 字段
    LaunchedEffect(Unit) {
        snapshotFlow { PlayerManager.isPlaying.value }.collect {
            if (webReady) pushTime()
        }
    }
    // 队列内切歌 → 重新注入 song
    LaunchedEffect(Unit) {
        snapshotFlow { PlayerManager.currentIndex.value }.collect { idx ->
            val cur = PlayerManager.queue.value.getOrNull(idx) ?: return@collect
            if (cur.id != enteredTrackId) {
                enteredTrackId = cur.id
                pushSong()
            }
        }
    }

    Box(Modifier.fillMaxSize().background(Color.Black)) {
        // ---------- Web 渲染层 ----------
        if (!loadFailed) {
            // 重试（webKey++）时强制重建 WebView：渲染进程已挂的旧实例无法 reload
            androidx.compose.runtime.key(webKey) {
                AndroidView(
                    factory = { ctx ->
                        val wv = WebView(ctx).apply {
                            webViewRef.value = this
                            setBackgroundColor(AColor.BLACK)
                            isVerticalScrollBarEnabled = false
                            isHorizontalScrollBarEnabled = false
                            overScrollMode = WebView.OVER_SCROLL_NEVER
                            settings.javaScriptEnabled = true
                            settings.domStorageEnabled = true
                            settings.mediaPlaybackRequiresUserGesture = false
                        }
                    val assetLoader = WebViewAssetLoader.Builder()
                        .addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(ctx))
                        .build()
                    wv.webViewClient = object : WebViewClient() {
                        override fun shouldInterceptRequest(
                            view: WebView?,
                            request: WebResourceRequest?,
                        ): WebResourceResponse? {
                            return request?.let { assetLoader.shouldInterceptRequest(it.url) }
                        }

                        override fun onPageStarted(view: WebView, url: String?, favicon: android.graphics.Bitmap?) {
                            injectShim(view)
                            isLoading = true   // #22
                        }

                        override fun onPageFinished(view: WebView?, url: String?) {
                            loadFailed = false
                            isLoading = false   // #22
                        }

                        override fun onReceivedError(
                            view: WebView?,
                            request: WebResourceRequest?,
                            error: WebResourceError?,
                        ) {
                            if (request?.isForMainFrame == true) loadFailed = true
                        }

                        // 渲染进程崩溃接管（API 26+）：
                        // folia 3D 歌词在低端机/内存压力下 WebView 渲染进程可能被杀，
                        // 默认系统会直接闪退整个 App —— 返回 true 由应用接管，走重试兜底 UI
                        override fun onRenderProcessGone(
                            view: WebView,
                            detail: android.webkit.RenderProcessGoneDetail,
                        ): Boolean {
                            loadFailed = true
                            return true
                        }
                    }
                    wv.addJavascriptInterface(
                        AndroidFoliaBridge { json -> bridgePost(json) },
                        "AndroidFoliaBridge",
                    )
                    wv.loadUrl(FOLIA_URL)
                    wv
                },
                onRelease = { wv: WebView ->
                    // key 重建（重试）时释放旧 WebView：防止渲染进程已挂的旧实例泄漏
                    if (webViewRef.value === wv) webViewRef.value = null
                    (wv.parent as? ViewGroup)?.removeView(wv)
                    wv.stopLoading()
                    wv.destroy()
                },
                modifier = Modifier.fillMaxSize(),
                )
            }
        }

        // ---------- 首屏加载骨架（#22）：弱机 folia 首屏 JS 解析期间给出占位 ----------
        if (isLoading && !loadFailed) {
            Column(
                Modifier.fillMaxSize().background(Color.Black),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
            ) {
                CircularProgressIndicator(
                    color = Color.White.copy(alpha = 0.75f),
                    strokeWidth = 2.dp,
                    modifier = Modifier.size(28.dp),
                )
                Spacer(Modifier.height(14.dp))
                Text("正在加载歌词引擎…", color = Color.White.copy(alpha = 0.5f), fontSize = 13.sp)
            }
        }

        // ---------- 加载失败兜底 ----------
        if (loadFailed) {
            Column(
                Modifier.fillMaxSize().background(Color.Black),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
            ) {
                Text("歌词引擎加载失败", color = Color.White.copy(alpha = 0.8f), fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.height(12.dp))
                Text("请重试", color = Color.White.copy(alpha = 0.5f), fontSize = 13.sp)
                Spacer(Modifier.height(16.dp))
                Box(
                    Modifier.clip(CircleShape).background(Color.White.copy(alpha = 0.16f))
                        .clickable {
                            // 重建 WebView 重试（渲染进程已挂时 reload() 无效，必须重建实例）
                            loadFailed = false
                            webReady = false
                            webKey++
                        }
                        .padding(horizontal = 28.dp, vertical = 10.dp),
                ) {
                    Text("重试", color = Color.White, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                }
            }
        }

        // ---------- 顶栏（沉浸模式下隐藏，对齐 iOS） ----------
        AnimatedVisibility(
            visible = chromeVisible,
            enter = fadeIn() + slideInVertically { -it },
            exit = fadeOut() + slideOutVertically { -it },
        ) {
            Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp)) {
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    // 关闭
                    Box(
                        Modifier.size(36.dp).clip(CircleShape).background(Color.White.copy(alpha = 0.14f))
                            .clickable { onDismiss() },
                        contentAlignment = Alignment.Center,
                    ) {
                        Icon(Icons.Filled.KeyboardArrowDown, contentDescription = "关闭", tint = Color.White, modifier = Modifier.size(22.dp))
                    }
                    Spacer(Modifier.weight(1f))
                    Text("全貌版", color = Color.White.copy(alpha = 0.85f), fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.weight(1f))
                    // 沉浸按钮：隐藏顶栏并同步网页控件进入沉浸
                    Box(
                        Modifier.size(36.dp).clip(CircleShape).background(Color.White.copy(alpha = 0.14f))
                            .clickable {
                                chromeVisible = false
                                webViewRef.value?.post {
                                    webViewRef.value?.evaluateJavascript(
                                        "window.__foliaPatch && window.__foliaPatch.setImmersive(true)",
                                        null,
                                    )
                                }
                            },
                        contentAlignment = Alignment.Center,
                    ) {
                        Icon(Icons.Filled.Fullscreen, contentDescription = "沉浸", tint = Color.White, modifier = Modifier.size(18.dp))
                    }
                }
                Spacer(Modifier.height(8.dp))
            }
        }
    }
}
