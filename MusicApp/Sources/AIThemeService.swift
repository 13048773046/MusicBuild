import Foundation
import SwiftUI

/// AI 生成的配色主题（参考 Folia major）
struct PlayerTheme: Codable, Equatable {
    let backgroundColor: String
    let primaryColor: String
    let accentColor: String
    let secondaryColor: String

    var bgColor: Color { Color(hex: backgroundColor) ?? .black }
    var primary: Color { Color(hex: primaryColor) ?? .accentColor }
    var accent: Color { Color(hex: accentColor) ?? .blue }
    var secondary: Color { Color(hex: secondaryColor) ?? .gray }
}

/// AI 配色主题服务：调用后端 API 生成/缓存配色
@MainActor
final class AIThemeService: ObservableObject {
    static let shared = AIThemeService()

    @Published var currentTheme: PlayerTheme?
    @Published var isLoading = false
    /// 最近一次**新生成**成功的歌曲 ID（缓存命中不触发；View 据此判断是否需要
    /// 用新 AI 配色换肤——全屏 Folia 播放器在 AI 生成完成前已用封面主色兜底，
    /// 生成后走轻量 theme 消息换肤，不重推 song 以免重置播放器 UI）
    @Published var lastGeneratedTrackId: String?

    /// 内存缓存：trackId -> theme
    private var memoryCache: [String: PlayerTheme] = [:]
    private let cacheKey = "ai_theme_cache"
    /// 最近一次请求的歌曲：切歌后旧请求迟到返回时据此丢弃，避免覆盖当前曲主题
    private var latestRequested: String?
    /// 进行中的请求（同一首并发请求去重）
    private var inFlight: Set<String> = []

    private init() {
        // 启动时加载磁盘缓存
        if let data = UserDefaults.standard.data(forKey: cacheKey),
           let dict = try? JSONDecoder().decode([String: PlayerTheme].self, from: data) {
            memoryCache = dict
        }
    }

    /// 获取歌曲的 AI 配色主题
    /// - Parameter trackId: 歌曲 ID
    func fetchTheme(for trackId: String) async {
        latestRequested = trackId
        // 命中内存缓存直接返回（不触发 lastGeneratedTrackId——主题未变无需重推）
        if let cached = memoryCache[trackId] {
            currentTheme = cached
            return
        }
        // 同一首已有请求在飞：直接返回，由那次请求统一发布结果
        guard !inFlight.contains(trackId) else { return }
        inFlight.insert(trackId)
        isLoading = true
        defer {
            inFlight.remove(trackId)
            // 所有请求都结束后才收起 loading（切歌时被丢弃的旧请求也不会把新请求的 loading 关掉）
            if inFlight.isEmpty { isLoading = false }
        }

        do {
            let theme = try await requestTheme(trackId: trackId)
            // 切歌竞态防护：请求期间用户已切到别的歌 → 丢弃这次结果
            guard latestRequested == trackId else { return }
            memoryCache[trackId] = theme
            currentTheme = theme
            lastGeneratedTrackId = trackId
            saveCache()
        } catch {
            // 失败时不设置 theme，播放页继续用封面取色兜底
            guard latestRequested == trackId else { return }
            currentTheme = nil
        }
    }

    /// 同步读取歌曲的 AI 配色主题（仅内存缓存命中，供推送链路即时取用）
    func cachedTheme(for trackId: String) -> PlayerTheme? {
        memoryCache[trackId]
    }

    /// 请求后端 API
    private func requestTheme(trackId: String) async throws -> PlayerTheme {
        // 注意：API.base 已含 /api（.../music/api），这里不能再拼 "/api/ai/..."，
        // 否则会请求到 /music/api/api/ai/generate-theme → 404（历史 bug，2026-11-03 修复）
        let url = API.base
            .appendingPathComponent("ai/generate-theme")
            .appendingPathComponent(trackId)

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token = AuthManager.shared.authToken {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        request.timeoutInterval = 15

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw URLError(.badServerResponse)
        }

        struct ThemeResponse: Codable {
            let ok: Bool
            let theme: PlayerTheme
        }

        let result = try JSONDecoder().decode(ThemeResponse.self, from: data)
        return result.theme
    }

    /// 保存磁盘缓存
    private func saveCache() {
        if let data = try? JSONEncoder().encode(memoryCache) {
            UserDefaults.standard.set(data, forKey: cacheKey)
        }
    }

    /// 清空缓存
    func clearCache() {
        memoryCache.removeAll()
        UserDefaults.standard.removeObject(forKey: cacheKey)
        currentTheme = nil
    }
}

/// Hex 颜色初始化扩展
extension Color {
    init?(hex: String) {
        var hex = hex.trimmingCharacters(in: .whitespacesAndNewlines)
        if hex.hasPrefix("#") { hex.removeFirst() }
        guard hex.count == 6 else { return nil }
        var rgb: UInt64 = 0
        Scanner(string: hex).scanHexInt64(&rgb)
        let r = Double((rgb & 0xFF0000) >> 16) / 255.0
        let g = Double((rgb & 0x00FF00) >> 8) / 255.0
        let b = Double(rgb & 0x0000FF) / 255.0
        self.init(red: r, green: g, blue: b)
    }
}
