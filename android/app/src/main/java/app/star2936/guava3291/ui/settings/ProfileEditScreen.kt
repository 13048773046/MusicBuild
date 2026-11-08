package app.star2936.guava3291.ui.settings

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.PhotoCamera
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
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
import androidx.core.content.FileProvider
import app.star2936.guava3291.ui.theme.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileOutputStream
import java.io.InputStream

@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
fun ProfileEditScreenCompat(onBack: () -> Unit) {
    val context = LocalContext.current
    val statusBarTop = WindowInsets.statusBars.asPaddingValues().calculateTopPadding()
    var name by remember { mutableStateOf("") }
    var gender by remember { mutableStateOf("保密") }
    var ageText by remember { mutableStateOf("") }
    var signature by remember { mutableStateOf("") }
    val genders = listOf("男", "女", "保密")
    val scope = androidx.compose.runtime.rememberCoroutineScope()
    var busy by remember { mutableStateOf(false) }

    // ===== 头像更换状态 =====
    var showAvatarOptions by remember { mutableStateOf(false) }
    /// 新选头像压缩后的字节（保存时上传；null 表示未更换）
    var pendingAvatar by remember { mutableStateOf<ByteArray?>(null) }
    /// 预览位图（选图后本地即时预览，不依赖网络加载）
    var avatarPreview by remember { mutableStateOf<Bitmap?>(null) }
    /// 拍照临时文件 Uri
    var cameraUri by remember { mutableStateOf<Uri?>(null) }

    /// 选图后统一处理：采样压缩 → 更新预览与待上传字节（lambda 变量，避免局部函数前向引用）
    val applyAvatar: (Uri) -> Unit = { uri ->
        scope.launch {
            val bytes = withContext(Dispatchers.IO) {
                try {
                    decodeAndCompress(context, uri)
                } catch (_: Exception) { null }
            }
            if (bytes != null && bytes.isNotEmpty()) {
                pendingAvatar = bytes
                avatarPreview = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                showAvatarOptions = false
            }
        }
    }

    // 相册选择（无需权限）：GetContent 返回可读 Uri
    val galleryPicker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) applyAvatar(uri)
    }
    // 拍照（需 CAMERA 权限 + FileProvider 临时文件）
    val cameraLauncher = rememberLauncherForActivityResult(ActivityResultContracts.TakePicture()) { ok ->
        if (ok) cameraUri?.let { applyAvatar(it) }
    }
    // 相机权限请求：授权后创建临时文件并拉起相机
    val startCamera: () -> Unit = {
        try {
            val file = File(context.cacheDir, "avatar_camera.jpg")
            if (file.exists()) file.delete()
            FileOutputStream(file).close()
            val uri = FileProvider.getUriForFile(
                context,
                context.packageName + ".fileprovider",
                file
            )
            cameraUri = uri
            cameraLauncher.launch(uri)
        } catch (_: Exception) {
            showAvatarOptions = false
        }
    }
    val permissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) {
            startCamera()
        } else {
            // 无权限：降级提示（相机不可用）
            showAvatarOptions = false
        }
    }

    LaunchedEffect(Unit) {
        val u = app.star2936.guava3291.state.AuthManager.currentUser.value ?: return@LaunchedEffect
        name = u.name; gender = u.gender ?: "保密"; signature = u.signature ?: ""
        ageText = (u.age ?: 0).let { if (it > 0) it.toString() else "" }
    }

    Box(Modifier.fillMaxSize().background(Background)) {
        Column(Modifier.fillMaxSize().padding(top = statusBarTop)) {
            Row(
                Modifier.padding(horizontal = 20.dp).padding(top = 10.dp, bottom = 6.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Box(
                    Modifier.size(38.dp).clip(CircleShape).background(Card).clickable { onBack() },
                    contentAlignment = Alignment.Center
                ) { Icon(Icons.Filled.ArrowBack, null, tint = TextPrimary, modifier = Modifier.size(18.dp)) }
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f)) {
                    Text("编辑资料", color = TextPrimary, fontSize = 30.sp, fontWeight = FontWeight.Bold)
                    Text("完善你的个人主页", color = TextSecondary, fontSize = 12.sp)
                }
                Text(
                    if (busy) "…" else "保存",
                    color = Accent, fontSize = 16.sp, fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.clickable(enabled = !busy) {
                        busy = true
                        scope.launch {
                            try {
                                app.star2936.guava3291.state.AuthManager.updateProfile(
                                    name.trim(), gender, ageText.toIntOrNull() ?: 0, signature.trim(), pendingAvatar, null
                                )
                                onBack()
                            } catch (_: Exception) { busy = false }
                        }
                    }
                )
            }

            Column(
                Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(20.dp)
                    // 全屏子页面：底部补系统手势区，保存/按钮不被手势条压住
                    .navigationBarsPadding()
                    // 键盘弹出时自动滚动，保证输入框可见
                    .imePadding()
            ) {
                Box(
                    Modifier.fillMaxWidth().height(200.dp).clip(RoundedCornerShape(20.dp)).background(Card)
                ) {
                    // 背景图
                    val user = app.star2936.guava3291.state.AuthManager.currentUser.value
                    if (user?.backgroundURL != null) {
                        coil.compose.AsyncImage(
                            model = user.backgroundURL,
                            contentDescription = null,
                            modifier = Modifier.fillMaxWidth().height(160.dp),
                            contentScale = ContentScale.Crop
                        )
                    } else {
                        Box(Modifier.fillMaxWidth().height(160.dp).background(Accent.copy(alpha = 0.2f)))
                    }
                    // 头像：优先本地预览（新选）→ 原头像 → 占位图标（不再透明不可见）
                    Box(
                        Modifier
                            .padding(start = 16.dp)
                            .offset(y = 122.dp)
                            .size(76.dp)
                            .clip(CircleShape)
                            .background(Accent.copy(alpha = 0.2f))
                            // 点击更换头像：弹出「拍照 / 相册 / 取消」
                            .clickable { showAvatarOptions = true },
                        contentAlignment = Alignment.Center
                    ) {
                        val preview = avatarPreview
                        if (preview != null) {
                            Image(
                                preview.asImageBitmap(), contentDescription = "新头像预览",
                                modifier = Modifier.fillMaxSize(),
                                contentScale = ContentScale.Crop
                            )
                        } else if (user?.avatarURL != null) {
                            coil.compose.AsyncImage(
                                model = user.avatarURL,
                                contentDescription = null,
                                modifier = Modifier.fillMaxSize(),
                                contentScale = ContentScale.Crop
                            )
                        } else {
                            // 无头像占位：Person 图标（修复透明不可见问题）
                            Icon(
                                Icons.Filled.Person, contentDescription = null,
                                tint = Accent, modifier = Modifier.size(28.dp)
                            )
                        }
                        // 右下角相机图标提示（告诉用户可以点击更换）
                        Box(
                            Modifier
                                .align(Alignment.BottomEnd)
                                .size(24.dp)
                                .clip(CircleShape)
                                .background(Accent)
                        ) {
                            Icon(
                                Icons.Filled.PhotoCamera, contentDescription = "更换头像",
                                tint = Color.White, modifier = Modifier.size(14.dp).align(Alignment.Center)
                            )
                        }
                    }
                }

                Spacer(Modifier.height(18.dp))

                Column(
                    Modifier.clip(RoundedCornerShape(18.dp)).background(Card).padding(18.dp),
                    verticalArrangement = Arrangement.spacedBy(14.dp)
                ) {
                    Text("昵称", color = TextTertiary, fontSize = 12.sp, fontWeight = FontWeight.Medium)
                    InputField("输入昵称", name) { name = it }

                    Text("性别", color = TextTertiary, fontSize = 12.sp, fontWeight = FontWeight.Medium)
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        genders.forEach { g ->
                            Box(
                                Modifier.weight(1f).clip(RoundedCornerShape(10.dp))
                                    .background(if (gender == g) Accent else Background)
                                    .clickable { gender = g }
                                    .padding(vertical = 10.dp),
                                contentAlignment = Alignment.Center
                            ) { Text(g, color = if (gender == g) Color.White else TextSecondary, fontSize = 14.sp) }
                        }
                    }

                    Text("年龄", color = TextTertiary, fontSize = 12.sp, fontWeight = FontWeight.Medium)
                    InputField("输入年龄", ageText) { ageText = it }

                    Text("签名", color = TextTertiary, fontSize = 12.sp, fontWeight = FontWeight.Medium)
                    InputField("一句话介绍自己", signature) { signature = it }
                }

                Spacer(Modifier.height(40.dp))
            }
        }
    }

    // ===== 更换头像底部弹窗：拍照 / 从相册选择 / 取消 =====
    if (showAvatarOptions) {
        ModalBottomSheet(
            onDismissRequest = { showAvatarOptions = false },
            sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
            containerColor = Card
        ) {
            Column(Modifier.fillMaxWidth().padding(bottom = 24.dp)) {
                Text(
                    "更换头像",
                    color = TextPrimary, fontSize = 16.sp, fontWeight = FontWeight.Bold,
                    modifier = Modifier.padding(horizontal = 20.dp, vertical = 14.dp)
                )
                HorizontalDivider(color = CardBorder)
                // 拍照：需相机权限（未授权先请求；拒绝则降级不打开相机）
                TextButton(
                    onClick = {
                        if (androidx.core.content.ContextCompat.checkSelfPermission(
                                context, android.Manifest.permission.CAMERA
                            ) == android.content.pm.PackageManager.PERMISSION_GRANTED
                        ) {
                            showAvatarOptions = false
                            startCamera()
                        } else {
                            permissionLauncher.launch(android.Manifest.permission.CAMERA)
                        }
                    },
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 20.dp)
                ) { Text("拍照", color = TextPrimary, fontSize = 15.sp) }
                // 相册：GetContent 无需权限
                TextButton(
                    onClick = {
                        showAvatarOptions = false
                        galleryPicker.launch("image/*")
                    },
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 20.dp)
                ) { Text("从相册选择", color = TextPrimary, fontSize = 15.sp) }
                HorizontalDivider(color = CardBorder)
                TextButton(
                    onClick = { showAvatarOptions = false },
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 20.dp)
                ) { Text("取消", color = TextSecondary, fontSize = 15.sp) }
            }
        }
    }
}

