package app.star2936.guava3291.ui.settings

import androidx.compose.animation.core.animateDpAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.ScrollState
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
import kotlinx.coroutines.launch
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.star2936.guava3291.ui.theme.*
import app.star2936.guava3291.ui.components.MemberBadge
import app.star2936.guava3291.BuildConfig
import app.star2936.guava3291.data.Api
import app.star2936.guava3291.data.UpdateCheck
import android.widget.Toast
import android.content.Context
import coil.Coil
import android.content.Intent
import android.net.Uri
import androidx.compose.material3.AlertDialog
import androidx.compose.ui.platform.LocalContext

@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreenCompat(
    settingsScrollState: ScrollState,
    onBack: () -> Unit,
    onOpenProfileEdit: () -> Unit,
    onOpenChangePassword: () -> Unit,
    onOpenAbout: () -> Unit,
    onOpenLicenses: () -> Unit,
    onOpenLogin: () -> Unit = {},
    onOpenWebView: (String, String) -> Unit = { _, _ -> },
) {
    val statusBarTop = WindowInsets.statusBars.asPaddingValues().calculateTopPadding()
    var showSwitchDialog by remember { mutableStateOf(false) }
    var showLogoutDialog by remember { mutableStateOf(false) }
    var showLoginSheet by remember { mutableStateOf(false) }
    var showVerifyDialog by remember { mutableStateOf(false) }
    var verifyTip by remember { mutableStateOf<String?>(null) }
    var showMemberCenter by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current
    var updateResult by remember { mutableStateOf<UpdateCheck?>(null) }
    var showUpdateDialog by remember { mutableStateOf(false) }

    Box(Modifier.fillMaxSize().background(Background)) {
        Column(
            Modifier
                .fillMaxSize()
                .verticalScroll(settingsScrollState)
                .padding(top = statusBarTop)
                // 全屏子页面（已盖住底部 TabBar）：底部补系统手势区，退出登录不被手势条压住
                .navigationBarsPadding()
        ) {
            // Header
            Row(
                Modifier.padding(horizontal = 20.dp, vertical = 14.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Box(
                    Modifier
                        .size(40.dp)
                        .clip(CircleShape)
                        .background(Card)
                        .clickable { onBack() },
                    contentAlignment = Alignment.Center
                ) {
                    Icon(Icons.Filled.ArrowBack, contentDescription = null, tint = TextPrimary, modifier = Modifier.size(20.dp))
                }
                Spacer(Modifier.width(14.dp))
                Column {
                    Text("设置", color = TextPrimary, fontSize = 26.sp, fontWeight = FontWeight.Bold)
                    Text("账号、通用与关于", color = TextSecondary, fontSize = 13.sp)
                }
            }

            Spacer(Modifier.height(12.dp))

            // 账号
            GroupTitle("账号")
            SectionCard {
                NavRow(Icons.Filled.AccountCircle, Accent, "修改用户信息", onClick = onOpenProfileEdit)
                DividerLine()
                NavRow(Icons.Filled.Lock, Color(0xFF5AC8FA), "修改密码", onClick = onOpenChangePassword)
            }

            Spacer(Modifier.height(24.dp))

            // 会员与认证（2026-10-19 会员体系）
            GroupTitle("会员与认证")
            SectionCard {
                Row(
                    Modifier
                        .fillMaxWidth()
                        .clickable { showMemberCenter = true }
                        .padding(horizontal = 16.dp, vertical = 13.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Box(
                        Modifier.size(28.dp).clip(RoundedCornerShape(8.dp)).background(Color(0xFFD4AF37).copy(alpha = 0.14f)),
                        contentAlignment = Alignment.Center
                    ) { Icon(Icons.Filled.Star, contentDescription = null, tint = Color(0xFFD4AF37), modifier = Modifier.size(16.dp)) }
                    Spacer(Modifier.width(12.dp))
                    val u = app.star2936.guava3291.state.AuthManager.currentUser.value
                    Text("我的会员", color = TextPrimary, fontSize = 15.sp)
                    Spacer(Modifier.weight(1f))
                    MemberBadge(u?.member)
                    Spacer(Modifier.width(6.dp))
                    val exp = maxOf(u?.memberExpire ?: 0.0, u?.svipExpire ?: 0.0)
                    Text(
                        if (exp > 0) "有效期至 " + java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.CHINA)
                            .format(java.util.Date((exp * 1000).toLong())) else "未开通",
                        color = TextTertiary, fontSize = 12.sp
                    )
                }
                DividerLine()
                Row(
                    Modifier
                        .fillMaxWidth()
                        .clickable {
                            // 已认证用户点击只提示"你已认证通过"，不打开申请弹窗（对齐 iOS）
                            val uu = app.star2936.guava3291.state.AuthManager.currentUser.value
                            if (!uu?.verified.isNullOrEmpty()) {
                                verifyTip = "你已认证通过，无需重复申请"
                            } else {
                                showVerifyDialog = true
                            }
                        }
                        .padding(horizontal = 16.dp, vertical = 13.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Box(
                        Modifier.size(28.dp).clip(RoundedCornerShape(8.dp)).background(Accent.copy(alpha = 0.14f)),
                        contentAlignment = Alignment.Center
                    ) { Icon(Icons.Filled.VerifiedUser, contentDescription = null, tint = Accent, modifier = Modifier.size(16.dp)) }
                    Spacer(Modifier.width(12.dp))
                    Text("认证申请", color = TextPrimary, fontSize = 15.sp)
                    Spacer(Modifier.weight(1f))
                    val u2 = app.star2936.guava3291.state.AuthManager.currentUser.value
                    if (!u2?.verified.isNullOrEmpty()) {
                        Text("已认证", color = Accent, fontSize = 12.sp, fontWeight = FontWeight.Medium)
                    } else {
                        Text("未认证", color = TextTertiary, fontSize = 12.sp)
                    }
                    Spacer(Modifier.width(4.dp))
                    Text("›", color = TextTertiary.copy(alpha = 0.7f), fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                }
            }

            Spacer(Modifier.height(24.dp))

            // 通用
            GroupTitle("通用")
            SectionCard {
                // 清理缓存：实际清理封面主色内存缓存 + Coil 磁盘/内存缓存（对齐 iOS clearCache）
                ActionRow(Icons.Filled.Delete, Color(0xFFFF9500), "清理缓存", value = null, showChevron = false, onClick = {
                    scope.launch {
                        app.star2936.guava3291.util.ColorPalette.clear()
                        app.star2936.guava3291.state.CoverCache.map.clear()
                        runCatching {
                            Coil.imageLoader(ctx).memoryCache?.clear()
                            Coil.imageLoader(ctx).diskCache?.clear()
                        }
                        Toast.makeText(ctx, "缓存已清理", Toast.LENGTH_SHORT).show()
                    }
                })
                DividerLine()
                ActionRow(Icons.Filled.Refresh, Lilac, "检查更新", value = "v${BuildConfig.VERSION_NAME}", showChevron = true, onClick = {
                    scope.launch {
                        val upd = try { Api.checkUpdate() } catch (_: Exception) { null }
                        if (upd?.hasUpdate == true) {
                            updateResult = upd
                            showUpdateDialog = true
                        } else {
                            Toast.makeText(ctx, "已是最新版本", Toast.LENGTH_SHORT).show()
                        }
                    }
                })
            }

            Spacer(Modifier.height(24.dp))

            // 可视化效果
            GroupTitle("可视化效果")
            SectionCard {
                SwitchRow(
                    icon = Icons.Filled.BarChart,
                    tint = Sky,
                    title = "波形进度条",
                    subtitle = "用波形样式替换普通进度条",
                    checked = app.star2936.guava3291.state.VisualizationSettings.waveformBarEnabled.collectAsState().value,
                    onCheckedChange = { app.star2936.guava3291.state.VisualizationSettings.setWaveformBar(it) }
                )
                DividerLine()
                SwitchRow(
                    icon = Icons.Filled.GraphicEq,
                    tint = Lilac,
                    title = "音频频谱背景",
                    subtitle = "播放页显示动态频谱波动效果",
                    checked = app.star2936.guava3291.state.VisualizationSettings.audioSpectrumEnabled.collectAsState().value,
                    onCheckedChange = { app.star2936.guava3291.state.VisualizationSettings.setAudioSpectrum(it) }
                )
            }

            Spacer(Modifier.height(24.dp))

            // 关于
            GroupTitle("关于")
            SectionCard {
                NavRow(Icons.Filled.Info, Lilac, "关于 APP", onClick = onOpenAbout)
                DividerLine()
                // 用户协议
                NavRow(Icons.Filled.Description, Color(0xFF6C8EFF), "用户协议", onClick = {
                    onOpenWebView("https://www.dbmt999.asia/user-agreement.html", "用户协议")
                })
                DividerLine()
                // 隐私政策
                NavRow(Icons.Filled.PrivacyTip, Color(0xFFFF6B6B), "隐私政策", onClick = {
                    onOpenWebView("https://www.dbmt999.asia/privacy-policy.html", "隐私政策")
                })
                DividerLine()
                // 个人信息收集清单（《个人信息保护法》合规展示）
                NavRow(Icons.Filled.List, Color(0xFF59C77B), "个人信息收集清单", onClick = {
                    onOpenWebView("https://www.dbmt999.asia/personal-info.html", "个人信息收集清单")
                })
                DividerLine()
                // 应用权限清单（告知 APP 申请的系统权限与用途）
                NavRow(Icons.Filled.Security, Color(0xFFFFA000), "应用权限清单", onClick = {
                    onOpenWebView("https://www.dbmt999.asia/app-permission.html", "应用权限清单")
                })
                DividerLine()
                // 开源许可（对齐 iOS SettingsView.OpenSourceLicensesView 合规展示）
                NavRow(Icons.Filled.Code, Color(0xFF6C8EFF), "开源许可", onClick = onOpenLicenses)
            }

            Spacer(Modifier.height(24.dp))

            // 登录
            GroupTitle("登录")
            SectionCard {
                ActionRow(Icons.Filled.SwapHoriz, Accent, "切换账号", showChevron = false, onClick = { showSwitchDialog = true })
                DividerLine()
                ActionRow(Icons.Filled.Logout, Color(0xFFFF6B6B), "退出登录", showChevron = false, onClick = { showLogoutDialog = true })
            }

            Spacer(Modifier.height(40.dp))
        }
    }

    // 切换账号底部弹窗
    if (showSwitchDialog) {
        androidx.compose.material3.ModalBottomSheet(
            onDismissRequest = { showSwitchDialog = false },
            containerColor = Background,
            shape = RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp)
        ) {
            val user = app.star2936.guava3291.state.AuthManager.currentUser.value
            Column(Modifier.padding(bottom = 20.dp)) {
                // 头部
                Row(Modifier.padding(horizontal = 20.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("切换账号", color = TextPrimary, fontSize = 24.sp, fontWeight = FontWeight.Bold)
                        Text("选择本地保存的账号快速登录", color = TextSecondary, fontSize = 12.sp)
                    }
                    Box(
                        Modifier.size(34.dp).clip(CircleShape).background(Card).clickable { showSwitchDialog = false },
                        contentAlignment = Alignment.Center
                    ) { Text("✕", color = TextPrimary, fontSize = 16.sp) }
                }
                Spacer(Modifier.height(8.dp))

                // 账号列表
                val savedList = app.star2936.guava3291.state.AuthManager.savedAccounts.collectAsState().value
                val currentId = app.star2936.guava3291.state.AuthManager.currentUser.value?.id
                Column(Modifier.padding(horizontal = 20.dp).clip(RoundedCornerShape(18.dp)).background(Card)) {
                    savedList.forEachIndexed { idx, u ->
                        Row(
                            Modifier.fillMaxWidth().clickable {
                                showSwitchDialog = false
                                if (u.id != currentId) {
                                    app.star2936.guava3291.state.AuthManager.switchTo(u)
                                }
                            }.padding(horizontal = 16.dp, vertical = 14.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            coil.compose.AsyncImage(
                                model = u.avatarURL,
                                contentDescription = null,
                                modifier = Modifier.size(48.dp).clip(CircleShape),
                                contentScale = androidx.compose.ui.layout.ContentScale.Crop
                            )
                            Spacer(Modifier.width(14.dp))
                            Column {
                                Text(u.name, color = TextPrimary, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                                Text("账号 ${u.account}", color = TextSecondary, fontSize = 12.sp)
                            }
                            Spacer(Modifier.weight(1f))
                            Box(
                                Modifier.size(22.dp).clip(CircleShape)
                                    .background(if (u.id == currentId) Accent else Color.Transparent)
                                    .then(
                                        if (u.id != currentId) Modifier.border(1.5.dp, TextTertiary.copy(alpha = 0.5f), CircleShape) else Modifier
                                    ),
                                contentAlignment = Alignment.Center
                            ) {
                                if (u.id == currentId) Text("✓", color = Color.White, fontSize = 12.sp, fontWeight = FontWeight.Bold)
                            }
                        }
                        if (idx < savedList.size - 1) {
                            Box(Modifier.padding(start = 78.dp).fillMaxWidth().height(0.5.dp).background(CardBorder))
                        }
                    }

                    // 添加账号
                    Row(
                        Modifier.fillMaxWidth().clickable {
                            showSwitchDialog = false
                            onOpenLogin()
                        }.padding(horizontal = 16.dp, vertical = 14.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        // 虚线圆圈+加号
                        Box(
                            Modifier.size(48.dp).clip(CircleShape).background(AccentSoft),
                            contentAlignment = Alignment.Center
                        ) { Text("+", color = Accent, fontSize = 22.sp, fontWeight = FontWeight.SemiBold) }
                        Spacer(Modifier.width(14.dp))
                        Column {
                            Text("添加账号", color = TextPrimary, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                            Text("登录新的账号", color = TextSecondary, fontSize = 12.sp)
                        }
                    }
                }
            }
        }
    }

    // 认证申请拦截提示（已认证用户点击申请）
    verifyTip?.let { msg ->
        androidx.compose.material3.AlertDialog(
            onDismissRequest = { verifyTip = null },
            confirmButton = {
                androidx.compose.material3.TextButton(onClick = { verifyTip = null }) { Text("好的", color = Accent) }
            },
            title = { Text("提示", color = TextPrimary) },
            text = { Text(msg, color = TextSecondary) }
        )
    }

    // 退出登录弹窗
    if (showLogoutDialog) {
        androidx.compose.material3.AlertDialog(
            onDismissRequest = { showLogoutDialog = false },
            title = { Text("退出登录", color = TextPrimary, fontWeight = FontWeight.Bold) },
            text = { Text("确定要退出当前账号吗？", color = TextSecondary, fontSize = 14.sp) },
            confirmButton = {
                Text("退出", color = Color(0xFFFF6B6B), fontWeight = FontWeight.SemiBold, modifier = Modifier.clickable {
                    showLogoutDialog = false
                    app.star2936.guava3291.state.AuthManager.logout()
                })
            },
            dismissButton = {
                Text("取消", color = TextTertiary, modifier = Modifier.clickable { showLogoutDialog = false })
            }
        )
    }

    // 认证申请弹窗（2026-10-19 会员体系）
    if (showVerifyDialog) {
        VerifyApplyDialog(onDismiss = { showVerifyDialog = false })
    }

    // 会员中心（选档开通，易支付）
    if (showMemberCenter) {
        MemberCenterScreen(onBack = { showMemberCenter = false })
    }

    // 检查更新弹窗：有新版显示更新内容，无新版提示已是最新；
    // forceUpdate=1 时为强制更新（不可关闭、无取消按钮，与 iOS UpdateSheet 对齐）
    if (showUpdateDialog) {
        val upd = updateResult
        val forced = upd?.forceUpdate == 1
        AlertDialog(
            onDismissRequest = { if (!forced) showUpdateDialog = false },
            title = { Text("发现新版本 ${upd?.version ?: ""}", color = TextPrimary, fontWeight = FontWeight.Bold) },
            text = { Text(upd?.content ?: "", color = TextSecondary, fontSize = 14.sp) },
            confirmButton = {
                Text(if (forced) "立即更新" else "去更新", color = Accent, fontWeight = FontWeight.SemiBold, modifier = Modifier.clickable {
                    showUpdateDialog = false
                    val url = upd?.downloadUrl
                    if (!url.isNullOrEmpty()) {
                        try { ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) } catch (_: Exception) { }
                    }
                })
            },
            dismissButton = {
                if (!forced) {
                    Text("以后再说", color = TextTertiary, modifier = Modifier.clickable { showUpdateDialog = false })
                }
            }
        )
    }

}

@Composable
private fun GroupTitle(title: String) {
    Text(title, color = TextTertiary, fontSize = 12.sp, fontWeight = FontWeight.Medium, modifier = Modifier.padding(start = 24.dp, bottom = 8.dp))
}

@Composable
private fun SectionCard(content: @Composable ColumnScope.() -> Unit) {
    Column(
        Modifier
            .padding(horizontal = 20.dp)
            .clip(RoundedCornerShape(20.dp))
            .background(Card)
    ) {
        content()
    }
}

@Composable
private fun DividerLine() {
    Box(
        Modifier
            .padding(start = 56.dp)
            .fillMaxWidth()
            .height(0.5.dp)
            .background(CardBorder)
    )
}

@Composable
private fun RowScopeBase(icon: androidx.compose.ui.graphics.vector.ImageVector, tint: Color, title: String, value: String?, showChevron: Boolean, onClick: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .clickable { onClick() }
            .padding(horizontal = 16.dp, vertical = 13.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(
            Modifier
                .size(28.dp)
                .clip(RoundedCornerShape(8.dp))
                .background(tint.copy(alpha = 0.14f)),
            contentAlignment = Alignment.Center
        ) {
            Icon(icon, contentDescription = null, tint = tint, modifier = Modifier.size(16.dp))
        }
        Spacer(Modifier.width(12.dp))
        Text(title, color = TextPrimary, fontSize = 15.sp)
        Spacer(Modifier.weight(1f))
        value?.let { Text(it, color = TextTertiary, fontSize = 13.sp) }
        if (showChevron) {
            Spacer(Modifier.width(4.dp))
            Text("›", color = TextTertiary.copy(alpha = 0.7f), fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
        }
    }
}

@Composable
private fun ActionRow(icon: androidx.compose.ui.graphics.vector.ImageVector, tint: Color, title: String, value: String? = null, showChevron: Boolean = true, onClick: () -> Unit) {
    RowScopeBase(icon, tint, title, value, showChevron, onClick)
}

@Composable
private fun NavRow(icon: androidx.compose.ui.graphics.vector.ImageVector, tint: Color, title: String, onClick: () -> Unit) {
    RowScopeBase(icon, tint, title, null, true, onClick)
}

@Composable
private fun SwitchRow(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    tint: Color,
    title: String,
    subtitle: String? = null,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
) {
    Row(
        Modifier
            .fillMaxWidth()
            .clickable { onCheckedChange(!checked) }
            .padding(horizontal = 16.dp, vertical = 13.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(
            Modifier
                .size(28.dp)
                .clip(RoundedCornerShape(8.dp))
                .background(tint.copy(alpha = 0.14f)),
            contentAlignment = Alignment.Center
        ) {
            Icon(icon, contentDescription = null, tint = tint, modifier = Modifier.size(16.dp))
        }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(title, color = TextPrimary, fontSize = 15.sp)
            subtitle?.let {
                Text(it, color = TextTertiary, fontSize = 12.sp, modifier = Modifier.padding(top = 2.dp))
            }
        }
        Spacer(Modifier.width(12.dp))
        // 自定义开关（修复：滑块位置随状态左右移动，关=左、开=右，带过渡动画）
        val thumbTravel = 22.dp   // 行程 = 轨道 44 - 滑块 22
        val thumbOffset by animateDpAsState(if (checked) thumbTravel else 0.dp, label = "switchThumb")
        Box(
            Modifier
                .size(width = 44.dp, height = 26.dp)
                .clip(RoundedCornerShape(13.dp))
                .background(if (checked) Accent else CardBorder)
                .clickable { onCheckedChange(!checked) },
            contentAlignment = Alignment.CenterStart
        ) {
            Box(
                Modifier
                    .offset(x = thumbOffset)
                    .size(22.dp)
                    .padding(horizontal = 2.dp)
                    .clip(CircleShape)
                    .background(Color.White)
            )
        }
    }
}
