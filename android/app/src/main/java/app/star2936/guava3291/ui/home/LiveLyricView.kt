package app.star2936.guava3291.ui.home

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.sp
import app.star2936.guava3291.util.KRCLine
import kotlin.math.pow

/**
 * 播放页两句歌词预览（对应 iOS LyricView.swift LyricPreview）。
 * 两句视口 + 切句 0.28s easeOutCubic 上滑 + 逐字卡拉OK白色 mask 填充。
 */
@Composable
fun LiveLyricPreview(
    lines: List<KRCLine>,
    progressSec: Double,
    modifier: Modifier = Modifier,
) {
    if (lines.isEmpty()) return

    val fontSize = 18.sp
    val density = LocalDensity.current
    val fontSizePx = with(density) { fontSize.toPx() }
    val rowHeight = fontSizePx * 1.3f
    val rowGap = fontSizePx * 0.4f
    val step = rowHeight + rowGap
    val viewHeight = rowHeight * 2 + rowGap

    // 当前行索引（二分）
    var currentIndex by remember(lines) { mutableIntStateOf(-1) }
    LaunchedEffect(progressSec, lines) {
        if (lines.isEmpty()) return@LaunchedEffect
        var lo = 0; var hi = lines.size
        while (lo < hi) {
            val mid = (lo + hi) / 2
            if (lines[mid].start <= progressSec) lo = mid + 1 else hi = mid
        }
        currentIndex = lo - 1
    }

    // 切句滚动进度（0~1，0.28s easeOutCubic）
    var scrollT by remember(lines) { mutableFloatStateOf(1f) }
    var lastC by remember(lines) { mutableIntStateOf(-2) }
    LaunchedEffect(currentIndex, progressSec) {
        if (currentIndex != lastC) {
            lastC = currentIndex
            scrollT = 0f
        }
        if (currentIndex > 0) {
            val raw = ((progressSec - lines[currentIndex].start) / 0.28).toFloat().coerceIn(0f, 1f)
            if (raw > scrollT) scrollT = raw
        } else {
            scrollT = 1f
        }
    }

    val eased = if (currentIndex > 0) 1f - (1f - scrollT).pow(3) else 0f
    val scrolling = currentIndex > 0 && scrollT < 1f
    val base = if (scrolling) currentIndex - 1 else currentIndex
    val offsetY = if (scrolling) -step * eased else 0f

    // 字宽缓存（每行每个 word 的渲染宽度，typeface 与 drawText 一致）
    val measurePaint = remember(fontSize) {
        android.graphics.Paint().apply {
            textSize = fontSizePx
            isAntiAlias = true
            typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
        }
    }
    // Paint 复用缓存：逐字填充每帧都会重绘，若每帧 new Paint + Typeface.create
    // （Typeface.create 是重量级 native 操作）会产生大量 GC 压力 → 一卡一卡。
    // 这里只创建一次，draw 时仅 setTextSize/setColor（廉价操作）。
    val paintCache = remember(fontSize) {
        arrayOf(
            android.graphics.Paint().apply {
                isAntiAlias = true
                typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
            },
            android.graphics.Paint().apply {
                isAntiAlias = true
                typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
            },
        )
    }
    val charWidthsMap = remember(lines, fontSize) {
        lines.map { line ->
            line.words.map { w -> measurePaint.measureText(w.text) }
        }
    }

    Canvas(
        modifier = modifier
            .fillMaxWidth()
            .height(with(density) { viewHeight.toDp() })
            // 对齐 iOS LyricPreview .clipped()：视口只露两行，第三行超出部分直接裁掉，
            // 滚动滑入时从底部"长出来"叠在背景上，下方互动行自然承接——无需遮罩层
            .clipToBounds()
    ) {
        drawIntoCanvas { canvas ->
            val native = canvas.nativeCanvas
            // 遍历 3 行
            for (idx in 0..2) {
                val li = base + idx
                if (li < 0 || li >= lines.size) continue
                val line = lines[li]
                val isCurrent = li == currentIndex
                val isOld = li == currentIndex - 1
                val scale = when {
                    isCurrent -> if (scrolling) 0.8f + 0.2f * eased else 1f
                    isOld -> 1f - 0.15f * eased
                    else -> 0.8f
                }
                val opacity = if (isOld) 1f - eased else 1f
                val ct = when {
                    isCurrent -> progressSec
                    isOld -> progressSec
                    else -> -1.0
                }

                val y = idx * step + offsetY + fontSizePx * scale
                val baseAlpha = 0.4f + 0.1f * ((scale - 0.8f) / 0.2f).coerceIn(0f, 1f)

                // 灰色底（复用缓存 Paint，仅改 textSize/color）
                val grayPaint = paintCache[0].apply {
                    textSize = fontSizePx * scale
                    color = Color.White.copy(alpha = baseAlpha * opacity).toArgb()
                }
                native.drawText(line.text, 0f, y, grayPaint)

                // 白色已唱部分（clipRect 精确 y 范围）
                if (ct >= 0) {
                    val cw = charWidthsMap.getOrElse(li) { emptyList() }
                    val filledWidth = calcFilledWidth(line, ct, cw, fontSizePx)
                    if (filledWidth > 0f) {
                        val whitePaint = paintCache[1].apply {
                            textSize = fontSizePx * scale
                            color = Color.White.copy(alpha = opacity).toArgb()
                        }
                        native.save()
                        // clipRect: left, top, right, bottom — 精确到本行 y 范围
                        native.clipRect(0f, y - fontSizePx * scale, filledWidth * scale, y + 2f)
                        native.drawText(line.text, 0f, y, whitePaint)
                        native.restore()
                    }
                }
            }
        }
    }
}

