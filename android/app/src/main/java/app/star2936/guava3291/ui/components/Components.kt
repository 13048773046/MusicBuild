package app.star2936.guava3291.ui.components

import android.graphics.BitmapFactory
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.MusicNote
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.star2936.guava3291.data.Track
import app.star2936.guava3291.state.CoverCache
import app.star2936.guava3291.ui.theme.Card
import app.star2936.guava3291.ui.theme.TextPrimary
import app.star2936.guava3291.ui.theme.TextSecondary
import app.star2936.guava3291.ui.theme.TextTertiary
import app.star2936.guava3291.ui.theme.placeholderPalette
import app.star2936.guava3291.util.formatTime
import coil.compose.AsyncImage
import kotlin.math.max
import kotlin.math.roundToInt
import kotlin.random.Random

// 对应 iOS Components.swift 通用组件

/** 无封面时渐变占位（含 MusicNote 图标） */
@Composable
fun AlbumArtPlaceholder(title: String, modifier: Modifier = Modifier) {
    val pair = remember(title) { placeholderPalette(title) }
    Box(
        modifier = modifier
            .background(Brush.verticalGradient(listOf(pair.first, pair.second)))
            .clip(RoundedCornerShape(12.dp)),
        contentAlignment = Alignment.Center
    ) {
        Icon(
            Icons.Outlined.MusicNote,
            contentDescription = null,
            tint = Color.White.copy(alpha = 0.55f),
            modifier = Modifier.size(28.dp)
        )
    }
}

/** 封面图：Coil 加载 + 内存缓存 + 渐变占位（对应 CachedCoverImage） */
@Composable
fun CoverImage(
    track: Track,
    modifier: Modifier = Modifier,
    cornerRadius: Dp = 12.dp,
    onLoaded: (() -> Unit)? = null,
) {
    val url = track.coverURL
    val placeholder = @Composable {
        AlbumArtPlaceholder(track.title, modifier = Modifier.fillMaxSize())
    }
    if (url.isNullOrEmpty()) {
        placeholder()
        return
    }
    Box(modifier = modifier.clip(RoundedCornerShape(cornerRadius))) {
        coil.compose.SubcomposeAsyncImage(
            model = url,
            contentDescription = track.title,
            modifier = Modifier.fillMaxSize(),
            contentScale = androidx.compose.ui.layout.ContentScale.Crop,
            loading = { placeholder() },
            error = { placeholder() },
            onSuccess = { onLoaded?.invoke() },
        )
    }
}

/** 通用远程图：Coil 加载（自带内存+磁盘缓存）+ 统一占位底色（#21：零散 AsyncImage 统一入口）。 */
@Composable
fun RemoteImage(
    url: String?,
    modifier: Modifier = Modifier,
    contentScale: androidx.compose.ui.layout.ContentScale = androidx.compose.ui.layout.ContentScale.Crop,
    cornerRadius: Dp = 0.dp,
    placeholderColor: Color = Color(0xFF2A2A2E),
) {
    if (url.isNullOrEmpty()) {
        Box(modifier.clip(RoundedCornerShape(cornerRadius)).background(placeholderColor))
        return
    }
    Box(modifier.clip(RoundedCornerShape(cornerRadius))) {
        coil.compose.SubcomposeAsyncImage(
            model = url,
            contentDescription = null,
            modifier = Modifier.fillMaxSize(),
            contentScale = contentScale,
            loading = { Box(Modifier.fillMaxSize().background(placeholderColor)) },
            error = { Box(Modifier.fillMaxSize().background(placeholderColor)) },
        )
    }
}

