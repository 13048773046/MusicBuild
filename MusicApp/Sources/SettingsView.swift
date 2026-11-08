import SwiftUI

// MARK: - 设置页

/// 设置页：从「我的」右上角齿轮进入。
/// 沉浸式自定义头部（与我的页一致风格），分组：账号 / 通用 / 关于 APP / 退出登录。
struct SettingsView: View {
    @EnvironmentObject private var auth: AuthManager
    @Environment(\.dismiss) private var dismiss

    @State private var showClearAlert = false
    @State private var showNoUpdate = false
    @State private var showUpdateSheet = false
    @State private var showLogoutConfirm = false
    @State private var showSwitchAccountSheet = false
    @State private var showVerifyApply = false
    @State private var showAlreadyVerified = false
    @State private var checkBusy = false
    @State private var updateCheck: API.UpdateCheck?

    private var appVersion: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0.0"
    }

    /// 会员状态文本（SVIP 优先；显示到期时间）
    private var memberText: String {
        guard let u = auth.currentUser else { return "未登录" }
        let expire = max(u.memberExpire ?? 0, u.svipExpire ?? 0)
        if u.member == "svip" && expire > 0 {
            return "SVIP · 有效期至 " + SettingsView.dateText(expire)
        }
        if u.member == "vip" && expire > 0 {
            return "VIP · 有效期至 " + SettingsView.dateText(expire)
        }
        return "未开通"
    }

    private static func dateText(_ ts: Double) -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: Date(timeIntervalSince1970: ts))
    }

    var body: some View {
        ZStack {
            Theme.background.ignoresSafeArea()

            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    header

                    VStack(alignment: .leading, spacing: 10) {
                        groupTitle("账号")
                        sectionCard {
                            navRow(icon: "person.crop.circle", tint: Theme.accent,
                                   title: "修改用户信息") {
                                ProfileEditView()
                                    .environmentObject(auth)
                            }
                            divider
                            navRow(icon: "lock", tint: Theme.sky,
                                   title: "修改密码") {
                                ChangePasswordView()
                                    .environmentObject(auth)
                            }
                        }
                    }

                    VStack(alignment: .leading, spacing: 10) {
                        groupTitle("会员与认证")
                        sectionCard {
                            // 会员状态：VIP绿/SVIP金；点击进入会员中心（独立页面，选档开通）
                            NavigationLink {
                                MemberCenterView()
                                    .environmentObject(auth)
                            } label: {
                                HStack(spacing: 12) {
                                    Image(systemName: "crown")
                                        .font(.system(size: 14, weight: .semibold))
                                        .foregroundColor(.yellow)
                                        .frame(width: 28, height: 28)
                                        .background(Color.yellow.opacity(0.14), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                                    Text("我的会员")
                                        .font(.system(size: 15))
                                        .foregroundColor(Theme.textPrimary)
                                    Spacer()
                                    if let u = auth.currentUser, !(u.member ?? "").isEmpty {
                                        MemberBadge(member: u.member)
                                        Text(memberText)
                                            .font(.system(size: 13))
                                            .foregroundColor(Theme.textTertiary)
                                    } else {
                                        Text(memberText)
                                            .font(.system(size: 13))
                                            .foregroundColor(Theme.textTertiary)
                                            .lineLimit(1)
                                            .minimumScaleFactor(0.7)
                                    }
                                    Image(systemName: "chevron.right")
                                        .font(.system(size: 12, weight: .semibold))
                                        .foregroundColor(Theme.textTertiary)
                                }
                            }
                            .padding(.horizontal, 16)
                            .padding(.vertical, 13)
                            .frame(minHeight: 44)
                            divider
                            // 认证申请入口（理由+类别提交，后台审核）：与 row() 对齐
                            // 已认证通过（verified 非空）时只提示，不进入申请页
                            Button {
                                if let u = auth.currentUser, !(u.verified ?? "").isEmpty {
                                    showAlreadyVerified = true
                                } else {
                                    showVerifyApply = true
                                }
                            } label: {
                                HStack(spacing: 12) {
                                    Image(systemName: "checkmark.seal")
                                        .font(.system(size: 14, weight: .semibold))
                                        .foregroundColor(Theme.sky)
                                        .frame(width: 28, height: 28)
                                        .background(Theme.sky.opacity(0.14), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                                    Text("认证申请")
                                        .font(.system(size: 15))
                                        .foregroundColor(Theme.textPrimary)
                                    Spacer()
                                    if let u = auth.currentUser, !(u.verified ?? "").isEmpty {
                                        VerifyBadge(verified: u.verified)
                                    } else {
                                        Text("未认证")
                                            .font(.system(size: 13))
                                            .foregroundColor(Theme.textTertiary)
                                    }
                                    Image(systemName: "chevron.right")
                                        .font(.system(size: 12, weight: .semibold))
                                        .foregroundColor(Theme.textTertiary)
                                }
                                .padding(.horizontal, 16)
                                .padding(.vertical, 13)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                        }
                    }

                    VStack(alignment: .leading, spacing: 10) {
                        groupTitle("通用")
                        sectionCard {
                            Button {
                                clearCache()
                            } label: {
                                row(icon: "trash", tint: .orange, title: "清理缓存")
                            }
                            .buttonStyle(.plain)
                            divider
                            Button {
                                Task { await checkUpdate() }
                            } label: {
                                row(icon: "arrow.clockwise.circle", tint: Theme.lilac,
                                    title: "检查更新",
                                    value: checkBusy ? "检查中…" : "v\(appVersion)",
                                    showChevron: !checkBusy)
                            }
                            .buttonStyle(.plain)
                            .disabled(checkBusy)
                        }
                    }

                    VStack(alignment: .leading, spacing: 10) {
                        groupTitle("关于")
                        sectionCard {
                            navRow(icon: "info.circle", tint: Theme.lilac,
                                   title: "关于 APP") {
                                AboutView()
                            }
                            divider
                            // 用户协议（App 内嵌浏览器打开）
                            navRow(icon: "doc.plaintext", tint: Theme.accent, title: "用户协议") {
                                WebViewPageView(url: URL(string: "https://www.dbmt999.asia/user-agreement.html")!,
                                                title: "用户协议")
                            }
                            divider
                            // 隐私政策
                            navRow(icon: "hand.raised", tint: Color(red: 1, green: 0.42, blue: 0.42),
                                   title: "隐私政策") {
                                WebViewPageView(url: URL(string: "https://www.dbmt999.asia/privacy-policy.html")!,
                                                title: "隐私政策")
                            }
                            divider
                            // 个人信息收集清单（《个人信息保护法》合规展示）
                            navRow(icon: "person.text.rectangle", tint: Color(red: 0.35, green: 0.78, blue: 0.55),
                                   title: "个人信息收集清单") {
                                WebViewPageView(url: URL(string: "https://www.dbmt999.asia/personal-info.html")!,
                                                title: "个人信息收集清单")
                            }
                            divider
                            // 应用权限清单（告知 APP 申请的系统权限与用途）
                            navRow(icon: "lock.shield", tint: .orange,
                                   title: "应用权限清单") {
                                WebViewPageView(url: URL(string: "https://www.dbmt999.asia/app-permission.html")!,
                                                title: "应用权限清单")
                            }
                            divider
                            // 开源许可：AGPL-3.0 等开源组件合规展示（Folia 歌词引擎等）
                            navRow(icon: "doc.text", tint: Theme.accent,
                                   title: "开源许可") {
                                OpenSourceLicensesView()
                            }
                        }
                    }

                    VStack(alignment: .leading, spacing: 10) {
                        groupTitle("登录")
                        sectionCard {
                            // 切换账号
                            Button {
                                showSwitchAccountSheet = true
                            } label: {
                                row(icon: "arrow.left.arrow.right", tint: Theme.accent,
                                    title: "切换账号", showChevron: false)
                            }
                            .buttonStyle(.plain)
                            divider
                            // 退出登录
                            Button {
                                showLogoutConfirm = true
                            } label: {
                                row(icon: "rectangle.portrait.and.arrow.right",
                                    tint: Color(hex: 0xFF6B6B),
                                    title: "退出登录", showChevron: false)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
                .padding(20)
                .padding(.bottom, 30)
            }
            .appBottomTabSpacing()
        }
        .navigationBarHidden(true)
        .onAppear { NavBack.enableSwipeBack() }
        .alert("缓存已清理", isPresented: $showClearAlert) {
            Button("好", role: .cancel) {}
        } message: {
            Text("封面图片缓存已释放，下次加载会自动重新缓存。")
        }
        .alert("已是最新版本", isPresented: $showNoUpdate) {
            Button("好", role: .cancel) {}
        } message: {
            Text("当前已是最新版本 v\(appVersion)。")
        }
        .sheet(isPresented: $showSwitchAccountSheet) {
            AccountSwitchSheet()
                .environmentObject(auth)
        }
        .sheet(isPresented: $showVerifyApply) {
            VerifyApplySheet()
                .environmentObject(auth)
        }
        .alert("认证已通过", isPresented: $showAlreadyVerified) {
            Button("好的", role: .cancel) {}
        } message: {
            Text("你已申请通过认证，无需重复申请。")
        }
        .alert("确认退出登录？", isPresented: $showLogoutConfirm) {
            Button("取消", role: .cancel) {}
            Button("退出", role: .destructive) {
                auth.logout()
            }
        } message: {
            Text("退出后本地登录状态将被清除，需要重新登录才能使用。")
        }
        .sheet(isPresented: $showUpdateSheet) {
            if let u = updateCheck {
                UpdateSheet(update: UpdateNotice(
                    id: u.id ?? "",
                    title: u.title ?? "发现新版本",
                    content: u.content ?? "",
                    version: u.version ?? "",
                    forceUpdate: u.forceUpdate ?? 0,
                    downloadUrl: u.downloadUrl ?? "",
                    downloadUrlIos: u.downloadUrlIos ?? ""
                ))
                .presentationDetents([.medium])
            }
        }
    }

    // MARK: - 头部（返回 + 大标题，与我的页一致）

    private var header: some View {
        HStack(spacing: 12) {
            Button {
                NavBack.goBack(dismiss)
            } label: {
                ZStack {
                    Circle().fill(Theme.card)
                    Image(systemName: "chevron.left")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundColor(Theme.textPrimary)
                }
                .frame(width: 38, height: 38)
            }
            .buttonStyle(.plain)

            VStack(alignment: .leading, spacing: 2) {
                Text("设置")
                    .font(.system(size: 30, weight: .bold))
                    .foregroundColor(Theme.textPrimary)
                Text("账号、通用与关于")
                    .font(.system(size: 12))
                    .foregroundColor(Theme.textSecondary)
            }
            Spacer()
        }
    }

    // MARK: - 分组与卡片

    private func groupTitle(_ title: String) -> some View {
        Text(title)
            .font(.system(size: 12, weight: .medium))
            .foregroundColor(Theme.textTertiary)
            .padding(.horizontal, 4)
    }

    /// 连续卡片容器：组内多行共用一张卡片，行间分隔线
    private func sectionCard<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        VStack(spacing: 0) {
            content()
        }
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous)
            .stroke(Theme.cardBorder, lineWidth: 1))
    }

    /// 行间分隔线（从图标后开始，避免顶到卡片边缘）
    private var divider: some View {
        Rectangle()
            .fill(Theme.cardBorder)
            .frame(height: 0.5)
            .frame(maxWidth: .infinity)
            .padding(.leading, 56)
    }

    /// 通用行
    private func row(icon: String, tint: Color, title: String,
                     value: String? = nil, showChevron: Bool = true) -> some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 14, weight: .semibold))
                .foregroundColor(tint)
                .frame(width: 28, height: 28)
                .background(tint.opacity(0.14), in: RoundedRectangle(cornerRadius: 8, style: .continuous))

            Text(title)
                .font(.system(size: 15))
                .foregroundColor(Theme.textPrimary)

            Spacer()

            if let value {
                Text(value)
                    .font(.system(size: 13))
                    .foregroundColor(Theme.textTertiary)
            }
            if showChevron {
                Image(systemName: "chevron.right")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(Theme.textTertiary.opacity(0.7))
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 13)
        .contentShape(Rectangle())
    }

    /// 可跳转子页的行
    private func navRow<Destination: View>(icon: String, tint: Color,
                                           title: String,
                                           @ViewBuilder destination: () -> Destination) -> some View {
        NavigationLink {
            destination()
        } label: {
            row(icon: icon, tint: tint, title: title)
        }
        .buttonStyle(.plain)
    }

    // MARK: - 逻辑

    /// 清理缓存：封面内存缓存 + 歌词缓存 + URL 会话缓存
    private func clearCache() {
        CoverImageCache.shared.clear()
        LyricsCache.shared.clear()
        URLCache.shared.removeAllCachedResponses()
        showClearAlert = true
    }

    /// 手动检查版本更新：有新版弹更新提示，无新版提示已是最新
    private func checkUpdate() async {
        checkBusy = true
        let result = await API.checkUpdate()
        checkBusy = false
        if let result, result.hasUpdate {
            updateCheck = result
            showUpdateSheet = true
        } else {
            showNoUpdate = true
        }
    }
}

