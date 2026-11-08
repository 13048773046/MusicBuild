import Foundation

/// 轻量字符串混淆（防静态反编译直读敏感配置）。
///
/// 服务器域名、接口路径等敏感字符串编译期为 XOR 掩码后的 hex 密文，
/// 运行时才还原——二进制中不出现明文。掩码与 Android 端 Obfuscator 一致（0x5A）。
/// 说明：这是"防静态分析"层，配合 Release 符号剥离 + 反调试 + 签名自校验
/// 构成发布包整体加固。
enum Obfuscator {

    private static let key: UInt8 = 0x5A

    /// hex 密文 → 明文
    static func dec(_ hex: String) -> String {
        var out = ""
        var i = hex.startIndex
        while i < hex.endIndex {
            let next = hex.index(i, offsetBy: 2)
            guard next <= hex.endIndex, let byte = UInt8(hex[i..<next], radix: 16) else { break }
            out.append(Character(UnicodeScalar(byte ^ key)))
            i = next
        }
        return out
    }

    // ---------- 敏感配置（密文与 Android 端同源生成，勿手改） ----------

    /// https://www.dbmt999.asia/music/api
    static let apiBase: String = dec("322e2e2a296075752d2d2d743e38372e636363743b29333b75372f293339753b2a33")
}