/** 计算逐字填充宽度（与 iOS LyricRowView.filledWidth 一致） */
private fun calcFilledWidth(
    line: KRCLine,
    currentTime: Double,
    charWidths: List<Float>,
    fontSizePx: Float,
): Float {
    if (currentTime < 0) return 0f
    if (line.words.size <= 1) {
        val w = line.words.first()
        return if (currentTime >= w.start) charWidths.sum() else 0f
    }
    var w = 0f
    var carry = 0.0
    var prevEnd = line.words[0].start
    for ((i, word) in line.words.withIndex()) {
        val gap = word.start - prevEnd
        if (gap > 0.6) carry += gap - 0.15
        val s = word.start - carry
        val e = word.end - carry
        val cw = charWidths.getOrElse(i) { fontSizePx }
        if (currentTime >= e) {
            w += cw
        } else if (currentTime >= s) {
            val span = (e - s).coerceAtLeast(0.001)
            val p = ((currentTime - s) / span).coerceIn(0.0, 1.0).toFloat()
            w += cw * p
            break
        } else break
        prevEnd = e
    }
    return w
}

/**
 * 无歌词占位（对标 iOS NoLyricPlaceholder）：
 * 两行内容——第一行「作者·xxx」，第二行「该歌曲暂无歌词」；
 * 停留 5s 后 0.28s easeOut 平滑上滑滚动到第二行，同时文字大小 0.75↔1 过渡，
 * 与正常歌词预览的滚动/字号过渡效果一致。高度约 46dp，不溢出播放页 56dp 歌词区。
 */
@Composable
fun NoLyricPlaceholder(
    artist: String,
    modifier: Modifier = Modifier,
) {
    val fontSize = 20.sp
    val density = LocalDensity.current
    val fontSizePx = with(density) { fontSize.toPx() }
    val rowHeight = fontSizePx * 1.15f
    val rowGap = fontSizePx * 0.3f
    val step = rowHeight + rowGap
    val viewHeight = rowHeight * 2 + rowGap

    var moved by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) {
        kotlinx.coroutines.delay(5000)
        moved = true
    }
    val scrollT by androidx.compose.animation.core.animateFloatAsState(
        targetValue = if (moved) 1f else 0f,
        animationSpec = androidx.compose.animation.core.tween(
            durationMillis = 280,
            easing = androidx.compose.animation.core.FastOutSlowInEasing
        ),
        label = "noLyricScroll"
    )
    val offsetY = -step * scrollT

    // 绘制用 Paint/Typeface 只创建一次并复用（绘制阶段反复 new Paint + Typeface.create
    // 会产生垃圾对象、拖慢每帧 draw）；textSize/color 每帧按需覆盖
    val paint = remember {
        android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG).apply {
            typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
        }
    }

    Canvas(
        modifier = modifier.fillMaxWidth().height(with(density) { viewHeight.toDp() })
    ) {
        drawIntoCanvas { canvas ->
            val native = canvas.nativeCanvas
            // 两行：第一行「作者·xxx」（1→0.75），第二行「该歌曲暂无歌词」（0.75→1）
            val rows = listOf(
                "作者·$artist" to (1f - 0.25f * scrollT),
                "该歌曲暂无歌词" to (0.75f + 0.25f * scrollT)
            )
            rows.forEachIndexed { idx, (text, scale) ->
                if (scale <= 0f) return@forEachIndexed
                paint.textSize = fontSizePx * scale
                paint.color = Color.White.copy(alpha = 0.5f).toArgb()
                val y = idx * step + offsetY + fontSizePx * scale
                native.drawText(text, 0f, y, paint)
            }
        }
    }
}

@Composable
private fun androidx.compose.ui.unit.TextUnit.toDpSafe() =
    with(LocalDensity.current) { this@toDpSafe.toDp() }
