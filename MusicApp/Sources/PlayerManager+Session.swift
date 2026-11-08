// PlayerManager+Session.swift
// P2-13：从 PlayerManager.swift 拆出的 会话恢复与预取 逻辑（extension 保持零引用改动）
import AVFoundation
import MediaPlayer
import UIKit
import Combine

extension PlayerManager {

    func saveSessionToDisk() {
        guard !queue.isEmpty, queue.indices.contains(currentIndex) else { return }
        // 截取当前歌附近的窗口（可上滑回退/下滑续听），控制 UserDefaults 体积
        let start = max(0, currentIndex - Self.sessionKeepBefore)
        let end = min(queue.count, currentIndex + Self.sessionKeepAfter + 1)
        let saved = Array(queue[start..<end])
        let idx = currentIndex - start
        let playIdx = PlayMode.allCases.firstIndex(of: playMode) ?? 0
        let normalIdx = PlayMode.allCases.firstIndex(of: savedNormalMode) ?? 0
        let s = SavedSession(queue: saved, currentIndex: idx,
                             listenMode: listenMode.rawValue,
                             playModeIndex: playIdx,
                             savedNormalModeIndex: normalIdx,
                             searchContext: Library.shared.searchContext,
                             wasPlaying: isPlaying,
                             savedAt: Date().timeIntervalSince1970,
                             sceneTag: sceneTag)
        if let data = try? JSONEncoder().encode(s) {
            UserDefaults.standard.set(data, forKey: Self.sessionKey)
        }
    }

    /// 冷启动恢复上次播放会话：
    /// - 距上次保存 < sessionResumeWindow（短时间重开）：恢复队列并播「上次那首的下一首」
    ///   （上次在队尾则回绕第一首，与列表循环语义一致），保持原听歌模式/循环模式；
    /// - 超过窗口（长时间重开）：丢弃旧会话返回 false，由调用方重新进入新鲜推荐流；
    /// 数据损坏/队列为空/索引越界同样返回 false 并清掉脏数据。
    func restoreLastSessionIfNeeded() -> Bool {
        guard queue.isEmpty else { return false }
        guard let data = UserDefaults.standard.data(forKey: Self.sessionKey),
              let s = try? JSONDecoder().decode(SavedSession.self, from: data),
              !s.queue.isEmpty, s.queue.indices.contains(s.currentIndex) else {
            UserDefaults.standard.removeObject(forKey: Self.sessionKey)
            return false
        }
        // 长时间重开（如隔 10 分钟）：不接着上次，重新进带随机性的新鲜推荐流
        let age = Date().timeIntervalSince1970 - s.savedAt
        guard age < Self.sessionResumeWindow else {
            UserDefaults.standard.removeObject(forKey: Self.sessionKey)
            return false
        }
        // 恢复模式 / 循环模式 / 搜索上下文 / 队列
        listenMode = ListenMode(rawValue: s.listenMode) ?? .normal
        // 场景上下文（2026-11-05）：上次听的是场景音乐 → 还原 sceneTag + 有限列表循环，
        // 侧滑栏据此高亮对应场景卡（否则重开 App 后场景"没显示/不高亮"）
        sceneTag = s.sceneTag
        sidebarSelection = nil
        finiteLoopList = (s.sceneTag != nil)
        if PlayMode.allCases.indices.contains(s.playModeIndex) {
            playMode = PlayMode.allCases[s.playModeIndex]
        }
        if PlayMode.allCases.indices.contains(s.savedNormalModeIndex) {
            savedNormalMode = PlayMode.allCases[s.savedNormalModeIndex]
        }
        // 搜索上下文只属于 normal 模式：liked/works 恢复时清空，避免污染后续推荐流
        if listenMode == .normal {
            Library.shared.searchContext = s.searchContext
        } else {
            Library.shared.searchContext = nil
        }
        queue = s.queue
        // 恢复队列里的歌加入推荐去重集合：后续滑到队尾续推不重复这批
        recommendedIds.formUnion(s.queue.map { $0.id })
        // 接着上次那首的下一首（上次在最后一首则回绕第一首，列表循环语义）
        let resume = s.queue.indices.contains(s.currentIndex + 1) ? s.currentIndex + 1 : 0
        currentIndex = resume
        playCurrent(autoResume: s.wasPlaying)
        return true
    }

