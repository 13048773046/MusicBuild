// PlayerManager+Queue.swift
// P2-13：从 PlayerManager.swift 拆出的 队列与推荐流 逻辑（extension 保持零引用改动）
import AVFoundation
import MediaPlayer
import UIKit
import Combine

extension PlayerManager {

    func play(_ track: Track, in tracks: [Track]) {
        guard !tracks.isEmpty else { return }   // 空队列不进入播放
        consecutiveFailCount = 0   // 用户手动播放新歌：重置连续失败计数
        if queue != tracks {
            queue = tracks
            // 队列换代：通知 VerticalPager 强制重建页面。
            // 此前只在「插入一首」时 bump —— 场景音乐 A→B 这类「同长度、不同内容」的
            // 整单替换不会 bump，页面索引复用旧数据（显示上一场景的歌）。
            queueRevision += 1
        }
        if let index = tracks.firstIndex(of: track) {
            currentIndex = index
        } else if !queue.isEmpty {
            // 目标歌不在队列（极端竞态/调用错误）：兜底到队首，
            // 避免旧 currentIndex 指向错误歌曲
            currentIndex = 0
        }
        playCurrent()
    }

    /// 场景音乐（侧滑栏场景卡片）：临时电台队列，播完列表循环回第一首。
    /// - 进入前先保存当前听歌模式会话（之后切回该模式可恢复）；
    /// - 场景不算任何听歌模式：清掉侧滑栏模式高亮 / 有限列表续拉 / 搜索相关流，
    ///   再把 `sceneTag` 置位供侧滑栏高亮场景卡片；
    /// - 场景队列是有限列表（播完回第一首），绝不自动续拉推荐或搜索结果。
    func playScene(_ tag: String, track: Track, in tracks: [Track]) {
        guard !tracks.isEmpty else { return }
        // 已在场景中切另一个场景：不要把上一个场景队列写进听歌模式会话
        if sceneTag == nil {
            saveSession(for: listenMode)
        }
        sceneTag = tag
        // 场景不归属任何听歌模式；用 normal 作为中性占位，靠 sceneTag 区分
        listenMode = .normal
        sidebarSelection = nil
        finiteLoopList = true
        playMode = .listLoop
        savedNormalMode = .listLoop
        // 离开搜索相关流：否则场景播到队尾时会续拉搜索结果
        Library.shared.searchContext = nil
        play(track, in: tracks)
    }

    /// 离开场景上下文（用户切听歌模式 / 从其他入口点歌时调用）
    func exitSceneContext() {
        guard sceneTag != nil else { return }
        sceneTag = nil
        finiteLoopList = false
    }

    /// 缓冲卡死分级自愈：先同曲重载（保持进度续播，规避弱网瞬时抖动/服务器慢响应），
    /// 同一首歌只自动重载一次（autoRetriedTrackId 防重）；重载后仍卡死 → 升级失败跳歌。
    func playInNormalMode(_ track: Track, in tracks: [Track]) {
        exitSceneContext()
        if listenMode != .normal {
            saveSession(for: listenMode)
            listenMode = .normal
        }
        // 普通流播放（搜索/发现页猜你喜欢等入口）：非侧滑栏选择，清选中态；离开有限列表来源
        sidebarSelection = nil
        finiteLoopList = false
        // 记忆续播：normal 流点歌记录最近点播的歌（侧滑栏「默认模式」重载时定位下一首）
        noteResumeTrack(.normal, track.id)
        play(track, in: tracks)
    }

    /// 通知点击播放：延续用户当前听歌模式与音乐流。
    /// 通知歌曲插入到「当前歌曲之后」并播放——不丢原歌（上滑可回原曲）、
    /// 下滑（next）自动接回原有队列下一首，不是只有一首、播完即停。
    func playNotificationTrack(_ track: Track) {
        guard !queue.isEmpty else {
            // 无当前流（冷启动直接点通知）：退化为单曲播放
            play(track, in: [track])
            return
        }
        let idx = (queue.indices.contains(currentIndex) ? currentIndex : 0)
        if queue[idx].id == track.id {
            // 已是当前位置的歌曲：直接续播
            currentIndex = idx
            playCurrent()
            return
        }
        if let found = queue.firstIndex(where: { $0.id == track.id }) {
            // 本就在当前流中：定位播放（不改变队列）
            currentIndex = found
            playCurrent()
            return
        }
        // 不在当前流：插入到当前位置之后播放；队列长度+1，
        // 提升 revision 让 VerticalPager 强制重建页面（索引缓存的旧页会显示错歌）
        var q = queue
        q.insert(track, at: idx + 1)
        queue = q
        currentIndex = idx + 1
        queueRevision += 1
        playCurrent()
    }

