import Foundation

/// 原音 / 引擎音效共享的音频文件缓存（弱网播放与缓存共享的基础）。
///
/// 设计（工业级）：
/// - 目录：Caches/liuying_audio/（系统可清理，不占用户文档空间）
/// - 命名：`{trackId}.m4a`（原码率）、`{trackId}_96.m4a`（低码率）——以稳定 trackId 为键，
///   与签名 URL 的临时参数无关，原音与引擎可共同命中同一文件
/// - 写入：引擎整曲下载完成后落盘到此（downloadTask 临时文件会被系统回收，必须先拷贝）
/// - 读取：原音 AVPlayer 播放前优先查缓存 → 命中则用 file:// 本地播放（秒开 + 离线可播）；
///   引擎 AVAudioFile 加载同样优先查缓存 → 命中则免下载
/// - 淘汰：LRU（按修改时间），超过上限删最旧；被系统清理时自动重建目录
final class AudioCache {
    static let shared = AudioCache()

    /// 分离音轨类型（人声 / 伴奏）
    enum StemKind: String {
        case vocals
        case acc
        var suffix: String { "_" + rawValue + ".opus" }
    }

    /// 缓存文件数量上限（LRU 淘汰阈值）
    private let maxFiles = 200
    private let directory: URL
    private let lock = NSLock()

    private init() {
        let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        directory = caches.appendingPathComponent("liuying_audio", isDirectory: true)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    /// 缓存文件 URL（可能不存在）
    func fileURL(trackId: String, low: Bool) -> URL {
        directory.appendingPathComponent("\(trackId)\(low ? "_96" : "").m4a")
    }

    /// 缓存是否命中
    func exists(trackId: String, low: Bool) -> Bool {
        FileManager.default.fileExists(atPath: fileURL(trackId: trackId, low: low).path)
    }

    /// 从临时文件写入缓存（下载完成落盘）。返回落盘后的缓存路径；失败返回 nil。
    @discardableResult
    func store(fromTemp tempURL: URL, trackId: String, low: Bool) -> URL? {
        lock.lock()
        defer { lock.unlock() }
        let dest = fileURL(trackId: trackId, low: low)
        // 同曲重复下载：覆盖旧文件（文件本身可被 AVAudioEngine 安全替换，AVAudioFile 按路径重开）
        try? FileManager.default.removeItem(at: dest)
        do {
            try FileManager.default.copyItem(at: tempURL, to: dest)
        } catch {
            print("[AudioCache] store fail: \(error.localizedDescription)")
            return nil
        }
        evictIfNeeded()
        return dest
    }

    /// 线程安全地读缓存文件（命中返回 URL，未命中返回 nil）
    func existing(trackId: String, low: Bool) -> URL? {
        lock.lock()
        defer { lock.unlock() }
        let u = fileURL(trackId: trackId, low: low)
        return FileManager.default.fileExists(atPath: u.path) ? u : nil
    }

    // MARK: - 分离音轨 opus 缓存

    /// 分离音轨缓存路径
    func stemURL(trackId: String, kind: StemKind) -> URL {
        directory.appendingPathComponent("\(trackId)\(kind.suffix)")
    }

    /// 分离音轨是否已缓存
    func stemExists(trackId: String, kind: StemKind) -> Bool {
        FileManager.default.fileExists(atPath: stemURL(trackId: trackId, kind: kind).path)
    }

    /// 读分离音轨缓存（命中返回 URL）
    func stemExisting(trackId: String, kind: StemKind) -> URL? {
        lock.lock()
        defer { lock.unlock() }
        let u = stemURL(trackId: trackId, kind: kind)
        return FileManager.default.fileExists(atPath: u.path) ? u : nil
    }

    /// 分离音轨下载完成落盘
    @discardableResult
    func storeStem(fromTemp tempURL: URL, trackId: String, kind: StemKind) -> URL? {
        lock.lock()
        defer { lock.unlock() }
        let dest = stemURL(trackId: trackId, kind: kind)
        try? FileManager.default.removeItem(at: dest)
        do {
            try FileManager.default.copyItem(at: tempURL, to: dest)
        } catch {
            print("[AudioCache] storeStem fail: \(error.localizedDescription)")
            return nil
        }
        return dest
    }

    /// 预取：后台下载指定歌曲音频到缓存（启动广告 5s 窗口预热用——广告结束进入
    /// 首页续播/点歌时直接命中 file:// 秒开，避免与广告图竞争带宽导致播放变慢）。
    /// 按决策码率下载（与播放时 playbackURL 的 low/high 语义一致）；已缓存/失败静默。
    func prefetch(trackId: String, low: Bool) {
        Task {
            if exists(trackId: trackId, low: low) { return }
            guard let url = try? await API.signedStreamURL(for: trackId, bitrate: low ? .low : .high) else { return }
            let tmp = FileManager.default.temporaryDirectory
                .appendingPathComponent("prefetch_\(UUID().uuidString).m4a")
            do {
                let (data, resp) = try await URLSession.shared.data(from: url)
                guard let http = resp as? HTTPURLResponse, http.statusCode == 200,
                      !data.isEmpty, !Task.isCancelled else {
                    try? FileManager.default.removeItem(at: tmp)
                    return
                }
                try data.write(to: tmp)
                _ = store(fromTemp: tmp, trackId: trackId, low: low)
                try? FileManager.default.removeItem(at: tmp)
            } catch {
                try? FileManager.default.removeItem(at: tmp)
            }
        }
    }

    // MARK: - LRU 淘汰

    private func evictIfNeeded() {
        let fm = FileManager.default
        guard let items = try? fm.contentsOfDirectory(at: directory,
                                                      includingPropertiesForKeys: [.contentModificationDateKey],
                                                      options: []) else { return }
        var files = items.filter { $0.pathExtension == "m4a" || $0.pathExtension == "opus" }
        guard files.count > maxFiles else { return }
        files.sort { lhs, rhs in
            let ld = (try? lhs.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            let rd = (try? rhs.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            return ld < rd
        }
        let excess = files.count - maxFiles
        files.prefix(excess).forEach { try? fm.removeItem(at: $0) }
    }
}
