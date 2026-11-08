// PlayerManager+Playback.swift
// P2-13：从 PlayerManager.swift 拆出的 播放引擎 逻辑（extension 保持零引用改动）
import AVFoundation
import MediaPlayer
import UIKit
import Combine

extension PlayerManager {

    func applyEQ(_ name: String) {
        guard name != selectedEQ else { return }
        LogReporter.shared.log(level: "info", tag: "eq", message: "applyEQ 切换音效 name=\(name) 当前=\(selectedEQ) loadFailed=\(loadFailed)")
        // 先取消进行中的引擎预加载切换（再次切换/切歌/失败都会重新发起）
        engineSwitchTask?.cancel()
        engineSwitchTask = nil
        engineSwitchInProgress = false
        engineDownloadFraction = 0
        if loadFailed {
            selectedEQ = name
            reloadCurrent()   // 失败态：切音效即重试（playCurrent 按新音效加载）
            return
        }
        let isEngine = (name != "原音")
        if usingEngine == isEngine {
            selectedEQ = name
            if isEngine {
                let newMode = Self.engineMode(for: name)
                // 涉及全景（多轨链）的进入/退出必须重建播放链，不能只改参数
                if newMode == .panorama || enginePlayer.isMultiTrack {
                    reloadCurrent()
                } else {
                    // 引擎模式间切换：直接换参数，无需重载
                    enginePlayer.setMode(newMode)
                    showEQToast("\(name)已生效")
                    LogReporter.shared.log(level: "info", tag: "eq", message: "applyEQ 引擎模式内切换完成 name=\(name)")
                    updateNowPlaying(force: true)
                }
            }
            // 原音 → 原音：无操作
        } else if isEngine {
            // 原音 → 引擎：无缝接力。AVPlayer 继续播放（原音出声、可拖动），
            // 引擎在后台下载整曲；就绪后从 AVPlayer 当前进度无缝接管。
            guard let current = current else { return }
            let targetID = current.id
            let mode = Self.engineMode(for: name)
            selectedEQ = name
            engineSwitchInProgress = true
            engineDownloadFraction = 0
            let task = Task {
                // 弱网：引擎预下载也用低码率 + 共享缓存优先（下载体积减半，命中缓存免下载）
                let (url, bitrate) = await self.playbackURL(for: targetID)
                guard !Task.isCancelled else { return }
                // 全景模式：查分离音轨（trigger=1 同时触发后台分离）；done 才走多轨
                var multiURLs: (v: URL, a: URL)?
                if mode == .panorama,
                   let stem = await API.stemInfo(for: targetID, trigger: true),
                   stem.status == "done",
                   let vStr = stem.vocalsUrl, let aStr = stem.accUrl,
                   let vURL = URL(string: vStr), let aURL = URL(string: aStr) {
                    multiURLs = (vURL, aURL)
                }
                await MainActor.run { self.activeBitrate = bitrate }
                await MainActor.run {
                    // 预下载期间切歌/切回原音/再次切换：丢弃迟到接管并清状态
                    guard self.current?.id == targetID, self.selectedEQ != "原音" else {
                        self.engineSwitchInProgress = false
                        self.engineDownloadFraction = 0
                        return
                    }
                    // 引擎回调接线（接管后由引擎驱动进度/完成/缓冲）
                    self.enginePlayer.onProgress = { [weak self] cur, dur in
                        guard let self = self else { return }
                        // 加载期间拖动待应用：进度数据一到就跳过去
                        if let pf = self.pendingSeekFraction, dur > 0 {
                            self.pendingSeekFraction = nil
                            let t = pf * dur
                            self.enginePlayer.seek(toSeconds: t)
                            self.currentTime = t
                            self.progress = min(max(pf, 0), 1)
                            return
                        }
                        self.currentTime = cur
                        self.duration = dur
                        self.progress = dur > 0 ? min(cur / dur, 1) : 0
                        self.updateNowPlaying()
                    }
                    self.enginePlayer.onCompletion = { [weak self] in
                        guard let self else { return }
                        if self.listen.trackId == self.current?.id {
                            self.listen.completedPlays += 1
                        }
                        self.flushListen(.complete)
                        self.next()
                    }
                    self.enginePlayer.onLoadingChanged = { [weak self] loading in
                        self?.isBuffering = loading
                        if !loading { self?.bufferProgress = 1 }
                    }
                    self.enginePlayer.onDownloadProgress = { [weak self] f in
                        self?.engineDownloadFraction = f
                    }
                    // 预加载失败：AVPlayer 仍在正常播，回退原音继续，不触发失败自愈
                    self.enginePlayer.onLoadFailed = { [weak self] in
                        guard let self else { return }
                        DispatchQueue.main.async {
                            if self.engineSwitchInProgress {
                                self.engineSwitchInProgress = false
                                self.engineDownloadFraction = 0
                                self.selectedEQ = "原音"
                                self.showEQToast("音效加载失败，已恢复原音")
                                self.updateNowPlaying(force: true)
                            } else {
                                self.handlePlaybackFailure()
                            }
                        }
                    }
                    if let m = multiURLs {
                        // 多轨：人声前景 + 伴奏 HRTF 包围
                        self.enginePlayer.prepareMultiTrack(
                            vocalsURL: m.v, accURL: m.a,
                            trackId: targetID, mode: mode) { [weak self] in
                            guard let self else { return }
                            self.engineSwitchInProgress = false
                            self.engineDownloadFraction = 0
                            guard self.current?.id == targetID, self.selectedEQ != "原音" else {
                                self.enginePlayer.stop()
                                return
                            }
                            var resume = self.currentTime
                            if let pf = self.pendingSeekFraction, self.duration > 0 {
                                resume = pf * self.duration
                            }
                            self.pendingSeekFraction = nil
                            let shouldPlay = self.isPlaying
                            self.enginePlayer.playMultiFrom(seconds: resume, autoPlay: shouldPlay)
                            self.usingEngine = true
                            self.player.pause()
                            self.player.replaceCurrentItem(with: nil)
                            LogReporter.shared.log(level: "info", tag: "eq",
                                                   message: "applyEQ 多轨接管成功 track=\(targetID) resume=\(resume)")
                            self.showEQToast("全景音效已生效")
                            self.updateNowPlaying(force: true)
                        }
                    } else {
                        self.enginePlayer.prepare(url: url,
                                                  trackId: targetID,
                                                  low: self.activeBitrate == .low,
                                                  mode: mode) { [weak self] in
                            guard let self else { return }
                            self.engineSwitchInProgress = false
                            self.engineDownloadFraction = 0
                            // 就绪后再次确认未被切歌/切回原音
                            guard self.current?.id == targetID, self.selectedEQ != "原音" else {
                                self.enginePlayer.stop()   // 丢弃就绪结果，释放下载文件
                                LogReporter.shared.log(level: "info", tag: "eq", message: "applyEQ 引擎就绪但已切走，丢弃 track=\(targetID)")
                                return
                            }
                            // 无缝接管：取 AVPlayer 最新进度 → 停 AVPlayer → 引擎从该处续播
                            var resume = self.currentTime
                            if let pf = self.pendingSeekFraction, self.duration > 0 {
                                resume = pf * self.duration
                            }
                            self.pendingSeekFraction = nil
                            let shouldPlay = self.isPlaying
                            self.enginePlayer.playFrom(seconds: resume, autoPlay: shouldPlay)
                            self.usingEngine = true
                            self.player.pause()
                            self.player.replaceCurrentItem(with: nil)
                            LogReporter.shared.log(level: "info", tag: "eq", message: "applyEQ 无缝接管成功 track=\(targetID) resume=\(resume)")
                            self.showEQToast("\(name)已生效")
                            self.updateNowPlaying(force: true)
                        }
                    }
                }
            }
            engineSwitchTask = task
        } else {
            // 引擎 → 原音：跨播放器切换。记录切换瞬间进度与播放状态，
            // AVPlayer 加载完成后从该位置无缝续播（不从头、不闪零）；暂停中切换则保持暂停。
            let wasPlaying = isPlaying
            let resumeSeconds = currentTime
            selectedEQ = name
            usingEngine = isEngine
            playCurrent(flushReason: .heartbeat,
                        resumeSeconds: resumeSeconds,
                        autoResume: wasPlaying,
                        preserveProgress: true)
        }
    }

