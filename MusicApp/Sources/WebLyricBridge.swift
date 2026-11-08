import WebKit

/// Folia 网页渲染层 ↔ iOS 原生的消息桥接（WKScriptMessageHandler）。
///
/// Web → Native：网页通过 `window.webkit.messageHandlers.folia.postMessage(...)` 发送，
/// 消息体为 `{ "type": "ready" | "seek" | "mode" | "control", "payload": {...} }`。
/// Native → Web：见 FoliaWebPusher（evaluateJavaScript 调 `window.__foliaHost.receive(...)`）。
final class WebLyricBridge: NSObject, WKScriptMessageHandler {
    /// 网页渲染层 JS 就绪（此时才可推送 song/time）
    var onReady: (() -> Void)?
    /// 用户点击歌词行/网页进度跳转：payload.t（秒）
    var onSeek: ((Double) -> Void)?
    /// 用户在网页侧切换歌词模式：payload.mode（如 "diorama"）
    var onModeChange: ((String) -> Void)?
    /// 用户点击网页播放控制条：payload.action（play / pause / next / previous）
    var onControl: ((String) -> Void)?
    /// 网页侧切换沉浸（轻点舞台隐藏/显示控件）：payload.visible（false=隐藏进入沉浸）
    var onImmersive: ((Bool) -> Void)?

    func userContentController(
        _ userContentController: WKUserContentController,
        didReceive message: WKScriptMessage
    ) {
        guard message.name == "folia",
              let body = message.body as? [String: Any],
              let type = body["type"] as? String
        else { return }

        let payload = body["payload"] as? [String: Any]

        // WKScriptMessageHandler 回调默认在后台线程，但所有下游操作
        // （evaluateJavaScript / AVAudioEngine / SwiftUI 状态）都必须在主线程——
        // 不转主线程会抛 "Call must be made on main thread" 崩溃。
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            switch type {
            case "ready":
                self.onReady?()
            case "seek":
                if let t = payload?["t"] as? Double {
                    self.onSeek?(t)
                }
            case "mode":
                if let mode = payload?["mode"] as? String {
                    self.onModeChange?(mode)
                }
            case "control":
                if let action = payload?["action"] as? String {
                    self.onControl?(action)
                }
            case "immersive":
                if let visible = payload?["visible"] as? Bool {
                    self.onImmersive?(visible)
                }
            default:
                break
            }
        }
    }
}

/// 向网页侧推送消息（Native → Web）。
/// 消息用 JSON 序列化后嵌入 JS 调用：`window.__foliaHost.receive(<json>)`。
/// JSON 是合法 JS 对象字面量，可直接嵌入，无需额外转义。
enum FoliaWebPusher {
    static func push(to webView: WKWebView?, _ dict: [String: Any]) {
        LogReporter.shared.checkMain("FoliaWebPusher.push")
        guard let webView,
              let data = try? JSONSerialization.data(withJSONObject: dict),
              let json = String(data: data, encoding: .utf8) else { return }
        let js = "window.__foliaHost && window.__foliaHost.receive(\(json))"
        webView.evaluateJavaScript(js, completionHandler: nil)
    }

    /// 原生主动切换沉浸（顶栏隐藏/显示按钮）：调用补丁 JS 暴露的全局状态机，
    /// 与网页内轻点舞台共用同一沉浸状态，避免两套状态不同步。
    static func pushImmersive(to webView: WKWebView?, hidden: Bool) {
        LogReporter.shared.checkMain("FoliaWebPusher.pushImmersive")
        guard let webView else { return }
        let js = "window.__foliaPatch && window.__foliaPatch.setImmersive(\(hidden))"
        webView.evaluateJavaScript(js, completionHandler: nil)
    }
}
