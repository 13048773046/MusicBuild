package app.star2936.guava3291.ui.login

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.star2936.guava3291.data.Api
import app.star2936.guava3291.state.AuthManager
import app.star2936.guava3291.ui.theme.Accent
import app.star2936.guava3291.ui.theme.Background
import app.star2936.guava3291.ui.theme.Card
import app.star2936.guava3291.ui.theme.CardBorder
import app.star2936.guava3291.ui.theme.TextPrimary
import app.star2936.guava3291.ui.theme.TextSecondary
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout

// 邮箱验证码登录/注册（合一：输入邮箱+验证码，后端自动判断新用户注册/老用户登录）
@Composable
fun LoginScreen(onLoginSuccess: () -> Unit = {}, onOpenWebView: (String, String) -> Unit = { _, _ -> }) {
    var email by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var countdown by remember { mutableIntStateOf(0) }
    var sending by remember { mutableStateOf(false) }
    var agreed by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    val canSubmit = !busy && email.contains("@") && code.length == 6 && agreed

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Background)
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(24.dp)
                .padding(top = 48.dp),
        ) {
            // Logo + App 名
            Box(
                modifier = Modifier
                    .size(76.dp)
                    .clip(RoundedCornerShape(20.dp))
                    .background(Accent),
                contentAlignment = Alignment.Center
            ) {
                Text("♪", color = Color.White, fontSize = 40.sp, fontWeight = FontWeight.Bold)
            }
            Spacer(Modifier.height(12.dp))
            Text("流萤音乐", color = TextPrimary, fontSize = 24.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(4.dp))
            Text("用邮箱一键登录 / 注册", color = TextSecondary, fontSize = 14.sp)

            Spacer(Modifier.height(32.dp))

            // 邮箱输入框
            OutlinedTextField(
                value = email,
                onValueChange = { email = it; error = null },
                placeholder = { Text("请输入邮箱地址", color = TextSecondary, fontSize = 15.sp) },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
                shape = RoundedCornerShape(12.dp),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedContainerColor = Card,
                    unfocusedContainerColor = Card,
                    focusedTextColor = TextPrimary,
                    unfocusedTextColor = TextPrimary,
                    cursorColor = Accent,
                    focusedBorderColor = Accent,
                    unfocusedBorderColor = CardBorder,
                )
            )

            Spacer(Modifier.height(12.dp))

            // 验证码输入框 + 发送按钮
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically
            ) {
                OutlinedTextField(
                    value = code,
                    onValueChange = { if (it.length <= 6) { code = it; error = null } },
                    placeholder = { Text("验证码", color = TextSecondary, fontSize = 15.sp) },
                    modifier = Modifier.weight(1f),
                    singleLine = true,
                    shape = RoundedCornerShape(12.dp),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedContainerColor = Card,
                        unfocusedContainerColor = Card,
                        focusedTextColor = TextPrimary,
                        unfocusedTextColor = TextPrimary,
                        cursorColor = Accent,
                        focusedBorderColor = Accent,
                        unfocusedBorderColor = CardBorder,
                    )
                )
                Spacer(Modifier.width(10.dp))
                // 发送验证码按钮
                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(12.dp))
                        .background(if (countdown > 0) Card else Accent)
                        .clickable(enabled = countdown == 0 && !sending && email.contains("@")) {
                            sending = true
                            scope.launch {
                                try {
                                    // 20s 硬超时：任何异常网络都不会让按钮卡死 loading
                                    withTimeout(20_000) {
                                        withContext(Dispatchers.IO) { Api.sendEmailCode(email.trim().lowercase()) }
                                    }
                                    countdown = 60
                                    // 倒计时
                                    while (countdown > 0) {
                                        delay(1000)
                                        countdown--
                                    }
                                } catch (e: TimeoutCancellationException) {
                                    error = "发送超时，请检查网络后重试"
                                } catch (e: Exception) {
                                    error = e.message ?: "发送失败，请重试"
                                }
                                sending = false
                            }
                        }
                        .padding(horizontal = 16.dp, vertical = 14.dp),
                    contentAlignment = Alignment.Center
                ) {
                    if (sending) {
                        CircularProgressIndicator(modifier = Modifier.size(16.dp), color = Color.White, strokeWidth = 2.dp)
                    } else {
                        Text(
                            if (countdown > 0) "${countdown}s" else "发送验证码",
                            color = if (countdown > 0) TextSecondary else Color.White,
                            fontSize = 14.sp,
                            fontWeight = FontWeight.Medium
                        )
                    }
                }
            }

            Spacer(Modifier.height(16.dp))

            // 协议勾选（验证码栏下方、登录按钮上方）
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.padding(horizontal = 8.dp)
            ) {
                // 圆形勾选框
                Box(
                    modifier = Modifier
                        .size(18.dp)
                        .clip(CircleShape)
                        .background(if (agreed) Accent else Color.Transparent)
                        .border(1.5.dp, if (agreed) Accent else TextSecondary, CircleShape)
                        .clickable { agreed = !agreed },
                    contentAlignment = Alignment.Center
                ) {
                    if (agreed) {
                        Text("✓", color = Color.White, fontSize = 12.sp, fontWeight = FontWeight.Bold)
                    }
                }
                Spacer(Modifier.width(8.dp))
                // 协议文字（一行：「我已阅读并同意《用户协议》《隐私政策》」协议可点击）
                Row(modifier = Modifier.weight(1f)) {
                    Text(
                        "我已阅读并同意",
                        color = TextSecondary,
                        fontSize = 12.sp
                    )
                    Text(
                        "《用户协议》",
                        color = Accent,
                        fontSize = 12.sp,
                        modifier = Modifier.clickable {
                            onOpenWebView("https://www.dbmt999.asia/user-agreement.html", "用户协议")
                        }
                    )
                    Text(
                        "《隐私政策》",
                        color = Accent,
                        fontSize = 12.sp,
                        modifier = Modifier.clickable {
                            onOpenWebView("https://www.dbmt999.asia/privacy-policy.html", "隐私政策")
                        }
                    )
                }
            }

            Spacer(Modifier.height(20.dp))

            // 登录/注册按钮
            Button(
                onClick = {
                    busy = true
                    error = null
                    scope.launch {
                        try {
                            withContext(Dispatchers.IO) {
                                AuthManager.loginEmail(email.trim().lowercase(), code)
                            }
                        } catch (e: Exception) {
                            error = e.message ?: "登录失败"
                        }
                        busy = false
                        if (error == null) onLoginSuccess()
                    }
                },
                enabled = canSubmit,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(50.dp),
                shape = RoundedCornerShape(14.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = Accent,
                    disabledContainerColor = Accent.copy(alpha = 0.4f),
                    contentColor = Color.White,
                    disabledContentColor = Color.White.copy(alpha = 0.7f)
                ),
            ) {
                if (busy) {
                    CircularProgressIndicator(modifier = Modifier.size(18.dp), color = Color.White, strokeWidth = 2.dp)
                } else {
                    Text("登录 / 注册", fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
                }
            }

            Spacer(Modifier.height(16.dp))

            // 提示
            Text(
                "未注册的邮箱将自动创建账号",
                color = TextSecondary,
                fontSize = 12.sp,
                modifier = Modifier.align(Alignment.CenterHorizontally)
            )

            Spacer(Modifier.height(12.dp))

            error?.let {
                Text(it, color = Color(0xFFFF6B6B), fontSize = 13.sp)
            }
        }
    }
}