    func toggle() {
        guard current != nil else { return }
        if isPlaying {
            pausePlayback()
        } else {
            resumePlayback()
        }
    }

    func next() {
        guard !queue.isEmpty else { return }
        // actionAtItemEnd=.pause 后系统不再自动前进，播完统一由 didPlayToEndTime → next()
        // 手动前进，消除「系统自动前进 与 代码 advanceToNextItem 双前进」的时序竞争
        // （曾偶发自动切歌跳到下下首：实际播放下下首、界面索引仍是下一首）。
        // syncIndexWithPlayer 保留作兜底防御：万一仍有外部因素（如遥控/系统行为）
        // 改变了 currentItem，先把索引同步到实际正在播放的歌曲，再决定是否推进。
        let prevIndex = currentIndex
        // 引擎接管期间 AVQueuePlayer 一直被暂停（currentItem 可能已被预取项占据，但并非
        // 正在播放）。此时不能用 syncIndexWithPlayer 的 autoAdvanced 结果——它会让 next()
        // 误以为下一首已在播、走免播放分支，结果切歌后无声。引擎模式强制顺序切歌 + playCurrent。
        let autoAdvanced = usingEngine ? false : syncIndexWithPlayer()
        switch playMode {
        case .singleLoop:
            // 单曲循环：无论自动前进到哪，都重播「刚播完的这首」
            currentIndex = prevIndex
            playCurrent()
            return
        case .shuffle:
            // shuffle 始终随机下一首：无论是否自动前进，队列顺序不适用随机语义。
            // （autoAdvanced 后 currentIndex 已同步到实际播放的歌，随机排除它即可）
            let target = randomIndex(excluding: currentIndex)
            currentIndex = target
            playCurrent()
        default:  // listLoop
            if autoAdvanced {
                // 下一首已在播：不再推进；到队尾时按模式续播/回绕，并补队列保持预缓冲
                adoptAutoAdvancedTrack()
                topUpQueue()
                if currentIndex >= queue.count - 1 {
                    if listenMode == .normal && !finiteLoopList {
                        loadNextBatchOrLoop()
                    } else {
                        // 有限列表（历史/歌单/喜欢/作品）或推荐流回绕：播完回第一首循环
                        currentIndex = 0
                        playCurrent()
                    }
                }
            } else if currentIndex < queue.count - 1 {
                // 未到队尾：顺序切下一首（走 playCurrent 的 advance/重建逻辑）
                currentIndex += 1
                playCurrent()
            } else if listenMode == .normal && !finiteLoopList {
                // 默认模式已到队尾：不立即回绕重播第一组，而是异步加载下一组随机新歌续播。
                // 之前列表循环在追加前就 `% queue.count` 回绕到第 0 首，
                // 导致「听完一组→加载第二组」时听到的仍是第一组。
                loadNextBatchOrLoop()
                return
            } else {
                // 有限列表（历史/歌单）或非默认模式（喜欢/新鲜）到队尾：按列表循环回绕第一首
                currentIndex = 0
                playCurrent()
            }
        }
    }

    /// 自动切歌辅助：把 currentIndex 同步到 AVQueuePlayer 当前实际播放的歌曲（按 id 匹配）。
    /// 返回是否发生了同步，即 AVQueuePlayer 已自动前进、当前正在播「索引所指之外」的歌。
    @discardableResult
    func syncIndexWithPlayer() -> Bool {
        guard !queue.isEmpty,
              let itemId = player.currentItem.flatMap({ Self.trackId(of: $0) }),
              let idx = queue.firstIndex(where: { $0.id == itemId }) else { return false }
        guard idx != currentIndex else { return false }
        currentIndex = idx
        return true
    }

