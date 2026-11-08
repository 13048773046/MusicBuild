import Foundation

/// 发布包自防护（仅 Release 生效，DEBUG 构建跳过以免影响开发调试）：
/// 1) 反调试——ptrace(PT_DENY_ATTACH) 拒绝被调试器附加；
/// 2) 越狱/注入环境检测——常见越狱路径/写入测试，越狱机（含注入框架）被拦截。
/// 注：iOS 无公开的代码签名自校验 API（SecStaticCode/SecTask 均为 macOS 或私有），
///     签名完整性依赖系统侧保证，本层覆盖调试器与越狱注入两个主要攻击面。
enum AntiDebug {

    /// 启动入口：不通过返回 false，调用方终止业务初始化
    static func verify() -> Bool {
        #if DEBUG
        return true // debug 包不做自防护
        #else
        denyAttach()
        return !isJailbroken()
        #endif
    }

    /// ptrace(PT_DENY_ATTACH)：拒绝调试器附加（越狱/调试防护标配）
    private static func denyAttach() {
        #if !targetEnvironment(simulator)
        // PT_DENY_ATTACH = 31
        typealias PtraceFn = @convention(c) (Int32, Int32, Int32, Int32) -> Int32
        guard let handle = dlopen("/usr/lib/libSystem.B.dylib", RTLD_NOW),
              let sym = dlsym(handle, "ptrace") else { return }
        let ptrace = unsafeBitCast(sym, to: PtraceFn.self)
        _ = ptrace(31, 0, 0, 0)
        dlclose(handle)
        #endif
    }

    /// 越狱/注入环境检测（标准路径集合 + 沙盒外写入测试）
    private static func isJailbroken() -> Bool {
        // 1. 常见越狱与注入框架路径
        let markers = [
            "/Applications/Cydia.app",
            "/Applications/Sileo.app",
            "/Applications/Zebra.app",
            "/usr/sbin/sshd",
            "/usr/bin/ssh",
            "/usr/lib/libcycript.dylib",
            "/usr/lib/libsubstrate.dylib",
            "/usr/lib/substrate",
            "/Library/MobileSubstrate/MobileSubstrate.dylib",
            "/var/lib/apt",
            "/var/cache/apt",
            "/var/lib/cydia",
            "/bin/bash",
            "/etc/apt",
        ]
        for p in markers where FileManager.default.fileExists(atPath: p) {
            return true
        }
        // 2. 沙盒外写入测试：正常 App 无法写 /private，能写即越狱
        do {
            let probe = "/private/liuying_probe_" + UUID().uuidString
            try "probe".write(toFile: probe, atomically: true, encoding: .utf8)
            try? FileManager.default.removeItem(atPath: probe)
            return true
        } catch {
            // 写不进去 → 沙盒正常
        }
        return false
    }
}
