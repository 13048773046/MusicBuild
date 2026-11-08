import SwiftUI

/// 相似歌曲弹窗（2026-11-05）
///
/// 数据来自后端 `/api/similar/{id}`：用 948 维语义向量（歌词/标签/画像 × MERT 听感）
/// 算出的**跨歌手**"听起来像"邻居，不是"同一个歌手的其他歌"。
/// 例如《诀别书》会连到《梦中的婚礼》《城南花已开》，《曹操》会连到《雪龙吟》《天地龙鳞》。
struct SimilarSongsSheet: View {
    let seed: Track

    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var player: PlayerManager

    @State private var items: [(track: Track, score: Double)] = []
    @State private var loading = true
    @State private var failed = false

    var body: some View {
        NavigationStack {
            Group {
                if loading {
                    VStack(spacing: 12) {
                        ProgressView().tint(Theme.accent)
                        Text("正在找听起来像的歌…")
                            .font(.footnote)
                            .foregroundColor(Theme.textSecondary)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if failed || items.isEmpty {
                    StatusPlaceholder(
                        icon: "questionmark.circle",
                        title: "暂时没有相似歌曲",
                        message: "试着播放几首歌，推荐会越来越准"
                    )
                } else {
                    List {
                        Section {
                            ForEach(Array(items.enumerated()), id: \.element.track.id) { _, item in
                                Button {
                                    play(item.track)
                                } label: {
                                    row(item.track, score: item.score)
                                }
                                .buttonStyle(.plain)
                            }
                        } header: {
                            Text("听起来像《\(seed.title)》")
                        }
                    }
                    .listStyle(.plain)
                }
            }
            .navigationTitle("相似歌曲")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("完成") { dismiss() }
                }
            }
        }
        .task { await load() }
    }

    private func row(_ track: Track, score: Double) -> some View {
        HStack(spacing: 12) {
            AlbumArt(size: 48, title: track.title, coverURL: track.coverURL, radius: 8)
            VStack(alignment: .leading, spacing: 3) {
                Text(track.title)
                    .font(.system(size: 15, weight: .medium))
                    .foregroundColor(.primary)
                    .lineLimit(1)
                Text(track.displayAuthor)
                    .font(.system(size: 12))
                    .foregroundColor(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
            // 相似度只做弱提示：数值本身对用户没有意义，用三段式表达更直观
            Text(score >= 0.78 ? "很像" : (score >= 0.6 ? "较像" : "有点像"))
                .font(.system(size: 11, weight: .semibold))
                .foregroundColor(Theme.accent)
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(Theme.accent.opacity(0.12), in: Capsule())
        }
        .padding(.vertical, 6)
        .contentShape(Rectangle())
    }

    private func play(_ track: Track) {
        Haptics.selectionChanged()
        let queue = items.map { $0.track }
        player.play(track, in: queue)
        dismiss()
    }

    private func load() async {
        loading = true
        failed = false
        defer { loading = false }
        do {
            items = try await API.similar(seed.id, n: 20)
        } catch {
            failed = true
            LogReporter.shared.log(level: "info", tag: "similar",
                                   message: "load fail \(seed.id) \(error.localizedDescription)")
        }
    }
}