/// Uri → 采样压缩：目标最长边 512px、JPEG 质量 80，避免原图直传。
/// 注意 bounds 预读与实际解码必须各开一次流（同流第二次读不到数据）。
private fun decodeAndCompress(context: android.content.Context, uri: Uri): ByteArray? {
    // 第一遍：只读尺寸
    val opts = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    context.contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, opts) }
    if (opts.outWidth <= 0 || opts.outHeight <= 0) return null
    // 计算采样率（2 的幂）
    var sample = 1
    val maxSide = maxOf(opts.outWidth, opts.outHeight)
    while (maxSide / (sample * 2) >= 512) sample *= 2
    // 第二遍：采样解码（重新开流）
    val opts2 = BitmapFactory.Options().apply { inSampleSize = sample }
    val bmp = context.contentResolver.openInputStream(uri)?.use {
        BitmapFactory.decodeStream(it, null, opts2)
    } ?: return null
    // 等比缩到 512px 内
    val w = bmp.width; val h = bmp.height
    val scale = minOf(1f, 512f / maxOf(w, h))
    val out = if (scale < 1f) {
        Bitmap.createScaledBitmap(bmp, (w * scale).toInt().coerceAtLeast(1), (h * scale).toInt().coerceAtLeast(1), true)
    } else bmp
    val bos = ByteArrayOutputStream()
    out.compress(Bitmap.CompressFormat.JPEG, 80, bos)
    if (out !== bmp) out.recycle()
    bmp.recycle()
    return bos.toByteArray()
}

@Composable
private fun InputField(hint: String, value: String, onChange: (String) -> Unit) {
    BasicTextField(
        value = value,
        onValueChange = onChange,
        textStyle = androidx.compose.ui.text.TextStyle(color = TextPrimary, fontSize = 15.sp),
        cursorBrush = androidx.compose.ui.graphics.SolidColor(Accent),
        modifier = Modifier
            .clip(RoundedCornerShape(12.dp))
            .background(Background)
            .border(1.dp, CardBorder, RoundedCornerShape(12.dp))
            .padding(horizontal = 14.dp, vertical = 14.dp),
        decorationBox = { inner ->
            if (value.isEmpty()) Text(hint, color = TextTertiary, fontSize = 15.sp)
            inner()
        }
    )
}
