package app.star2936.guava3291.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.WorkspacePremium
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

// 会员/认证/头衔徽章（2026-10-19 会员体系，对齐 iOS Components.swift）

/** 会员胶囊：SVIP 金色 / VIP 绿色，非纯色渐变背景 + 皇冠 + 加粗文字 + 白色高光描边；开了 SVIP 只显示 SVIP */
@Composable
fun MemberBadge(member: String?) {
    when (member) {
        "svip" -> MemberCapsule("SVIP", listOf(Color(0xFF8A6D1F), Color(0xFFD4AF37)))
        "vip" -> MemberCapsule("VIP", listOf(Color(0xFF1F6A4A), Color(0xFF2ECC8F)))
    }
}

@Composable
private fun MemberCapsule(text: String, colors: List<Color>) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .background(
                brush = Brush.linearGradient(colors = colors),
                shape = RoundedCornerShape(50)
            )
            .border(
                width = 0.6.dp,
                brush = Brush.verticalGradient(
                    listOf(Color.White.copy(alpha = 0.55f), Color.White.copy(alpha = 0.1f))
                ),
                shape = RoundedCornerShape(50)
            )
            .padding(horizontal = 7.dp, vertical = 2.dp)
    ) {
        Icon(
            Icons.Filled.WorkspacePremium,
            contentDescription = null,
            tint = Color.White,
            modifier = Modifier.size(8.dp)
        )
        Spacer(Modifier.width(3.dp))
        Text(
            text = text,
            fontSize = 10.sp,
            fontWeight = FontWeight.Black,
            color = Color.White
        )
    }
}

/** 官方认证蓝 V 标（固定样式，认证类型非空即显示） */
@Composable
fun VerifyBadge(verified: String?) {
    if (!verified.isNullOrEmpty()) {
        Box(
            modifier = Modifier
                .size(16.dp)
                .background(
                    brush = Brush.verticalGradient(
                        colors = listOf(Color(0xFF2A6DF4), Color(0xFF5B8DEF))
                    ),
                    shape = RoundedCornerShape(4.dp)
                ),
            contentAlignment = androidx.compose.ui.Alignment.Center
        ) {
            Text(
                text = "V",
                fontSize = 10.sp,
                fontWeight = FontWeight.Black,
                color = Color.White
            )
        }
    }
}

/** 圆形蓝色认证标（2026-10-19 细化版，对齐 iOS CertBadge）：
 *  鲜亮蓝渐变 + 白 V + 高光描边 + 阴影；替换头像右下角在线绿点位置（右下角偏移）。 */
@Composable
fun CertBadge(avatarSize: Int, modifier: Modifier = Modifier) {
    val size = maxOf((avatarSize * 0.26f).toInt(), 12).dp
    if (size.value <= 0f) return
    Box(
        modifier = modifier
            .size(size)
            .shadow(
                elevation = (size.value / 4f).dp,
                shape = CircleShape,
                ambientColor = Color(0xFF2A6DF4).copy(alpha = 0.5f),
                spotColor = Color(0xFF2A6DF4).copy(alpha = 0.5f)
            )
            .clip(CircleShape)
            .background(
                brush = Brush.verticalGradient(
                    colors = listOf(Color(0xFF3D7BFF), Color(0xFF2A6DF4))
                )
            )
            .border(
                width = maxOf(size.value / 9f, 1f).dp,
                brush = Brush.verticalGradient(
                    colors = listOf(Color.White.copy(alpha = 0.6f), Color.White.copy(alpha = 0.15f))
                ),
                shape = CircleShape
            ),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text = "V",
            fontSize = (size.value * 0.46f).sp,
            fontWeight = FontWeight.Black,
            color = Color.White
        )
    }
}

/** 头衔胶囊（半透明浅色背景 + 文字；空头衔不显示） */
@Composable
fun TitleCapsule(title: String?, accent: Color = Color(0xFF4CD9A5)) {
    if (!title.isNullOrEmpty()) {
        Text(
            text = title,
            fontSize = 10.sp,
            fontWeight = FontWeight.SemiBold,
            color = accent,
            modifier = Modifier
                .background(
                    color = accent.copy(alpha = 0.12f),
                    shape = RoundedCornerShape(50)
                )
                .padding(horizontal = 6.dp, vertical = 1.dp)
        )
    }
}

/** 会员到期时间（VIP/SVIP 取较晚者，展示"会员有效期至 yyyy-MM-dd"） */
@Composable
fun MemberExpireText(
    memberExpire: Double?,
    svipExpire: Double?,
    textColor: Color = Color(0x99FFFFFF),
    fontSize: Int = 12
) {
    val expire = maxOf(memberExpire ?: 0.0, svipExpire ?: 0.0)
    if (expire > 0) {
        val sdf = java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.CHINA)
        Text(
            text = "会员有效期至 " + sdf.format(java.util.Date((expire * 1000).toLong())),
            fontSize = fontSize.sp,
            color = textColor,
            modifier = Modifier.padding(start = 8.dp)
        )
    }
}

/** 名字行：名字 + 会员 + 认证（紧凑组合） */
@Composable
fun MemberRow(
    member: String?,
    verified: String?,
    title: String?,
    accent: Color = Color(0xFF4CD9A5),
    modifier: Modifier = Modifier
) {
    Row(
        horizontalArrangement = Arrangement.spacedBy(5.dp),
        modifier = modifier
    ) {
        MemberBadge(member)
        VerifyBadge(verified)
        TitleCapsule(title, accent)
    }
}
