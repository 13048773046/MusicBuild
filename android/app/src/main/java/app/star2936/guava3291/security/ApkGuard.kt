package app.star2936.guava3291.security

import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.Debug
import app.star2936.guava3291.BuildConfig
import java.io.File
import java.security.MessageDigest

/**
 * 发布包自防护（仅 release 生效，debug 包跳过以免影响开发调试）：
 * 1) 签名自校验——启动时比对当前 APK 签名与发布签名，不一致判定为二次打包/篡改；
 * 2) 反调试——检测调试器连接 / tracer 附加；
 * 3) 反模拟器（可选）——当前不启用，避免误伤用户自测；后续如需可在 [simulatorDetected] 扩展。
 */
object ApkGuard {

    /** 启动入口：不通过返回 false，调用方（Application.onCreate）终止业务初始化 */
    fun verify(ctx: Context): Boolean {
        if (BuildConfig.DEBUG) return true // debug 包不做自防护
        // Java 层反调试（JDWP 调试器连接）
        if (Debug.isDebuggerConnected()) return false
        // native 层反调试（TracerPid）+ 注入框架检测（frida/xposed 等）
        if (NativeGuard.isDebugged()) return false
        if (NativeGuard.hasHookFramework()) return false
        return signatureMatches(ctx)
    }

    /** 当前安装包签名证书 SHA-256 是否等于发布签名 */
    private fun signatureMatches(ctx: Context): Boolean {
        return try {
            val expected = Obfuscator.EXPECTED_SIGNATURE_SHA256
            val sigs = if (Build.VERSION.SDK_INT >= 28) {
                ctx.packageManager
                    .getPackageInfo(ctx.packageName, PackageManager.GET_SIGNING_CERTIFICATES)
                    .signingInfo?.apkContentsSigners ?: emptyArray()
            } else {
                @Suppress("DEPRECATION")
                ctx.packageManager.getPackageInfo(ctx.packageName, PackageManager.GET_SIGNATURES).signatures
                    ?: emptyArray()
            }
            sigs.any { sha256Hex(it.toByteArray()).equals(expected, ignoreCase = true) }
        } catch (_: Exception) {
            false
        }
    }

    private fun sha256Hex(bytes: ByteArray): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(bytes)
        return digest.joinToString("") { "%02x".format(it) }
    }
}
