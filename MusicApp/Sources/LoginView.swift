import SwiftUI

/// 邮箱验证码登录 / 注册页：未登录用户启动后看到。
/// 登录/注册合一：输入邮箱+验证码，后端自动判断新用户注册/老用户登录；
/// 成功后自动写入 Keychain 直接进首页。
struct LoginView: View {
    @EnvironmentObject private var auth: AuthManager

    @State private var email = ""
    @State private var code = ""
    @State private var busy = false
    @State private var error: String?
    @State private var countdown = 0
    @State private var timer: Timer?
    @State private var agreed = false
    @State private var webPage: WebPageTarget?

    /// 协议页目标（登录页无 NavigationStack，用全屏内嵌浏览器打开）
    private enum WebPageTarget: Int, Identifiable {
        case userAgreement, privacyPolicy
        var id: Int { rawValue }
        var url: URL {
            switch self {
            case .userAgreement:
                return URL(string: "https://www.dbmt999.asia/user-agreement.html")!
            case .privacyPolicy:
                return URL(string: "https://www.dbmt999.asia/privacy-policy.html")!
            }
        }
        var title: String {
            switch self {
            case .userAgreement: return "用户协议"
            case .privacyPolicy: return "隐私政策"
            }
        }
    }

    private var canSubmit: Bool {
        !busy
            && email.contains("@")
            && code.count == 6
            && agreed
    }

    private var canSendCode: Bool {
        !busy && email.contains("@") && countdown == 0
    }

    var body: some View {
        ZStack {
            Theme.background.ignoresSafeArea()

            ScrollView {
                VStack(spacing: 16) {
                    Image("AppLogo")
                        .resizable()
                        .scaledToFit()
                        .frame(width: 76, height: 76)
                        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))

                    Text("流萤音乐")
                        .font(.system(size: 24, weight: .bold))
                        .foregroundColor(Theme.textPrimary)

                    Text("登录 / 注册")
                        .font(.system(size: 14))
                        .foregroundColor(Theme.textSecondary)

                    field("邮箱地址", text: $email)
                        .keyboardType(.emailAddress)

                    HStack(spacing: 10) {
                        field("验证码", text: $code)
                            .keyboardType(.numberPad)
                            .frame(maxWidth: .infinity)

                        Button {
                            Task { await sendCode() }
                        } label: {
                            HStack(spacing: 6) {
                                if busy {
                                    ProgressView().tint(.white)
                                }
                                Text(countdown > 0 ? "\(countdown)s" : "发送验证码")
                                    .font(.system(size: 14, weight: .medium))
                            }
                            .foregroundColor(.white)
                            .padding(.horizontal, 16)
                            .padding(.vertical, 12)
                            .background(canSendCode ? Theme.accent : Theme.card,
                                        in: RoundedRectangle(cornerRadius: 12))
                        }
                        .disabled(!canSendCode)
                        .buttonStyle(.plain)
                    }

                    // 协议勾选（验证码栏下方、登录按钮上方）
                    HStack(alignment: .top, spacing: 8) {
                        // 圆形勾选框
                        Image(systemName: agreed ? "checkmark.circle.fill" : "circle")
                            .font(.system(size: 18))
                            .foregroundColor(agreed ? Theme.accent : Theme.textSecondary)
                            .onTapGesture {
                                agreed.toggle()
                            }
                            .padding(.top, 1)

                        // 一行：「我已阅读并同意《用户协议》《隐私政策》」协议可点击
                        HStack(spacing: 0) {
                            Text("我已阅读并同意")
                                .font(.system(size: 12))
                                .foregroundColor(Theme.textSecondary)
                            Text("《用户协议》")
                                .font(.system(size: 12))
                                .foregroundColor(Theme.accent)
                                .onTapGesture { webPage = .userAgreement }
                            Text("《隐私政策》")
                                .font(.system(size: 12))
                                .foregroundColor(Theme.accent)
                                .onTapGesture { webPage = .privacyPolicy }
                        }

                        Spacer()
                    }

                    Button {
                        Task { await submit() }
                    } label: {
                        HStack(spacing: 8) {
                            if busy {
                                ProgressView().tint(.white)
                            } else {
                                Text("登录 / 注册")
                                    .fontWeight(.semibold)
                            }
                        }
                        .foregroundColor(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .background(Theme.accent, in: RoundedRectangle(cornerRadius: 14))
                    }
                    .disabled(!canSubmit)
                    .buttonStyle(.plain)

                    if let error {
                        Text(error)
                            .font(.footnote)
                            .foregroundColor(.red)
                    }
                }
                .padding(24)
                .padding(.top, 36)
            }
        }
        .onDisappear {
            timer?.invalidate()
        }
        // 协议页：App 内嵌浏览器全屏打开（标题显示网页标题）
        .fullScreenCover(item: $webPage) { target in
            WebViewPageView(url: target.url, title: target.title)
        }
    }

    // MARK: - 发送验证码

    private func sendCode() async {
        busy = true
        error = nil
        do {
            try await API.sendEmailCode(email: email.trimmingCharacters(in: .whitespaces).lowercased())
            // 开始倒计时
            countdown = 60
            timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { t in
                if countdown > 0 {
                    countdown -= 1
                } else {
                    t.invalidate()
                }
            }
        } catch {
            self.error = error.localizedDescription
        }
        busy = false
    }

    // MARK: - 提交

    private func submit() async {
        busy = true
        error = nil
        do {
            try await auth.loginEmail(
                email: email.trimmingCharacters(in: .whitespaces).lowercased(),
                code: code
            )
        } catch {
            self.error = error.localizedDescription
        }
        busy = false
    }

    private func field(_ title: String, text: Binding<String>) -> some View {
        TextField(title, text: text)
            .textInputAutocapitalization(.never)
            .disableAutocorrection(true)
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
            .background(Theme.card, in: RoundedRectangle(cornerRadius: 12))
            .foregroundColor(Theme.textPrimary)
    }
}
