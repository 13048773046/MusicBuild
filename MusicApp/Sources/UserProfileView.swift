import SwiftUI
import UIKit
import Kingfisher

/// 用户主页页面（v2 重做版：背景更紧凑、单返回键、信息更精致）
struct UserProfileView: View {
    let userId: String
    @EnvironmentObject private var player: PlayerManager
    @Environment(\.dismiss) private var dismiss
    @State private var profile: UserProfile?
    @State private var userTracks: [Track] = []
    @State private var isLoading = true
    @State private var isFollowing = false
    @State private var page = 1
    @State private var hasMore = true

    /// 状态栏高度：背景图延伸到状态栏后面需要用它铺满顶部
    private static var statusBarHeight: CGFloat {
        for scene in UIApplication.shared.connectedScenes {
            guard let ws = scene as? UIWindowScene,
                  let window = ws.keyWindow else { continue }
            let top = window.safeAreaInsets.top
            if top > 0 { return top }
        }
        return 44
    }

    var body: some View {
        ZStack(alignment: .top) {
            Theme.background.ignoresSafeArea()

            ScrollView {
                VStack(spacing: 0) {
                    // 背景图区域：顶部延伸到状态栏（与"我的"页面一致）——
                    // ScrollView ignoresSafeArea(.top) 让内容从屏幕顶开始，
                    // 高度 = 展示区(150) + 状态栏真实高度(statusBarHeight) + 透明导航栏(44)，
                    // 背景图完整铺到状态栏后面，任何机型（刘海/灵动岛）都无缝隙
                    headerBackground
                        .frame(height: 150 + Self.statusBarHeight + 44)

                    // 头像 + 基本信息（向上重叠，与背景衔接）
                    profileInfoSection
                        .offset(y: -38)
                        .padding(.bottom, -38)

                    // 关注按钮
                    if let p = profile, p.userId != AuthManager.shared.currentUser?.id {
                        followButton
                            .padding(.top, 12)
                            .padding(.bottom, 16)
                    }

                    // 数据统计（5 项）
                    if let p = profile {
                        statsCard(p: p)
                            .padding(.horizontal, 16)
                            .padding(.bottom, 20)
                    }

                    // 歌曲列表
                    tracksSection
                        .padding(.bottom, 90)
                }
            }
            // 顶部延伸到状态栏后（背景图铺满），内容不被状态栏/灵动岛遮挡：
            // headerBackground 高度已含真实状态栏高度，头像/信息区仍在安全区内
            .ignoresSafeArea(edges: .top)
        }
        // 独立页面 push：隐藏系统返回，仅保留自定义返回（避免双返回键）
        .navigationBarBackButtonHidden(true)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(.visible, for: .navigationBar)
        .toolbar {
            ToolbarItem(placement: .navigationBarLeading) {
                // NavBack 可靠返回：消息页→用户主页等多层 push 场景下
                // @Environment(\.dismiss) 偶发失效导致返回无响应，统一走 UIKit pop
                Button { NavBack.goBack(dismiss) } label: {
                    Circle()
                        .fill(.black.opacity(0.3))
                        .frame(width: 32, height: 32)
                        .overlay(
                            Image(systemName: "chevron.left")
                                .font(.system(size: 14, weight: .semibold))
                                .foregroundColor(.white)
                        )
                }
            }
        }
        .toolbarBackground(.hidden, for: .navigationBar)
        .task { await load() }
    }

    // MARK: - 背景图

