package app.star2936.guava3291.ui.splash

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import app.star2936.guava3291.data.SplashAd
import app.star2936.guava3291.ui.theme.Background
import app.star2936.guava3291.ui.theme.TextSecondary
import kotlinx.coroutines.delay

// 对应 iOS SplashAdView：启动页广告（每次启动随机一张，非轮播）
// 上部 75% 展示广告图（点击可跳 link），底部 25% 品牌区（logo+名称+标语），右上角 5s 倒计时跳过
@Composable
fun SplashAdScreen(ad: SplashAd, onFinish: () -> Unit) {
    var countdown by remember { mutableIntStateOf(5) }
    val ctx = androidx.compose.ui.platform.LocalContext.current
    // 品牌区紧凑化（对齐 iOS 6cc7119：不再固定 25% 屏高，改为紧凑自适应高度）
    val brandHeightDp = 104f

    // 5s 倒计时：读到 0 自动进入主界面
    LaunchedEffect(Unit) {
        while (countdown > 1) {
            delay(1000)
            countdown--
        }
        onFinish()
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Background)
    ) {
        // 广告图（占满屏幕，底部被品牌区渐变遮罩衔接）
        AsyncImage(
            model = ad.imageUrl,
            contentDescription = null,
            contentScale = ContentScale.Crop,
            modifier = Modifier
                .fillMaxSize()
                .clickable(enabled = ad.link.isNotEmpty()) {
                    if (ad.link.isNotEmpty()) {
                        ctx.startActivity(
                            android.content.Intent(
                                android.content.Intent.ACTION_VIEW,
                                android.net.Uri.parse(ad.link)
                            ).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
                        )
                    }
                },
            onError = { /* 广告图加载失败也正常倒计时进入 */ }
        )

        // 底部品牌区：紧凑自适应高度 + 底部导航栏/手势区避让
        // （用系统级 navigationBarsPadding，跟随设备配置自动更新，
        //   避免手动读 insets 在部分设备/Android 15 强制 edge-to-edge 下返回 0 导致被导航栏挡住）
        Column(
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth()
                .height(brandHeightDp.dp)
                .navigationBarsPadding()
                .background(
                    Brush.verticalGradient(
                        colors = listOf(Color.Transparent, Background, Background)
                    )
                ),
            verticalArrangement = Arrangement.Bottom
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 28.dp, vertical = 20.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Image(
                    painter = androidx.compose.ui.res.painterResource(app.star2936.guava3291.R.drawable.app_logo),
                    contentDescription = null,
                    modifier = Modifier
                        .size(52.dp)
                        .clip(RoundedCornerShape(13.dp))
                )
                Spacer(Modifier.width(14.dp))
                Column {
                    Text("流萤音乐", color = Color(0xFFECECEC), fontSize = 24.sp, fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(5.dp))
                    Text("让好音乐如流萤般点亮夜晚", color = TextSecondary, fontSize = 13.sp)
                }
                Spacer(Modifier.weight(1f))
            }
        }

        // 右上角倒计时跳过按钮（statusBarsPadding 可靠避让状态栏）
        Box(
            modifier = Modifier
                .align(Alignment.TopEnd)
                .statusBarsPadding()
                .padding(top = 8.dp, end = 16.dp)
                .clip(CircleShape)
                .background(Color.Black.copy(alpha = 0.45f))
                .clickable { onFinish() }
                .padding(horizontal = 14.dp, vertical = 7.dp)
        ) {
            Text("${countdown}s 跳过", color = Color.White, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
        }

        // 广告图底部中央：点击提示（仅广告配置了跳转链接时显示，提示用户广告可点）
        if (ad.link.isNotEmpty()) {
            Box(
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .padding(bottom = brandHeightDp.dp + 12.dp)
                    .navigationBarsPadding()
                    .clip(RoundedCornerShape(50))
                    .background(Color.Black.copy(alpha = 0.35f))
                    .padding(horizontal = 14.dp, vertical = 6.dp)
            ) {
                Text("点击了解更多  ›", color = Color.White, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
            }
        }
    }
}
