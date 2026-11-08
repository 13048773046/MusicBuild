package app.star2936.guava3291.ui.settings

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.outlined.Visibility
import androidx.compose.material.icons.outlined.VisibilityOff
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.star2936.guava3291.ui.theme.*
import kotlinx.coroutines.launch

@Composable
fun ChangePasswordScreenCompat(onBack: () -> Unit) {
    val statusBarTop = WindowInsets.statusBars.asPaddingValues().calculateTopPadding()
    var oldPwd by remember { mutableStateOf("") }
    var newPwd by remember { mutableStateOf("") }
    var confirmPwd by remember { mutableStateOf("") }
    var oldVisible by remember { mutableStateOf(false) }
    var newVisible by remember { mutableStateOf(false) }
    var confirmVisible by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var success by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    val scope = androidx.compose.runtime.rememberCoroutineScope()

    Box(Modifier.fillMaxSize().background(Background)) {
        Column(Modifier.fillMaxSize().padding(top = statusBarTop)) {
            // 顶部导航栏
            Row(
                Modifier.padding(horizontal = 20.dp).padding(top = 12.dp, bottom = 8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Box(
                    Modifier.size(40.dp).clip(CircleShape).background(Card).clickable { onBack() },
                    contentAlignment = Alignment.Center
                ) { Icon(Icons.Filled.ArrowBack, null, tint = TextPrimary, modifier = Modifier.size(20.dp)) }
                Spacer(Modifier.width(14.dp))
                Column(Modifier.weight(1f)) {
                    Text("修改密码", color = TextPrimary, fontSize = 26.sp, fontWeight = FontWeight.Bold)
                    Text("设置新的登录密码", color = TextSecondary, fontSize = 13.sp)
                }
                Text(
                    if (busy) "提交中…" else "确认",
                    color = Accent, fontSize = 16.sp, fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.clickable(enabled = !busy) {
                        if (oldPwd.isEmpty()) { error = "请输入当前密码"; success = null; return@clickable }
                        if (newPwd.length !in 6..64) { error = "新密码长度需 6-64 位"; success = null; return@clickable }
                        if (newPwd != confirmPwd) { error = "两次输入的新密码不一致"; success = null; return@clickable }
                        if (newPwd == oldPwd) { error = "新密码不能与旧密码相同"; success = null; return@clickable }
                        error = null; success = null; busy = true
                        scope.launch {
                            try {
                                app.star2936.guava3291.state.AuthManager.changePassword(oldPwd, newPwd)
                                success = "密码修改成功"
                                oldPwd = ""; newPwd = ""; confirmPwd = ""
                                kotlinx.coroutines.delay(1200)
                                onBack()
                            }
                            catch (e: Exception) { error = e.message; busy = false }
                        }
                    }
                )
            }

            Column(
                Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 20.dp)
                    .navigationBarsPadding()
            ) {
                Spacer(Modifier.height(16.dp))

                // 密码输入卡片
                Column(
                    Modifier.clip(RoundedCornerShape(20.dp)).background(Card)
                ) {
                    PwdField("当前密码", oldPwd, oldVisible, { oldPwd = it }, { oldVisible = !oldVisible })
                    DividerLine()
                    PwdField("新密码（6-64 位）", newPwd, newVisible, { newPwd = it }, { newVisible = !newVisible })
                    DividerLine()
                    PwdField("确认新密码", confirmPwd, confirmVisible, { confirmPwd = it }, { confirmVisible = !confirmVisible })
                }

                // 错误提示
                error?.let {
                    Spacer(Modifier.height(14.dp))
                    Box(
                        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp))
                            .background(DangerRed.copy(alpha = 0.12f))
                            .padding(horizontal = 14.dp, vertical = 10.dp)
                    ) {
                        Text(it, color = DangerRed, fontSize = 13.sp)
                    }
                }

                // 成功提示
                success?.let {
                    Spacer(Modifier.height(14.dp))
                    Box(
                        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp))
                            .background(Accent.copy(alpha = 0.12f))
                            .padding(horizontal = 14.dp, vertical = 10.dp)
                    ) {
                        Text(it, color = Accent, fontSize = 13.sp, fontWeight = FontWeight.Medium)
                    }
                }

                Spacer(Modifier.height(24.dp))

                // 安全提示
                Text(
                    "建议使用字母、数字和符号的组合，提高账号安全性。",
                    color = TextTertiary, fontSize = 12.sp, lineHeight = 18.sp
                )

                Spacer(Modifier.height(40.dp))
            }
        }
    }
}

@Composable
private fun PwdField(
    hint: String,
    value: String,
    visible: Boolean,
    onChange: (String) -> Unit,
    onToggleVisibility: () -> Unit
) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 16.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        BasicTextField(
            value = value,
            onValueChange = onChange,
            textStyle = androidx.compose.ui.text.TextStyle(color = TextPrimary, fontSize = 15.sp),
            visualTransformation = if (visible) VisualTransformation.None else PasswordVisualTransformation(),
            modifier = Modifier.weight(1f),
            decorationBox = { inner ->
                if (value.isEmpty()) Text(hint, color = TextTertiary, fontSize = 15.sp)
                inner()
            }
        )
        Icon(
            if (visible) Icons.Outlined.Visibility else Icons.Outlined.VisibilityOff,
            contentDescription = if (visible) "隐藏密码" else "显示密码",
            tint = TextTertiary,
            modifier = Modifier
                .size(20.dp)
                .clickable { onToggleVisibility() }
        )
    }
}

@Composable
private fun DividerLine() {
    Box(Modifier.padding(start = 16.dp).fillMaxWidth().height(0.5.dp).background(CardBorder))
}