    private var headerBackground: some View {
        ZStack(alignment: .bottom) {
            // 用户背景图（有图用图，无图用渐变，与"我的"页面一致）
            Group {
                if let p = profile, !p.backgroundImage.isEmpty {
                    CachedCoverImage(url: API.backgroundURL(p.userId), title: p.name,
                                     placeholder: Rectangle().fill(Theme.card))
                        .frame(maxWidth: .infinity) // 锁宽：scaledToFill 铺满容器宽，不被原始尺寸撑宽/裁偏
                } else {
                    LinearGradient(
                        colors: [Theme.accent.opacity(0.24), Theme.accent.opacity(0.06)],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                }
            }

            // 顶部轻微压暗：让返回按钮始终清晰
            LinearGradient(
                colors: [Color.black.opacity(0.18), Color.clear],
                startPoint: .top,
                endPoint: .center
            )

            // 底部渐变遮罩：与下方内容自然融合
            LinearGradient(
                colors: [Color.clear, Theme.background],
                startPoint: .top,
                endPoint: .bottom
            )
            .frame(height: 90)
        }
        .frame(maxWidth: .infinity)
        .clipped()
    }

    // MARK: - 个人信息

    private var profileInfoSection: some View {
        HStack(spacing: 14) {
            // 头像
            if let p = profile, !p.avatar.isEmpty {
                KFImage(API.avatarURL(p.userId))
                    .placeholder {
                        Circle().fill(Theme.accentSoft).overlay(
                            Image(systemName: "person.fill").foregroundColor(Theme.accent)
                        )
                    }
                    .resizable()
                    .scaledToFill()
                    .frame(width: 76, height: 76)
                    .clipShape(Circle())
                    .overlay(Circle().stroke(Theme.background, lineWidth: 3))
                    // 在线状态绿点
                    .overlay { if p.isOnline == true { OnlineDot(avatarSize: 76) } }
            } else {
                Circle()
                    .fill(Theme.accentSoft)
                    .frame(width: 76, height: 76)
                    .overlay(
                        Image(systemName: "person.fill")
                            .font(.system(size: 30))
                            .foregroundColor(Theme.accent)
                    )
                    .overlay(Circle().stroke(Theme.background, lineWidth: 3))
                    // 在线状态绿点
                    .overlay { if profile?.isOnline == true { OnlineDot(avatarSize: 76) } }
            }

            VStack(alignment: .leading, spacing: 4) {
                // 名字 + 会员图标（VIP绿/SVIP金）+ 认证蓝V
                HStack(spacing: 6) {
                    Text(profile?.name.isEmpty ?? true ? "用户" : profile!.name)
                        .font(.system(size: 19, weight: .bold))
                        .foregroundColor(Theme.textPrimary)
                        .lineLimit(1)
                    if let p = profile { MemberBadge(member: p.member) }
                    if let p = profile { VerifyBadge(verified: p.verified) }
                }
                // 头衔胶囊（后台设置）
                if let p = profile { TitleCapsule(title: p.title) }

                // 性别 · 年龄 · 城市（大致地址，有值才显示）
                if let p = profile {
                    let meta = [p.gender.isEmpty ? nil : p.gender,
                                p.age > 0 ? "\(p.age) 岁" : nil,
                                p.city.isEmpty ? nil : p.city]
                        .compactMap { $0 }
                    if !meta.isEmpty {
                        Text(meta.joined(separator: " · "))
                            .font(.system(size: 12))
                            .foregroundColor(Theme.textSecondary)
                    }
                }

                if let sig = profile?.signature, !sig.isEmpty {
                    Text(sig)
                        .font(.system(size: 12))
                        .foregroundColor(Theme.textTertiary)
                        .lineLimit(1)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 18)
    }

    // MARK: - 关注按钮

    private var followButton: some View {
        Button {
            Task { await toggleFollow() }
        } label: {
            Text(isFollowing ? "已关注" : "关注")
                .font(.system(size: 14, weight: .semibold))
                .foregroundColor(isFollowing ? Theme.textSecondary : .white)
                .frame(width: 104, height: 34)
                .background(
                    Capsule()
                        .fill(isFollowing ? Theme.card : Theme.accent)
                )
        }
        .buttonStyle(PressableButtonStyle())
    }

    // MARK: - 统计卡片（歌曲/粉丝/关注/互关/获赞）

    private func statsCard(p: UserProfile) -> some View {
        HStack(spacing: 0) {
            statItem(value: "\(p.trackCount)", label: "歌曲")
            Divider().frame(height: 22)
            statItem(value: "\(p.followerCount)", label: "粉丝")
            Divider().frame(height: 22)
            statItem(value: "\(p.followingCount)", label: "关注")
            Divider().frame(height: 22)
            statItem(value: "\(p.mutualCount)", label: "互关")
            Divider().frame(height: 22)
            statItem(value: "\(p.likeCount)", label: "获赞")
        }
        .padding(.vertical, 12)
        .background(Theme.card, in: RoundedRectangle(cornerRadius: 14))
    }

    private func statItem(value: String, label: String) -> some View {
        VStack(spacing: 3) {
            Text(value)
                .font(.system(size: 15, weight: .bold))
                .foregroundColor(Theme.textPrimary)
                .lineLimit(1)
                .minimumScaleFactor(0.6)
            Text(label)
                .font(.system(size: 11))
                .foregroundColor(Theme.textSecondary)
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: - 歌曲列表

    private var tracksSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("TA 的歌曲")
                .font(.system(size: 16, weight: .semibold))
                .foregroundColor(Theme.textPrimary)
                .padding(.horizontal, 18)

            if userTracks.isEmpty && isLoading {
                ProgressView()
                    .tint(Theme.accent)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 40)
            } else if userTracks.isEmpty {
                Text("还没有上传歌曲")
                    .font(.system(size: 14))
                    .foregroundColor(Theme.textTertiary)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 40)
            } else {
                ForEach(userTracks) { t in
                    Button {
                        // 用户主页点歌 = 普通流播放：听歌模式归「默认模式」
                        // （侧滑栏选中态 = 当前听歌模式，与发现/搜索/歌单/历史一致）
                        player.playInNormalMode(t, in: userTracks)
                    } label: {
                        trackRow(t)
                    }
                    .buttonStyle(PressableButtonStyle())
                }

                // 加载更多
                if hasMore {
                    Button {
                        Task { await loadMoreTracks() }
                    } label: {
                        Text("加载更多")
                            .font(.system(size: 13))
                            .foregroundColor(Theme.accent)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 12)
                    }
                }
            }
        }
    }

