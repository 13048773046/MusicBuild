package app.star2936.guava3291.ui.theme

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.unit.dp
import kotlin.math.abs

// 精致深色主题（升级配色：更细腻的层次、更现代的质感）

val Background = Color(0xFF0E0E11)      // 更深的黑底（提升沉浸感）
val BackgroundElevated = Color(0xFF16161A)  // 悬浮层背景
val Card = Color(0xFF1A1A1F)            // 深灰卡片（微调，更柔和）
val CardHighlight = Color(0xFF222229)   // 卡片高亮/按压态
val CardBorder = Color(0xFF2C2C34)      // 卡片描边（更细腻）
val Accent = Color(0xFF5CE0A8)          // 薄荷绿主色（微调，更明亮）
val AccentSoft = Color(0xFF1E3D33)      // 深薄荷底
val Lilac = Color(0xFF9B8FD6)           // 暗紫
val Peach = Color(0xFFD6947A)           // 暗蜜桃
val Sky = Color(0xFF7BAFD6)             // 暗蓝
val TextPrimary = Color(0xFFF2F2F5)    // 近白（更柔和）
val TextSecondary = Color(0xFFA8A8B0)   // 灰
val TextTertiary = Color(0xFF6E6E78)    // 暗灰
val DangerRed = Color(0xFFFF7070)       // 退出登录/错误红
val UpdateGreen = Color(0xFF65DE92)     // 更新弹窗版本色

// 卡片圆角规范（统一设计系统）
val RadiusSmall = 12.dp
val RadiusMedium = 16.dp
val RadiusLarge = 20.dp
val RadiusXLarge = 24.dp

// 占位封面暗色渐变组
data class PalettePair(val first: Color, val second: Color)

val PlaceholderPalettes = listOf(
    PalettePair(AccentSoft, Accent.copy(alpha = 0.45f)),
    PalettePair(Color(0xFF2B2542), Lilac.copy(alpha = 0.55f)),
    PalettePair(Color(0xFF34211D), Peach.copy(alpha = 0.55f)),
    PalettePair(Color(0xFF1E2C3D), Sky.copy(alpha = 0.55f)),
    PalettePair(Color(0xFF1E3D33), Accent.copy(alpha = 0.5f)),
    PalettePair(Color(0xFF342A1E), Color(0xFF8F6F50)),
)

// 按文字 hash 选渐变
fun placeholderPalette(title: String): PalettePair {
    var hash = 0
    for (ch in title) {
        hash = (hash * 31 + ch.code) and 0x7fffffff
    }
    return PlaceholderPalettes[hash % PlaceholderPalettes.size]
}

// 亮度：与 iOS luminance 一致（0.299R + 0.587G + 0.114B）
fun luminance(color: Color): Float {
    return 0.299f * color.red + 0.587f * color.green + 0.114f * color.blue
}

/** 把主色压暗到 factor 比例（播放页 0.4 / 侧边栏 0.3） */
fun darkened(color: Color, factor: Float): Color {
    return Color(color.red * factor, color.green * factor, color.blue * factor, color.alpha)
}

// 取封面平均色
fun averageColor(r: Float, g: Float, b: Float): Color = Color(r, g, b)

fun Color.isLightish(threshold: Float = 0.55f): Boolean = luminance(this) > threshold

fun lerpColor(a: Color, b: Color, t: Float): Color = lerp(a, b, t.coerceIn(0f, 1f))

fun clamp01(v: Float): Float = if (v.isNaN()) 0f else v.coerceIn(0f, 1f)

fun absf(v: Double): Double = abs(v)
