import SwiftUI
import WebKit

/// 通用 WebView 页面：主色导航栏（返回 + 居中网页标题）+ WKWebView 内嵌网页。
///
/// 用途：用户协议 / 隐私政策 / 个人信息收集清单 / 应用权限清单 / 绑定邮箱等
/// 需要在 App 内打开的 H5 页面。标题实时显示所访问网页的 `<title>`。
/// - 登录 token 自动附加到 URL 的 `?token=` 参数（线上 H5 约定：优先读取 URL 参数，其次读 APP JS 桥）
/// - 注入 `window.close` 桥接：网页调用 `window.close()`（如绑定成功后自动跳回）时关闭本页
struct WebViewPageView: View {
    @Environment(\.dismiss) private var dismiss

    /// 目标网页 URL（不含 token 参数，token 自动附加）
    let url: URL
    /// 初始标题（网页标题加载前显示，加载后自动替换为网页 <title>）
    var title: String = ""
    /// 附加到 URL 的登录 token（可选）
    var token: String?

    @State private var pageTitle: String

    init(url: URL, title: String = "", token: String? = nil) {
        self.url = url
        self.title = title
        self.token = token
        _pageTitle = State(initialValue: title)
    }

    /// 主色导航栏上的深色文字（薄荷绿主色上保证对比度）
    private var navForeground: Color { Color(hex: 0x0F2E24) }

    private var topInset: CGFloat {
        (UIApplication.shared.connectedScenes.first as? UIWindowScene)?
            .keyWindow?.safeAreaInsets.top ?? 20
    }

    var body: some View {
        ZStack {
            Theme.background.ignoresSafeArea()

            VStack(spacing: 0) {
                header
                WebViewPage(url: url, token: token) {
                    NavBack.goBack(dismiss)
                } onTitleChange: { t in
                    pageTitle = t
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .navigationBarHidden(true)
        .onAppear { NavBack.enableSwipeBack() }
    }

    /// 导航栏：45 高，主色背景，左侧返回键，中间标题（网页标题）
    private var header: some View {
        HStack(spacing: 0) {
            // 返回键
            Button {
                NavBack.goBack(dismiss)
            } label: {
                Image(systemName: "chevron.left")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundColor(navForeground)
                    .frame(width: 38, height: 38)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            Spacer(minLength: 0)

            // 中间标题：显示访问网页的标题（未加载出来前显示初始标题）
            Text(pageTitle)
                .font(.system(size: 16, weight: .semibold))
                .foregroundColor(navForeground)
                .lineLimit(1)
                .truncationMode(.tail)

            Spacer(minLength: 0)

            // 右侧对称占位，保证标题真正居中
            Color.clear
                .frame(width: 38, height: 38)
        }
        .padding(.horizontal, 12)
        .frame(height: 45)
        .padding(.top, topInset)
        .background(Theme.accent)
    }
}

// MARK: - WKWebView 包装

/// WKWebView 包装：加载 URL（自动附加 token）+ 网页标题回调 + `window.close` 桥接。
private struct WebViewPage: UIViewRepresentable {
    let url: URL
    let token: String?
    let onClose: () -> Void
    let onTitleChange: (String) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(onClose: onClose, onTitleChange: onTitleChange)
    }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()

        // window.close 桥：H5（如绑定邮箱成功）调用 window.close() 时通知原生关闭本页
        let closeJS = """
        if (!window.__iosClosePatched) {
            window.__iosClosePatched = true;
            window.close = function() {
                window.webkit.messageHandlers.iosClose.postMessage(null);
            };
        }
        """
        let script = WKUserScript(source: closeJS,
                                  injectionTime: .atDocumentStart,
                                  forMainFrameOnly: true)
        config.userContentController.addUserScript(script)
        config.userContentController.add(context.coordinator, name: "iosClose")
        config.allowsInlineMediaPlayback = true

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.backgroundColor = UIColor(Theme.background)
        webView.isOpaque = false
        webView.navigationDelegate = context.coordinator
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        LogReporter.shared.checkMain("WebViewPage.updateUIView")
        var finalURL = url
        if let token, !token.isEmpty {
            // 附加 token 参数（H5 约定：优先读 ?token=；页面已有 token 参数则不覆盖）
            var components = URLComponents(url: url, resolvingAgainstBaseURL: false)
            if components?.queryItems?.contains(where: { $0.name == "token" }) != true {
                var items = components?.queryItems ?? []
                items.append(URLQueryItem(name: "token", value: token))
                components?.queryItems = items
                if let u = components?.url { finalURL = u }
            }
        }
        // 避免父视图重建导致重复加载
        if webView.url?.absoluteString != finalURL.absoluteString {
            webView.load(URLRequest(url: finalURL))
        }
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKScriptMessageHandler {
        let onClose: () -> Void
        let onTitleChange: (String) -> Void
        init(onClose: @escaping () -> Void, onTitleChange: @escaping (String) -> Void) {
            self.onClose = onClose
            self.onTitleChange = onTitleChange
        }

        func userContentController(_ userContentController: WKUserContentController,
                                   didReceive message: WKScriptMessage) {
            guard message.name == "iosClose" else { return }
            DispatchQueue.main.async { self.onClose() }
        }

        // 网页加载完成后读取 <title> 更新导航栏标题
        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            if let t = webView.title, !t.isEmpty {
                DispatchQueue.main.async { self.onTitleChange(t) }
            }
        }
        // 页面跳转（如 H5 内点击链接）后标题也会变化
        func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
            if let t = webView.title, !t.isEmpty {
                DispatchQueue.main.async { self.onTitleChange(t) }
            }
        }
    }
}
