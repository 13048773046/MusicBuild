package app.star2936.guava3291.ui.settings

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.star2936.guava3291.data.Api
import app.star2936.guava3291.state.AuthManager
import app.star2936.guava3291.ui.components.MemberBadge
import app.star2936.guava3291.ui.theme.*
import kotlinx.coroutines.launch
import java.util.Locale

/// 会员中心（Android 全面对标 iOS MemberCenterView）：顶部光晕大标题 → 档位 tab（VIP 绿/SVIP 金）
/// → 三档套餐卡（月/季/年，年卡推荐）→ 套餐说明 → 支付说明 → 权益三宫格 → 常用工具四宫格
/// → 底部协议勾选 + 动态价格大按钮。价格/时长后台「网站管理→支付设置」可配，前端不写死。
/// 无虚构功能（无自动续费/金币抵扣）。
@Composable
fun MemberCenterScreen(
    onBack: () -> Unit,
) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val statusBarTop = WindowInsets.statusBars.asPaddingValues().calculateTopPadding()
    var plan by remember { mutableStateOf("svip") }        // 默认选中 SVIP
    var term by remember { mutableStateOf("month") }       // month 月卡 / quarter 季卡 / year 年卡
    var agree by remember { mutableStateOf(true) }          // 协议勾选
    var busy by remember { mutableStateOf(false) }
    var tip by remember { mutableStateOf<String?>(null) }
    var pricing by remember { mutableStateOf(Api.MemberPricing()) }
    val u by AuthManager.currentUser.collectAsState()

    val vipColors = listOf(Color(0xFF1B5E3C), Color(0xFF2ECC8F))
    val svipColors = listOf(Color(0xFF7A5E1F), Color(0xFFE3C25B))
    val curColors = if (plan == "svip") svipColors else vipColors
    val curAccent = curColors[1]
    val price = pricing.price(plan, term)
    val days = pricing.days(plan, term)
    val priceText = "¥%.0f".format(Locale.CHINA, price)
    val perDayText = if (days > 0) "%.2f 元/天".format(Locale.CHINA, price / days) else "配置中"

    fun openPay() {
        if (!agree) {
            tip = "请先阅读并同意《会员服务协议》"
            return
        }
        if (busy) return
        busy = true
        scope.launch {
            try {
                val r = Api.payCreate(plan, term)
                busy = false
                val url = r.payUrl
                if (url.isNullOrEmpty()) {
                    tip = r.note ?: "支付未启用，请稍后再试"
                    return@launch
                }
                ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
                tip = "支付将在浏览器中完成，支付完成后返回本页将自动刷新会员状态"
                AuthManager.refreshMemberStatus()
            } catch (_: Exception) {
                busy = false
                tip = "下单失败，请检查网络后重试"
            }
        }
    }

    LaunchedEffect(Unit) {
        AuthManager.refreshMemberStatus()
        pricing = runCatching { Api.memberPricing() }.getOrDefault(Api.MemberPricing())
    }

    fun priceOf(t: String) = "¥%.0f".format(Locale.CHINA, pricing.price(plan, t))
    fun daysOf(t: String) = "${pricing.days(plan, t)} 天"
    fun perDayOf(t: String): String {
        val d = pricing.days(plan, t)
        return if (d > 0) "仅 %.2f 元/天".format(Locale.CHINA, pricing.price(plan, t) / d) else "配置中"
    }

    Box(Modifier.fillMaxSize().background(Brush.verticalGradient(listOf(Color(0xFF0B0D10), Color(0xFF0E1016))))) {
        // 顶部档位色渐变（延伸到状态栏，随安全区自适应）
        Box(
            Modifier
                .fillMaxWidth()
                .height(520.dp)
                .background(
                    Brush.verticalGradient(
                        listOf(curAccent.copy(alpha = 0.14f), curAccent.copy(alpha = 0.05f), curAccent.copy(alpha = 0f))
                    )
                )
                .padding(top = statusBarTop)
        )

        Column(
            Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(top = statusBarTop)
        ) {
            // 返回键统一样式：圆底 chevron（与全 App 一致，对齐 iOS NavBack）
            Row(
                Modifier.padding(horizontal = 20.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Box(
                    Modifier
                        .size(38.dp)
                        .clip(CircleShape)
                        .background(Color.Black.copy(alpha = 0.28f))
                        .clickable { onBack() },
                    contentAlignment = Alignment.Center
                ) {
                    Icon(Icons.Filled.ArrowBack, contentDescription = null,
                        tint = Color.White, modifier = Modifier.size(16.dp))
                }
                Spacer(Modifier.width(4.dp))
            }

            // 1. 大标题区（白色主字 + 档位色点缀）
            Column(Modifier.padding(horizontal = 20.dp).padding(top = 6.dp, bottom = 24.dp)) {
                Text(
                    if (plan == "svip") "即刻成为 超级会员" else "超低价开通 流萤会员",
                    color = Color.White, fontSize = 26.sp, fontWeight = FontWeight.Black,
                    maxLines = 1
                )
                Spacer(Modifier.height(10.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("新用户低至", color = Color.White.copy(alpha = 0.55f), fontSize = 13.sp)
                    Spacer(Modifier.width(8.dp))
                    Text(perDayText, color = curAccent, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                }
            }

            // 2. 档位分段控件（VIP 绿 / SVIP 金，胶囊式切换）
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 20.dp).padding(bottom = 16.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                PlanSegment(
                    name = "VIP", desc = "享歌曲畅听", colors = vipColors,
                    selected = plan == "vip",
                    modifier = Modifier.weight(1f),
                    onClick = { plan = "vip" }
                )
                PlanSegment(
                    name = "SVIP", desc = "享免广告·专属标识", colors = svipColors,
                    selected = plan == "svip",
                    modifier = Modifier.weight(1f),
                    onClick = { plan = "svip" }
                )
            }

            // 3. 三档套餐卡（月/季/年，年卡推荐）
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 20.dp),
                horizontalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                TermCard(name = "月卡", price = priceOf("month"), days = daysOf("month"),
                    perDay = perDayOf("month"), recommended = false, selected = term == "month",
                    accent = curAccent, modifier = Modifier.weight(1f), onClick = { term = "month" })
                TermCard(name = "季卡", price = priceOf("quarter"), days = daysOf("quarter"),
                    perDay = perDayOf("quarter"), recommended = false, selected = term == "quarter",
                    accent = curAccent, modifier = Modifier.weight(1f), onClick = { term = "quarter" })
                TermCard(name = "年卡", price = priceOf("year"), days = daysOf("year"),
                    perDay = perDayOf("year"), recommended = true, selected = term == "year",
                    accent = curAccent, modifier = Modifier.weight(1f), onClick = { term = "year" })
            }

            // 4. 套餐说明（紧凑小字）
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 20.dp).padding(top = 12.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Icon(Icons.Filled.CheckCircle, contentDescription = null, tint = curAccent, modifier = Modifier.size(10.dp))
                Spacer(Modifier.width(5.dp))
                Text("会员到期后自动恢复普通权益，可随时重新开通",
                    color = Color.White.copy(alpha = 0.45f), fontSize = 10.5.sp)
            }

            // 5. 支付说明
            Row(
                Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 20.dp)
                    .padding(vertical = 12.dp)
                    .background(Color.White.copy(alpha = 0.035f))
                    .padding(top = 6.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text("支付由易支付提供，开通即时到账",
                    color = Color.White.copy(alpha = 0.55f), fontSize = 11.5.sp)
                Spacer(Modifier.weight(1f))
                Text("官方认证", color = curAccent, fontSize = 10.5.sp, fontWeight = FontWeight.SemiBold)
            }

            // 6. 专属权益三宫格
            Column(Modifier.padding(horizontal = 20.dp).padding(top = 20.dp)) {
                Text(if (plan == "svip") "SVIP 专属权益" else "VIP 基础权益",
                    color = Color.White, fontSize = 16.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(12.dp))
                BenefitGrid(plan = plan, accent = curAccent)
            }

            // 7. 常用工具四宫格
            Column(Modifier.padding(horizontal = 20.dp).padding(top = 22.dp)) {
                Text("常用工具", color = Color.White, fontSize = 16.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(12.dp))
                ToolsGrid(
                    onExplain = {
                        tip = "· 同时开通 VIP 与 SVIP 时，仅展示 SVIP 标识并按 SVIP 权益生效\n" +
                                "· 会员到期后自动恢复普通用户权益\n· 支付由易支付提供，开通即时到账"
                    },
                    onMail = {
                        runCatching {
                            ctx.startActivity(Intent(Intent.ACTION_SENDTO, Uri.parse("mailto:yosar027@outlook.com")))
                        }
                    },
                    onWeb = {
                        runCatching {
                            ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://www.dbmt999.asia")))
                        }
                    },
                    onQQ = {
                        val cm = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                        cm.setPrimaryClip(ClipData.newPlainText("qq", "807910369"))
                        tip = "QQ 群号 807910369 已复制，打开 QQ 搜索加群"
                    }
                )
            }

            Spacer(Modifier.height(180.dp))
        }

        // 底部：协议勾选 + 动态价格大按钮
        Column(
            Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth()
                .background(
                    Brush.verticalGradient(
                        listOf(Color(0xFF0B0D10).copy(alpha = 0f), Color(0xFF0B0D10).copy(alpha = 0.98f))
                    )
                )
                .padding(horizontal = 20.dp)
                .padding(top = 10.dp, bottom = 8.dp)
                .navigationBarsPadding()
        ) {
            Row(
                Modifier.fillMaxWidth().clickable { agree = !agree },
                verticalAlignment = Alignment.CenterVertically
            ) {
                Icon(
                    if (agree) Icons.Filled.CheckBox else Icons.Filled.CheckBoxOutlineBlank,
                    contentDescription = null,
                    tint = if (agree) curAccent else Color.White.copy(alpha = 0.35f),
                    modifier = Modifier.size(14.dp)
                )
                Spacer(Modifier.width(6.dp))
                Text("已阅读并同意《会员服务协议》",
                    color = Color.White.copy(alpha = 0.5f), fontSize = 11.sp, maxLines = 1)
            }
            Spacer(Modifier.height(11.dp))
            // 档位色按钮：VIP 绿色渐变 / SVIP 金黄色渐变（顶部高光 + 底部光晕）
            Box(
                Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(18.dp))
                    .background(
                        Brush.verticalGradient(
                            if (plan == "svip") listOf(Color(0xFFB98A2E), Color(0xFFE3C25B))
                            else listOf(Color(0xFF0F9D58), Color(0xFF2ECC8F))
                        )
                    )
                    .border(
                        width = 0.7.dp,
                        brush = Brush.verticalGradient(listOf(Color.White.copy(alpha = 0.45f), Color.White.copy(alpha = 0.12f))),
                        shape = RoundedCornerShape(18.dp)
                    )
                    .shadow(
                        elevation = 14.dp,
                        ambientColor = (if (plan == "svip") Color(0xFFE3C25B) else Color(0xFF2ECC8F)).copy(alpha = 0.32f),
                        spotColor = (if (plan == "svip") Color(0xFFE3C25B) else Color(0xFF2ECC8F)).copy(alpha = 0.32f)
                    )
                    .clickable(enabled = !busy) { openPay() }
                    .padding(horizontal = 18.dp, vertical = 14.dp),
                contentAlignment = Alignment.CenterStart
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        if (busy) "正在下单…" else "$priceText 立即开通 ${if (plan == "svip") "SVIP" else "VIP"}",
                        color = Color.White, fontSize = 16.sp, fontWeight = FontWeight.Black, maxLines = 1
                    )
                    Spacer(Modifier.weight(1f))
                    if (!busy) {
                        Text(perDayText, color = Color.White.copy(alpha = 0.85f), fontSize = 11.sp,
                            fontWeight = FontWeight.SemiBold, maxLines = 1)
                    }
                }
            }
        }
    }

    tip?.let { msg ->
        androidx.compose.material3.AlertDialog(
            onDismissRequest = { tip = null },
            confirmButton = {
                androidx.compose.material3.TextButton(onClick = {
                    tip = null
                    scope.launch { AuthManager.refreshMemberStatus() }
                }) { Text("好的", color = Accent) }
            },
            title = { Text("提示", color = TextPrimary) },
            text = { Text(msg, color = TextSecondary) }
        )
    }
}