/** 声波动画条（对应 EqualizerBars：5 根圆角条随机高度跳动） */
@Composable
fun EqualizerBars(
    modifier: Modifier = Modifier,
    barCount: Int = 5,
    barColor: Color = Color.White.copy(alpha = 0.65f),
    active: Boolean = true,
) {
    val transition = rememberInfiniteTransition(label = "eq")
    val heights = remember { List(barCount) { Random.nextFloat() } }
    val anims = heights.map { h ->
        transition.animateFloat(
            initialValue = h,
            targetValue = 1f - h,
            animationSpec = infiniteRepeatable(
                animation = tween(320, easing = LinearEasing),
                repeatMode = RepeatMode.Reverse
            ),
            label = "eqbar"
        )
    }
    val barWidth = with(LocalDensity.current) { (3f).toDp() }
    Row(modifier = modifier, verticalAlignment = Alignment.Bottom, horizontalArrangement = androidx.compose.foundation.layout.Arrangement.spacedBy(3.dp)) {
        anims.forEach { a ->
            val h = if (active) a.value.coerceIn(0.2f, 1f) else 0.35f
            Canvas(modifier = Modifier
                .width(barWidth)
                .height((18 * h).dp)
                .clip(RoundedCornerShape(3.dp))) {
                drawRect(barColor)
            }
        }
    }
}

/** 歌名跑马灯（对应 MarqueeText：溢出才滚动，约 50sp/s，暂停 1.2s） */
@Composable
fun MarqueeText(
    text: String,
    modifier: Modifier = Modifier,
    style: TextStyle,
) {
    var textWidth by remember { mutableFloatStateOf(0f) }
    var containerWidth by remember { mutableFloatStateOf(0f) }
    val overflow = containerWidth > 0f && textWidth > containerWidth

    val measurer = androidx.compose.ui.text.rememberTextMeasurer()
    val layout = remember(text, style) { measurer.measure(text, style) }
    LaunchedEffect(layout.size.width) { textWidth = layout.size.width.toFloat() }

    // 双份无缝滚动：滚动一份文字宽即完成一个周期；Restart 回跳点两帧画面完全一致
    // （第一份与第二份文字相同、位置重合）→ 永不见空档/「消失再从右边出现」。
    // key(text)：切换歌曲后强制重建动画实例（offset 归零 + 暂停后从新歌重新滚动）——
    // 否则 rememberInfiniteTransition 不随 text 变化重启，旧 offset 作用于新文字
    // 会造成「切歌后跑马灯不动/卡住」（与 iOS 之前的问题同根因）。
    val distance = textWidth
    val scrollDuration = (distance / (50f * LocalDensity.current.density)).toLong()
        .coerceIn(1500L, 60_000L)
    val pauseMs = 1200L

    val infinite = key(text) { rememberInfiniteTransition(label = "marquee") }
    val offsetPx by infinite.animateFloat(
        initialValue = 0f,
        targetValue = -distance,
        animationSpec = infiniteRepeatable(
            animation = tween(
                durationMillis = (scrollDuration + pauseMs).toInt(),
                easing = LinearEasing,
            ),
            repeatMode = RepeatMode.Restart,
            initialStartOffset = androidx.compose.animation.core.StartOffset(pauseMs.toInt(), androidx.compose.animation.core.StartOffsetType.Delay),
        ),
        label = "marqueeOffset",
    )

    Box(
        modifier = modifier
            .fillMaxWidth()
            .onSizeChanged { containerWidth = it.width.toFloat() }
            .clipToBounds(),
    ) {
        Row(
            // offsetPx 已是像素值：必须用 px 版 offset（IntOffset），
            // 之前错用 .dp 会把位移再乘 density（如 2.75x）→ 滚动速度/距离失真
            modifier = Modifier.offset {
                androidx.compose.ui.unit.IntOffset(
                    if (overflow) offsetPx.roundToInt() else 0,
                    0
                )
            },
        ) {
            Text(
                text = text,
                style = style,
                maxLines = 1,
                overflow = TextOverflow.Visible,
                softWrap = false,
            )
            if (overflow) {
                // 第二份与第一份无缝衔接（间距 0），第一份滚出左端时第二份恰好补位，循环无空档
                Text(
                    text = text,
                    style = style,
                    maxLines = 1,
                    overflow = TextOverflow.Visible,
                    softWrap = false,
                )
            }
        }
    }
}