    /// 自动切歌已生效（AVQueuePlayer 已在播新歌，未走 playCurrent）时：
    /// 结算上一首收听并把收听状态/自动重试机会切到新歌，避免时长记到上一首头上。
    func adoptAutoAdvancedTrack() {
        guard let cur = current else { return }
        if listen.trackId != cur.id {
            flushListen(.switchTrack)   // 听完整首切走 frac≈1，不会产生 skip 信号
            listen = ListenState(trackId: cur.id)
            autoRetriedTrackId = nil
        }
    }

    /// 默认模式队尾续播（汽水式无限流）：
    /// - 推荐流（无搜索上下文）：异步拉取下一批「个性化推荐」（排除已推荐歌曲，防重复），
    ///   追加成功则从「新歌第一首」接续播放——每一首都是多路召回按口味推荐的歌；
    /// - 搜索相关流（有搜索上下文）：维持原有「关键词相关歌曲」续拉；
    /// 两种都无新歌（曲库不足/拉取失败）才回绕第一首（列表循环兜底，避免卡在队尾）。
    /// 追加期间若用户已手动切走（不再停在队尾），不覆盖用户当前位置。
    func loadNextBatchOrLoop() {
        let lastIndex = currentIndex   // 队尾索引
        let baseCount = queue.count    // 追加前长度
        let isSearchStream = Library.shared.searchContext != nil
        Task { @MainActor in
            if isSearchStream {
                // 搜索相关流：关键词相关歌曲续拉（原有逻辑）
                await Library.shared.loadMore()
            } else {
                // 推荐流：拉下一批个性化推荐，排除已推荐 id，实现汽水式无限续推
                // M2：附带天气场景参数（定位未就绪时为 nil，后端按时段兜底）
                let excluding = Array(recommendedIds.suffix(recommendedExcludeCap))
                let next = (try? await API.recommend(excluding: excluding,
                                                     lat: WeatherContext.shared.lat,
                                                     lng: WeatherContext.shared.lng,
                                                     hour: WeatherContext.shared.hour)) ?? []
                if !next.isEmpty {
                    recommendedIds.formUnion(next.map { $0.id })
                    let existing = Set(queue.map { $0.id })
                    let fresh = next.filter { !existing.contains($0.id) }
                    if !fresh.isEmpty {
                        queue.append(contentsOf: fresh)
                        trimQueueIfNeeded()
                    }
                }
            }
            let grew = queue.count > baseCount
            let stillAtEnd = currentIndex == lastIndex
            if grew, stillAtEnd, listenMode == .normal, !finiteLoopList {
                // 新歌追加成功且仍停在队尾：从新歌第一首续播（仅推荐流；有限列表不续拉）
                currentIndex = baseCount
                playCurrent()
            } else if !grew, stillAtEnd {
                // 无新歌可用（或有限列表不续拉）：回绕第一首，维持列表循环
                currentIndex = 0
                playCurrent()
            }
            // 追加成功但用户已切走：保持用户当前位置，不干预
        }
    }

    func previous() {
        guard !queue.isEmpty else { return }
        switch playMode {
        case .singleLoop:
            playCurrent()
            return
        case .shuffle:
            let target = randomIndex(excluding: currentIndex)
            currentIndex = target
        default:
            currentIndex = (currentIndex - 1 + queue.count) % queue.count
        }
        playCurrent()
    }

    /// 切换播放模式：列表循环 → 单曲循环 → 随机 → 列表循环
    func cyclePlayMode() {
        let all = PlayMode.allCases
        let idx = all.firstIndex(of: playMode) ?? 0
        playMode = all[(idx + 1) % all.count]
    }

    // MARK: - 侧边栏听歌模式（默认 / 我的喜欢 / 新鲜）

