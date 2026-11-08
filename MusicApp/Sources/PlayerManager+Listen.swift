// PlayerManager+Listen.swift
// P2-13：从 PlayerManager.swift 拆出的 收听行为上报 逻辑（extension 保持零引用改动）
import AVFoundation
import MediaPlayer
import UIKit
import Combine

extension PlayerManager {

    func listenTick() {
        guard current != nil, isPlaying, !loadFailed, !isBuffering else { return }
        listen.seconds += 1
        // 每 30s 自动上报一次，避免 App 被杀导致收听数据丢失
        if listen.seconds - listen.reported >= 30 {
            flushListen(.heartbeat)
        }
    }

    /// 结算当前收听段：所有播放时长都累计上报 listen（用于听歌时长统计）；
    /// 切走时进度 < 30% 且听了 ≥ 5 秒额外记为"跳过"（负向信号）
    func flushListen(_ reason: ListenFlushReason) {
        guard !listen.trackId.isEmpty else { return }
        let delta = listen.seconds - listen.reported
        guard delta >= 1 else { return }
        let frac = duration > 0 ? min(listen.seconds / duration, 1.0) : 0
        // P1 归因闭环：meta 携带 route（推荐来源路）、seekCount（负信号）、plays（深度正信号）
        let meta = API.BehaviorEvent.metaJSON([
            "fraction": (frac * 100).rounded() / 100,
            "plays": listen.completedPlays,
            "seconds": Int(listen.seconds),
            "route": listen.route,
            "seekCount": listen.seekCount
        ])
        let ev = API.BehaviorEvent(trackId: listen.trackId, event: "listen",
                                   value: delta, meta: meta)
        Task { await API.reportBehavior([ev]) }
        listen.reported = listen.seconds
        // 切歌且听的时间很短（<30%），额外记 skip 负向信号
        if reason == .switchTrack && delta >= 5 && frac < 0.3 {
            let skipEv = API.BehaviorEvent(trackId: listen.trackId, event: "skip",
                                           value: 0, meta: API.BehaviorEvent.metaJSON(["fraction": (frac * 100).rounded() / 100]))
            Task { await API.reportBehavior([skipEv]) }
        }
    }

}
