package app.star2936.guava3291.ui.settings

// 开源许可页（对应 iOS OpenSourceLicensesView：合规展示第三方开源组件与许可）

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.star2936.guava3291.ui.theme.Background
import app.star2936.guava3291.ui.theme.Card
import app.star2936.guava3291.ui.theme.TextPrimary
import app.star2936.guava3291.ui.theme.TextSecondary
import app.star2936.guava3291.ui.theme.TextTertiary

private data class License(val name: String, val license: String, val notice: String)

private val licenses = listOf(
    License("Folia 全貌版歌词引擎", "Proprietary / 随包分发", "Folia 网页渲染层（React + PixiJS），随 App 资源包本地分发"),
    License("Media3 ExoPlayer", "Apache-2.0", "Android 媒体播放核心（对应 iOS AVQueuePlayer）"),
    License("Jetpack Compose", "Apache-2.0", "Android 声明式 UI 框架"),
    License("OkHttp", "Apache-2.0", "HTTP / WebSocket 网络层"),
    License("Retrofit 2", "Apache-2.0", "声明式 REST 接口封装"),
    License("kotlinx.serialization", "Apache-2.0", "JSON 序列化"),
    License("Coil", "Apache-2.0", "图片加载与封面缓存"),
    License("DataStore", "Apache-2.0", "偏好存储（对应 iOS UserDefaults）"),
    License("AndroidX Security Crypto", "Apache-2.0", "会话加密存储（对应 iOS Keychain）"),
    License("kotlinx.coroutines", "Apache-2.0", "协程异步框架"),
    License("WebViewAssetLoader", "Apache-2.0", "Folia 网页包本地伺服（https 源）"),
)

@Composable
fun OpenSourceLicensesScreen(onBack: () -> Unit) {
    Box(Modifier.fillMaxSize().background(Background)) {
        LazyColumn(
            Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding(),
            contentPadding = PaddingValues(horizontal = 20.dp, vertical = 12.dp),
        ) {
            item {
                Row(Modifier.padding(vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        Modifier.size(38.dp).clip(CircleShape).background(Card)
                            .clickable { onBack() },
                        contentAlignment = Alignment.Center
                    ) {
                        Icon(Icons.Filled.ArrowBack, contentDescription = null, tint = TextPrimary, modifier = Modifier.size(18.dp))
                    }
                    Spacer(Modifier.width(12.dp))
                    Column {
                        Text("开源许可", color = TextPrimary, fontSize = 30.sp, fontWeight = FontWeight.Bold)
                        Text("第三方开源组件与许可声明", color = TextSecondary, fontSize = 12.sp)
                    }
                }
            }
            item {
                Text(
                    "本 App 使用以下开源组件，遵守其对应开源许可。完整许可文本以各项目官方发布为准。",
                    color = TextTertiary, fontSize = 13.sp, lineHeight = 19.sp,
                    modifier = Modifier.padding(vertical = 8.dp),
                )
            }
            items(licenses) { lic ->
                Column(
                    Modifier.fillMaxWidth().padding(vertical = 8.dp)
                        .clip(RoundedCornerShape(12.dp)).background(Card).padding(16.dp)
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(lic.name, color = TextPrimary, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                        Text(lic.license, color = Color(0xFF6C8EFF), fontSize = 12.sp, fontWeight = FontWeight.Medium)
                    }
                    Spacer(Modifier.height(6.dp))
                    Text(lic.notice, color = TextSecondary, fontSize = 13.sp, lineHeight = 19.sp)
                }
            }
        }
    }
}