    /// 每种听歌模式独立维护一份播放会话：切走时缓存「队列 + 当前歌曲 + 播放状态」，
    /// 切回该模式时恢复队列，并播放「切走时那首歌的下一首」——不重复听刚听过的那首，
    /// 也不重新随机打乱（列表循环播到末尾则回绕第一首）。
    struct ModeSession {
        var queue: [Track] = []
        var currentIndex: Int = -1     // 切走时正在播放的歌曲索引
        var wasPlaying: Bool = false
        var searchContext: String? = nil  // 默认模式下的相关流关键词（仅 normal 会话使用）
        var mode: ListenMode = .normal
    }


    /// 切换侧边栏听歌模式。
    /// 切走前保存当前模式会话；目标模式有缓存则恢复缓存，无缓存才全新加载；
    /// 目标模式加载失败/无内容时回退上一模式，保证「听到的歌 = 看到的模式」。
    /// - 默认模式：恢复个性化推荐流 + 用户自选循环模式
    /// - 我的作品：只播用户上传的作品，列表循环播完回第一首（无限循环）
    /// - 我的喜欢：只播用户点赞过的歌曲，播完回到第一首循环
    /// - 新鲜模式：完全随机（队列打乱 + 随机播放）
    func applyListenMode(_ mode: ListenMode) {
        // 每次点击侧滑栏都「重新加载」该模式：不做同模式去重拦截
        let previous = listenMode
        // 场景音乐的队列不是任何听歌模式的会话：从场景切走时不要把它写进 previous 会话，
        // 否则「场景 → 我的喜欢 → 我的作品 → 我的喜欢」时点赞列表会变成场景歌单。
        if sceneTag == nil {
            saveSession(for: previous)
        }
        sceneTag = nil
        listenMode = mode
        // 侧边栏主动选择：记录选中态（侧滑栏显示高亮/对勾）
        sidebarSelection = mode
        // 切模式离开有限列表来源
        finiteLoopList = false
        switch mode {
        case .normal:
            restoreNormalStream(forceReload: true)
        case .works:
            switchToWorks(fallback: previous, forceReload: true)
        case .liked:
            switchToLiked(fallback: previous, forceReload: true)
        case .fresh:
            switchToFresh(fallback: previous, forceReload: true)
        }
    }

    /// 保存当前模式会话快照（切走前调用）
    func noteRecommended(_ ids: [String]) {
        recommendedIds.formUnion(ids)
    }

    /// 进入个性化推荐流（对齐汽水音乐「推荐即队列」的无限听歌体验）：
    /// 队列 = 后端多路召回 + MMR 重排的个性化推荐，滑到队尾自动续拉下一批推荐（不重复）。
    /// - Parameter track: 期望从这首歌开始（发现页点击歌曲时传入；不在推荐列表则置为队首，后续接推荐）
    /// - Parameter resumeId: 侧滑栏「默认模式」重新加载时的记忆续播点——新列表里定位到这首歌的下一首自动播放
    /// - Parameter fallback: 推荐拉取失败时的保底队列（全库流），保证点歌必有声音
    /// - Parameter fresh: 长时间重开 App 的新鲜会话——后端加大随机扰动/探索权重，
    ///   重新开始的一批推荐带随机性、不固定重复上次
    func startRecommendedStream(startingAt track: Track? = nil, resumeId: String? = nil,
                                 fallback: [Track] = [], fresh: Bool = false) {
        exitSceneContext()
        if listenMode != .normal {
            saveSession(for: listenMode)
            listenMode = .normal
        }
        // 推荐流：非侧滑栏主动选择，清选中态；离开有限列表来源
        sidebarSelection = nil
        finiteLoopList = false
        // 记忆续播点：发现页点歌时记录最近点播的歌（侧滑栏「默认模式」重载时用它定位下一首）
        if let track {
            noteResumeTrack(.normal, track.id)
        }
        let excluding = Array(recommendedIds.suffix(recommendedExcludeCap))
        Task { @MainActor in
            // M2：附带天气场景参数（定位未就绪时为 nil，后端按时段兜底）
            let list = (try? await API.recommend(excluding: excluding, fresh: fresh,
                                                 lat: WeatherContext.shared.lat,
                                                 lng: WeatherContext.shared.lng,
                                                 hour: WeatherContext.shared.hour)) ?? []
            if list.isEmpty {
                // 推荐不可用（未登录/曲库太小/网络失败）：回退保底队列
                if !fallback.isEmpty {
                    let base = fallback
                    if let track, base.contains(where: { $0.id == track.id }) {
                        play(track, in: base)
                    } else if let track {
                        play(track, in: [track] + base.filter { $0.id != track.id })
                    } else if let first = base.first {
                        play(first, in: base)
                    }
                } else if let track {
                    play(track, in: [track])
                }
                return
            }
            recommendedIds.formUnion(list.map { $0.id })
            if let track, let idx = list.firstIndex(where: { $0.id == track.id }) {
                // 目标歌就在推荐里：以它为当前曲，后续接推荐
                play(track, in: list)
            } else if let track {
                // 目标歌不在推荐列表：置为队首（当前曲），后续接推荐
                var q = list
                q.insert(track, at: 0)
                recommendedIds.insert(track.id)
                play(track, in: q)
            } else if let resumeId, let idx = list.firstIndex(where: { $0.id == resumeId }) {
                // 记忆续播：从记忆歌曲的「下一首」开始（在最后一首则回绕第一首）
                let next = list.indices.contains(idx + 1) ? idx + 1 : 0
                play(list[next], in: list)
            } else if let first = list.first {
                play(first, in: list)
            }
        }
    }