/// 档位分段控件（胶囊：选中项档位色渐变 + 白字 + 副标题）
@Composable
private fun PlanSegment(
    name: String, desc: String, colors: List<Color>,
    selected: Boolean, modifier: Modifier = Modifier, onClick: () -> Unit,
) {
    Column(
        modifier
            .clip(RoundedCornerShape(14.dp))
            .let {
                if (selected) it.background(
                    Brush.linearGradient(
                        listOf(colors[0].copy(alpha = 0.85f), colors[1].copy(alpha = 0.95f))
                    )
                ) else it.background(Color.White.copy(alpha = 0.04f))
            }
            .border(
                if (selected) 1.dp else 1.dp,
                if (selected) colors[1].copy(alpha = 0.55f) else Color.White.copy(alpha = 0.07f),
                RoundedCornerShape(14.dp)
            )
            .shadow(
                elevation = if (selected) 10.dp else 0.dp,
                ambientColor = colors[1].copy(alpha = 0.28f),
                spotColor = colors[1].copy(alpha = 0.28f)
            )
            .clickable { onClick() }
            .padding(vertical = 11.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text(name, color = if (selected) Color.White else Color.White.copy(alpha = 0.45f),
            fontSize = 20.sp, fontWeight = FontWeight.Black, maxLines = 1)
        Spacer(Modifier.height(3.dp))
        Text(desc, color = if (selected) Color.White.copy(alpha = 0.9f) else Color.White.copy(alpha = 0.35f),
            fontSize = 10.sp, maxLines = 1)
    }
}

/// 三档套餐卡（标签 + 大价格 + 天数 + 每日价胶囊）
@Composable
private fun TermCard(
    name: String, price: String, days: String, perDay: String,
    recommended: Boolean, selected: Boolean, accent: Color,
    modifier: Modifier = Modifier, onClick: () -> Unit,
) {
    Column(
        modifier
            .clip(RoundedCornerShape(14.dp))
            .let {
                if (selected) it.background(
                    Brush.linearGradient(
                        listOf(accent.copy(alpha = 0.28f), Color.White.copy(alpha = 0.05f))
                    )
                ) else it.background(Color.White.copy(alpha = 0.035f))
            }
            .let {
                if (selected) it.border(
                    1.6.dp,
                    Brush.linearGradient(
                        listOf(accent.copy(alpha = 0.9f), accent.copy(alpha = 0.25f))
                    ),
                    RoundedCornerShape(14.dp)
                ) else it.border(
                    1.dp,
                    Color.White.copy(alpha = 0.08f),
                    RoundedCornerShape(14.dp)
                )
            }
            .shadow(
                elevation = if (selected) 10.dp else 0.dp,
                ambientColor = accent.copy(alpha = 0.18f),
                spotColor = accent.copy(alpha = 0.18f)
            )
            .clickable { onClick() }
            .padding(vertical = 14.dp, horizontal = 6.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(name, color = Color.White.copy(alpha = 0.92f), fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
            if (recommended) {
                Spacer(Modifier.width(5.dp))
                Box(
                    Modifier
                        .clip(RoundedCornerShape(50))
                        .background(accent)
                        .padding(horizontal = 5.dp, vertical = 1.5.dp)
                ) {
                    Text("推荐", color = Color.White, fontSize = 8.5.sp, fontWeight = FontWeight.Bold)
                }
            }
        }
        Spacer(Modifier.height(9.dp))
        Text(price, color = Color.White, fontSize = 30.sp, fontWeight = FontWeight.Black, maxLines = 1)
        Spacer(Modifier.height(9.dp))
        Text(days, color = Color.White.copy(alpha = 0.4f), fontSize = 9.5.sp)
        Spacer(Modifier.height(9.dp))
        Text(perDay, color = if (selected) accent else accent.copy(alpha = 0.65f),
            fontSize = 9.sp, fontWeight = FontWeight.SemiBold, maxLines = 1)
    }
}

/// 权益三宫格（icon 圆底 + 标题 + 副标题）
@Composable
private fun BenefitGrid(plan: String, accent: Color) {
    val items = if (plan == "svip") {
        listOf(Triple<androidx.compose.ui.graphics.vector.ImageVector, String, String>(
            Icons.Filled.AutoAwesome, "免广告", "免开屏广告"),
            Triple(Icons.Filled.WorkspacePremium, "专属标识", "SVIP 金色徽章"),
            Triple(Icons.Filled.AutoAwesome, "精准推送", "个性化推荐"))
    } else {
        listOf(Triple(Icons.Filled.WorkspacePremium, "专属标识", "VIP 绿色徽章"),
            Triple(Icons.Filled.AutoAwesome, "精准推送", "个性化推荐"),
            Triple(Icons.Filled.PhoneAndroid, "双端畅听", "手机 / 电脑"))
    }
    Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        items.forEach { (icon, title, sub) ->
            Column(
                Modifier
                    .weight(1f)
                    .clip(RoundedCornerShape(12.dp))
                    .background(Color.White.copy(alpha = 0.035f))
                    .padding(vertical = 12.dp),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Box(
                    Modifier.size(38.dp).clip(CircleShape).background(accent.copy(alpha = 0.12f)),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(icon, contentDescription = null, tint = accent, modifier = Modifier.size(15.dp))
                }
                Spacer(Modifier.height(7.dp))
                Text(title, color = Color.White.copy(alpha = 0.92f), fontSize = 12.5.sp,
                    fontWeight = FontWeight.SemiBold, maxLines = 1)
                Spacer(Modifier.height(2.dp))
                Text(sub, color = Color.White.copy(alpha = 0.38f), fontSize = 9.5.sp, maxLines = 1)
            }
        }
    }
}

/// 常用工具四宫格（真实可用：说明 / 邮箱 / 官网 / QQ 群）
@Composable
private fun ToolsGrid(onExplain: () -> Unit, onMail: () -> Unit, onWeb: () -> Unit, onQQ: () -> Unit) {
    val items = listOf(
        Triple(Icons.Filled.Article, "会员说明", onExplain),
        Triple(Icons.Filled.Email, "客服邮箱", onMail),
        Triple(Icons.Filled.Public, "官方网站", onWeb),
        Triple(Icons.Filled.Groups, "QQ 群", onQQ),
    )
    Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        items.forEach { (icon, title, action) ->
            Column(
                Modifier
                    .weight(1f)
                    .clip(RoundedCornerShape(12.dp))
                    .background(Color.White.copy(alpha = 0.028f))
                    .clickable { action() }
                    .padding(vertical = 10.dp),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Box(
                    Modifier.size(38.dp).clip(CircleShape).background(Color.White.copy(alpha = 0.06f)),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(icon, contentDescription = null,
                        tint = Color.White.copy(alpha = 0.85f), modifier = Modifier.size(15.dp))
                }
                Spacer(Modifier.height(7.dp))
                Text(title, color = Color.White.copy(alpha = 0.85f), fontSize = 11.sp,
                    fontWeight = FontWeight.SemiBold, maxLines = 1)
            }
        }
    }
}
