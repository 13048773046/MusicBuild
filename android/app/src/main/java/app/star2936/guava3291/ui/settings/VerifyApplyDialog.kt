package app.star2936.guava3291.ui.settings

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.star2936.guava3291.data.Api
import app.star2936.guava3291.ui.components.TitleCapsule
import app.star2936.guava3291.ui.theme.*
import kotlinx.coroutines.launch

/** 认证申请弹窗（2026-10-19 会员体系，对齐 iOS VerifyApplySheet） */
@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
fun VerifyApplyDialog(onDismiss: () -> Unit) {
    val scope = rememberCoroutineScope()
    var categories by remember { mutableStateOf<List<Api.VerifyCategory>>(emptyList()) }
    var level1 by remember { mutableStateOf("") }
    var level2 by remember { mutableStateOf("") }
    var label by remember { mutableStateOf("") }
    var reason by remember { mutableStateOf("") }
    var submitting by remember { mutableStateOf(false) }
    var errorText by remember { mutableStateOf<String?>(null) }
    var success by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        categories = Api.fetchVerifyCategories()
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        containerColor = Background,
        shape = RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp)
    ) {
        Column(
            Modifier
                .padding(horizontal = 20.dp)
                .verticalScroll(rememberScrollState())
                .padding(bottom = 28.dp)
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("认证申请", color = TextPrimary, fontSize = 24.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.weight(1f))
                Box(
                    Modifier.size(34.dp).clip(RoundedCornerShape(17.dp)).background(Card).clickable { onDismiss() },
                    contentAlignment = Alignment.Center
                ) { Text("✕", color = TextPrimary, fontSize = 16.sp) }
            }
            Spacer(Modifier.height(6.dp))
            Text(
                "认证通过后，你的评论、主页、我的页面将显示官方蓝 V 认证标识与自定义认证标签。请如实填写，管理员审核通过后生效。",
                color = TextTertiary, fontSize = 12.sp, lineHeight = 18.sp
            )

            Spacer(Modifier.height(16.dp))
            Text("认证类别（第一级）", color = TextSecondary, fontSize = 13.sp, fontWeight = FontWeight.Medium)
            Spacer(Modifier.height(8.dp))
            val l1s = categories.map { it.level1 }.toSet().sorted()
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                l1s.forEach { l ->
                    Text(
                        l, color = if (level1 == l) Color.White else TextSecondary,
                        fontSize = 13.sp, fontWeight = if (level1 == l) FontWeight.SemiBold else FontWeight.Normal,
                        modifier = Modifier
                            .clip(RoundedCornerShape(50))
                            .background(if (level1 == l) Accent else Card)
                            .clickable { level1 = l; level2 = "" }
                            .padding(horizontal = 12.dp, vertical = 7.dp)
                    )
                }
            }

            if (level1.isNotEmpty()) {
                Spacer(Modifier.height(12.dp))
                Text("认证类别（第二级，可选）", color = TextSecondary, fontSize = 13.sp, fontWeight = FontWeight.Medium)
                Spacer(Modifier.height(8.dp))
                val l2s = categories.filter { it.level1 == level1 }.map { it.level2 }.filter { it.isNotEmpty() }.distinct()
                if (l2s.isNotEmpty()) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        l2s.forEach { l ->
                            Text(
                                l, color = if (level2 == l) Color.White else TextSecondary,
                                fontSize = 13.sp, fontWeight = if (level2 == l) FontWeight.SemiBold else FontWeight.Normal,
                                modifier = Modifier
                                    .clip(RoundedCornerShape(50))
                                    .background(if (level2 == l) Accent else Card)
                                    .clickable { level2 = l }
                                    .padding(horizontal = 12.dp, vertical = 7.dp)
                            )
                        }
                    }
                }
            }

            Spacer(Modifier.height(16.dp))
            Text("认证标签（如「XX 官方账户」「XX 工作室」，选填）", color = TextSecondary, fontSize = 13.sp, fontWeight = FontWeight.Medium)
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = label, onValueChange = { label = it },
                placeholder = { Text("例如：流萤音乐官方账户", color = TextTertiary, fontSize = 13.sp) },
                singleLine = true,
                textStyle = androidx.compose.ui.text.TextStyle(color = TextPrimary, fontSize = 14.sp),
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(12.dp),
                colors = androidx.compose.material3.OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = Accent, unfocusedBorderColor = CardBorder,
                    focusedContainerColor = Card, unfocusedContainerColor = Card, cursorColor = Accent
                )
            )

            Spacer(Modifier.height(16.dp))
            Text("申请理由（必填）", color = TextSecondary, fontSize = 13.sp, fontWeight = FontWeight.Medium)
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = reason, onValueChange = { reason = it },
                placeholder = { Text("说明你的身份与认证用途", color = TextTertiary, fontSize = 13.sp) },
                minLines = 4, maxLines = 6,
                textStyle = androidx.compose.ui.text.TextStyle(color = TextPrimary, fontSize = 14.sp),
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(12.dp),
                colors = androidx.compose.material3.OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = Accent, unfocusedBorderColor = CardBorder,
                    focusedContainerColor = Card, unfocusedContainerColor = Card, cursorColor = Accent
                )
            )

            errorText?.let {
                Spacer(Modifier.height(10.dp))
                Text(it, color = Color(0xFFFF6B6B), fontSize = 12.sp)
            }

            Spacer(Modifier.height(18.dp))
            Box(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(Accent)
                    .clickable(enabled = !submitting && level1.isNotEmpty() && reason.isNotBlank()) {
                        submitting = true
                        errorText = null
                        scope.launch {
                            val ok = Api.submitVerifyApply(level1, level2, label.trim(), reason.trim())
                            submitting = false
                            if (ok) success = true else errorText = "提交失败，请稍后重试"
                        }
                    }.padding(vertical = 13.dp),
                contentAlignment = Alignment.Center
            ) {
                if (submitting) {
                    CircularProgressIndicator(color = Color.White, strokeWidth = 2.dp, modifier = Modifier.size(18.dp))
                } else {
                    Text("提交申请", color = Color.White, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                }
            }
        }
    }

    if (success) {
        AlertDialog(
            onDismissRequest = onDismiss,
            title = { Text("申请已提交", color = TextPrimary, fontWeight = FontWeight.Bold) },
            text = { Text("审核通过后你将获得认证标识。", color = TextSecondary, fontSize = 14.sp) },
            confirmButton = {
                Text("好", color = Accent, fontWeight = FontWeight.SemiBold, modifier = Modifier.clickable { onDismiss() })
            }
        )
    }
}
