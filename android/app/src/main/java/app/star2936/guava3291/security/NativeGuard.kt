package app.star2936.guava3291.security

/**
 * native 防护层（libguard.so，见 src/main/cpp/guard.c）。
 * 反调试 / 注入框架检测 / 字符串解密在 C 层执行：
 * - dex 中不含 XOR 密钥与明文敏感串，核心校验逻辑不可被 Java 反编译直读。
 */
object NativeGuard {

    init {
        System.loadLibrary("guard")
    }

    /** TracerPid 检测：非 0 表示被调试器/附加工具跟踪 */
    external fun isDebugged(): Boolean

    /** /proc/self/maps 注入框架特征检测（frida/xposed/substrate/zygisk 等） */
    external fun hasHookFramework(): Boolean

    /** hex 密文 XOR 还原（密钥仅存在于 so 二进制） */
    external fun decrypt(hex: String): String
}