    private func trackRow(_ t: Track) -> some View {
        HStack(spacing: 12) {
            AlbumArt(size: 46, title: t.title, coverURL: t.coverURL, radius: 8)

            VStack(alignment: .leading, spacing: 4) {
                Text(t.title)
                    .font(.system(size: 15, weight: .medium))
                    .foregroundColor(Theme.textPrimary)
                    .lineLimit(1)
                Text(t.artist ?? "")
                    .font(.system(size: 12))
                    .foregroundColor(Theme.textSecondary)
                    .lineLimit(1)
            }
            Spacer()
            Image(systemName: "play.fill")
                .font(.system(size: 14))
                .foregroundColor(Theme.textTertiary)
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 8)
    }

    // MARK: - 数据加载

    private func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            let p = try await API.fetchUserProfile(userId)
            profile = p
            isFollowing = p.isFollowing
            page = 1
            let tracks = try await API.fetchUserTracks(userId)
            userTracks = tracks
            hasMore = tracks.count >= 20
        } catch {}
    }

    private func loadMoreTracks() async {
        do {
            page += 1
            let tracks = try await API.fetchUserTracks(userId)
            userTracks.append(contentsOf: tracks)
            hasMore = tracks.count >= 20
        } catch {
            page -= 1
        }
    }

    private func toggleFollow() async {
        do {
            let newState = try await API.toggleFollow(userId)
            isFollowing = newState
            if let p = profile {
                profile = UserProfile(
                    userId: p.userId, account: p.account, name: p.name, avatar: p.avatar,
                    signature: p.signature, created: p.created, gender: p.gender, age: p.age,
                    trackCount: p.trackCount, likeCount: p.likeCount,
                    followerCount: p.followerCount + (newState ? 1 : -1),
                    followingCount: p.followingCount, mutualCount: p.mutualCount,
                    isFollowing: newState,
                    backgroundImage: p.backgroundImage, city: p.city,
                    isOnline: p.isOnline,
                    title: p.title, verified: p.verified, verifiedLabel: p.verifiedLabel,
                    member: p.member, memberExpire: p.memberExpire, svipExpire: p.svipExpire
                )
            }
        } catch {}
    }
}

extension String: Identifiable {
    public var id: String { self }
}
