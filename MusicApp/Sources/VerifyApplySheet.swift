import SwiftUI

// MARK: - 认证申请（2026-10-19 会员体系）
/// 认证申请表单：选择认证类别（后台两级分类）+ 自定义认证标签 + 申请理由，提交后后台审核。
/// 审核通过后自动获得官方蓝 V + 标签（后台审核接口处理）。
struct VerifyApplySheet: View {
    @EnvironmentObject private var auth: AuthManager
    @Environment(\.dismiss) private var dismiss

    struct VCategory: Decodable, Identifiable, Hashable {
        let id: String
        let level1: String
        let level2: String
    }

    @State private var categories: [VCategory] = []
    @State private var level1: String = ""
    @State private var level2: String = ""
    @State private var label: String = ""
    @State private var reason: String = ""
    @State private var submitting = false
    @State private var errorText: String?
    @State private var success = false
    @State private var loadFailed = false

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.background.ignoresSafeArea()
                ScrollView {
                    VStack(alignment: .leading, spacing: 20) {
                        Text("认证申请")
                            .font(.system(size: 20, weight: .bold))
                            .foregroundColor(Theme.textPrimary)

                        Text("认证通过后，你的评论、主页、我的页面将显示官方蓝 V 认证标识与自定义认证标签。请如实填写，管理员审核通过后生效。")
                            .font(.system(size: 13))
                            .foregroundColor(Theme.textTertiary)

                        // 认证类别（两级：一级必选，二级可选）
                        VStack(alignment: .leading, spacing: 8) {
                            Text("认证类别（第一级）")
                                .font(.system(size: 13, weight: .medium))
                                .foregroundColor(Theme.textSecondary)
                            if categories.isEmpty {
                                Text(loadFailed ? "类别加载失败，请检查网络后重试" : "暂无可用认证类别，请稍后重试")
                                    .font(.system(size: 13))
                                    .foregroundColor(Theme.textTertiary)
                                    .padding(.vertical, 12)
                            } else {
                                HStack(spacing: 8) {
                                    ForEach(l1s, id: \.self) { l in
                                        capsuleButton(l, selected: level1 == l) { level1 = l; level2 = "" }
                                    }
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                            }

                            if !level1.isEmpty {
                                Text("认证类别（第二级，可选）")
                                    .font(.system(size: 13, weight: .medium))
                                    .foregroundColor(Theme.textSecondary)
                                    .padding(.top, 6)
                                let l2s = l2s(for: level1)
                                if !l2s.isEmpty {
                                    HStack(spacing: 8) {
                                        ForEach(l2s, id: \.self) { l in
                                            capsuleButton(l, selected: level2 == l) { level2 = l }
                                        }
                                    }
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                }
                            }
                        }

                        // 自定义认证标签
                        VStack(alignment: .leading, spacing: 8) {
                            Text("认证标签（如「XX 官方账户」「XX 工作室」，选填）")
                                .font(.system(size: 13, weight: .medium))
                                .foregroundColor(Theme.textSecondary)
                            TextField("例如：流萤音乐官方账户", text: $label)
                                .textFieldStyle(.plain)
                                .padding(12)
                                .background(Theme.card, in: RoundedRectangle(cornerRadius: 10))
                                .foregroundColor(Theme.textPrimary)
                        }

                        // 申请理由
                        VStack(alignment: .leading, spacing: 8) {
                            Text("申请理由（必填）")
                                .font(.system(size: 13, weight: .medium))
                                .foregroundColor(Theme.textSecondary)
                            TextEditor(text: $reason)
                                .frame(minHeight: 90)
                                .padding(10)
                                .background(Theme.card, in: RoundedRectangle(cornerRadius: 10))
                                .foregroundColor(Theme.textPrimary)
                                .scrollContentBackground(.hidden)
                        }

                        if let errorText {
                            Text(errorText)
                                .font(.system(size: 12))
                                .foregroundColor(Color(hex: 0xFF6B6B))
                        }

                        Button {
                            submit()
                        } label: {
                            HStack(spacing: 8) {
                                if submitting {
                                    ProgressView().tint(.white)
                                }
                                Text("提交申请")
                                    .font(.system(size: 15, weight: .semibold))
                                    .foregroundColor(.white)
                            }
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 13)
                            .background(Theme.accent, in: RoundedRectangle(cornerRadius: 12))
                        }
                        .buttonStyle(PressableButtonStyle())
                        .disabled(submitting || level1.isEmpty || reason.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

                        Spacer(minLength: 20)
                    }
                    .padding(20)
                }
            }
            .navigationBarHidden(true)
            .overlay(alignment: .top) {
                // 返回
                HStack {
                    Button { dismiss() } label: {
                        Image(systemName: "chevron.left")
                            .font(.system(size: 16, weight: .semibold))
                            .foregroundColor(Theme.textPrimary)
                            .frame(width: 38, height: 38)
                            .background(Theme.card, in: Circle())
                    }
                    .buttonStyle(PressableButtonStyle())
                    Spacer()
                }
                .padding(.top, 8)
                .padding(.horizontal, 16)
            }
        }
        .task {
            await loadCategories()
        }
        .alert(success ? "申请已提交" : "提交失败", isPresented: Binding(
            get: { success || errorText != nil },
            set: { if !$0 { if success { dismiss() }; errorText = nil } }
        )) {
            Button("好", role: .cancel) { if success { dismiss() }; errorText = nil }
        } message: {
            Text(success ? "审核通过后你将获得认证标识。" : (errorText ?? ""))
        }
    }

    private var l1s: [String] {
        Array(Set(categories.map { $0.level1 })).sorted()
    }

    private func l2s(for l1: String) -> [String] {
        categories.filter { $0.level1 == l1 }.map { $0.level2 }.filter { !$0.isEmpty }
    }

    private func capsuleButton(_ text: String, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(text)
                .font(.system(size: 13, weight: selected ? .semibold : .regular))
                .foregroundColor(selected ? .white : Theme.textSecondary)
                .padding(.horizontal, 12)
                .padding(.vertical, 7)
                .background(
                    Capsule().fill(selected ? Theme.accent : Theme.card)
                )
        }
        .buttonStyle(PressableButtonStyle())
    }

    private func loadCategories() async {
        do {
            let list = try await API.fetchVerifyCategories()
            categories = list.map { VCategory(id: $0.id, level1: $0.level1, level2: $0.level2) }
            loadFailed = false
        } catch {
            loadFailed = true
        }
    }

    private func submit() {
        guard !level1.isEmpty, !reason.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        submitting = true
        errorText = nil
        Task {
            do {
                try await API.submitVerifyApply(level1: level1, level2: level2,
                                                label: label.trimmingCharacters(in: .whitespacesAndNewlines), reason: reason.trimmingCharacters(in: .whitespacesAndNewlines))
                success = true
            } catch {
                errorText = error.localizedDescription
            }
            submitting = false
        }
    }
}
