package app.star2936.guava3291.ui.home

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp
import app.star2936.guava3291.state.PlayerManager
import app.star2936.guava3291.ui.theme.Accent
import kotlin.random.Random
import kotlinx.coroutines.flow.collect

/**
 * 波形进度条组件：预计算随机波形，已播部分高亮，支持拖动跳转。
 * - 波形铺满整个内容宽度（与其他控件对齐）；
 * - 只保留上半：柱从中线向上波动（下半镜像去掉）；
 * - 拖动结束用「最新」scrubFraction 执行 seek（rememberUpdatedState 修闭包过期问题）。
 */
@Composable
fun WaveformProgressBar(
    modifier: Modifier = Modifier,
    scrubFraction: Float? = null,
    onScrubChange: (Float?) -> Unit = {},
    active: Boolean = true,
) {
    // 高频进度（4Hz）只在前台可见页订阅；离屏页保持最后值，不再逐帧重组
    val progress by androidx.compose.runtime.produceState(
        initialValue = PlayerManager.progress.value,
        key1 = active,
    ) {
        if (active) PlayerManager.progress.collect { value = it }
    }
    val shown = (scrubFraction ?: progress.toFloat()).coerceIn(0f, 1f)
    // 手势闭包不随重组重启，必须用 rememberUpdatedState 拿最新的拖动值
    val currentScrub by rememberUpdatedState(scrubFraction)

    // 预计算波形（按歌曲ID生成稳定随机数）
    val track = PlayerManager.current
    val barCount = 88
    val waveHeights = remember(track?.id) {
        val rand = Random(track?.id?.hashCode()?.toLong() ?: 42)
        List(barCount) { i ->
            // 生成类似音频波形的高度：中间高、两边低的分布 + 随机波动
            val centerBoost = 1f - kotlin.math.abs(i - barCount / 2f) / (barCount / 2f)
            val base = 0.25f + centerBoost * 0.6f
            (base * (0.55f + rand.nextFloat() * 0.45f)).coerceIn(0.12f, 1f)
        }
    }

    val density = LocalDensity.current
    val barHeight = 32.dp

    Box(
        modifier = modifier
            .fillMaxWidth()
            .height(barHeight)
    ) {
        Canvas(
            modifier = Modifier.fillMaxWidth().height(barHeight)
        ) {
            val totalWidth = size.width
            val barSpace = totalWidth / barCount
            val barW = (barSpace * 0.55f).coerceAtLeast(1.5f)
            // 柱从中线向上生长（只保留上半，下半镜像去掉）
            val midY = size.height / 2f
            val maxBarH = size.height * 0.46f

            for (i in 0 until barCount) {
                val x = i * barSpace + (barSpace - barW) / 2f
                val barH = waveHeights[i] * maxBarH
                val yTop = midY - barH

                // 已播部分颜色
                val barProgress = i.toFloat() / barCount
                val isPlayed = barProgress <= shown
                val color = if (isPlayed) Accent else Color.White.copy(alpha = 0.25f)

                // 画圆角竖条（底端固定在中线，顶端向上波动）
                drawRoundRect(
                    color = color,
                    topLeft = Offset(x, yTop),
                    size = Size(barW, barH),
                    cornerRadius = CornerRadius(barW / 2, barW / 2)
                )
            }

            // 拖动指示器（thumb）
            if (scrubFraction != null) {
                val thumbX = shown * totalWidth
                drawCircle(
                    color = Color.White,
                    radius = with(density) { 5.dp.toPx() },
                    center = Offset(thumbX, size.height / 2)
                )
            }
        }

        // 手势层（填满整条高度，拖动手感更佳）
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(barHeight)
                .pointerInput(track?.id) {
                    detectHorizontalDragGestures(
                        onDragStart = { offset ->
                            onScrubChange((offset.x / size.width).coerceIn(0f, 1f))
                        },
                        onDragEnd = {
                            currentScrub?.let { PlayerManager.seekToFraction(it.toDouble()) }
                            onScrubChange(null)
                        },
                        onDragCancel = { onScrubChange(null) },
                        onHorizontalDrag = { change, _ ->
                            change.consume()
                            onScrubChange((change.position.x / size.width).coerceIn(0f, 1f))
                        }
                    )
                }
        )
    }
}