    /// 我的喜欢：侧滑栏进入（forceReload）重新拉全量点赞列表并记忆续播；
    /// 我的页点歌（startAt）在会话/新列表里定位到它播放；拉取失败或列表为空时回退上一模式。
    /// - Parameter startAt: 从「我的」页点击进入时传入目标歌曲——在会话/新列表里定位到它播放
    /// - Parameter known: 点击来源页已加载的列表（全量拉取失败时的兜底队列）
    /// - Parameter forceReload: 侧滑栏主动选择——总是重新加载列表，按记忆续播点定位下一首；无记忆从头
    func switchToLiked(fallback: ListenMode?, startAt: Track? = nil, known: [Track] = [],
                       forceReload: Bool = false) {
        if !forceReload, let s = sessions[.liked], !s.queue.isEmpty {
            if let startAt, !s.queue.contains(where: { $0.id == startAt.id }) {
                // 目标歌不在缓存会话（新点赞）：重新拉全量列表
            } else {
                if let startAt, let idx = s.queue.firstIndex(where: { $0.id == startAt.id }) {
                    play(startAt, in: s.queue)   // 定位到点击的歌播放
                    return
                }
                restoreSession(s)
                return
            }
        }
        savedNormalMode = playMode
        playMode = .listLoop
        guard let uid = AuthManager.shared.currentUser?.id else {
            if let fb = fallback { handleFailedSwitch(fallback: fb) }
            return
        }
        Task {
            let list = (try? await API.likedTracks(userId: uid)) ?? []
            await MainActor.run {
                guard listenMode == .liked else { return }   // 期间用户又切走了：丢弃
                var base = list
                if base.isEmpty, !known.isEmpty {
                    base = known   // 全量拉取失败：用点击来源页已加载的列表兜底
                }
                guard !base.isEmpty else {
                    if let fb = fallback { handleFailedSwitch(fallback: fb) }
                    return
                }
                if let startAt, let idx = base.firstIndex(where: { $0.id == startAt.id }) {
                    play(startAt, in: base)   // 从点击的歌曲开始，后续列表循环
                    return
                }
                // 记忆续播：侧滑栏重载 → 从「最近点播的歌」的下一首开始（最后一首回绕第一首）
                if let resumeId = resumeByMode[.liked], let idx = base.firstIndex(where: { $0.id == resumeId }) {
                    let next = base.indices.contains(idx + 1) ? idx + 1 : 0
                    play(base[next], in: base)
                    return
                }
                // 无记忆：从头开始自动播放
                if let first = base.first {
                    play(first, in: base)
                }
            }
        }
    }

