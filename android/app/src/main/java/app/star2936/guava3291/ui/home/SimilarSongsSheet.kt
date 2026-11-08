package app.star2936.guava3291.ui.home

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.star2936.guava3291.data.Api
import app.star2936.guava3291.data.SimilarTrack
import app.star2936.guava3291.data.Track
import app.star2936.guava3291.ui.components.RemoteImage
import app.star2936.guava3291.ui.theme.Accent
import app.star2936.guava3291.ui.theme.Card
import app.star2936.guava3291.ui.theme.CardBorder
import app.star2936.guava3291.ui.theme.TextPrimary
import app.star2936.guava3291.ui.theme.TextSecondary
import app.star2936.guava3291.ui.theme.TextTertiary

/**
 * 相似歌曲底部弹窗（2026-11-05）
 *
 * 数据来自后端 `/api/similar/{id}`：948 维语义向量（歌词/标签/画像 × MERT 听感）
 * 算出的**跨歌手**"听起来像"邻居，不是"同一个歌手的其他歌"。
 * 例如《诀别书》会连到《梦中的婚礼》《城南花已开》，《曹操》会连到《雪龙吟》《天地龙鳞》。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SimilarSongsSheet(
    seed: Track,
    onDismiss: () -> Unit,
    onPick: (Track, List<Track>) -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var loading by remember { mutableStateOf(true) }
    var items by remember { mutableStateOf<List<SimilarTrack>>(emptyList()) }

    LaunchedEffect(seed.id) {
        loading = true
        items = try {
            Api.similar(seed.id, 20)
        } catch (_: Exception) {
            emptyList()
        }
        loading = false
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = Card,
    ) {
        Column(Modifier.fillMaxWidth().padding(bottom = 24.dp)) {
            // 标题区：图标 + 「听起来像《X》」
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box(
                    Modifier
                        .size(34.dp)
                        .clip(RoundedCornerShape(10.dp))
                        .background(Accent.copy(alpha = 0.16f)),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        Icons.Default.AutoAwesome,
                        contentDescription = null,
                        tint = Accent,
                        modifier = Modifier.size(18.dp),
                    )
                }
                Spacer(Modifier.width(12.dp))
                Column {
                    Text(
                        "相似歌曲",
                        fontSize = 17.sp,
                        fontWeight = FontWeight.Bold,
                        color = TextPrimary,
                    )
                    Text(
                        "听起来像《${seed.title}》",
                        fontSize = 12.sp,
                        color = TextTertiary,
                        maxLines = 1,
                    )
                }
            }

            Box(
                Modifier
                    .fillMaxWidth()
                    .padding(top = 8.dp, bottom = 4.dp)
                    .height(0.5.dp)
                    .background(CardBorder)
            )

            when {
                loading -> Row(
                    Modifier.fillMaxWidth().heightIn(min = 160.dp).padding(24.dp),
                    horizontalArrangement = Arrangement.Center,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    CircularProgressIndicator(
                        color = Accent,
                        strokeWidth = 2.dp,
                        modifier = Modifier.size(22.dp),
                    )
                    Spacer(Modifier.width(12.dp))
                    Text("正在找听起来像的歌…", fontSize = 13.sp, color = TextSecondary)
                }

                items.isEmpty() -> Column(
                    Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 40.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Text("暂时没有相似歌曲", fontSize = 15.sp, color = TextSecondary)
                    Spacer(Modifier.height(6.dp))
                    Text(
                        "多播几首歌，推荐会越来越准",
                        fontSize = 12.sp,
                        color = TextTertiary,
                    )
                }

                else -> LazyColumn(Modifier.fillMaxWidth().heightIn(max = 420.dp)) {
                    items(items, key = { it.track.id }) { item ->
                        SimilarRow(
                            item = item,
                            onClick = { onPick(item.track, items.map { it.track }) },
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun SimilarRow(item: SimilarTrack, onClick: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 20.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RemoteImage(
            url = item.track.coverURL,
            modifier = Modifier.size(46.dp),
            cornerRadius = 9.dp,
            placeholderColor = CardBorder,
        )
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(
                item.track.title,
                fontSize = 15.sp,
                fontWeight = FontWeight.Medium,
                color = TextPrimary,
                maxLines = 1,
            )
            Spacer(Modifier.height(3.dp))
            Text(
                item.track.displayAuthor,
                fontSize = 12.sp,
                color = TextTertiary,
                maxLines = 1,
            )
        }
        Spacer(Modifier.width(10.dp))
        // 相似度只做弱提示：数值对用户没意义，用三段式表达更直观
        Box(
            Modifier
                .clip(CircleShape)
                .background(Accent.copy(alpha = 0.12f))
                .padding(horizontal = 9.dp, vertical = 4.dp)
        ) {
            Text(item.label, fontSize = 11.sp, fontWeight = FontWeight.SemiBold, color = Accent)
        }
    }
}
