package app.star2936.guava3291.ui.home

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import app.star2936.guava3291.state.PlayerManager
import app.star2936.guava3291.ui.theme.Accent
import kotlin.math.sin
import kotlin.random.Random

/**
 * 音频频谱背景效果：模拟实时频谱波动。
 * - 只保留上半：柱从中线向上波动（下半镜像去掉）；
 * - 位于播放页背景底部区域（上方留白给封面与歌词），半透明叠加。
 *
 * 性能优化（2026-10-25，视觉不变）：
 * 1. 手动节流驱动替代 rememberInfiniteTransition（60fps→播放 30fps，正弦波视觉无差，开销减半）；
 * 2. 暂停时保留原 0.15 速微动，但降到 5fps 驱动（原 60fps×0.15 → 5fps×0.15，开销降 ~92%，视觉同前）；
 * 3. 每柱固定相位预计算（sin(time*k + phase) 分解，启动时算一次，每帧省乘法/相位计算）；
 * 4. 调用方将 Canvas 裁到光效区域（原只画屏高 55% 以下），重绘面积减半。
 */
@Composable
fun AudioSpectrumBackground(
    modifier: Modifier = Modifier,
    tint: Color = Accent,
) {
    val isPlaying by PlayerManager.isPlaying.collectAsState()

    // 预生成的频谱柱数
    val barCount = 48
    // 每柱固定相位（原 i*0.3 / i*0.7 / i*0.15，启动时算一次，每帧直接查表）
    val phases = remember {
        List(barCount) { i ->
            Triple(i * 0.3f, i * 0.7f, i * 0.15f)
        }
    }
    val baseHeights = remember {
        val rand = Random(42)
        List(barCount) { i ->
            val centerBoost = 1f - kotlin.math.abs(i - barCount / 2f) / (barCount / 2f)
            0.2f + centerBoost * 0.6f + rand.nextFloat() * 0.2f
        }
    }

    // 手动节流：播放 30fps 全速、暂停 5fps×0.15 微动（保留原暂停微动视觉，几乎零开销）
    var animTime by remember { mutableFloatStateOf(0f) }
    LaunchedEffect(isPlaying) {
        val frameNanos = if (isPlaying) 33_333_333L else 200_000_000L   // 30fps / 5fps
        val speed = if (isPlaying) 1f else 0.15f
        var last = 0L
        while (true) {
            withFrameNanos { now ->
                if (now - last >= frameNanos) {
                    animTime += (now - last) / 1_000_000_000f * speed
                    last = now
                }
            }
        }
    }

    Canvas(modifier = modifier.fillMaxSize()) {
        // Canvas 尺寸即光效区域（调用方已裁到屏高 55% 以下底部区域）
        val width = size.width
        val height = size.height
        val barWidth = width / barCount

        // 区域上界即 Canvas 顶，中线 = 高度一半（柱从中线向上波动，下半留空）
        val midY = height / 2f
        val maxBarH = height * 0.42f

        val time = animTime

        for (i in 0 until barCount) {
            val x = i * barWidth + barWidth / 2

            // 多重正弦波叠加模拟波动（相位查表，波形与原版逐帧等价）
            val (p1, p2, p3) = phases[i]
            val wave1 = sin(time * 0.05f + p1) * 0.3f + 0.7f
            val wave2 = sin(time * 0.08f + p2) * 0.2f
            val wave3 = sin(time * 0.03f + p3) * 0.15f
            val wave = (wave1 + wave2 + wave3).coerceIn(0.2f, 1.2f)

            val barH = baseHeights[i] * wave * maxBarH

            // 只画上半部分：柱从中线向上波动，下半镜像去掉
            drawLine(
                color = tint.copy(alpha = 0.25f),
                start = Offset(x, midY - barH),
                end = Offset(x, midY),
                strokeWidth = barWidth * 0.4f
            )
        }
    }
}