    /// 音效名称 → 引擎模式映射（与 VerticalPager eqOptions 一一对应）
    static func engineMode(for name: String) -> AudioEnginePlayer.Mode {
        switch name {
        case "3D 立体音效": return .surround3D
        case "360 环绕": return .surround360
        case "全景音效": return .panorama
        case "复古": return .retro
        case "现场": return .live
        case "流行": return .pop
        case "摇滚": return .rock
        default: return .original
        }
    }

    /// 切换音效后重新加载当前歌曲以应用新音效（同曲重载，不算切歌/跳过）
    func reloadCurrent() {
        guard current != nil else { return }
        let wasPlaying = isPlaying
        playCurrent(flushReason: .heartbeat,
                    resumeSeconds: currentTime,
                    autoResume: wasPlaying,
                    preserveProgress: true)
    }


    // MARK: - AVPlayerItem ↔ 歌曲 id 关联（判断 AVQueuePlayer 队列是否已预取到目标歌）

    /// 取 AVPlayerItem 上关联的歌曲 id（nil = 未关联）
    static func trackId(of item: AVPlayerItem) -> String? {
        objc_getAssociatedObject(item, &trackIdAssocKey) as? String
    }
    /// 在 AVPlayerItem 上关联歌曲 id（队列连续性判断用）
    static func setTrackId(_ id: String, on item: AVPlayerItem) {
        objc_setAssociatedObject(item, &trackIdAssocKey, id, .OBJC_ASSOCIATION_RETAIN_NONATOMIC)
    }

