package app.star2936.guava3291.security

/**
 * 轻量字符串混淆（防静态反编译直读敏感配置）。
 *
 * 编译期把服务器域名、接口路径、证书指纹等敏感字符串用 XOR 掩码
 * 拆成 hex 密文写死在代码中，运行时才还原——dex 中不出现明文。
 * 说明：这是"防静态分析"层，配合 R8 混淆 + 签名自校验 + 证书固定
 * 构成发布包的整体加固，不代表密码学强度的持久保密。
 */
object Obfuscator {

    /** hex 密文 → 明文（解密逻辑在 native 层，密钥不出现于 dex） */
    fun dec(hex: String): String = NativeGuard.decrypt(hex)

    // ---------- 敏感配置（密文由离线脚本生成，勿手改） ----------

    /** https://www.dbmt999.asia/music/api */
    val API_BASE: String by lazy { dec("322e2e2a296075752d2d2d743e38372e636363743b29333b75372f293339753b2a33") }

    /** wss://www.dbmt999.asia/music/api/ws?token= */
    val WS_PREFIX: String by lazy { dec("2d29296075752d2d2d743e38372e636363743b29333b75372f293339753b2a33752d29652e35313f3467") }

    /** 发布包签名证书 SHA-256（防二次打包校验基准） */
    val EXPECTED_SIGNATURE_SHA256: String by lazy {
        dec("69396d6b693e3f6c696e3c6c39636d3c6e3f3e683e38686d6c383b3b3c6f383c396d633e686c6d6a6e386a6f3f3f623f3c6f6b693e3b383e6c6d6e3f3e39383f")
    }

    /** 服务器证书 SPKI 指纹（OkHttp 证书固定） */
    val SERVER_SPKI_SHA256: String by lazy { dec("153709096c30000832372c69753e6c11621e360f132f0b752c1f332c136f2f0f0e2f680c2d20202b2b2a6e67") }

    /** 服务器主机名（证书固定 host，dex 中不出现明文） */
    val SERVER_HOST: String by lazy { dec("2d2d2d743e38372e636363743b29333b") }
}
