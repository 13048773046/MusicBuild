package app.star2936.guava3291

import android.app.Application
import android.content.ComponentCallbacks2
import app.star2936.guava3291.security.ApkGuard
import app.star2936.guava3291.state.AuthManager
import app.star2936.guava3291.state.LocalPlayHistory
import app.star2936.guava3291.state.NetworkMonitor
import app.star2936.guava3291.state.NotificationBus
import app.star2936.guava3291.state.PlayerManager
import app.star2936.guava3291.state.PlayerSession
import app.star2936.guava3291.state.VisualizationSettings
import app.star2936.guava3291.state.WeatherContext

class MusicApplication : Application(), ComponentCallbacks2 {

    override fun onCreate() {
        super.onCreate()
        // 发布包自防护（签名自校验 + 反调试；debug 包自动跳过）
        if (!ApkGuard.verify(this)) {
            // 检测到篡改/调试：不初始化业务，直接结束进程
            android.os.Process.killProcess(android.os.Process.myPid())
            return
        }
        app.star2936.guava3291.data.DeviceInfo.appContext = this
        AuthManager.init(this)
        PlayerSession.init(this)
        PlayerManager.init(this)
        NotificationBus.register()
        WeatherContext.init(this)
        NetworkMonitor.init(this)
        LocalPlayHistory.init(this)
        VisualizationSettings.init(this)
        // 图片加载（封面/头像/背景）复用带证书固定+防抓包拦截的 OkHttp：
        // 抓包工具注入 CA 后 Coil 流量同样握手失败，图片请求也不可被解密
        try {
            coil.Coil.setImageLoader(
                coil.ImageLoader.Builder(this)
                    .okHttpClient(app.star2936.guava3291.data.Api.http)
                    .crossfade(true)
                    .build()
            )
        } catch (_: Exception) { }
        // 注意：不要 registerComponentCallbacks(this)！
        // Application 的 onConfigurationChanged/onTrimMemory/onLowMemory 由系统自动回调，
        // 注册自己会导致 config change 时 onConfigurationChanged 无限递归 → StackOverflow 崩溃。
    }

    override fun onTrimMemory(level: Int) {
        super.onTrimMemory(level)
        if (level >= ComponentCallbacks2.TRIM_MEMORY_RUNNING_LOW) {
            PlayerManager.onLowMemory()
        }
    }

    override fun onLowMemory() {
        super.onLowMemory()
        PlayerManager.onLowMemory()
    }
}