    /// 我的作品：有缓存恢复缓存；无缓存循环拉取用户全部上传作品（列表循环，播完回第一首，无限循环）。
    /// 拉取失败或列表为空时回退上一模式，不出现「模式已切、歌曲没换」。
    /// - Parameter startAt: 从「我的」页点击进入时传入目标歌曲——在会话/新列表里定位到它播放；
    ///   不在会话缓存（如新上传）时强制重新拉取全量作品列表。
    /// - Parameter known: 点击来源页已加载的列表（全量拉取失败时的兜底队列）。
    func switchToWorks(fallback: ListenMode?, startAt: Track? = nil, known: [Track] = [],
                       forceReload: Bool = false) {
        if !forceReload, let s = sessions[.works], !s.queue.isEmpty {
            if let startAt, !s.queue.contains(where: { $0.id == startAt.id }) {
                // 目标歌不在缓存会话（新上传）：重新拉全量列表
            } else {
                if let startAt, let idx = s.queue.firstIndex(where: { $0.id == startAt.id }) {
                    play(startAt, in: s.queue)   // 定位到点击的歌播放
                    return
                }
                restoreSession(s)
                return
            }
        }
        savedNormalMode = playMode
        playMode = .listLoop
        guard let uid = AuthManager.shared.currentUser?.id else {
            if let fb = fallback { handleFailedSwitch(fallback: fb) }
            return
        }
        Task {
            // 循环拉取全部作品（分页直到 hasMore=false；上限保护防止异常无限循环）
            var list: [Track] = []
            var page = 1
            while page <= 20 {
                guard let r = try? await API.myTracks(page: page, pageSize: 50) else { break }
                list += r.list
                if !r.hasMore { break }
                page += 1
            }
            await MainActor.run {
                guard listenMode == .works else { return }   // 期间用户又切走了：丢弃
                var base = list
                if base.isEmpty, !known.isEmpty {
                    base = known   // 全量拉取失败：用点击来源页已加载的列表兜底
                }
                guard !base.isEmpty else {
                    if let fb = fallback { handleFailedSwitch(fallback: fb) }
                    return
                }
                if let startAt, let idx = base.firstIndex(where: { $0.id == startAt.id }) {
                    play(startAt, in: base)   // 从点击的作品开始，后续列表循环
                    return
                }
                // 记忆续播：侧滑栏重载 → 从「最近点播的歌」的下一首开始（最后一首回绕第一首）
                if let resumeId = resumeByMode[.works], let idx = base.firstIndex(where: { $0.id == resumeId }) {
                    let next = base.indices.contains(idx + 1) ? idx + 1 : 0
                    play(base[next], in: base)
                    return
                }
                // 无记忆：从头开始自动播放
                if let first = base.first {
                    play(first, in: base)
                }
            }
        }
    }

    /// 从「我的」页点击我的喜欢歌曲进入：切换侧边栏到「我的喜欢」模式，
    /// 队列 = 点赞列表（列表循环），从点击的歌曲开始播放；
    /// 后续切走再切回、加载失败回退，均走与侧边栏切换一致的会话/回退机制。
    func playLikedTrack(_ t: Track, known: [Track]) {
        exitSceneContext()
        if listenMode != .liked {
            saveSession(for: listenMode)
        }
        listenMode = .liked
        // 从「我的」页点入：非侧滑栏选择，清选中态（侧滑栏不高亮）
        sidebarSelection = nil
        // 记忆续播：记录最近点播的歌（侧滑栏「我的喜欢」重载时定位下一首）
        noteResumeTrack(.liked, t.id)
        switchToLiked(fallback: nil, startAt: t, known: known)
    }

    /// 从「我的」页点击我的作品歌曲进入：切换侧边栏到「我的作品」模式，
    /// 队列 = 我的全部作品（列表循环，播到最后一个回到第一个无限循环），从点击的作品开始播放；
    /// 后续切走再切回、加载失败回退，均走与侧边栏切换一致的会话/回退机制。
    func playWorksTrack(_ t: Track, known: [Track]) {
        exitSceneContext()
        if listenMode != .works {
            saveSession(for: listenMode)
        }
        listenMode = .works
        // 从「我的」页点入：非侧滑栏选择，清选中态（侧滑栏不高亮）
        sidebarSelection = nil
        // 记忆续播：记录最近点播的歌（侧滑栏「我的作品」重载时定位下一首）
        noteResumeTrack(.works, t.id)
        switchToWorks(fallback: nil, startAt: t, known: known)
    }