// MARK: - 修改密码页

/// 修改密码：输入旧密码 + 新密码（6-64 位）+ 确认，调后端 change-password。
/// 沉浸式自定义头部 + 固定底部确认按钮（不被遮挡）。
struct ChangePasswordView: View {
    @EnvironmentObject private var auth: AuthManager
    @Environment(\.dismiss) private var dismiss

    @State private var oldPwd = ""
    @State private var newPwd = ""
    @State private var confirmPwd = ""
    @State private var busy = false
    @State private var error: String?
    @State private var showDone = false

    var body: some View {
        ZStack {
            Theme.background.ignoresSafeArea()

            VStack(spacing: 0) {
                // 固定头部：返回 + 标题 + 确认按钮（独立于内容层，与编辑资料页一致）
                header
                    .padding(.horizontal, 20)
                    .padding(.top, 10)
                    .padding(.bottom, 6)

                ScrollView {
                    VStack(spacing: 18) {
                        VStack(spacing: 0) {
                            secureField("当前密码", text: $oldPwd)
                            divider
                            secureField("新密码（6-64 位）", text: $newPwd)
                            divider
                            secureField("确认新密码", text: $confirmPwd)
                        }
                        .background(Theme.card, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous)
                            .stroke(Theme.cardBorder, lineWidth: 1))

                        if let error {
                            Text(error)
                                .font(.footnote)
                                .foregroundColor(Color(hex: 0xFF6B6B))
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.horizontal, 4)
                        }
                    }
                    .padding(20)
                }
                .appBottomTabSpacing()
            }
        }
        .navigationBarHidden(true)
        .onAppear { NavBack.enableSwipeBack() }
        .alert("密码已修改", isPresented: $showDone) {
            Button("好", role: .cancel) { NavBack.goBack(dismiss) }
        } message: {
            Text("下次登录请使用新密码。")
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            Button {
                NavBack.goBack(dismiss)
            } label: {
                ZStack {
                    Circle().fill(Theme.card)
                    Image(systemName: "chevron.left")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundColor(Theme.textPrimary)
                }
                .frame(width: 38, height: 38)
            }
            .buttonStyle(.plain)

            VStack(alignment: .leading, spacing: 2) {
                Text("修改密码")
                    .font(.system(size: 30, weight: .bold))
                    .foregroundColor(Theme.textPrimary)
                Text("设置新的登录密码")
                    .font(.system(size: 12))
                    .foregroundColor(Theme.textSecondary)
            }
            Spacer()

            // 确认修改按钮：右上角，与返回按钮同一行（与编辑资料页保存按钮一致）
            Button {
                Task { await save() }
            } label: {
                Group {
                    if busy {
                        ProgressView().tint(Theme.accent)
                    } else {
                        Text("确认")
                            .font(.system(size: 16, weight: .semibold))
                            .foregroundColor(Theme.accent)
                    }
                }
                .frame(minWidth: 52, minHeight: 38)
                .contentShape(Rectangle())
            }
            .disabled(busy)
            .buttonStyle(.plain)
        }
    }

    private var divider: some View {
        Rectangle()
            .fill(Theme.cardBorder)
            .frame(height: 0.5)
            .frame(maxWidth: .infinity)
            .padding(.leading, 16)
    }

    private func secureField(_ title: String, text: Binding<String>) -> some View {
        SecureField(title, text: text)
            .textInputAutocapitalization(.never)
            .disableAutocorrection(true)
            .padding(.horizontal, 16)
            .padding(.vertical, 14)
            .background(Theme.card)
            .foregroundColor(Theme.textPrimary)
    }

    private func save() async {
        let new = newPwd.trimmingCharacters(in: .whitespaces)
        if oldPwd.isEmpty {
            error = "请输入当前密码"
            return
        }
        if new.count < 6 || new.count > 64 {
            error = "新密码长度需 6-64 位"
            return
        }
        if new != confirmPwd {
            error = "两次输入的新密码不一致"
            return
        }
        busy = true
        error = nil
        do {
            try await auth.changePassword(old: oldPwd, new: new)
            showDone = true
        } catch {
            self.error = error.localizedDescription
        }
        busy = false
    }
}
