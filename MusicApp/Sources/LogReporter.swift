import Foundation
import Darwin
import UIKit

/// App 日志 / 崩溃自动上报：
/// - 未捕获 NSException（崩溃）→ 写本地文件；崩溃瞬间网络往往来不及，由下次启动补报。
/// - signal 级捕获（SIGABRT/SIGSEGV/SIGBUS/SIGILL/SIGFPE）→ 覆盖 Swift 崩溃
///   （数组越界、强制解包、野指针等不走 NSException handler 的场景），同步 POSIX 写盘。
/// - 支持主动记录 error/warn/info 日志，落盘后批量上报。
/// - 上报接口：POST {base}api/log-report（服务器写入 app_logs 表，管理端可查）。
/// 全局预生成 C 字符串（signal handler 安装期写入，崩溃时只读；UnsafeMutablePointer 为 trivial 值类型）
private var g_sigPathABRT: UnsafeMutablePointer<CChar>? = nil
private var g_sigPathSEGV: UnsafeMutablePointer<CChar>? = nil
private var g_sigPathBUS: UnsafeMutablePointer<CChar>? = nil
private var g_sigPathILL: UnsafeMutablePointer<CChar>? = nil
private var g_sigPathFPE: UnsafeMutablePointer<CChar>? = nil
private var g_sigContentABRT: UnsafeMutablePointer<CChar>? = nil
private var g_sigContentSEGV: UnsafeMutablePointer<CChar>? = nil
private var g_sigContentBUS: UnsafeMutablePointer<CChar>? = nil
private var g_sigContentILL: UnsafeMutablePointer<CChar>? = nil
private var g_sigContentFPE: UnsafeMutablePointer<CChar>? = nil

/// Swift 崩溃信号处理：无捕获闭包（可转 C 函数指针），仅 POSIX async-signal-safe 操作；
/// 按信号号 switch 到预生成的全局 C 字符串（不捕获局部变量）

final class LogReporter {

    static let shared = LogReporter()

    private let logsDir: URL
    private let queue = DispatchQueue(label: "log-reporter", qos: .utility)
    private var installed = false

    private init() {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        logsDir = base.appendingPathComponent("Logs", isDirectory: true)
        try? FileManager.default.createDirectory(at: logsDir, withIntermediateDirectories: true)
    }

    /// 注册崩溃捕获 + 启动补报（App 启动时调用一次）
    func start() {
        guard !installed else { return }
        installed = true
        // 未捕获 NSException（覆盖 Swift/ObjC 未捕获异常）
        NSSetUncaughtExceptionHandler { exception in
            let stack = exception.callStackSymbols.joined(separator: "\n")
            let msg = "\(exception.name.rawValue): \(exception.reason ?? "")"
            LogReporter.shared.persistNow(level: "crash", tag: "uncaught", message: msg, stack: stack)
            // 崩溃瞬间网络往往来不及发出，交给下次启动补报
        }
        // signal 级捕获：Swift 崩溃（数组越界/强制解包/野指针/BAD_ACCESS）不走 NSException handler，
        // 以 SIGABRT/SIGSEGV 等信号终止进程。signal handler 内只做 async-signal-safe 的 POSIX 写盘。
        installSignalHandlers()
        // 每次回前台补报一次：崩溃落盘后不一定立刻重启 App（如后台切换），
        // 回前台即尝试上报，比「仅下次冷启动补报」更快到达服务器。
        NotificationCenter.default.addObserver(
            forName: UIApplication.didBecomeActiveNotification,
            object: nil, queue: .main) { [weak self] _ in
            self?.queue.async { self?.flushAll() }
        }
        // 启动补报上次未上行的日志
        queue.async { [weak self] in self?.flushAll() }
    }

    /// signal 级崩溃捕获（async-signal-safe：open/write/close 写盘，纯 C 实现）
    /// 实现要点：路径与内容字符串在安装阶段用 Foundation 预生成并转成 C 字符串存入全局；
    /// handler 为 @convention(c) 全局函数（无捕获），体内仅调用 async-signal-safe 的 POSIX 函数。
    private func installSignalHandlers() {
        let dir = logsDir.path
        let signals: [Int32] = [SIGABRT, SIGSEGV, SIGBUS, SIGILL, SIGFPE]
        for sig in signals {
            let pathStr = dir + "/log_sig_\(sig).txt"
            let contentStr = "LEVEL=crash\nTAG=signal\nMSG=signal \(sig)\nSTACK=swift-signal-\(sig)\n"
            let pathPtr = UnsafeMutablePointer<CChar>.allocate(capacity: 1024)
            if let c = (pathStr as NSString).utf8String { strncpy(pathPtr, c, 1023) }
            pathPtr[1023] = 0
            let contentPtr = UnsafeMutablePointer<CChar>.allocate(capacity: 512)
            if let c = (contentStr as NSString).utf8String { strncpy(contentPtr, c, 511) }
            contentPtr[511] = 0
            switch sig {
            case SIGABRT: g_sigPathABRT = pathPtr; g_sigContentABRT = contentPtr
            case SIGSEGV: g_sigPathSEGV = pathPtr; g_sigContentSEGV = contentPtr
            case SIGBUS:  g_sigPathBUS = pathPtr;  g_sigContentBUS = contentPtr
            case SIGILL:  g_sigPathILL = pathPtr;  g_sigContentILL = contentPtr
            default:      g_sigPathFPE = pathPtr;  g_sigContentFPE = contentPtr
            }
            // 无捕获闭包 → 可直接作为 C 函数指针（全局变量访问不算捕获）
            signal(sig) { s in
                let pathPtr: UnsafeMutablePointer<CChar>?
                let contentPtr: UnsafeMutablePointer<CChar>?
                switch s {
                case SIGABRT: pathPtr = g_sigPathABRT; contentPtr = g_sigContentABRT
                case SIGSEGV: pathPtr = g_sigPathSEGV; contentPtr = g_sigContentSEGV
                case SIGBUS:  pathPtr = g_sigPathBUS;  contentPtr = g_sigContentBUS
                case SIGILL:  pathPtr = g_sigPathILL;  contentPtr = g_sigContentILL
                default:      pathPtr = g_sigPathFPE;  contentPtr = g_sigContentFPE
                }
                guard let p = pathPtr, let c = contentPtr else {
                    // 未初始化（不应发生）：恢复默认并重触发，不吞崩溃
                    signal(s, SIG_DFL)
                    raise(s)
                    return
                }
                let fd = open(p, O_WRONLY | O_CREAT | O_APPEND, 0o644)
                if fd >= 0 {
                    _ = write(fd, c, strlen(c))
                    close(fd)
                }
                // 恢复默认信号处理并重新触发，保留系统崩溃行为（崩溃报告/日志）
                signal(s, SIG_DFL)
                raise(s)
            }
        }
    }