    /// 重建 AVQueuePlayer 队列：当前歌 + 后续若干首（用预取签名 URL，未命中的跳过）。
    /// AVQueuePlayer 会自动预取下一个 item 的数据，顺序切下一首时可秒开、无需等网络首包。
    func prepareQueuePlayer(currentIndex idx: Int, currentURL: URL) {
        player.removeAllItems()
        let cur = AVPlayerItem(url: currentURL)
        cur.preferredForwardBufferDuration = 30   // 向前多缓冲 30s，减少卡顿
        Self.setTrackId(queue[idx].id, on: cur)
        player.insert(cur, after: nil)
        // 后续限 prefetchAheadCount 首，用已缓存的签名 URL（缓存未命中则先停，
        // 保持顺序；切到时兜底重建，之后由 syncPrefetchedItems 按就绪顺序补插）
        let end = min(idx + 1 + prefetchAheadCount, queue.count)
        for i in (idx + 1)..<end {
            guard let u = signedURLPool[queue[i].id] else { break }
            let item = AVPlayerItem(url: u)
            item.preferredForwardBufferDuration = 30
            Self.setTrackId(queue[i].id, on: item)
            player.insert(item, after: nil)
        }
    }

    /// 顺序切歌（advanceToNextItem）后补充队列：把「队列末尾歌之后」的后续歌补 insert，
    /// 让 AVQueuePlayer 始终保有「当前歌之后 prefetchAheadCount 首」可预取，避免连续切歌几首后队列耗尽。
    /// 已存在的项不重复插入；预取 URL 未命中的先停（保持顺序），就绪后由 syncPrefetchedItems 补插。
    func topUpQueue() {
        guard !queue.isEmpty, let lastItem = player.items().last,
              let lastId = Self.trackId(of: lastItem),
              let lastIdx = queue.firstIndex(where: { $0.id == lastId }) else { return }
        let end = min(lastIdx + 1 + prefetchAheadCount, queue.count)
        var inserted = [String]()
        let existingIDs = player.items().compactMap { Self.trackId(of: $0) }
        for i in (lastIdx + 1)..<end {
            let t = queue[i]
            guard let u = signedURLPool[t.id] else { break }
            guard !inserted.contains(t.id), !existingIDs.contains(t.id) else { continue }
            inserted.append(t.id)
            let item = AVPlayerItem(url: u)
            item.preferredForwardBufferDuration = 30
            Self.setTrackId(t.id, on: item)
            player.insert(item, after: nil)
        }
    }

    func handleBufferingStall() {
        guard !loadFailed, let trackId = current?.id else { return }
        // 播放中被卡死：标记「网络中断待续播」（若用户此刻手动暂停则清除）
        if isPlaying { interruptedWhilePlaying = true }
        // 同一首歌已自动重试过：不再重载，直接走失败跳歌
        guard autoRetriedTrackId != trackId else {
            handlePlaybackFailure()
            return
        }
        autoRetriedTrackId = trackId
        // 弱网低码率卡死：升级原码率再试一次（low → high 兜底；播放 URL 决策自动生效）
        if effectiveBitrate == .low, !bitrateUpgraded.contains(trackId) {
            bitrateUpgraded.insert(trackId)
        }
        let resume = currentTime
        // 重载并保持进度：加载完成后 seek 回卡死前位置续播（弱网恢复/服务器响应后无感续播）
        playCurrent(flushReason: .heartbeat,
                    resumeSeconds: resume,
                    autoResume: true,
                    preserveProgress: true)
    }

    /// 播放失败统一自愈入口（原音 AVPlayer 失败通知 / item status failed / 缓冲卡死 / 3D 引擎加载失败共用）。
    /// 行为：优先同曲自动重试一次（弱网瞬时抖动场景无感恢复），再失败才自动切下一首；
    /// 连续失败超过上限则停止，避免无限循环切歌。
    func handlePlaybackFailure() {
        guard !loadFailed else { return }   // 已在失败状态，避免重复触发
        // 播放中被失败打断：标记「网络中断待续播」（若用户此刻手动暂停则清除）
        if isPlaying { interruptedWhilePlaying = true }
        // 同一首歌尚未自动重试过：先同曲重载一次（1.5s 后，避开失败瞬间的抖动叠加）。
        // 期间 loadFailed=true（UI 展示重试入口），用户手动点重试会清除并提前重载。
        if autoRetriedTrackId != current?.id {
            autoRetriedTrackId = current?.id
            // 弱网低码率失败：升级原码率再试（low → high 兜底）
            if effectiveBitrate == .low, let id = current?.id, !bitrateUpgraded.contains(id) {
                bitrateUpgraded.insert(id)
            }
            loadFailed = true
            isBuffering = false
            isPlaying = false
            updateNowPlaying(force: true)
            let retryTrackId = current?.id
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in
                guard let self,
                      self.loadFailed,                       // 用户手动重试过则跳过
                      self.current?.id == retryTrackId else { return }  // 已切走则跳过
                self.loadFailed = false
                self.playCurrent(flushReason: .heartbeat,
                                 resumeSeconds: self.currentTime,
                                 autoResume: true,
                                 preserveProgress: true)
            }
            return
        }
        // 已重试过仍失败：真正的失败处理——切下一首，连续失败超过上限则停止
        loadFailed = true
        isBuffering = false
        isPlaying = false
        bufferingStallStart = nil
        consecutiveFailCount += 1
        updateNowPlaying(force: true)

