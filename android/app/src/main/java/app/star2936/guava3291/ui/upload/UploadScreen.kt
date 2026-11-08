package app.star2936.guava3291.ui.upload

import android.graphics.BitmapFactory
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.star2936.guava3291.data.Api
import app.star2936.guava3291.state.AuthManager
import app.star2936.guava3291.ui.theme.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** 上传页（对应 iOS UploadSheet：封面选择/移除 + 100MB 上限 + 上传） */
@Composable
fun UploadScreenCompat(fileUri: Uri?, onBack: () -> Unit) {
    val context = LocalContext.current
    val statusBarTop = WindowInsets.statusBars.asPaddingValues().calculateTopPadding()
    var title by remember { mutableStateOf("") }
    var artist by remember { mutableStateOf("") }
    var album by remember { mutableStateOf("") }
    /// 选中封面位图（预览用）
    var coverBitmap by remember { mutableStateOf<android.graphics.Bitmap?>(null) }
    /// 封面原始字节（上传用）
    var coverBytes by remember { mutableStateOf<ByteArray?>(null) }
    var uploading by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    // 相册选封面（对齐 iOS PhotosPicker）
    val coverPicker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        uri ?: return@rememberLauncherForActivityResult
        scope.launch {
            val bmp = withContext(Dispatchers.IO) {
                try {
                    BitmapFactory.decodeStream(context.contentResolver.openInputStream(uri))
                } catch (_: Exception) { null }
            }
            if (bmp != null) {
                coverBitmap = bmp
                coverBytes = withContext(Dispatchers.IO) {
                    try {
                        val bos = java.io.ByteArrayOutputStream()
                        bmp.compress(android.graphics.Bitmap.CompressFormat.JPEG, 90, bos)
                        bos.toByteArray()
                    } catch (_: Exception) { null }
                }
            }
        }
    }

    fun upload() {
        val u = AuthManager.currentUserValue ?: return
        val uri = fileUri ?: return
        if (title.trim().isEmpty()) { android.widget.Toast.makeText(context, "请填写标题", android.widget.Toast.LENGTH_SHORT).show(); return }
        uploading = true
        scope.launch {
            try {
                // 100MB 上限（对齐 iOS）
                val size = withContext(Dispatchers.IO) {
                    context.contentResolver.openAssetFileDescriptor(uri, "r")?.use { it.length } ?: -1L
                }
                if (size > 100L * 1024 * 1024) {
                    android.widget.Toast.makeText(context, "文件超过 100MB 上限，无法上传", android.widget.Toast.LENGTH_SHORT).show()
                    uploading = false
                    return@launch
                }
                // 大文件后台读（最高 100MB，避免阻塞 UI）
                val data = withContext(Dispatchers.IO) {
                    context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                } ?: throw Exception("读取文件失败")
                Api.upload(
                    data = data,
                    filename = uri.lastPathSegment ?: "upload.mp3",
                    title = title.trim(),
                    artist = artist.trim(),
                    album = album.trim(),
                    userId = u.id,
                    coverData = coverBytes,
                )
                android.widget.Toast.makeText(context, "上传成功", android.widget.Toast.LENGTH_SHORT).show()
                onBack()
            } catch (e: Exception) {
                android.widget.Toast.makeText(context, "上传失败：${e.message ?: "未知错误"}", android.widget.Toast.LENGTH_SHORT).show()
            }
            uploading = false
        }
    }

    Box(Modifier.fillMaxSize().background(Background)) {
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(top = statusBarTop)
                // 全屏子页面：底部补系统手势区，发布按钮不被手势条压住
                .navigationBarsPadding()
        ) {
            // Header
            Row(
                Modifier.padding(horizontal = 20.dp, vertical = 12.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Box(
                    Modifier.size(38.dp).clip(CircleShape).background(Card).clickable { onBack() },
                    contentAlignment = Alignment.Center
                ) { Icon(Icons.Filled.ArrowBack, contentDescription = null, tint = TextPrimary, modifier = Modifier.size(18.dp)) }
                Spacer(Modifier.width(12.dp))
                Text("上传歌曲", color = TextPrimary, fontSize = 20.sp, fontWeight = FontWeight.Bold)
            }

            Spacer(Modifier.height(16.dp))

            // 歌曲信息
            Text("歌曲信息", color = TextTertiary, fontSize = 12.sp, fontWeight = FontWeight.Medium, modifier = Modifier.padding(start = 20.dp, bottom = 8.dp))
            Column(
                Modifier.padding(horizontal = 20.dp).clip(RoundedCornerShape(18.dp)).background(Card)
            ) {
                FormField("标题", title) { title = it }
                DividerLine()
                FormField("歌手", artist) { artist = it }
                DividerLine()
                FormField("专辑", album) { album = it }
            }

            Spacer(Modifier.height(24.dp))

            // 歌曲封面（对齐 iOS：选择封面 / 更换封面 / 移除封面）
            Text("歌曲封面", color = TextTertiary, fontSize = 12.sp, fontWeight = FontWeight.Medium, modifier = Modifier.padding(start = 20.dp, bottom = 8.dp))
            Column(
                Modifier.padding(horizontal = 20.dp).clip(RoundedCornerShape(18.dp)).background(Card).padding(16.dp)
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        Modifier.size(84.dp).clip(RoundedCornerShape(12.dp)).background(CardBorder)
                            .clickable { coverPicker.launch("image/*") },
                        contentAlignment = Alignment.Center
                    ) {
                        val bmp = coverBitmap
                        if (bmp != null) {
                            Image(bmp.asImageBitmap(), contentDescription = "封面预览",
                                modifier = Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
                        } else {
                            Text("+", color = TextTertiary, fontSize = 26.sp)
                        }
                    }
                    Spacer(Modifier.width(16.dp))
                    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text(
                            if (coverBitmap != null) "更换封面" else "选择封面",
                            color = Accent, fontSize = 15.sp, fontWeight = FontWeight.SemiBold,
                            modifier = Modifier.clickable { coverPicker.launch("image/*") }
                        )
                        if (coverBitmap != null) {
                            Text(
                                "移除封面", color = Color(0xFFFF6B6B), fontSize = 13.sp,
                                modifier = Modifier.clickable { coverBitmap = null; coverBytes = null }
                            )
                        }
                    }
                }
                Spacer(Modifier.height(8.dp))
                Text("必须选择封面后才能上传", color = Color(0xFFFF9500), fontSize = 12.sp)
            }

            Spacer(Modifier.height(24.dp))

            // 上传按钮（对齐 iOS：上传中转圈，禁用时不可点）
            Box(
                Modifier.padding(horizontal = 20.dp).fillMaxWidth()
                    .clip(RoundedCornerShape(14.dp)).background(Accent)
                    .clickable(enabled = !uploading && coverBitmap != null) { upload() },
                contentAlignment = Alignment.Center
            ) {
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 14.dp)) {
                    if (uploading) {
                        androidx.compose.material3.CircularProgressIndicator(
                            modifier = Modifier.size(16.dp), color = Color.White, strokeWidth = 2.dp
                        )
                        Spacer(Modifier.width(8.dp))
                    }
                    Text(if (uploading) "上传中…" else "上传", color = Color.White, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                }
            }

            Spacer(Modifier.height(40.dp))
        }
    }
}

@Composable
private fun FormField(label: String, value: String, onChange: (String) -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(label, color = TextTertiary, fontSize = 15.sp, modifier = Modifier.width(60.dp))
        BasicTextField(
            value = value,
            onValueChange = onChange,
            textStyle = androidx.compose.ui.text.TextStyle(color = TextPrimary, fontSize = 15.sp),
            modifier = Modifier.weight(1f)
        )
    }
}

@Composable
private fun DividerLine() {
    Box(Modifier.padding(start = 16.dp).fillMaxWidth().height(0.5.dp).background(CardBorder))
}
