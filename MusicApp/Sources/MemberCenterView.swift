import SwiftUI
import UIKit

// MARK: - 会员中心（开通 / 查看会员状态）· 独立页面

/// 会员开通页（独立 push 页面）：高保真汽水音乐风格 · 精修版。
/// 顶部光晕大标题（白字+档位色点缀，保证对比度）→ 文字档位 tab（VIP 绿 / SVIP 金）
/// → 三档套餐卡（标签/大价格/天数/每日价胶囊）→ 套餐说明 → 支付说明 → 权益三宫格
/// → 常用工具四宫格 → 底部协议勾选 + 动态价格大按钮。
/// 支付：调 /api/pay/create 拿易支付收银台链接 → 跳浏览器 → 返回自动刷新。
/// 价格/时长后台可配，前端不写死；无虚构功能（无自动续费/金币抵扣）。
struct MemberCenterView: View {
    @EnvironmentObject private var auth: AuthManager
    @Environment(\.openURL) private var openURL
    @Environment(\.dismiss) private var dismiss

    @State private var plan = "svip"           // 默认选中 SVIP
    @State private var term = "month"          // 套餐：month 月卡 / quarter 季卡 / year 年卡
    @State private var agree = true            // 协议勾选
    @State private var busy = false
    @State private var alertMsg = ""
    @State private var showAlert = false
    @State private var showPayWeb = false
    @State private var payWebURL: URL?
    @State private var pricing = API.MemberPricing()   // 价格/时长：后台可配，前端不写死

    private var vipColors: [Color] { [Color(hex: 0x1B5E3C), Color(hex: 0x2ECC8F)] }
    private var svipColors: [Color] { [Color(hex: 0x7A5E1F), Color(hex: 0xE3C25B)] }
    private var currentColors: [Color] { plan == "svip" ? svipColors : vipColors }
    private var currentAccent: Color { currentColors.last ?? Theme.accent }

    private var tier: API.MemberPricing.Tier { pricing.tier(plan: plan, term: term) }
    private var priceText: String { String(format: "¥%.0f", tier.price) }
    private var perDayText: String {
        tier.days > 0 ? String(format: "%.2f 元/天", tier.price / Double(tier.days)) : "配置中"
    }

    private func openPay() {
        if !agree {
            alertMsg = "请先阅读并同意《会员服务协议》"
            showAlert = true
            return
        }
        guard !busy else { return }
        busy = true
        Task {
            do {
                let r = try await API.payCreate(plan: plan, term: term)
                busy = false
                guard let urlStr = r.payUrl, !urlStr.isEmpty, let url = URL(string: urlStr) else {
                    alertMsg = r.note ?? "支付未启用，请稍后再试"
                    showAlert = true
                    return
                }
                // 支付改在内置浏览器完成：弹出 App 内 WebView（主色头部+返回键），
                // 关闭后自动刷新会员状态（见 .sheet onDismiss）
                payWebURL = url
                showPayWeb = true
            } catch {
                busy = false
                alertMsg = "下单失败，请检查网络后重试"
                showAlert = true
            }
        }
    }

