package app.star2936.guava3291.ui.profile

import androidx.compose.foundation.border
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.star2936.guava3291.data.Api
import app.star2936.guava3291.data.Track
import app.star2936.guava3291.data.UserProfile
import app.star2936.guava3291.state.PlayerManager
import app.star2936.guava3291.ui.components.StatusPlaceholder
import app.star2936.guava3291.ui.components.TrackRow
import app.star2936.guava3291.ui.components.MemberBadge
import app.star2936.guava3291.ui.components.VerifyBadge
import app.star2936.guava3291.ui.components.TitleCapsule
import app.star2936.guava3291.ui.theme.Accent
import app.star2936.guava3291.ui.theme.AccentSoft
import app.star2936.guava3291.ui.theme.Background
import app.star2936.guava3291.ui.theme.Card
import app.star2936.guava3291.ui.theme.CardBorder
import app.star2936.guava3291.ui.theme.TextPrimary
import app.star2936.guava3291.ui.theme.TextSecondary
import app.star2936.guava3291.ui.theme.TextTertiary
import coil.compose.AsyncImage
import kotlinx.coroutines.launch

/** 用户主页（对应 iOS UserProfileView v2） */
@Composable
fun UserProfileScreen(userId: String, onBack: () -> Unit) {
    var profile by remember { mutableStateOf<UserProfile?>(null) }
    var tracks by remember { mutableStateOf<List<Track>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var page by remember { mutableStateOf(1) }
    var hasMore by remember { mutableStateOf(true) }
    var loadingMore by remember { mutableStateOf(false) }
    var followingLoading by remember { mutableStateOf(false) }   // 关注按钮防重复点击
    val scope = rememberCoroutineScope()
    val listState = rememberLazyListState()

    LaunchedEffect(userId) {
        try {
            profile = Api.fetchUserProfile(userId)
            tracks = Api.fetchUserTracks(userId)
            hasMore = false  // 接口暂不支持分页
        } catch (_: Exception) { }
        loading = false
    }

    // 滚动监听暂时不用（接口暂不支持分页）

    Box(Modifier.fillMaxSize().background(Background)) {
        LazyColumn(state = listState, modifier = Modifier.fillMaxSize()) {
            // 背景图区域：延伸到状态栏，底部渐变过渡
            item {
                Box(
                    Modifier
                        .fillMaxWidth()
                        .height(180.dp)
                ) {
                    // 用户背景图或渐变
                    profile?.backgroundURL?.let {
                        AsyncImage(
                            model = it, contentDescription = null,
                            modifier = Modifier.fillMaxSize(), contentScale = ContentScale.Crop
                        )
                    } ?: run {
                        Box(
                            Modifier.fillMaxSize().background(
                                Brush.verticalGradient(
                                    listOf(Accent.copy(alpha = 0.24f), Accent.copy(alpha = 0.06f))
                                )
                            )
                        )
                    }
                    // 顶部压暗，让返回按钮清晰
                    Box(
                        Modifier.fillMaxSize().background(
                            Brush.verticalGradient(
                                listOf(Color.Black.copy(alpha = 0.18f), Color.Transparent)
                            )
                        )
                    )
                    // 底部渐变过渡到背景色
                    Box(
                        Modifier
                            .fillMaxWidth()
                            .height(90.dp)
                            .align(Alignment.BottomCenter)
                            .background(
                                Brush.verticalGradient(
                                    listOf(Color.Transparent, Background)
                                )
                            )
                    )
                }
            }

            // 头像 + 信息（横排，向上重叠）
            item {
                Row(
                    Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 18.dp)
                        .offset(y = (-38).dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    // 头像 + 在线绿点（认证在名字旁不动；绿点位置对齐认证 LOGO：中心偏右下 0.38×头像、盖住头像一点）
                    Box {
                        AsyncImage(
                            model = profile?.avatarURL, contentDescription = null,
                            modifier = Modifier
                                .size(76.dp)
                                .clip(CircleShape)
                                .background(AccentSoft),
                            contentScale = ContentScale.Crop
                        )
                        if (profile?.isOnline == true) {
                            Box(
                                Modifier
                                    .align(Alignment.Center)
                                    .offset(x = (76 * 0.38f).dp, y = (76 * 0.38f).dp)
                                    .size((76 * 0.22f).dp)
                                    .clip(CircleShape)
                                    .background(Color(0xFF29C95C))
                                    .border(2.dp, Color.White, CircleShape)
                            )
                        }
                    }
                    Spacer(Modifier.width(14.dp))
                    // 信息区
                    Column {
                        // 名字 + 会员图标 + 认证蓝V（用户主页认证保持名字旁，不改）
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(
                                profile?.name ?: "用户",
                                fontSize = 19.sp, fontWeight = FontWeight.Bold, color = TextPrimary,
                                maxLines = 1
                            )
                            Spacer(Modifier.width(6.dp))
                            MemberBadge(profile?.member)
                            Spacer(Modifier.width(4.dp))
                            VerifyBadge(profile?.verified)
                        }
                        Spacer(Modifier.height(4.dp))
                        // 头衔胶囊
                        TitleCapsule(profile?.title)
                        Spacer(Modifier.height(4.dp))
                        val meta = listOfNotNull(
                            profile?.gender?.takeIf { it.isNotEmpty() },
                            profile?.age?.takeIf { it > 0 }?.let { "$it 岁" },
                            profile?.city?.takeIf { it.isNotEmpty() },
                        )
                        if (meta.isNotEmpty()) {
                            Text(
                                meta.joinToString(" · "),
                                fontSize = 12.sp, color = TextSecondary, maxLines = 1
                            )
                        }
                        profile?.signature?.let {
                            Spacer(Modifier.height(2.dp))
                            Text(it, fontSize = 12.sp, color = TextTertiary, maxLines = 1)
                        }
                    }
                }
            }

            // 关注按钮
            if (profile != null && profile!!.id != app.star2936.guava3291.state.AuthManager.currentUserValue?.id) {
                item {
                    Box(Modifier.fillMaxWidth().padding(top = 12.dp, bottom = 16.dp), contentAlignment = Alignment.Center) {
                        Button(
                            enabled = !followingLoading,
                            onClick = {
                                if (followingLoading) return@Button
                                followingLoading = true
                                scope.launch {
                                    try {
                                        val following = Api.toggleFollow(userId)
                                        profile = profile!!.copy(isFollowing = following)
                                    } catch (_: Exception) {
                                    } finally {
                                        followingLoading = false
                                    }
                                }
                            },
                            colors = ButtonDefaults.buttonColors(
                                containerColor = if (profile!!.isFollowing) Card else Accent,
                                contentColor = if (profile!!.isFollowing) TextSecondary else Color.White
                            ),
                            shape = RoundedCornerShape(20.dp),
                        ) {
                            Text(if (profile!!.isFollowing) "已关注" else "关注", fontSize = 14.sp)
                        }
                    }
                }
            }

            // 统计卡片
            item {
                Row(
                    Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp)
                        .clip(RoundedCornerShape(14.dp))
                        .background(Card)
                        .padding(vertical = 12.dp),
                    horizontalArrangement = Arrangement.SpaceAround
                ) {
                    StatItem(profile?.workCount ?: 0, "歌曲")
                    StatDivider()
                    StatItem(profile?.followerCount ?: 0, "粉丝")
                    StatDivider()
                    StatItem(profile?.followingCount ?: 0, "关注")
                    StatDivider()
                    StatItem(profile?.mutualCount ?: 0, "互关")
                    StatDivider()
                    StatItem(profile?.likeCount ?: 0, "获赞")
                }
            }

            // 歌曲列表标题
            item {
                Text(
                    "TA 的歌曲",
                    Modifier.padding(start = 18.dp, top = 20.dp, bottom = 12.dp),
                    fontSize = 16.sp, fontWeight = FontWeight.SemiBold, color = TextPrimary
                )
            }

            // 歌曲列表
            if (loading) {
                item {
                    Box(Modifier.fillMaxWidth().padding(vertical = 40.dp), contentAlignment = Alignment.Center) {
                        CircularProgressIndicator(color = Accent)
                    }
                }
            } else if (tracks.isEmpty()) {
                item {
                    StatusPlaceholder(text = "还没有上传歌曲", modifier = Modifier.fillMaxWidth())
                }
            } else {
                items(tracks) { t ->
                    TrackRow(track = t, onClick = { PlayerManager.play(t, tracks) })
                }
            }

            // 底部留白
            item { Spacer(Modifier.height(80.dp)) }
        }

        // 返回按钮：浮在背景图上
        IconButton(
            onClick = onBack,
            modifier = Modifier
                .statusBarsPadding()
                .padding(8.dp)
                .size(32.dp)
                .clip(CircleShape)
                .background(Color.Black.copy(alpha = 0.3f))
                .align(Alignment.TopStart)
        ) {
            Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回", tint = Color.White, modifier = Modifier.size(16.dp))
        }
    }
}

@Composable
private fun StatItem(count: Int, label: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text("$count", fontSize = 15.sp, fontWeight = FontWeight.Bold, color = TextPrimary)
        Text(label, fontSize = 11.sp, color = TextSecondary)
    }
}

@Composable
private fun StatDivider() {
    Box(Modifier.width(1.dp).height(22.dp).background(CardBorder))
}
