import Foundation
import Network

/// Folia 网页包的极简本地 HTTP 服务。
///
/// 为什么不用 file://：WKWebView 在 file:// 下加载 ES Module 脚本会因 origin 为
/// null 触发 CORS 失败（Folia 的 React 应用是 ES Module 结构），因此用一个仅监听
/// 127.0.0.1 的本地 HTTP 服务伺服 App bundle 内的 `folia/` 网页包。
/// 仅支持 GET，只允许访问 folia 包内文件（严格前缀校验防路径穿越），无第三方依赖。
final class FoliaLocalServer {
    static let shared = FoliaLocalServer()

    private var listener: NWListener?
    private var port: UInt16 = 0
    private(set) var isRunning = false
    private let queue = DispatchQueue(label: "folia.local-server", qos: .userInitiated)

    /// folia 网页包在 bundle 内的根目录：<Bundle>/folia/
    /// 兼容旧包：若 folia/ 目录不存在（XcodeGen 扁平化构建时期），回退到 bundle 根目录
    private var foliaRoot: URL {
        let bundleURL = Bundle.main.bundleURL
        let foliaDir = bundleURL.appendingPathComponent("folia", isDirectory: true)
        if FileManager.default.fileExists(atPath: foliaDir.path) {
            return foliaDir
        }
        return bundleURL
    }

    /// 页面入口：http://127.0.0.1:<port>/host.html
    var baseURL: URL {
        URL(string: "http://127.0.0.1:\(port)")!
    }

    func startIfNeeded() {
        // 上一次启动失败/超时已清空 listener，此处允许重试
        guard !isRunning, listener == nil else { return }

        let params = NWParameters.tcp
        // 显式绑定回环地址：只监听 127.0.0.1 的随机端口。
        // ① 避免监听所有接口触发 iOS「本地网络」权限弹窗导致 listener 卡在 .waiting；
        // ② 与 baseURL（127.0.0.1）严格一致，WKWebView 必可达。
        params.requiredLocalEndpoint = NWEndpoint.hostPort(host: "127.0.0.1", port: .any)

        let l: NWListener
        do {
            l = try NWListener(using: params)
        } catch {
            assertionFailure("Folia local server start failed: \(error)")
            return
        }
        // 同步等待就绪：WKWebView 加载 URL 时必须已拿到实际端口（回环监听一般 <50ms）
        let ready = DispatchSemaphore(value: 0)
        l.stateUpdateHandler = { [weak self] state in
            switch state {
            case .ready:
                if let p = l.port { self?.port = p.rawValue }
                self?.isRunning = true
                ready.signal()
            case .waiting(let error):
                // 等待中不 signal：保持阻塞直至 ready / failed / 超时
                print("[folia-server] listener waiting: \(error)")
            case .failed(let error):
                self?.isRunning = false
                ready.signal()
                print("[folia-server] listener failed: \(error)")
            case .cancelled:
                ready.signal()
            default:
                break
            }
        }
        l.newConnectionHandler = { [weak self] conn in
            self?.handleConnection(conn)
        }
        l.start(queue: queue)
        listener = l
        let waitResult = ready.wait(timeout: .now() + 2.0)  // 回环启动极快；2s 兜底

        if waitResult == .timedOut || !isRunning || port == 0 {
            // 启动未就绪：彻底清理，允许下一次 startIfNeeded 重新创建，
            // 避免「listener 非 nil 但未就绪」导致永久失败。
            l.cancel()
            if listener === l { listener = nil }
            isRunning = false
            port = 0
            print("[folia-server] start failed/timed out (port=\(port)); retry on next call")
        }
    }

    /// 强制重启：供加载失败后的「重试」按钮使用（stop + start + 换新端口）。
    func restart() {
        stop()
        startIfNeeded()
    }

    func stop() {
        listener?.cancel()
        listener = nil
        isRunning = false
        port = 0
    }

    // MARK: - HTTP 处理

    private func handleConnection(_ conn: NWConnection) {
        conn.start(queue: queue)
        var buffer = Data()
        let headerTerminator = Data("\r\n\r\n".utf8)
        // 简单实现：收满请求头（\r\n\r\n）即处理；本地回环场景请求头单次即可到达
        func receiveMore() {
            conn.receive(minimumIncompleteLength: 1, maximumLength: 16 * 1024) { [weak self] data, _, isComplete, error in
                guard let self else {
                    conn.cancel()
                    return
                }
                if let data, !data.isEmpty {
                    buffer.append(data)
                }
                if buffer.range(of: headerTerminator) != nil || isComplete || error != nil {
                    let text = String(data: buffer, encoding: .utf8) ?? ""
                    self.respond(conn, requestText: text)
                } else {
                    receiveMore()
                }
            }
        }
        receiveMore()
    }