        // 连续失败超过上限：停止自动切歌，停在当前状态让用户手动处理
        guard consecutiveFailCount <= maxConsecutiveFails else {
            return
        }
        // 延迟 1.5s 自动切下一首；期间用户手动操作则取消
        let failIdx = currentIndex
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in
            guard let self,
                  self.loadFailed,
                  self.currentIndex == failIdx else { return } // 用户已手动切走
            self.next()
        }
    }

    /// 从搜索/发现页直接播放普通流歌曲：若当前处于「我的喜欢/新鲜」模式，
    /// 先保存该模式会话并切回默认模式，保证界面显示的模式与实际播放流一致。
    func seek(toSeconds seconds: Double) {
        if usingEngine {
            enginePlayer.seek(toSeconds: seconds)
        } else {
            player.seek(to: CMTime(seconds: seconds, preferredTimescale: 600))
        }
    }

    // MARK: - Private

    /// 播放 URL 决策（缓存共享 + 码率档 + W2 缓存优先任意码率）：
    /// 命中任意码率缓存 → file:// 本地（秒开/离线/秒切换）；否则按码率档签发网络 URL。
    /// 返回 URL 与对应码率：命中文件的码率决定返回档位（与 prepare(low:) 缓存键严格一致，
    /// 避免"返回 high 文件却传 low 标记"导致引擎重复下载）。
    func playbackURL(for trackId: String) async -> (URL, API.Bitrate) {
        if let cached = AudioCache.shared.existing(trackId: trackId, low: true) {
            return (cached, .low)
        }
        if let cached = AudioCache.shared.existing(trackId: trackId, low: false) {
            return (cached, .high)
        }
        let low = effectiveBitrate == .low && !bitrateUpgraded.contains(trackId)
        let bitrate: API.Bitrate = low ? .low : .high
        return (await API.signedStreamURL(for: trackId, bitrate: bitrate), bitrate)
    }

    func playCurrent(flushReason: ListenFlushReason? = nil,
                             resumeSeconds: Double? = nil,
                             autoResume: Bool = true,
                             preserveProgress: Bool = false) {
        guard let current = current else { return }
        // 切歌/重载会终止一切进行中的切换：取消引擎预加载任务、清空待应用拖动
        engineSwitchTask?.cancel()
        engineSwitchTask = nil
        engineSwitchInProgress = false
        engineDownloadFraction = 0
        pendingSeekFraction = nil
        // 取消上一首的加载任务（快速连续切歌时旧签名 URL 请求不再继续）
        loadTask?.cancel()
        loadTask = nil
        // 取消未完成的预取启动轮询（新歌就绪后重新探测）
        prefetchReadyTimer?.invalidate()
        prefetchReadyTimer = nil
        // 先结算上一首的收听：真正切歌才可能产生“跳过”负向信号；
        // 同一首歌重播（单曲循环/音效重载）只结算有效收听，不算跳过
        let sameTrack = (listen.trackId == current.id)
        flushListen(flushReason ?? (sameTrack ? .heartbeat : .switchTrack))
        // 新歌：重置收听状态；同一首歌重播：保留累计（用于“听了几遍”）
        if !sameTrack {
            // P1 归因：携带当前曲推荐来源路（推荐流播放才有；歌单/搜索播放为空串）
            listen = ListenState(trackId: current.id,
                                 route: current.recRoute ?? "")
            // 新歌重新获得一次自动重试机会（自动重试按歌曲 id 防重）
            autoRetriedTrackId = nil
        }
        usingEngine = (selectedEQ != "原音")
        // 状态立即重置，UI 先反馈（避免切歌后旧状态残留）。
        // autoResume=false（暂停中跨播放器切换）：保持暂停态，恢复播放时从续播位置走
        isPlaying = autoResume
        // 跨播放器无缝续播（原音↔引擎音效）：保留当前进度显示，
        // 加载完成后从 resumeSeconds 位置接续，避免进度闪烁归零
        if !preserveProgress {
            duration = 0
            progress = 0
            currentTime = 0
            bufferProgress = 0
        }
        isBuffering = true
        loadFailed = false
        pendingArtwork = nil
        currentArtwork = nil
        bufferingStallStart = nil

        // 触发 AI 配色主题请求（后台异步，不阻塞播放；Folia major 移植）
        Task { @MainActor in
            await AIThemeService.shared.fetchTheme(for: current.id)
        }

        // 顺序切下一首时，AVQueuePlayer 队列已预取目标歌（下一项）→ 保留队列不清空，
        // 在播放分支里用 advanceToNextItem 秒开；其余场景（跳播/插播/模式切换）才清空重建。
        // 三态判断：
        //  alreadyOnTarget —— AVQueuePlayer 已自动切到目标歌（播完自动续播）：不重建不 advance，直接续播；
        //  canAdvance      —— 队列下一项正是目标歌（预取就绪）：advanceToNextItem 秒开；
        //  两者皆否        —— 跳播/插播/模式切换：清空重建。
        let currentItemId = player.currentItem.flatMap { Self.trackId(of: $0) }
        let nextTrackId = player.items().count > 1 ? Self.trackId(of: player.items()[1]) : nil
        let alreadyOnTarget = (currentItemId == current.id)
        let canAdvance = (nextTrackId == current.id)
        if !alreadyOnTarget && !canAdvance {
            // 关键：切歌瞬间同步终止上一首的播放与加载，而不是等新歌 URL 就绪后再替换——
            // AVPlayer：清空当前 item，立刻断开旧音频流（正在进行的缓冲/播放立即停止），
            // 这样即使新歌的签名 URL 还在网络请求中，上一首也已经不再发声、不再占用带宽
            player.pause()
            player.volume = 1.0   // 切歌时音量增益复位，防止残留状态影响新歌
            player.replaceCurrentItem(with: nil)
        }
        if (alreadyOnTarget || canAdvance) && !autoResume {
            // B17：暂停状态下切歌（复用预取队列但保持暂停）——
            // 先停掉上一首的出声，避免"有声音却显示暂停"的不一致状态
            player.pause()
        }
        enginePlayer.stop()

        let task = Task { [weak self] in
            guard let self else { return }
            let trackId = current.id
            // 播放 URL 决策（弱网 + 缓存共享 + W2 缓存优先任意码率）：
            // ① 共享缓存命中（引擎下载过/预热过的整曲，任意码率）→ file:// 本地播放
            //    （秒开 + 离线可播 + 原音/引擎切换秒开；蜂窝播本地 high 不耗流量、必然流畅，
            //     码率语义只在「需要下载/签发」时生效）
            // ② 预取签名 URL 池命中且码率与当前决策一致 → 免签发直接播（秒开）
            // ③ 其余 → 按码率档实时签发（蜂窝=low 96k / Wi-Fi=原码率）
            // 注意：signedStreamURL 内部 try? 静默回退普通 URL，不抛错；
            // 若回退后的 URL 仍加载失败，由 AVPlayerItem failed / 缓冲卡死自愈兜底。
            let lowDecision = (effectiveBitrate == .low && !bitrateUpgraded.contains(trackId))
            let cachedLow = AudioCache.shared.existing(trackId: trackId, low: true)
            let cachedHigh = AudioCache.shared.existing(trackId: trackId, low: false)
            let url: URL
            let bitrate: API.Bitrate
            if let cached = lowDecision ? (cachedLow ?? cachedHigh) : (cachedHigh ?? cachedLow) {
                url = cached
                bitrate = (cached == cachedLow) ? .low : .high   // 命中文件的码率决定档位
            } else if let poolURL = await MainActor.run { () -> URL? in
                // 码率与当前决策一致才复用（弱网语义严格：蜂窝必须 low，Wi-Fi 不播 low）
                guard let c = self.signedURLPool[trackId],
                      c.absoluteString.contains("br=96") == (lowDecision) else { return nil }
                self.signedURLPool.removeValue(forKey: trackId)
                self.urlPoolOrder.removeAll { $0 == trackId }
                return c
            } {
                url = poolURL
                bitrate = lowDecision ? .low : .high
            } else {
                url = await API.signedStreamURL(for: trackId, bitrate: lowDecision ? .low : .high)
                bitrate = lowDecision ? .low : .high
            }
            _ = await MainActor.run { self.activeBitrate = bitrate }
            guard !Task.isCancelled else { return }
            // 先在 async 上下文取引擎模式与分离状态（同步的 MainActor.run 闭包内不能 await）
            let engineMode = await MainActor.run { Self.engineMode(for: self.selectedEQ) }
            let stem = (engineMode == .panorama) ? await API.stemInfo(for: trackId, trigger: true) : nil
            await MainActor.run {
                // 切歌后旧任务迟到响应直接丢弃，防止覆盖新歌状态
                guard self.current?.id == trackId else { return }
                if self.usingEngine {
                    self.enginePlayer.onProgress = { [weak self] cur, dur in
                        guard let self = self else { return }
                        // 加载期间拖动待应用：进度数据一到就跳过去
                        if let pf = self.pendingSeekFraction, dur > 0 {
                            self.pendingSeekFraction = nil
                            let t = pf * dur
                            self.enginePlayer.seek(toSeconds: t)
                            self.currentTime = t
                            self.progress = min(max(pf, 0), 1)
                            return
                        }
                        self.currentTime = cur
                        self.duration = dur
                        self.progress = dur > 0 ? min(cur / dur, 1) : 0
                        self.updateNowPlaying()
                    }
                    self.enginePlayer.onCompletion = { [weak self] in
                        guard let self else { return }
                        // 完整听完一首：累计“听了几遍”，并结算收听时长
                        if self.listen.trackId == self.current?.id {
                            self.listen.completedPlays += 1
                        }
                        self.flushListen(.complete)
                        self.next()
                    }
                    self.enginePlayer.onLoadingChanged = { [weak self] loading in
                        self?.isBuffering = loading
                        if !loading { self?.bufferProgress = 1 }
                    }
                    // 3D 引擎加载失败也要走统一自愈（此前只 print，UI 无感知 → 静默卡死）
                    self.enginePlayer.onLoadFailed = { [weak self] in
                        guard let self else { return }
                        DispatchQueue.main.async {
                            self.handlePlaybackFailure()
                        }
                    }
                    // 全景模式：优先用服务端分离的人声/伴奏多轨；未就绪则回退单文件 HRTF
                    // （stemInfo 已在闭包外请求，trigger=1 已让服务端后台分离，下次播放即生效）
                    if engineMode == .panorama,
                       let stem,
                       stem.status == "done",
                       let vStr = stem.vocalsUrl, let aStr = stem.accUrl,
                       let vURL = URL(string: vStr), let aURL = URL(string: aStr) {
                        self.enginePlayer.loadMultiTrack(
                            vocalsURL: vURL, accURL: aURL, trackId: current.id,
                            mode: engineMode,
                            startSeconds: resumeSeconds ?? 0,
                            autoPlay: autoResume)
                    } else {
                        self.enginePlayer.load(url: url,
                                               trackId: current.id,
                                               low: self.activeBitrate == .low,
                                               mode: engineMode,
                                               startSeconds: resumeSeconds ?? 0,
                                               autoPlay: autoResume)
                    }
                } else {
                    // 顺序切歌且队列已预取目标歌：直接 advanceToNextItem 秒开（AVQueuePlayer 已预缓冲音频）；
                    // 已由 AVQueuePlayer 自动切到目标歌（播完自动续播）：不重建不 advance，直接续播；
                    // 其余（跳播/插播/模式切换）：重建队列——当前歌 + 后续若干首（用预取签名 URL）
                    if canAdvance {
                        self.player.advanceToNextItem()
                        self.topUpQueue()   // 补充队列，保持后续始终有可预取
                    } else if !alreadyOnTarget {
                        self.prepareQueuePlayer(currentIndex: self.currentIndex, currentURL: url)
                    }
                    if let rs = resumeSeconds {
                        // 无缝续播：先定位到切换前进度，再按原播放状态决定是否播放
                        if autoResume {
                            self.player.seek(to: CMTime(seconds: rs, preferredTimescale: 600)) { _ in
                                DispatchQueue.main.async { self.safePlay() }
                            }
                        } else {
                            self.player.seek(to: CMTime(seconds: rs, preferredTimescale: 600))
                        }
                    } else if autoResume {
                        self.safePlay()
                    }
                }
                self.updateNowPlaying(force: true)   // 切歌：立即刷新锁屏信息
                // 预取时序：当前曲真正加载完成（引擎 isPrepared / 原音已开始播放）后才为
                // 「接下来 3 首」启动预加载——预取绝不与当前曲抢加载带宽；
                // 且预取只取每首开头一小部分（AVQueuePlayer 系统预缓冲，非整曲下载）。
                self.schedulePrefetchWhenReady()
            }
        }
        loadTask = task
        loadArtworkIfNeeded()
        Task {
            await API.reportPlay(current.id)
            // 播放历史本地存储（MRU 去重置顶）：同一首歌重复播放直接排到第一
            LocalPlayHistory.record(track: current)
            // 后端上报保留（听歌时长统计/推荐行为数据依赖），失败静默不影响播放
            await API.recordHistory(
                trackId: current.id,
                title: current.title,
                artist: current.artist ?? "",
                cover: current.coverURL?.absoluteString ?? ""
            )
        }
    }

    /// 异步加载歌曲封面用于锁屏展示（无封面则保持默认渐变封面）。
    /// 下载完成时一次性构建 MPMediaItemArtwork 缓存起来，避免 0.1s 进度刷新反复创建。
    /// ★ 只做「纯降采样」，**不写入 CoverImageCache**：该缓存会被 CoverPalette 取色复用，
    ///   写入 900px 版本会让取色输入分辨率与原先（512px 独立解码）不一致，导致主色漂移。
    func loadArtworkIfNeeded() {
        guard let url = current?.coverURL else { return }
        URLSession.shared.dataTask(with: url) { [weak self] data, _, _ in
            guard let self, let data,
                  let image = CoverImageCache.downsample(data: data, maxPixel: 900) ?? UIImage(data: data) else { return }
            DispatchQueue.main.async {
                // 切歌后旧封面迟到：丢弃，防止旧封面盖到新歌上
                guard self.current?.coverURL == url else { return }
                self.pendingArtwork = image
                self.currentArtwork = MPMediaItemArtwork(boundsSize: image.size) { _ in image }
                self.updateNowPlaying(force: true)   // 封面就绪：立即刷新锁屏封面
            }
        }.resume()
    }

    func setupRemoteCommands() {
        let center = MPRemoteCommandCenter.shared()
        center.playCommand.isEnabled = true
        center.pauseCommand.isEnabled = true
        center.togglePlayPauseCommand.isEnabled = true
        center.nextTrackCommand.isEnabled = true
        center.previousTrackCommand.isEnabled = true
        center.changePlaybackPositionCommand.isEnabled = true

        // MPRemoteCommandCenter 的回调在任意线程触发，
        // 而 @Published 状态的写入必须在主线程，否则存在数据竞争。
        // 所有命令统一转发回主线程执行。
        center.playCommand.addTarget { [weak self] _ in
            DispatchQueue.main.async { self?.resumePlayback() }
            return .success
        }
        center.pauseCommand.addTarget { [weak self] _ in
            DispatchQueue.main.async { self?.pausePlayback() }
            return .success
        }
        center.togglePlayPauseCommand.addTarget { [weak self] _ in
            DispatchQueue.main.async { self?.toggle() }
            return .success
        }
        center.nextTrackCommand.addTarget { [weak self] _ in
            DispatchQueue.main.async { self?.next() }
            return .success
        }
        center.previousTrackCommand.addTarget { [weak self] _ in
            DispatchQueue.main.async { self?.previous() }
            return .success
        }
        center.changePlaybackPositionCommand.addTarget { [weak self] event in
            guard let e = event as? MPChangePlaybackPositionCommandEvent else {
                return .commandFailed
            }
            let seconds = e.positionTime
            DispatchQueue.main.async { self?.seek(toSeconds: seconds) }
            return .success
        }
    }

    // MARK: - 音频打断处理（微信语音 / 来电等打断后自动恢复播放）

    func setupInterruptionHandling() {
        NotificationCenter.default.addObserver(
            forName: AVAudioSession.interruptionNotification,
            object: nil,
            queue: .main
        ) { [weak self] note in
            guard let self = self,
                  let info = note.userInfo,
                  let typeValue = info[AVAudioSessionInterruptionTypeKey] as? UInt else { return }

            if typeValue == AVAudioSession.InterruptionType.began.rawValue {
                // 被打断（其他 App 抢占 / 来电 / 语音）：真正暂停当前播放器。
                // 之前只改 isPlaying 状态，AVPlayer 仍被系统静音挂着，
                // 恢复时容易状态错乱；引擎/原音两个播放器必须都停（防叠加）。
                self.enginePlayer.pause()
                self.player.pause()
                self.isPlaying = false
                self.updateNowPlaying(force: true)
            } else if typeValue == AVAudioSession.InterruptionType.ended.rawValue {
                // 打断结束：系统允许恢复时重新激活会话、复位音量并继续播放。
                // 必须按当前实际播放器恢复：引擎音效走 enginePlayer，原音走 AVPlayer。
                // 之前无条件 player.play() 会导致 3D 模式下两个播放器同时输出同一首歌，
                // 音量叠加翻倍、声音错乱（时大时小的直接来源）。
                guard let option = info[AVAudioSessionInterruptionOptionKey] as? UInt else { return }
                if AVAudioSession.InterruptionOptions(rawValue: option).contains(.shouldResume) {
                    try? AVAudioSession.sharedInstance().setActive(true)
                    self.restoreOutputVolume()
                    self.safeResumePlayback()
                }
            }
        }
    }

    /// 恢复输出音量：系统可能因打断 / 音频闪避（ducking）/ 后台降权改变输出增益，
    /// 恢复播放前统一复位，避免「切到其他应用再回来声音变小/变小后不复原」。
    func restoreOutputVolume() {
        player.volume = 1.0
        enginePlayer.restoreVolume()
    }

    // MARK: - 音频路由变化处理（拔耳机 / 蓝牙断连）

    /// P0-4：routeChange 监听——iOS 16 上拔耳机/蓝牙断连时系统不会自动暂停，
    /// 音频会突然切到扬声器外放（隐私缺口）。检测到输出设备被移除立即暂停。
    /// iOS 17+ 已声明 setPrefersInterruptionOnRouteDisconnect(true) 由系统接管。
    func setupRouteChangeHandling() {
        NotificationCenter.default.addObserver(
            forName: AVAudioSession.routeChangeNotification,
            object: nil,
            queue: .main
        ) { [weak self] note in
            guard let self,
                  let reasonValue = note.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt,
                  let reason = AVAudioSession.RouteChangeReason(rawValue: reasonValue) else { return }
            // 输出设备被移除（拔耳机/蓝牙断连/车载断开）：立即暂停，防止隐私外放
            if reason == .oldDeviceUnavailable {
                self.pausePlayback()
            }
        }
    }

    /// 引擎被系统停止后的自动恢复：
    /// 1) App 回前台：后台期间 AVAudioEngine 可能被系统停止（后台优化），引擎不再输出 → 回前台无声。
    /// 2) 音频会话配置变化（耳机插拔/采样率/蓝牙切换）：AVAudioEngine 会自动停止，
    ///    不处理则引擎播放从此无声（用户反馈「切后台再回来没声音」的直接来源）。
    /// 两者统一走 enginePlayer.restartIfNeeded()：检测引擎未运行则重启并挂载、从推算位置续播。
    func setupEngineRestoreHandling() {
        NotificationCenter.default.addObserver(
            forName: UIApplication.willEnterForegroundNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            guard let self else { return }
            // 先确保音频会话激活（后台期间可能被停用），再复位音量、恢复引擎
            try? AVAudioSession.sharedInstance().setActive(true)
            self.restoreOutputVolume()
            self.enginePlayer.restartIfNeeded()
            // 后台期间若被系统静音/暂停（无 interruption 通知的场景），回前台恢复播放
            if self.isPlaying, !self.usingEngine,
               self.player.rate == 0, self.player.currentItem != nil,
               !self.loadFailed {
                self.safePlay()
            }
        }
        NotificationCenter.default.addObserver(
            forName: .AVAudioEngineConfigurationChange,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            guard let self else { return }
            // 系统配置变化会使旧 graph 节点连接失效：
            // 无条件重建新连接（无论是否在播）——正在播立即重启恢复；
            // 下载/暂停中也先重建，保证下次 start/play 走有效图，
            // 消除 "player started when in a disconnected state" 崩溃窗口。
            self.enginePlayer.rebuildGraphIfNeeded()
        }
    }

    /// 恢复播放：按当前播放器分派（引擎音效 → enginePlayer；原音 → AVPlayer）。
    /// 锁屏/通知栏/来电恢复都走这里，避免双播放器同播导致音量叠加。
    /// 加载失败状态下点播放 = 重试加载（当前无有效 item，直接 play 会空转假播放）。
    func resumePlayback() {
        if loadFailed {
            reloadCurrent()
            return
        }
        // 原音播放前先激活会话，防 disconnected 崩溃；引擎播放内部已防护
        if usingEngine {
            enginePlayer.play()
        } else {
            do {
                try AVAudioSession.sharedInstance().setActive(true)
            } catch {
                LogReporter.shared.log(level: "error", tag: "audio",
                                       message: "resumePlayback 会话激活失败: \(error.localizedDescription)")
                return
            }
            guard player.currentItem != nil else { return }
            player.play()
        }
        isPlaying = true
        updateNowPlaying(force: true)
    }

    func pausePlayback() {
        // 暂停前结算一次收听时长（不触发“跳过”信号）
        flushListen(.pause)
        // 用户手动暂停：清除「网络中断待续播」标记，网络恢复时不得自动播
        interruptedWhilePlaying = false
        if usingEngine {
            enginePlayer.pause()
        } else {
            player.pause()
        }
        isPlaying = false
        updateNowPlaying(force: true)
    }

    // MARK: - 收听结算

    /// 每秒心跳：仅在真实出声时累计收听秒数（缓冲/加载/暂停不计入）
    func updateNowPlaying(force: Bool = false) {
        guard let current = current else {
            MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
            return
        }
        // 节流：进度每 0.1s 刷新，但锁屏信息无需那么频繁（每次写入都有开销）。
        // 切歌/暂停/恢复/封面就绪时 force=true 立即刷新，其余刷新间隔 ≥ 0.5s。
        let now = Date().timeIntervalSince1970
        if !force, now - lastNowPlayingUpdate < 0.5 { return }
        lastNowPlayingUpdate = now
        var info: [String: Any] = [
            MPMediaItemPropertyTitle: current.title,
            MPMediaItemPropertyArtist: current.displayAuthor,
            MPNowPlayingInfoPropertyElapsedPlaybackTime: currentTime,
            MPMediaItemPropertyPlaybackDuration: duration,
            MPNowPlayingInfoPropertyPlaybackRate: isPlaying ? 1.0 : 0.0
        ]
        if let album = current.album, !album.isEmpty {
            info[MPMediaItemPropertyAlbumTitle] = album
        }
        info[MPMediaItemPropertyArtwork] = currentArtwork ?? Self.fallbackArtwork
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }

}
