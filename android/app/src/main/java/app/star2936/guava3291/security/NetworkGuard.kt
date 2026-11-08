package app.star2936.guava3291.security

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import app.star2936.guava3291.data.Api
import java.net.InetSocketAddress
import java.net.Proxy
import java.net.ProxySelector
import java.net.URI

/**
 * 防抓包（对抓包工具"隐形"）：
 * 1) SSL 证书固定（Api.http CertificatePinner）——抓包工具注入 CA 后 TLS 握手直接失败，
 *    抓包端看到的是握手错误而非明文流量；
 * 2) 抓包代理端口检测——识别 Charles(8888/8889)、Fiddler(8888)、mitmproxy(8080/9090) 等
 *    本机代理特征端口，命中即拒绝连接（请求表现为失败，抓包工具连 HTTP 都看不到）；
 * 3) VPN 仅标记不阻断（避免误伤正常翻墙/企业 VPN 用户，证书固定已覆盖抓包 VPN）。
 */
object NetworkGuard {

    // 抓包工具常见本机代理端口；正常代理（Clash 7890 等）不在其中，不误伤
    private val captureProxyPorts = setOf(8888, 8889, 8080, 9090)

    /** 系统代理命中抓包特征端口 → 判定抓包环境，拒绝连接 */
    fun isCaptureProxy(): Boolean {
        val host = Api.SERVER_HOST
        if (host.isBlank()) {
            // 地址未配置时不构造 URI，避免 https:// 抛 URISyntaxException 被静默吞掉
            return false
        }
        return try {
            val selector = ProxySelector.getDefault() ?: return false
            selector.select(URI("https://$host")).any { p ->
                p.type() == Proxy.Type.HTTP &&
                    (p.address() as? InetSocketAddress)?.let { addr ->
                        (addr.hostName == "127.0.0.1" || addr.hostName == "localhost") &&
                            addr.port in captureProxyPorts
                    } == true
            }
        } catch (e: Exception) {
            android.util.Log.w("NetworkGuard", "isCaptureProxy check failed: ${e.message}")
            false
        }
    }

    /** VPN 传输存在（抓包 VPN / 正常 VPN 均含，仅标记不阻断） */
    fun isVpnActive(ctx: Context): Boolean {
        return try {
            val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager ?: return false
            val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return false
            caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)
        } catch (_: Exception) {
            false
        }
    }
}