    /// 主动记录一条日志（error/warn/info），落盘后异步批量上报
    func log(level: String, tag: String, message: String, stack: String = "") {
        persistNow(level: level, tag: tag, message: message, stack: stack)
        queue.async { [weak self] in self?.flushAll() }
    }

    /// 主线程守卫（诊断用）：仅在「当前不在主线程」时上报一条 error，
    /// 用于定位线上反复出现的 `Call must be made on main thread` 崩溃
    /// （1.1.20 → 1.1.34 均有，栈落在 SwiftUI 布局期的 UIViewRepresentable）。
    /// 正常路径（主线程）零开销、零日志。
    func checkMain(_ tag: String) {
        guard !Thread.isMainThread else { return }
        let stack = Thread.callStackSymbols.prefix(12).joined(separator: "\n")
        log(level: "error", tag: "thread", message: "off-main: \(tag)", stack: stack)
    }

    /// 崩溃回调里同步落盘（线程安全：串行写文件）
    private func persistNow(level: String, tag: String, message: String, stack: String) {
        let name = "log_\(Int(Date().timeIntervalSince1970 * 1000))_\(UUID().uuidString.prefix(6)).txt"
        let url = logsDir.appendingPathComponent(name)
        let content = "TIME=\(Int(Date().timeIntervalSince1970))\n"
            + "LEVEL=\(level)\nTAG=\(tag)\nMSG=\(message)\nSTACK=\(stack)\n"
        try? content.write(to: url, atomically: true, encoding: .utf8)
    }

    /// 批量补报；网络失败即停，留下次启动
    private func flushAll() {
        let files = (try? FileManager.default.contentsOfDirectory(
            at: logsDir, includingPropertiesForKeys: nil))
            .flatMap { $0.sorted { $0.lastPathComponent < $1.lastPathComponent } } ?? []
        guard !files.isEmpty else { return }
        for url in files {
            guard let content = try? String(contentsOf: url, encoding: .utf8) else {
                try? FileManager.default.removeItem(at: url)
                continue
            }
            let level = value(in: content, key: "LEVEL=") ?? "info"
            let tag = value(in: content, key: "TAG=") ?? ""
            let msg = value(in: content, key: "MSG=") ?? ""
            let stack = content.components(separatedBy: "STACK=").dropFirst().joined(separator: "STACK=")
            if upload(level: level, tag: tag, message: msg, stack: stack) {
                try? FileManager.default.removeItem(at: url)
            } else {
                break
            }
        }
    }

    private func value(in content: String, key: String) -> String? {
        guard let range = content.range(of: key) else { return nil }
        let start = range.upperBound
        guard let end = content[start...].firstIndex(of: "\n") else {
            return String(content[start...])
        }
        return String(content[start..<end])
    }

    /// 单条上报：POST {base}log-report（base 已含 /api，勿再拼 api）
    private func upload(level: String, tag: String, message: String, stack: String) -> Bool {
        var url = API.base
        url.appendPathComponent("log-report")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 6
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let payload: [String: Any] = [
            "platform": "ios",
            "app_version": Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "",
            "device_id": DeviceInfo.deviceId,
            "level": String(level.prefix(8)),
            "tag": String(tag.prefix(64)),
            "message": String(message.prefix(8192)),
            "stack": String(stack.prefix(8192)),
        ]
        req.httpBody = (try? JSONSerialization.data(withJSONObject: payload)) ?? Data()
        let sem = DispatchSemaphore(value: 0)
        var ok = false
        let task = URLSession.shared.dataTask(with: req) { _, resp, _ in
            if let http = resp as? HTTPURLResponse, (200...299).contains(http.statusCode) {
                ok = true
            }
            sem.signal()
        }
        task.resume()
        _ = sem.wait(timeout: .now() + 8)
        return ok
    }
}