    private func respond(_ conn: NWConnection, requestText: String) {
        // 解析请求行：GET /host.html HTTP/1.1
        let lines = requestText.components(separatedBy: "\r\n")
        guard let requestLine = lines.first else {
            send(conn, status: 400, mime: "text/plain", body: "Bad Request")
            return
        }
        let parts = requestLine.split(separator: " ")
        guard parts.count >= 2, parts[0] == "GET", let rawPath = parts.dropFirst().first else {
            send(conn, status: 400, mime: "text/plain", body: "Bad Request")
            return
        }

        // 剥离 query string（?v=...），仅取路径部分；并做 URL 解码
        let rawPathString = String(rawPath)
        let pathNoQuery = rawPathString.components(separatedBy: "?").first ?? rawPathString
        let decodedPath = pathNoQuery.removingPercentEncoding ?? pathNoQuery
        let cleanPath = decodedPath.hasPrefix("/") ? String(decodedPath.dropFirst()) : decodedPath
        guard !cleanPath.isEmpty else {
            // 根路径：直接伺服 host.html（而非无效 302——无 Location 头的 302 WKWebView 视为错误）
            let hostURL = foliaRoot.appendingPathComponent("host.html")
            if let data = try? Data(contentsOf: hostURL) {
                send(conn, status: 200, mime: "text/html; charset=utf-8", body: data)
            } else {
                send(conn, status: 404, mime: "text/plain", body: "Not Found")
            }
            return
        }

        // 防路径穿越：标准化后必须仍位于 folia 根内
        let fileURL = foliaRoot.appendingPathComponent(cleanPath).standardizedFileURL
        guard fileURL.path.hasPrefix(foliaRoot.standardizedFileURL.path + "/") || fileURL == foliaRoot.standardizedFileURL else {
            send(conn, status: 403, mime: "text/plain", body: "Forbidden")
            return
        }
        guard let data = try? Data(contentsOf: fileURL) else {
            send(conn, status: 404, mime: "text/plain", body: "Not Found")
            return
        }
        send(conn, status: 200, mime: Self.mimeType(for: fileURL.pathExtension), body: data)
    }

    private func send(_ conn: NWConnection, status: Int, mime: String, body: Data) {
        let statusText: String
        switch status {
        case 200: statusText = "OK"
        case 302: statusText = "Found"
        case 400: statusText = "Bad Request"
        case 403: statusText = "Forbidden"
        default: statusText = "Not Found"
        }
        let reason = (200...299).contains(status) ? "OK" : statusText
        let header = "HTTP/1.1 \(status) \(reason)\r\n" +
            "Content-Type: \(mime)\r\n" +
            "Content-Length: \(body.count)\r\n" +
            // no-store：禁止 WKWebView 持久化缓存任何 Folia 资源。
            // （仅 no-cache 允许存储、仅要求重验证；WKWebView 对本地 server 响应
            //   的验证存在偏差，可能导致新包仍加载旧版 patch → 背景覆盖修复不生效。
            //   配合 host.html?v=126 版本化 URL 双保险。）
            "Cache-Control: no-store, no-cache, must-revalidate\r\n" +
            "Pragma: no-cache\r\n" +
            "Access-Control-Allow-Origin: *\r\n" +
            "Connection: close\r\n\r\n"
        var out = Data(header.utf8)
        out.append(body)
        // 发送完成后才关闭连接：NWConnection.send 是异步的，若在 send 未完成时
        // 立即 cancel，WKWebView 会收到被截断的响应（大 JS 文件必现加载失败）。
        conn.send(content: out, completion: .contentProcessed { _ in
            conn.cancel()
        })
    }

    private func send(_ conn: NWConnection, status: Int, mime: String, body: String) {
        send(conn, status: status, mime: mime, body: Data(body.utf8))
    }

    /// ES Module 要求正确的 JS MIME，否则 WKWebView 报 module 加载失败
    private static func mimeType(for ext: String) -> String {
        switch ext.lowercased() {
        case "html", "htm": return "text/html; charset=utf-8"
        case "js", "mjs": return "application/javascript; charset=utf-8"
        case "css": return "text/css; charset=utf-8"
        case "json", "map": return "application/json; charset=utf-8"
        case "png": return "image/png"
        case "jpg", "jpeg": return "image/jpeg"
        case "webp": return "image/webp"
        case "gif": return "image/gif"
        case "svg": return "image/svg+xml"
        case "woff": return "font/woff"
        case "woff2": return "font/woff2"
        case "ttf": return "font/ttf"
        case "wasm": return "application/wasm"
        case "ico": return "image/x-icon"
        default: return "application/octet-stream"
        }
    }
}
