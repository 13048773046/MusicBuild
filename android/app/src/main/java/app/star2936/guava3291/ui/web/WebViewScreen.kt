package app.star2936.guava3291.ui.web

import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import app.star2936.guava3291.state.AuthManager
import app.star2936.guava3291.ui.theme.Accent
import app.star2936.guava3291.ui.theme.Background

/// 导航栏深色文字（薄荷绿主色上保证对比度）
private val NavForeground = Color(0xFF0F2E24)

@Composable
fun WebViewScreenCompat(url: String, title: String = "", onBack: () -> Unit) {
    // 网页加载后标题实时替换为网页 <title>
    var pageTitle by remember { mutableStateOf(title) }

    Box(Modifier.fillMaxSize().background(Background).statusBarsPadding()) {
        Column(Modifier.fillMaxSize()) {
            // Header：45dp 高，主色背景，左返回键 + 中间标题（网页标题）
            Row(
                Modifier
                    .fillMaxWidth()
                    .height(45.dp)
                    .background(Accent)
                    .padding(horizontal = 12.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                // 返回键
                Box(
                    Modifier.size(38.dp).clickable { onBack() },
                    contentAlignment = Alignment.Center
                ) {
                    Icon(Icons.Filled.ArrowBack, null, tint = NavForeground, modifier = Modifier.size(20.dp))
                }

                Spacer(Modifier.weight(1f))

                // 中间标题：显示访问网页的标题
                Text(
                    pageTitle.ifEmpty { "加载中..." },
                    color = NavForeground,
                    fontSize = 16.sp,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )

                Spacer(Modifier.weight(1f))

                // 右侧对称占位，保证标题居中
                Spacer(Modifier.width(38.dp))
            }

            // WebView
            AndroidView(
                factory = { ctx ->
                    WebView(ctx).apply {
                        webViewClient = WebViewClient()
                        // 监听网页标题：加载完成后更新导航栏标题
                        webChromeClient = object : WebChromeClient() {
                            override fun onReceivedTitle(view: WebView?, t: String?) {
                                super.onReceivedTitle(view, t)
                                if (!t.isNullOrEmpty()) pageTitle = t
                            }
                        }
                        settings.javaScriptEnabled = true
                        settings.domStorageEnabled = true
                        // 注入 token 到页面
                        val token = AuthManager.currentUser.value?.token ?: ""
                        // 页面加载完成后，通过 JavascriptInterface 把 token 传过去
                        addJavascriptInterface(object {
                            @android.webkit.JavascriptInterface
                            fun getToken(): String = token
                        }, "AndroidBridge")
                    }
                },
                update = { it.loadUrl(url) },
                modifier = Modifier.fillMaxSize()
            )
        }
    }
}
