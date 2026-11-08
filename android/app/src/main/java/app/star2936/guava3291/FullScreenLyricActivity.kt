package app.star2936.guava3291

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import app.star2936.guava3291.state.PlayerManager
import app.star2936.guava3291.ui.home.FullScreenFoliaLyricScreen

/**
 * 独立全屏歌词 Activity（横屏专用）
 * 从 Dialog 改为独立 Activity，避免主 Activity 配置变化导致的无限递归崩溃。
 */
class FullScreenLyricActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        val trackId = intent.getStringExtra("trackId") ?: run { finish(); return }
        // 从 PlayerManager 获取当前歌曲
        val track = PlayerManager.queue.value.find { it.id == trackId }
            ?: PlayerManager.current
            ?: run { finish(); return }

        setContent {
            FullScreenFoliaLyricScreen(
                track = track,
                onDismiss = { finish() }
            )
        }
    }

    // 返回主界面也用叠加淡入（与进入转场对称，避免系统默认滑入/缩放进场）
    override fun finish() {
        super.finish()
        overridePendingTransition(R.anim.fade_in, R.anim.fade_out)
    }
}