    /// 从「播放历史」页点击歌曲进入：只播该历史列表（有限列表），
    /// 播到最后一首回到第一首循环；侧滑栏不显示任何模式选中态。
    func playHistoryTrack(_ t: Track, known: [Track]) {
        exitSceneContext()
        if listenMode != .normal {
            saveSession(for: listenMode)
            listenMode = .normal
        }
        // 有限列表循环：历史列表播完回第一首；不自动续拉推荐
        finiteLoopList = true
        playMode = .listLoop
        sidebarSelection = nil
        play(t, in: known.isEmpty ? [t] : known)
    }

    /// 新鲜模式：侧滑栏进入（forceReload）重新打乱曲库从头播放；非重载时恢复缓存会话。
    func switchToFresh(fallback: ListenMode?, forceReload: Bool = false) {
        if !forceReload, let s = sessions[.fresh], !s.queue.isEmpty {
            restoreSession(s)
            return
        }
        savedNormalMode = playMode
        playMode = .shuffle
        let lib = Library.shared
        let base = lib.tracks.isEmpty ? queue : lib.tracks
        guard !base.isEmpty else {
            if let fb = fallback { handleFailedSwitch(fallback: fb) }
            return
        }
        var fresh = base.shuffled()
        if let cur = current, let idx = fresh.firstIndex(where: { $0.id == cur.id }) {
            fresh.remove(at: idx)
            fresh.insert(cur, at: 0)
        }
        // 重新加载语义：从打乱后的第一首开始自动播放
        if let first = fresh.first {
            play(first, in: fresh)
        }
    }

    func randomIndex(excluding current: Int) -> Int {
        guard queue.count > 1 else { return 0 }
        var target = Int.random(in: 0..<queue.count)
        if target == current {
            target = (target + 1) % queue.count
        }
        return target
    }

    // MARK: - 下一首预加载

    /// 根据当前播放位置，为「接下来 N 首」预加载：
    /// 1) 预取并缓存签名播放 URL（切歌免再请求，缩短等待）；
    /// 长时间播放防护：控制播放队列上限，防止无限 loadMore 导致内存与索引成本持续累积。
    /// 队列超过上限且已播放较多时，移除「当前歌之前的已播放头部」（保留最近 keepBefore 首
    /// 供上滑回退），同步调整 currentIndex 并 bump 版本号通知页面。低频触发（每累积到上限一次）。
    func trimQueueIfNeeded() {
        let limit = 200      // 队列保留最近 200 首
        let keepBefore = 20  // 当前歌之前保留 20 首（可上滑回退）
        guard queue.count > limit, currentIndex > keepBefore else { return }
        let removeCount = currentIndex - keepBefore
        guard removeCount > 0 else { return }
        queue.removeFirst(removeCount)
        currentIndex -= removeCount
        queueRevision += 1   // 队列内容已变：通知 VerticalPager 刷新（当前页索引已同步）
    }

    /// 2) 用 AVURLAsset 预热连接/资源头部（只拉前一小部分 metadata，不整首下载）。
    /// 播放位置或队列变化时重新计算窗口；同一位置只预加载一次。
    /// 幂等且防重：切歌会取消旧任务，队列再变也不会错取已不在队列的歌。
    /// 两阶段执行：
    ///   阶段1 并行签发「接下来 N 首」的签名 URL 并立即补进 AVQueuePlayer，
    ///         让系统对下一首预缓冲真实音频数据（顺序切歌走 advanceToNextItem 秒开/无缝）；
    /// 预取启动闸门：**当前曲加载完成**后才开始预取「接下来 3 首」。
    /// 就绪判定——引擎：isPrepared（整曲已就绪/缓存命中）；原音：AVPlayer 已开始播放
    /// （timeControlStatus==.playing，或 readyToPlay 且 rate>0）。
    /// 每 0.2s 探测一次；暂停（isPlaying=false）时不预取也不阻塞（进入后自然放行）。
}