    func saveSession(for mode: ListenMode) {
        guard !queue.isEmpty else { return }
        sessions[mode] = ModeSession(
            queue: queue,
            currentIndex: currentIndex,
            wasPlaying: isPlaying,
            searchContext: mode == .normal ? Library.shared.searchContext : nil,
            mode: mode
        )
    }

    /// 恢复模式会话：换回该模式的队列，播放「切走时那首的下一首」（从头开始）。
    /// normal 会话恢复时同步搜索上下文；发现页「全部歌曲」保持独立全库数据，
    /// 不再被播放队列覆盖（默认流已是推荐流，队列 ≠ 发现页数据源）。
    func restoreSession(_ s: ModeSession) {
        guard !s.queue.isEmpty, s.queue.indices.contains(s.currentIndex) else { return }
        if s.mode == .normal {
            let lib = Library.shared
            lib.searchContext = s.searchContext
        }
        // 切走时在最后一首：下一首回绕到第一首（列表循环语义）
        let resumeIndex = s.queue.indices.contains(s.currentIndex + 1) ? s.currentIndex + 1 : 0
        play(s.queue[resumeIndex], in: s.queue)
        if !s.wasPlaying { pausePlayback() }
    }

    /// 目标模式加载失败/无内容：回退到上一个模式。
    /// 上一模式无会话可恢复时回落到默认模式（曲库流保底），避免空队列挂起。
    func handleFailedSwitch(fallback: ListenMode) {
        guard fallback != listenMode else { return }
        let hasSession: Bool
        switch fallback {
        case .normal: hasSession = sessions[.normal] != nil
        case .works: hasSession = sessions[.works] != nil
        case .liked: hasSession = sessions[.liked] != nil
        case .fresh: hasSession = sessions[.fresh] != nil
        }
        guard hasSession else {
            listenMode = .normal
            restoreNormalStream()
            return
        }
        listenMode = fallback
        switch fallback {
        case .normal: restoreNormalStream()
        case .works: switchToWorks(fallback: nil)
        case .liked: switchToLiked(fallback: nil)
        case .fresh: switchToFresh(fallback: nil)
        }
    }

    /// 默认模式：侧滑栏进入（forceReload）重新拉推荐流并按记忆续播点定位下一首；
    /// 非重载时优先恢复缓存会话（切走那首的下一首续播），无缓存才进入个性化推荐流（汽水式无限听歌流）。
    /// 推荐流拉取失败时回退 Library 全库流保底，避免空队列挂起。
    func restoreNormalStream(forceReload: Bool = false) {
        playMode = savedNormalMode
        if !forceReload, let s = sessions[.normal], !s.queue.isEmpty {
            restoreSession(s)
            return
        }
        // 记忆续播：侧滑栏「默认模式」重载 → 新推荐流里定位「最近点播的歌」的下一首自动播放；无记忆从头
        startRecommendedStream(resumeId: resumeByMode[.normal], fallback: Library.shared.tracks)
    }

