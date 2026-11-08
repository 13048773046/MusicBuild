package app.star2936.guava3291.ui.settings

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import app.star2936.guava3291.data.Api
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.star2936.guava3291.BuildConfig
import app.star2936.guava3291.ui.theme.*

@Composable
fun AboutScreenCompat(onBack: () -> Unit) {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val statusBarTop = WindowInsets.statusBars.asPaddingValues().calculateTopPadding()
    // 联系方式：后台「网站管理 → 关于页配置」可配（/api/site-config），失败用本地默认值兜底
    var website by remember { mutableStateOf("www.dbmt999.asia") }
    var email by remember { mutableStateOf("yosar027@outlook.com") }
    var phone by remember { mutableStateOf("3065751692") }
    var qq by remember { mutableStateOf("807910369") }
    LaunchedEffect(Unit) {
        val cfg = Api.siteConfig()
        if (!cfg["about_website"].isNullOrBlank()) website = cfg["about_website"]!!.removePrefix("https://").removePrefix("http://")
        if (!cfg["about_email"].isNullOrBlank()) email = cfg["about_email"]!!
        if (!cfg["about_phone"].isNullOrBlank()) phone = cfg["about_phone"]!!
        if (!cfg["about_qq_group"].isNullOrBlank()) qq = cfg["about_qq_group"]!!
    }

    Box(Modifier.fillMaxSize().background(Background)) {
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(top = statusBarTop)
                // 全屏子页面：底部补系统手势区
                .navigationBarsPadding()
        ) {
            Row(
                Modifier.padding(horizontal = 20.dp).padding(top = 10.dp, bottom = 6.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Box(
                    Modifier.size(38.dp).clip(CircleShape).background(Card).clickable { onBack() },
                    contentAlignment = Alignment.Center
                ) { Icon(Icons.Filled.ArrowBack, null, tint = TextPrimary, modifier = Modifier.size(18.dp)) }
                Spacer(Modifier.width(12.dp))
                Column {
                    Text("关于 APP", color = TextPrimary, fontSize = 30.sp, fontWeight = FontWeight.Bold)
                    Text("流萤音乐 · 让好音乐如流萤般点亮夜晚", color = TextSecondary, fontSize = 12.sp)
                }
            }

            Spacer(Modifier.height(20.dp))

            // Logo + 版本
            Column(
                Modifier.fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                androidx.compose.foundation.Image(
                    painter = androidx.compose.ui.res.painterResource(app.star2936.guava3291.R.drawable.app_logo),
                    contentDescription = null,
                    modifier = Modifier.size(104.dp).clip(RoundedCornerShape(26.dp))
                )
                Spacer(Modifier.height(14.dp))
                Text("流萤音乐", color = TextPrimary, fontSize = 20.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(5.dp))
                Text("版本 v${BuildConfig.VERSION_NAME}", color = TextSecondary, fontSize = 12.sp)
            }

            Spacer(Modifier.height(28.dp))

            // 联系我们（可点击：官网/邮箱/拨号/复制，对齐 iOS AboutView 交互）
            Text("联系我们", color = TextTertiary, fontSize = 12.sp, fontWeight = FontWeight.Medium, modifier = Modifier.padding(start = 24.dp, bottom = 8.dp))
            Column(
                Modifier.padding(horizontal = 20.dp).clip(RoundedCornerShape(18.dp)).background(Card)
            ) {
                ContactRow(
                    Icons.Filled.Language, Accent, "官网", website,
                    onClick = {
                        runCatching {
                            val intent = android.content.Intent(
                                android.content.Intent.ACTION_VIEW,
                                android.net.Uri.parse("https://$website")
                            )
                            ctx.startActivity(intent)
                        }
                    },
                )
                DividerLine()
                ContactRow(
                    Icons.Filled.Email, Color(0xFF5AC8FA), "联系邮箱", email,
                    onClick = {
                        runCatching {
                            val intent = android.content.Intent(
                                android.content.Intent.ACTION_SENDTO,
                                android.net.Uri.parse("mailto:$email")
                            )
                            ctx.startActivity(intent)
                        }
                    },
                )
                DividerLine()
                ContactRow(
                    Icons.Filled.Phone, Color(0xFFFF9500), "客服热线", phone,
                    onClick = {
                        runCatching {
                            val intent = android.content.Intent(
                                android.content.Intent.ACTION_DIAL,
                                android.net.Uri.parse("tel:$phone")
                            )
                            ctx.startActivity(intent)
                        }
                    },
                )
                DividerLine()
                ContactRow(
                    Icons.Filled.Group, Lilac, "官方 QQ 群", qq,
                    onClick = {
                        runCatching {
                            val clipboard = ctx.getSystemService(android.content.Context.CLIPBOARD_SERVICE)
                                as android.content.ClipboardManager
                            clipboard.setPrimaryClip(android.content.ClipData.newPlainText("QQ 群号", qq))
                            android.widget.Toast.makeText(ctx, "QQ 群号已复制：$qq", android.widget.Toast.LENGTH_SHORT).show()
                        }
                    },
                )
            }

            Spacer(Modifier.height(40.dp))
        }
    }
}

@Composable
private fun ContactRow(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    tint: Color,
    title: String,
    value: String,
    onClick: () -> Unit,
) {
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 13.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(
            Modifier.size(28.dp).clip(RoundedCornerShape(8.dp)).background(tint.copy(alpha = 0.14f)),
            contentAlignment = Alignment.Center
        ) { Icon(icon, null, tint = tint, modifier = Modifier.size(16.dp)) }
        Spacer(Modifier.width(12.dp))
        Text(title, color = TextPrimary, fontSize = 15.sp)
        Spacer(Modifier.weight(1f))
        Text(value, color = TextTertiary, fontSize = 13.sp)
    }
}

@Composable
private fun DividerLine() {
    Box(Modifier.padding(start = 56.dp).fillMaxWidth().height(0.5.dp).background(CardBorder))
}