/** 列表歌曲行（对应 TrackRow：封面44 / 标题15 / 歌手13 / 时长12 mono） */
@Composable
fun TrackRow(
    track: Track,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    showProgress: Boolean = false,
    progress: Float = 0f,
    subtitleOverride: String? = null,
) {
    // 点击缩放效果：用 pressure 状态实现
    val interactionSource = remember { MutableInteractionSource() }
    val pressed = remember { androidx.compose.runtime.mutableStateOf(false) }
    val scale by animateFloatAsState(
        targetValue = if (pressed.value) 0.97f else 1f,
        animationSpec = tween(100),
        label = "trackRowScale"
    )

    LaunchedEffect(interactionSource) {
        interactionSource.interactions.collect { interaction ->
            when (interaction) {
                is androidx.compose.foundation.interaction.PressInteraction.Press -> pressed.value = true
                is androidx.compose.foundation.interaction.PressInteraction.Release,
                is androidx.compose.foundation.interaction.PressInteraction.Cancel -> pressed.value = false
                else -> {}
            }
        }
    }

    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 14.dp, vertical = 11.dp)
            .graphicsLayer { scaleX = scale; scaleY = scale }
            .clip(RoundedCornerShape(14.dp))
            .clickable(
                interactionSource = interactionSource,
                indication = null
            ) { onClick() },
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box {
            CoverImage(track = track, modifier = Modifier.size(44.dp), cornerRadius = 10.dp)
            if (showProgress) {
                Box(
                    modifier = Modifier
                        .size(44.dp)
                        .clip(RoundedCornerShape(10.dp))
                        .background(Color.Black.copy(alpha = 0.35f)),
                    contentAlignment = Alignment.Center
                ) {
                    Text(
                        text = "${(progress * 100).toInt()}%",
                        color = Color.White, fontSize = 11.sp,
                        fontWeight = FontWeight.SemiBold
                    )
                }
            }
        }
        Spacer(Modifier.width(12.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = track.title,
                color = TextPrimary, fontSize = 15.sp, fontWeight = FontWeight.SemiBold,
                maxLines = 1, overflow = TextOverflow.Ellipsis
            )
            Text(
                text = subtitleOverride ?: track.displayAuthor,
                color = TextSecondary, fontSize = 13.sp,
                maxLines = 1, overflow = TextOverflow.Ellipsis
            )
        }
        Text(
            text = track.durText ?: formatTime(track.dur ?: 0.0),
            color = TextTertiary, fontSize = 12.sp, fontFamily = FontFamily.Monospace
        )
    }
}

/** 空状态占位（对应 StatusPlaceholder） */
@Composable
fun StatusPlaceholder(
    icon: androidx.compose.ui.graphics.vector.ImageVector? = null,
    text: String,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier.padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        icon?.let {
            Icon(it, contentDescription = null, tint = TextTertiary, modifier = Modifier.size(36.dp))
            Spacer(Modifier.height(10.dp))
        }
        Text(text, color = TextTertiary, fontSize = 14.sp, textAlign = TextAlign.Center)
    }
}

/** 加载指示（对应 iOS ProgressView 小圆环） */
@Composable
fun LoadingSpinner(modifier: Modifier = Modifier, strokeWidth: Dp = 2.dp, size: Dp = 14.dp) {
    CircularProgressIndicator(
        modifier = modifier.size(size),
        strokeWidth = strokeWidth,
        color = TextTertiary
    )
}

/** 数量角标（99+） */
@Composable
fun CountBadge(count: Int, modifier: Modifier = Modifier) {
    if (count <= 0) return
    val label = if (count > 99) "99+" else "$count"
    Box(
        modifier = modifier
            .clip(RoundedCornerShape(8.dp))
            .background(Color(0xFF26262A))
            .padding(horizontal = 6.dp, vertical = 2.dp)
    ) {
        Text(label, color = TextPrimary, fontSize = 10.sp, fontWeight = FontWeight.SemiBold)
    }
}