    /// 记录已推荐歌曲 id（外部以推荐列表为队列播放时调用），
    /// 供推荐流无限续推去重（exclude 已推荐批次）。
    func schedulePrefetchWhenReady() {
        prefetchReadyTimer?.invalidate()
        prefetchReadyTimer = nil
        let timer = Timer(timeInterval: 0.2, repeats: true) { [weak self] _ in
            guard let self else { return }
            let ready: Bool
            if self.usingEngine {
                ready = self.enginePlayer.isPrepared
            } else {
                ready = (self.player.timeControlStatus == .playing)
                    || (self.player.currentItem?.status == .readyToPlay && self.player.rate > 0)
            }
            if ready || !self.isPlaying {
                self.prefetchReadyTimer?.invalidate()
                self.prefetchReadyTimer = nil
                self.schedulePrefetch()
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        prefetchReadyTimer = timer
    }

    ///   阶段2 顺序预热元数据连接 + 封面 + 歌词 + 主色（不阻塞阶段1的 URL 就绪与入队）。
    func schedulePrefetch() {
        prefetchTask?.cancel()
        prefetchTask = nil
        // 离线：不预取（无网络请求可发，避免无用连接尝试）
        guard !NetworkMonitor.shared.isOffline else { return }
        guard !queue.isEmpty, queue.indices.contains(currentIndex) else { return }
        let start = currentIndex + 1
        let end = min(start + prefetchAheadCount, queue.count)
        guard start < end else { return }
        // 快照目标，异步期间队列变化不影响本次预取目标
        let targets = Array(queue[start..<end])
        prefetchTask = Task { [weak self] in
            guard let self else { return }
            // 阶段1：并行签发 URL（各自一次轻量往返），谁先就绪谁先入队
            await withTaskGroup(of: Void.self) { group in
                for t in targets {
                    group.addTask { [weak self] in
                        await self?.warmURLOnly(t)
                    }
                }
            }
            // 阶段2：顺序预热元数据/封面/歌词/主色（URL 已全部在池中）
            for t in targets {
                guard !Task.isCancelled else { return }
                await self.warmMetadata(for: t)
            }
        }
    }

    /// 只签发并缓存签名 URL + 同步补进 AVQueuePlayer（音频预缓冲的关键路径）。
    /// URL 池与 AVQueuePlayer 都只允许主线程访问，入队统一在主线程完成。
    func warmURLOnly(_ t: Track) async {
        guard !Task.isCancelled else { return }
        let cached = await MainActor.run { [weak self] in self?.signedURLPool[t.id] }
        if cached == nil {
            // 预取码率沿用播放决策（effectiveBitrate + 升级豁免），与播放一致；
            // 不再用 isCellular 自判——已升级曲目播放走原码率，预取却存 low URL 会复用失败（不秒开）
            let low = await MainActor.run { [weak self] in
                guard let self else { return false }
                return self.effectiveBitrate == .low && !self.bitrateUpgraded.contains(t.id)
            }
            if low {
                let tid = t.id
                Task { await API.prepareLowBitrate(for: tid) }
            }
            let fetched = await API.signedStreamURL(for: t.id, bitrate: low ? .low : .high)   // 非 Optional，恒返回可用 URL
            guard !Task.isCancelled else { return }
            _ = await MainActor.run { [weak self] () -> Bool in
                guard let self else { return false }
                if self.signedURLPool.count >= self.urlPoolLimit {
                    if let oldest = self.urlPoolOrder.first {
                        self.signedURLPool.removeValue(forKey: oldest)
                        self.urlPoolOrder.removeFirst()
                    }
                }
                self.signedURLPool[t.id] = fetched
                self.urlPoolOrder.append(t.id)
                self.syncPrefetchedItems()
                return true
            }
        } else {
            await MainActor.run { [weak self] in
                self?.syncPrefetchedItems()
            }
        }
    }

    /// 供外部（Library 冷启动等）提前预热单曲签名 URL：入池后 playCurrent 直接命中，
    /// 首曲播放省一次网络往返。不触发封面/歌词预热（避免冷启动做太多无用功）。
    func prewarm(_ t: Track) async {
        await warmURLOnly(t)
    }

    /// 把「已缓存签名 URL 的后续歌曲」持续补进 AVQueuePlayer：
    /// AVQueuePlayer 会自动预缓冲下一首的音频数据，顺序切歌时 advanceToNextItem 秒开。
    /// 严格保持队列顺序——第一个 URL 未就绪就停止，避免插队导致播放顺序错乱；
    /// 后续 URL 就绪时（warmURLOnly 回调）再继续补。引擎音效模式不操作 AVPlayer。
    func syncPrefetchedItems() {
        guard !usingEngine, !queue.isEmpty, queue.indices.contains(currentIndex),
              !player.items().isEmpty else { return }
        guard let lastItem = player.items().last,
              let lastId = Self.trackId(of: lastItem),
              let lastIdx = queue.firstIndex(where: { $0.id == lastId }) else { return }
        let existingIDs = player.items().compactMap { Self.trackId(of: $0) }
        let end = min(lastIdx + 1 + prefetchAheadCount, queue.count)
        var inserted = [String]()
        for i in (lastIdx + 1)..<end {
            let t = queue[i]
            guard let url = signedURLPool[t.id] else { break }
            guard !inserted.contains(t.id), !existingIDs.contains(t.id) else { continue }
            let item = AVPlayerItem(url: url)
            // 只预缓冲开头一小部分（15s ≈ low 96k ~180KB / high 原码率 ~700KB），
            // 非整曲下载：切到该曲时从此位置继续流式加载，秒开且不占整曲带宽
            item.preferredForwardBufferDuration = 15
            Self.setTrackId(t.id, on: item)
            player.insert(item, after: nil)
            inserted.append(t.id)
        }
    }

    /// 预热单首：URL 已由阶段1入池，这里用 AVURLAsset 预热连接/资源头部（只拉前一小部分 metadata），
    /// 再预取封面/歌词/主色到内存缓存，切到它时封面立即显示、歌词区直接命中。
    func warmMetadata(for t: Track) async {
        guard !Task.isCancelled else { return }
        // 离线/蜂窝弱网：跳过封面/歌词/主色预取，音频下载带宽优先
        let net = NetworkMonitor.shared
        guard !net.isOffline else { return }
        let url: URL? = await MainActor.run { [weak self] in self?.signedURLPool[t.id] }
        guard let url, !Task.isCancelled else { return }
        let asset = AVURLAsset(url: url)
        _ = try? await asset.load(.duration, .isPlayable)
        guard !Task.isCancelled else { return }
        if !net.isCellular {
            await prefetchCover(for: t)
            await prefetchLyric(for: t)
        }
        await prefetchPalette(for: t)
    }

    /// 预提取封面主色到 CoverPalette 缓存：命中缓存则直接返回；未命中则下载封面+提取主色+入库。
    /// 这样切到下一首时，loadBackground 的 CoverPalette.color 立即命中，
    /// 背景色与侧边栏主色（playerCardDominant 压暗 0.3）无需再等网络/计算。
    func prefetchPalette(for t: Track) async {
        guard let url = t.coverURL, !Task.isCancelled else { return }
        _ = await CoverPalette.color(for: t.id, from: url)
    }

    /// 预取封面图到缓存（CoverImageCache）：切歌时 CachedCoverImage 立即命中。
    /// P0-2：与 CachedCoverImage 统一走 decodeAndCache（ImageIO 降采样 + 写内存 + 写磁盘），
    /// 消除第三条独立取图管线（此前只写内存、全分辨率解码）。
    func prefetchCover(for t: Track) async {
        guard let url = t.coverURL, !Task.isCancelled else { return }
        if CoverImageCache.shared.image(for: url) != nil { return }   // 已缓存
        guard !Task.isCancelled else { return }
        guard let (data, _) = try? await URLSession.shared.data(from: url),
              !Task.isCancelled else { return }
        _ = CoverImageCache.decodeAndCache(data: data, for: url)
    }

    /// 预取歌词到内存缓存（LyricsCache）：切歌时播放页直接查缓存，跳过网络+解析。
    func prefetchLyric(for t: Track) async {
        guard t.hasLyric == true, !Task.isCancelled else { return }
        if LyricsCache.shared.lines(for: t.id) != nil { return }       // 已缓存
        guard !Task.isCancelled else { return }
        guard let text = try? await API.fetchLyric(t.id), !Task.isCancelled else { return }
        let lines = parseLyrics(text)
        if !lines.isEmpty { LyricsCache.shared.store(lines, for: t.id) }
    }


}