    var body: some View {
        ZStack {
            // 深色底
            LinearGradient(colors: [Color(hex: 0x0B0D10), Color(hex: 0x0E1016)],
                           startPoint: .top, endPoint: .bottom)
                .ignoresSafeArea()
            // 顶部档位色渐变（延伸到状态栏，所有机型随安全区自适应）
            LinearGradient(colors: [currentAccent.opacity(0.14),
                                    currentAccent.opacity(0.05),
                                    currentAccent.opacity(0.0)],
                           startPoint: .top, endPoint: .bottom)
                .frame(height: 520)
                .frame(maxWidth: .infinity)
                .ignoresSafeArea()
                .allowsHitTesting(false)
            ScrollView(showsIndicators: false) {
                VStack(spacing: 0) {
                    // 1. 大标题区（白色主字 + 档位色点缀，深底上保证可读）
                    VStack(alignment: .leading, spacing: 10) {
                        Text(plan == "svip" ? "即刻成为 超级会员" : "超低价开通 流萤会员")
                            .font(.system(size: 26, weight: .heavy, design: .rounded))
                            .foregroundColor(.white)
                            .shadow(color: .black.opacity(0.35), radius: 6, y: 2)
                            .lineLimit(1)
                            .minimumScaleFactor(0.7)
                        HStack(spacing: 8) {
                            Text("新用户低至")
                                .font(.system(size: 13))
                                .foregroundColor(.white.opacity(0.55))
                            Text(perDayText)
                                .font(.system(size: 14, weight: .bold))
                                .foregroundColor(currentAccent)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 20)
                    .padding(.top, 14)
                    .padding(.bottom, 24)

                    // 2. 档位分段控件（VIP 绿 / SVIP 金，胶囊式精致切换）
                    HStack(spacing: 8) {
                        planSegment(plan: "vip", name: "VIP", desc: "享歌曲畅听", colors: vipColors)
                        planSegment(plan: "svip", name: "SVIP", desc: "享免广告·专属标识", colors: svipColors)
                    }
                    .padding(.horizontal, 20)
                    .padding(.bottom, 16)

                    // 3. 三档套餐卡（月/季/年）
                    HStack(alignment: .top, spacing: 12) {
                        termCard(term: "month", name: "月卡")
                        termCard(term: "quarter", name: "季卡")
                        termCard(term: "year", name: "年卡", recommended: true)
                    }
                    .padding(.horizontal, 20)

                    // 4. 套餐说明（紧凑小字）
                    HStack(spacing: 5) {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.system(size: 10))
                            .foregroundColor(currentAccent)
                        Text("会员到期后自动恢复普通权益，可随时重新开通")
                            .font(.system(size: 10.5))
                            .foregroundColor(.white.opacity(0.45))
                        Spacer()
                    }
                    .padding(.horizontal, 20)
                    .padding(.top, 12)

                    // 5. 支付说明（信息 + 右侧绿色标识）
                    HStack {
                        Text("支付由易支付提供，开通即时到账")
                            .font(.system(size: 11.5))
                            .foregroundColor(.white.opacity(0.55))
                        Spacer()
                        Text("官方认证")
                            .font(.system(size: 10.5, weight: .semibold))
                            .foregroundColor(currentAccent)
                    }
                    .padding(.horizontal, 20)
                    .padding(.vertical, 12)
                    .background(Color.white.opacity(0.035))
                    .padding(.top, 6)

                    // 6. 专属权益三宫格
                    VStack(alignment: .leading, spacing: 12) {
                        Text(plan == "svip" ? "SVIP 专属权益" : "VIP 基础权益")
                            .font(.system(size: 16, weight: .bold))
                            .foregroundColor(.white)
                        benefitGrid(plan: plan)
                    }
                    .padding(.horizontal, 20)
                    .padding(.top, 20)

                    // 7. 常用工具四宫格
                    VStack(alignment: .leading, spacing: 12) {
                        Text("常用工具")
                            .font(.system(size: 16, weight: .bold))
                            .foregroundColor(.white)
                        toolsGrid()
                    }
                    .padding(.horizontal, 20)
                    .padding(.top, 22)

                    Color.clear.frame(height: 30)
                }
            }
        }
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .navigationBarBackButtonHidden(true)
        .toolbarBackground(.hidden, for: .navigationBar)
        .toolbarBackground(.hidden, for: .tabBar)
        .toolbar {
            // 返回键统一样式：圆底 chevron，NavBack 可靠返回（与 App 其他页面一致）
            ToolbarItem(placement: .navigationBarLeading) {
                Button { NavBack.goBack(dismiss) } label: {
                    Circle()
                        .fill(.black.opacity(0.28))
                        .frame(width: 34, height: 34)
                        .overlay(
                            Image(systemName: "chevron.left")
                                .font(.system(size: 15, weight: .semibold))
                                .foregroundColor(.white)
                        )
                }
            }
        }
        .safeAreaInset(edge: .bottom) { bottomBar() }
        .alert("提示", isPresented: $showAlert) {
            Button("好的", role: .cancel) {
                if !busy { Task { await auth.refreshMemberStatus() } }
            }
        } message: { Text(alertMsg) }
        .onAppear {
            Task { await auth.refreshMemberStatus() }
            Task { pricing = await API.memberPricing() }
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.willEnterForegroundNotification)) { _ in
            Task { await auth.refreshMemberStatus() }
        }
        // 内置浏览器支付：关闭后自动刷新会员状态（等价原「跳浏览器返回自动刷新」）
        .sheet(isPresented: $showPayWeb, onDismiss: {
            Task { await auth.refreshMemberStatus() }
        }) {
            if let u = payWebURL {
                WebViewPageView(url: u, title: "会员支付")
            }
        }
    }

    // MARK: 档位分段控件（胶囊：选中项档位色渐变 + 白字 + 副标题）
    private func planSegment(plan: String, name: String, desc: String,
                             colors: [Color]) -> some View {
        let selected = self.plan == plan
        return VStack(spacing: 3) {
            Text(name)
                .font(.system(size: 20, weight: .heavy, design: .rounded))
                .foregroundColor(selected ? .white : .white.opacity(0.45))
                .lineLimit(1)
            Text(desc)
                .font(.system(size: 10))
                .foregroundColor(selected ? .white.opacity(0.9) : .white.opacity(0.35))
                .lineLimit(1)
                .minimumScaleFactor(0.75)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 11)
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(
                    LinearGradient(colors: selected
                                   ? [colors[0].opacity(0.85), colors[1].opacity(0.95)]
                                   : [Color.white.opacity(0.04)],
                                   startPoint: .topLeading, endPoint: .bottomTrailing)
                )
        )
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(selected ? colors[1].opacity(0.55) : Color.white.opacity(0.07),
                        lineWidth: selected ? 1 : 1)
        )
        .shadow(color: selected ? colors[1].opacity(0.28) : .clear, radius: 10, y: 3)
        .contentShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .onTapGesture { withAnimation(.easeOut(duration: 0.2)) { self.plan = plan } }
    }

    // MARK: 三档套餐卡（标签 + 大价格 + 天数 + 每日价胶囊）
    private func priceOf(_ t: String) -> String {
        String(format: "¥%.0f", pricing.tier(plan: plan, term: t).price)
    }
    private func daysOf(_ t: String) -> String {
        "\(pricing.tier(plan: plan, term: t).days) 天"
    }
    private func perDayOf(_ t: String) -> String {
        let t0 = pricing.tier(plan: plan, term: t)
        return t0.days > 0 ? String(format: "仅 %.2f 元/天", t0.price / Double(t0.days)) : "配置中"
    }
    private func termCard(term: String, name: String, recommended: Bool = false) -> some View {
        let selected = self.term == term
        return VStack(spacing: 9) {
            HStack(spacing: 5) {
                Text(name)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(.white.opacity(0.92))
                if recommended {
                    Text("推荐")
                        .font(.system(size: 8.5, weight: .bold))
                        .foregroundColor(.white)
                        .padding(.horizontal, 5).padding(.vertical, 1.5)
                        .background(currentAccent)
                        .clipShape(Capsule())
                }
            }
            .frame(height: 16)
            Text(priceOf(term))
                .font(.system(size: 30, weight: .heavy, design: .rounded))
                .foregroundColor(.white)
                .minimumScaleFactor(0.6)
                .lineLimit(1)
                .padding(.top, 2)
            Text(daysOf(term))
                .font(.system(size: 9.5))
                .foregroundColor(.white.opacity(0.4))
            Text(perDayOf(term))
                .font(.system(size: 9, weight: .semibold))
                .foregroundColor(selected ? currentAccent : currentAccent.opacity(0.65))
                .lineLimit(1)
                .minimumScaleFactor(0.7)
        }
        .padding(.vertical, 14)
        .padding(.horizontal, 6)
        .frame(maxWidth: .infinity)
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(
                    LinearGradient(colors: selected
                                   ? [currentAccent.opacity(0.28), Color.white.opacity(0.05)]
                                   : [Color.white.opacity(0.035)],
                                   startPoint: .topLeading, endPoint: .bottomTrailing)
                )
        )
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(LinearGradient(colors: selected
                                       ? [currentAccent.opacity(0.9), currentAccent.opacity(0.25)]
                                       : [Color.white.opacity(0.08)],
                                       startPoint: .topLeading, endPoint: .bottomTrailing),
                        lineWidth: selected ? 1.6 : 1)
        )
        .shadow(color: selected ? currentAccent.opacity(0.18) : .clear, radius: 10, y: 3)
        .contentShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .onTapGesture { withAnimation(.easeOut(duration: 0.2)) { self.term = term } }
    }

    // MARK: 权益三宫格（icon 圆底 + 标题 + 副标题，防截断）
    private func benefitGrid(plan: String) -> some View {
        let items: [(String, String, String)] = plan == "svip"
            ? [("nosign", "免广告", "免开屏广告"),
               ("crown.fill", "专属标识", "SVIP 金色徽章"),
               ("sparkles", "精准推送", "个性化推荐")]
            : [("crown.fill", "专属标识", "VIP 绿色徽章"),
               ("sparkles", "精准推送", "个性化推荐"),
               ("iphone", "双端畅听", "手机 / 电脑")]
        return HStack(spacing: 12) {
            ForEach(items, id: \.0) { item in
                VStack(spacing: 7) {
                    Image(systemName: item.0)
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundColor(currentAccent)
                        .frame(width: 38, height: 38)
                        .background(currentAccent.opacity(0.12), in: Circle())
                    Text(item.1)
                        .font(.system(size: 12.5, weight: .semibold))
                        .foregroundColor(.white.opacity(0.92))
                        .lineLimit(1)
                        .minimumScaleFactor(0.75)
                    Text(item.2)
                        .font(.system(size: 9.5))
                        .foregroundColor(.white.opacity(0.38))
                        .lineLimit(1)
                        .minimumScaleFactor(0.75)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 12)
                .background(RoundedRectangle(cornerRadius: 12, style: .continuous)
                                .fill(Color.white.opacity(0.035)))
            }
        }
    }

    // MARK: 常用工具四宫格（真实可用：说明/邮箱/官网/QQ群）
    private func toolsGrid() -> some View {
        let tools: [(String, String, () -> Void)] = [
            ("doc.text.fill", "会员说明", {
                self.alertMsg = "· 同时开通 VIP 与 SVIP 时，仅展示 SVIP 标识并按 SVIP 权益生效\n· 会员到期后自动恢复普通用户权益\n· 支付由易支付提供，开通即时到账"
                self.showAlert = true
            }),
            ("envelope.fill", "客服邮箱", {
                if let url = URL(string: "mailto:yosar027@outlook.com") { openURL(url) }
            }),
            ("globe", "官方网站", {
                if let url = URL(string: "https://www.dbmt999.asia") { openURL(url) }
            }),
            ("person.3.fill", "QQ 群", {
                UIPasteboard.general.string = "807910369"
                self.alertMsg = "QQ 群号 807910369 已复制，打开 QQ 搜索加群"
                self.showAlert = true
            }),
        ]
        return HStack(spacing: 12) {
            ForEach(tools.indices, id: \.self) { i in
                Button {
                    tools[i].2()
                } label: {
                    VStack(spacing: 7) {
                        Image(systemName: tools[i].0)
                            .font(.system(size: 15, weight: .semibold))
                            .foregroundColor(.white.opacity(0.85))
                            .frame(width: 38, height: 38)
                            .background(Color.white.opacity(0.06), in: Circle())
                        Text(tools[i].1)
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundColor(.white.opacity(0.85))
                            .lineLimit(1)
                            .minimumScaleFactor(0.75)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
                    .background(RoundedRectangle(cornerRadius: 12, style: .continuous)
                                    .fill(Color.white.opacity(0.028)))
                }
                .buttonStyle(.plain)
            }
        }
    }

    // MARK: 底部：协议勾选 + 动态价格大按钮
    private func bottomBar() -> some View {
        VStack(spacing: 11) {
            HStack(spacing: 6) {
                Image(systemName: agree ? "checkmark.square.fill" : "square")
                    .font(.system(size: 14))
                    .foregroundColor(agree ? currentAccent : .white.opacity(0.35))
                    .onTapGesture { agree.toggle() }
                Text("已阅读并同意《会员服务协议》")
                    .font(.system(size: 11))
                    .foregroundColor(.white.opacity(0.5))
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
                    .onTapGesture { agree.toggle() }
                Spacer()
            }
            .contentShape(Rectangle())
            Button {
                openPay()
            } label: {
                HStack(spacing: 10) {
                    if busy {
                        ProgressView().tint(.white)
                    } else {
                        Text("\(priceText) 立即开通 " + (plan == "svip" ? "SVIP" : "VIP"))
                            .font(.system(size: 16, weight: .heavy))
                            .lineLimit(1)
                            .minimumScaleFactor(0.75)
                    }
                    Spacer()
                    if !busy {
                        Text(perDayText)
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundColor(.white.opacity(0.85))
                            .lineLimit(1)
                            .minimumScaleFactor(0.75)
                    }
                }
                .padding(.horizontal, 18)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
                .background(
                    // 档位色按钮：VIP 绿色渐变 / SVIP 金黄色渐变（顶部高光 + 底部光晕）
                    LinearGradient(colors: plan == "svip"
                                   ? [Color(hex: 0xB98A2E), Color(hex: 0xE3C25B)]
                                   : [Color(hex: 0x0F9D58), Color(hex: 0x2ECC8F)],
                                   startPoint: .top, endPoint: .bottom),
                    in: RoundedRectangle(cornerRadius: 18, style: .continuous)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 18, style: .continuous)
                        .stroke(
                            LinearGradient(colors: [.white.opacity(0.45), .white.opacity(0.12)],
                                           startPoint: .top, endPoint: .bottom),
                            lineWidth: 0.7
                        )
                )
                .foregroundColor(.white)
                .shadow(color: (plan == "svip" ? Color(hex: 0xE3C25B) : Color(hex: 0x2ECC8F)).opacity(0.32),
                        radius: 14, y: 5)
            }
            .disabled(busy)
        }
        .padding(.horizontal, 20)
        .padding(.top, 10)
        .padding(.bottom, 8)
        .background(
            LinearGradient(colors: [Color(hex: 0x0B0D10).opacity(0.0), Color(hex: 0x0B0D10).opacity(0.98)],
                           startPoint: .top, endPoint: .bottom)
                .ignoresSafeArea(edges: .bottom)
        )
    }
}
