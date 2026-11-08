import Foundation
import UIKit

enum API {
    // 服务端地址运行时解密，二进制中无明文域名（配合 Security/Obfuscator.swift）
    static var base: URL { URL(string: Obfuscator.apiBase)! }

    /// 歌曲列表：可传 userId（拿点赞态）；keyword 非空时返回「相关歌曲流」
    /// （歌名/歌手/专辑/上传者匹配，随机 10 首，不足补全随机）
    static func tracks(userId: String? = nil, keyword: String? = nil) async throws -> [Track] {
        var items: [URLQueryItem] = []
        if let userId, !userId.isEmpty {
            items.append(URLQueryItem(name: "userId", value: userId))
        }
        if let keyword, !keyword.isEmpty {
            items.append(URLQueryItem(name: "q", value: keyword))
        }
        let data = try await get("tracks", query: items)
        return try JSONDecoder().decode([Track].self, from: data)
    }

    /// 单曲详情：按 id 精确查询（用户上传 + 采集歌曲都覆盖，通知点击跳转播放用）
    static func track(id: String) async throws -> Track {
        let data = try await get("track/\(id)")
        return try JSONDecoder().decode(Track.self, from: data)
    }

    /// 搜索歌曲：按歌名/歌手模糊匹配
    static func search(_ keyword: String) async throws -> [Track] {
        let data = try await get("search",
                                 query: [URLQueryItem(name: "q", value: keyword)],
                                 auth: true)
        return try JSONDecoder().decode([Track].self, from: data)
    }

    /// 码率档位（弱网播放）：原码率直出 / 低码率 96kbps（后端懒转码落盘缓存）
    enum Bitrate: Equatable {
        case high
        case low
        /// 后端低码率参数（low = 96；high 不附加参数走原码率）
        var brParam: String? { self == .low ? "96" : nil }
    }

    static func streamURL(_ id: String, bitrate: Bitrate = .high) -> URL {
        let baseURL = base.appendingPathComponent("stream").appendingPathComponent(id)
        guard let br = bitrate.brParam else { return baseURL }
        guard var comps = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else { return baseURL }
        comps.queryItems = [URLQueryItem(name: "br", value: br)]
        return comps.url ?? baseURL
    }

    /// 获取带签名时效的播放 URL（防链接泄露/盗链）：
    /// 先向 /api/stream-url/{id} 签发短效签名；接口不可用（老服务）时回退普通 URL
    static func signedStreamURL(for id: String, bitrate: Bitrate = .high) async -> URL {
        let signURL = base.appendingPathComponent("stream-url").appendingPathComponent(id)
        // #16：签名请求网络抖动失败时重试一次（1s 间隔），再失败才回退无签名 URL。
        // 服务器 SIGN_STRICT 开启时无签名 URL 会 403——重试可显著降低弱网下"签名刚好失败
        // 导致整首歌 403"的概率；同时 Logger 记录回退，便于线上诊断。
        var req = URLRequest(url: signURL)
        req.timeoutInterval = 6
        // 签名签发接口已做防护（登录态或分享 token）：登录用户附加 Bearer 才能拿到签名
        if let token = AuthManager.shared.authToken, !token.isEmpty {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        var signed: URL?
        for attempt in 0..<2 {
            if let (data, _) = try? await URLSession.shared.data(for: req),
               let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let exp = obj["exp"] as? Int, let sig = obj["sig"] as? String {
                var comps = URLComponents(url: streamURL(id, bitrate: bitrate), resolvingAgainstBaseURL: false)
                var items = comps?.queryItems ?? []
                items.append(contentsOf: [URLQueryItem(name: "exp", value: String(exp)),
                                          URLQueryItem(name: "sig", value: sig)])
                comps?.queryItems = items
                if let s = comps?.url { signed = s; break }
            }
            if attempt == 0 {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
            }
        }
        if let signed { return signed }
        // 签名两次均失败：回退无签名 URL（服务器 SIGN_STRICT 关闭时仍可播），
        // 并记录以便线上诊断弱网下播放 403 是否由签名失败引起
        #if DEBUG
        print("[API] signedStreamURL fallback to unsigned id=\(id)")
        #endif
        return streamURL(id, bitrate: bitrate)
    }

    /// 预热低码率版本（弱网预取时调用）：触发后端后台转码（stream-prepare），立即返回。
    /// 使低码率文件在切到该曲前就绪，弱网播放无需等待转码。失败静默（不影响主链路）。
    static func prepareLowBitrate(for id: String) async {
        // 先签发签名（与 stream 同体系）
        let signURL = base.appendingPathComponent("stream-url").appendingPathComponent(id)
        var req = URLRequest(url: signURL)
        req.timeoutInterval = 6
        // 签名签发接口已做防护（登录态或分享 token）：登录用户附加 Bearer 才能拿到签名
        if let token = AuthManager.shared.authToken, !token.isEmpty {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        guard let (data, _) = try? await URLSession.shared.data(for: req),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let exp = obj["exp"] as? Int, let sig = obj["sig"] as? String,
              var comps = URLComponents(url: base.appendingPathComponent("stream-prepare").appendingPathComponent(id),
                                        resolvingAgainstBaseURL: false) else { return }
        comps.queryItems = [URLQueryItem(name: "br", value: "96"),
                            URLQueryItem(name: "exp", value: String(exp)),
                            URLQueryItem(name: "sig", value: sig)]
        guard let u = comps.url else { return }
        var r = URLRequest(url: u)
        r.timeoutInterval = 10
        _ = try? await URLSession.shared.data(for: r)
    }

    /// 分离音轨状态（服务端 Demucs）：status=none/pending/processing/done；
    /// done 时附带人声/伴奏 opus 的完整 URL
    struct StemInfo: Decodable {
        let status: String
        let vocalsUrl: String?
        let accUrl: String?
    }

    /// 查询某首歌的分离音轨状态。
    /// - trigger: true 时若未处理则触发服务端按需分离（异步，不阻塞）
    static func stemInfo(for trackId: String, trigger: Bool = false) async -> StemInfo? {
        var comps = URLComponents(url: base.appendingPathComponent("stems").appendingPathComponent(trackId),
                                  resolvingAgainstBaseURL: false)!
        if trigger {
            comps.queryItems = [URLQueryItem(name: "trigger", value: "1")]
        }
        var req = URLRequest(url: comps.url!)
        req.timeoutInterval = 8
        applyAuth(&req)
        guard let (data, _) = try? await URLSession.shared.data(for: req),
              let info = try? JSONDecoder().decode(StemInfo.self, from: data) else { return nil }
        return info
    }

    /// 分离音轨文件 URL（服务端 basename → 完整接口地址）
    static func stemFileURL(_ name: String) -> URL {
        base.appendingPathComponent("stem-file").appendingPathComponent(name)
    }

    /// 启动页广告：每次启动随机返回一张（无广告/失败返回 nil，超时 3s 不阻塞启动）
    /// 会员套餐价格与时长：后台「网站管理→支付设置」可配（vip/svip × month/quarter/year），前端不写死
    struct MemberPricing {
        struct Tier: Equatable { var price: Double; var days: Int }
        var vip = Tier(price: 6, days: 30)
        var vipQuarter = Tier(price: 16, days: 90)
        var vipYear = Tier(price: 48, days: 365)
        var svip = Tier(price: 10, days: 30)
        var svipQuarter = Tier(price: 26, days: 90)
        var svipYear = Tier(price: 68, days: 365)
        var loaded = false

        func tier(plan: String, term: String) -> Tier {
            if plan == "svip" {
                return term == "quarter" ? svipQuarter : (term == "year" ? svipYear : svip)
            }
            return term == "quarter" ? vipQuarter : (term == "year" ? vipYear : vip)
        }
        func priceText(plan: String, term: String) -> String {
            String(format: "¥%.1f", tier(plan: plan, term: term).price)
        }
    }
    static func memberPricing() async -> MemberPricing {
        var p = MemberPricing()
        guard let data = try? await get("site-config", timeout: 8, retries: 1),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return p }
        func num(_ k: String) -> Double? {
            if let v = obj[k] as? Double, v > 0 { return v }
            if let v = obj[k] as? Int, Double(v) > 0 { return Double(v) }
            return nil
        }
        func int(_ k: String) -> Int? {
            if let v = obj[k] as? Int, v > 0 { return v }
            if let v = obj[k] as? Double, v > 0 { return Int(v) }
            return nil
        }
        if let v = num("vip_month_price") { p.vip.price = v }
        if let v = int("vip_month_days") { p.vip.days = v }
        if let v = num("vip_quarter_price") { p.vipQuarter.price = v }
        if let v = int("vip_quarter_days") { p.vipQuarter.days = v }
        if let v = num("vip_year_price") { p.vipYear.price = v }
        if let v = int("vip_year_days") { p.vipYear.days = v }
        if let v = num("svip_month_price") { p.svip.price = v }
        if let v = int("svip_month_days") { p.svip.days = v }
        if let v = num("svip_quarter_price") { p.svipQuarter.price = v }
        if let v = int("svip_quarter_days") { p.svipQuarter.days = v }
        if let v = num("svip_year_price") { p.svipYear.price = v }
        if let v = int("svip_year_days") { p.svipYear.days = v }
        p.loaded = true
        return p
    }

    static func splashAd() async -> SplashAd? {
        // 必须附加 Bearer Token：后端按登录用户判断 SVIP 无广告特权，
        // 不带 token 时 SVIP 用户也会被返回广告（2026-10-19 修复）
        var req = URLRequest(url: base.appendingPathComponent("splash-ad"))
        req.timeoutInterval = 3
        applyAuth(&req)
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            guard let http = resp as? HTTPURLResponse, http.statusCode == 200 else { return nil }
            let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            guard let d = obj?["ad"] as? [String: Any] else { return nil }
            return SplashAd(name: d["name"] as? String ?? "",
                            title: d["title"] as? String ?? "",
                            link: d["link"] as? String ?? "",
                            imgPath: d["img"] as? String ?? "")
        } catch {
            return nil
        }
    }

    /// 隐藏式分享短链（汽水风格）：https://域名/s/{8位短码}/ → H5 分享页；失败返回 nil
    static func shareLink(for id: String) async -> String? {
        let url = base.appendingPathComponent("share-url").appendingPathComponent(id)
        if let (data, _) = try? await URLSession.shared.data(from: url),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let code = obj["code"] as? String, !code.isEmpty,
           let host = base.host() {
            return "https://\(host)/s/\(code)/"
        }
        return nil
    }

    // MARK: - 鉴权请求基础设施（Bearer Token + 401 统一处理）

    /// 为请求附加当前登录用户的 Bearer Token（旧会话无 token 时不附加，依赖后端兼容模式）
    private static func applyAuth(_ req: inout URLRequest) {
        if let token = AuthManager.shared.authToken {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
    }

    /// 鉴权接口返回 401 时：清除本地登录态（下次进 App 要求重新登录）并抛错
    private static func checkAuth(_ resp: URLResponse?) throws {
        guard let http = resp as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        if http.statusCode == 401 {
            AuthManager.shared.handleUnauthorized()
            throw APIError.message("登录已过期，请重新登录")
        }
    }

    /// 上报类接口（播放/分享）：401 只静默清理登录态，不抛错打断播放流程
    private static func noticeAuthFailure(_ resp: URLResponse?) {
        if let http = resp as? HTTPURLResponse, http.statusCode == 401 {
            AuthManager.shared.handleUnauthorized()
        }
    }

    /// P2：统一 GET——固定超时 + 指数退避重试（幂等接口 2 次，0.5s/1s）+ 错误分类。
    /// 只对可重试的网络错误重试；4xx 立即抛（重试无意义）；401 走 checkAuth 清登录态。
    private static func get(_ path: String,
                            query: [URLQueryItem] = [],
                            auth: Bool = false,
                            timeout: TimeInterval = 15,
                            retries: Int = 2) async throws -> Data {
        var components = URLComponents(url: base.appendingPathComponent(path),
                                       resolvingAgainstBaseURL: false)!
        if !query.isEmpty {
            components.queryItems = query
        }
        var req = URLRequest(url: components.url!)
        req.timeoutInterval = timeout
        if auth {
            applyAuth(&req)
        }
        let startedAt = Date()
        var attempt = 0
        while true {
            do {
                let (data, resp) = try await URLSession.shared.data(for: req)
                if auth {
                    try checkAuth(resp)
                }
                guard let http = resp as? HTTPURLResponse else {
                    logRequest("GET", path, startedAt: startedAt, outcome: "invalid-response")
                    throw APIError.message("服务器响应异常")
                }
                if (200...299).contains(http.statusCode) {
                    logRequest("GET", path, startedAt: startedAt, outcome: "\(http.statusCode)")
                    return data
                }
                if http.statusCode >= 500 {
                    logRequest("GET", path, startedAt: startedAt, outcome: "server-\(http.statusCode)")
                    throw APIError.server(http.statusCode)
                }
                logRequest("GET", path, startedAt: startedAt, outcome: "client-\(http.statusCode)")
                throw APIError.client(http.statusCode)
            } catch let e as URLError {
                if e.code == .cancelled {
                    throw APIError.cancelled
                }
                let retryable = e.code == .timedOut || e.code == .notConnectedToInternet
                    || e.code == .networkConnectionLost || e.code == .cannotConnectToHost
                    || e.code == .cannotFindHost || e.code == .dnsLookupFailed
                if retryable && attempt < retries {
                    attempt += 1
                    let delay = 0.5 * pow(2.0, Double(attempt - 1))
                    try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
                    continue
                }
                logRequest("GET", path, startedAt: startedAt, outcome: "network-\(e.code.rawValue)")
                throw APIError.network(e.code)
            } catch let e as APIError {
                logRequest("GET", path, startedAt: startedAt, outcome: "api-\(e)")
                throw e
            } catch {
                logRequest("GET", path, startedAt: startedAt, outcome: "error-\(error.localizedDescription)")
                throw APIError.message(error.localizedDescription)
            }
        }
    }

    /// P0-3：统一请求日志（对齐 Alamofire EventMonitor 最小子集，DEBUG 才编译进包）。
    /// 记录 method / path / 耗时 / 结果，弱网与线上问题可一键定位。
    #if DEBUG
    private static func logRequest(_ method: String, _ path: String, startedAt: Date, outcome: String) {
        let ms = Int(Date().timeIntervalSince(startedAt) * 1000)
        print("[API] \(method) \(path) → \(outcome) (\(ms)ms)")
    }
    #else
    private static func logRequest(_ method: String, _ path: String, startedAt: Date, outcome: String) {}
    #endif

    // MARK: - 互动：播放 / 点赞 / 评论 / 分享

    static func reportPlay(_ trackId: String) async {
        var req = URLRequest(url: base.appendingPathComponent("play").appendingPathComponent(trackId))
        req.httpMethod = "POST"
        req.timeoutInterval = 10
        applyAuth(&req)
        let (_, resp) = (try? await URLSession.shared.data(for: req)) ?? (Data(), nil)
        noticeAuthFailure(resp)
    }

    static func toggleLike(trackId: String, userId: String) async throws -> (liked: Bool, count: Int) {
        var req = URLRequest(url: base.appendingPathComponent("like").appendingPathComponent(trackId))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["userId": userId])
        applyAuth(&req)
        let (data, resp) = try await URLSession.shared.data(for: req)
        try checkAuth(resp)
        let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        return (liked: (obj?["liked"] as? Bool) ?? false,
                count: (obj?["likeCount"] as? Int) ?? 0)
    }

    @discardableResult
    static func reportShare(_ trackId: String) async -> Int {
        var req = URLRequest(url: base.appendingPathComponent("share").appendingPathComponent(trackId))
        req.httpMethod = "POST"
        req.timeoutInterval = 10
        applyAuth(&req)
        let (data, resp) = (try? await URLSession.shared.data(for: req)) ?? (Data(), nil)
        noticeAuthFailure(resp)
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return 0 }
        return obj["shareCount"] as? Int ?? 0
    }

    // MARK: - 行为采集与个性化推荐

    /// 用户行为事件（批量上报，失败静默不打断播放流程）
    /// event: like / comment_open / comment / share / listen / search / skip
    /// value: listen 为本次新增收听秒数；meta 为 JSON 字符串（listen 携带 {fraction, plays, seconds}）
    struct BehaviorEvent: Encodable {
        let trackId: String
        let event: String
        var value: Double = 0
        var meta: String? = nil

        /// 生成 JSON 字符串形式的 meta
        static func metaJSON(_ dict: [String: Any]) -> String? {
            guard let data = try? JSONSerialization.data(withJSONObject: dict) else { return nil }
            return String(data: data, encoding: .utf8)
        }
    }

    /// 批量上报行为事件（失败静默，不打断播放流程）。
    /// 优先走 WebSocket 实时上报（即时通讯通道）：行为数据实时入库并触发 recommend 事件推送；
    /// WS 未连接（未登录/断线重连中）时回退 HTTP /api/behavior，保证行为不丢。
    static func reportBehavior(_ events: [BehaviorEvent]) async {
        guard !events.isEmpty else { return }
        if NotificationBus.shared.sendBehavior(events) { return }
        var req = URLRequest(url: base.appendingPathComponent("behavior"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 10
        applyAuth(&req)
        let payload = ["events": events.map { try? JSONEncoder().encode($0) }
            .compactMap { $0 }.compactMap { try? JSONSerialization.jsonObject(with: $0) }]
        req.httpBody = try? JSONSerialization.data(withJSONObject: payload)
        let (_, resp) = (try? await URLSession.shared.data(for: req)) ?? (Data(), nil)
        noticeAuthFailure(resp)
    }

    /// 个性化推荐：基于用户行为返回 20 首相关歌曲（未登录/无行为时后端返回随机兜底）。
    /// - Parameter excluding: 已推荐过的歌曲 id（推荐流无限续推防重复，后端逐条排除）
    /// - Parameter fresh: 长时间重开 App 的新鲜会话——后端加大随机扰动/探索权重，
    ///   让重新开始的一批推荐带随机性、不固定重复上次
    /// - Parameter lat/lng/hour: M2 天气×时段场景上下文——后端据此叠加"应景推荐"（个性化×场景双向融合）；
    ///   传 nil 时后端按时段兜底场景，推荐照常返回
    static func recommend(userId: String? = nil, excluding: [String] = [],
                           fresh: Bool = false, source: String? = nil,
                           lat: Double? = nil, lng: Double? = nil,
                           hour: Int? = nil) async throws -> [Track] {
        var items: [URLQueryItem] = []
        let uid = userId ?? AuthManager.shared.currentUser?.id
        if let uid, !uid.isEmpty {
            items.append(URLQueryItem(name: "userId", value: uid))
        }
        if !excluding.isEmpty {
            items.append(URLQueryItem(name: "exclude", value: excluding.joined(separator: ",")))
        }
        if fresh {
            items.append(URLQueryItem(name: "fresh", value: "1"))
        }
        if let source, !source.isEmpty {
            items.append(URLQueryItem(name: "source", value: source))
        }
        // M2 场景上下文：定位可用时叠加；不可用时缺省（后端时段兜底，不阻塞推荐）
        if let lat {
            items.append(URLQueryItem(name: "lat", value: String(format: "%.4f", lat)))
        }
        if let lng {
            items.append(URLQueryItem(name: "lng", value: String(format: "%.4f", lng)))
        }
        if let hour {
            items.append(URLQueryItem(name: "hour", value: String(hour)))
        }
        let data = try await get("recommend", query: items, auth: true)
        return try JSONDecoder().decode([Track].self, from: data)
    }

    /// 场景音乐 → 个性化分类歌单（2026-11-01）
    /// 类型由 tag 严格筛选（DJ 模式/深夜 EMO/助眠模式…），排序按用户行为画像个性化；
    /// 返回与 tracks 同构的 [Track]，前端点击场景卡片后直接播放。
    static func radio(tag: String, userId: String? = nil, seed: String? = nil) async throws -> [Track] {
        var items: [URLQueryItem] = []
        let uid = userId ?? AuthManager.shared.currentUser?.id
        if let uid, !uid.isEmpty {
            items.append(URLQueryItem(name: "userId", value: uid))
        }
        // 场景随机种子（2026-11-05）：后端按 seed 做「分档窗口洗牌」——
        // 同一 seed 顺序完全一致（一次播放会话内不跳变），不同 seed 顺序不同
        // （每次点场景都是一批新顺序）。不传则后端返回固定的个性化排序。
        if let seed, !seed.isEmpty {
            items.append(URLQueryItem(name: "seed", value: seed))
        }
        // 原始 tag 交给 get()：appendingPathComponent 会做一次百分号编码（中文/空格），
        // 这里不能再预编码，否则 % 被二次转义成 %25 导致后端解不出场景名
        let data = try await get("radio/\(tag)", query: items, auth: true)
        return try JSONDecoder().decode([Track].self, from: data)
    }

    /// 相似歌曲（2026-11-05）：基于后端 948 维语义向量（歌词/标签/画像 × MERT 听感）算出的
    /// 跨歌手"听起来像"邻居，不是同歌手/同专辑的机械关联。
    /// 返回 (歌曲, 相似度)，相似度 0~1。
    static func similar(_ trackId: String, n: Int = 20) async throws -> [(track: Track, score: Double)] {
        let data = try await get("similar/\(trackId)",
                                 query: [URLQueryItem(name: "n", value: String(n))], auth: true)
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let arr = obj["items"] as? [[String: Any]] else { return [] }
        let decoder = JSONDecoder()
        var out: [(Track, Double)] = []
        for item in arr {
            guard let raw = try? JSONSerialization.data(withJSONObject: item),
                  let t = try? decoder.decode(Track.self, from: raw) else { continue }
            out.append((t, (item["similarity"] as? Double) ?? 0))
        }
        return out
    }

    /// 每日推荐（2026-11-05）：后端按 (用户, 日期) 固定生成，当天多次进入结果一致、隔天换一批。
    static func daily(_ n: Int = 30) async throws -> [Track] {
        let data = try await get("daily",
                                 query: [URLQueryItem(name: "n", value: String(n))], auth: true)
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let arr = obj["items"] else { return [] }
        guard let raw = try? JSONSerialization.data(withJSONObject: arr) else { return [] }
        return (try? JSONDecoder().decode([Track].self, from: raw)) ?? []
    }

    /// 不感兴趣（2026-11-05）：kind = track / artist / genre / mood / scene。
    /// 后端会直接排除该歌，并按粒度给同歌手/同风格/同情绪的歌降权。
    @discardableResult
    static func dislike(trackId: String, kind: String = "track") async -> Bool {
        var req = URLRequest(url: base.appendingPathComponent("dislike"))
        req.httpMethod = "POST"
        req.timeoutInterval = 10
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["trackId": trackId, "kind": kind])
        applyAuth(&req)
        let (data, resp) = (try? await URLSession.shared.data(for: req)) ?? (Data(), nil)
        noticeAuthFailure(resp)
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return false }
        return (obj["ok"] as? Bool) ?? false
    }

    /// 天气×心情场景快照（M2）：发现页"今日心情卡片"用。
    /// 失败（网络/参数/服务不可用）返回 nil，调用方静默降级为"仅时段"。
    struct WeatherInfo: Decodable {
        let ok: Bool
        let temp: Double?
        let code: Int?
        let hour: Int?
        let scene: String?
        let sceneLabel: String?
    }

    static func fetchWeather(lat: Double, lng: Double, hour: Int? = nil) async -> WeatherInfo? {
        var components = URLComponents(url: base.appendingPathComponent("weather"),
                                       resolvingAgainstBaseURL: false)!
        var items = [
            URLQueryItem(name: "lat", value: String(format: "%.4f", lat)),
            URLQueryItem(name: "lng", value: String(format: "%.4f", lng)),
        ]
        if let hour {
            items.append(URLQueryItem(name: "hour", value: String(hour)))
        }
        components.queryItems = items
        var req = URLRequest(url: components.url!)
        req.timeoutInterval = 10
        let (data, resp) = (try? await URLSession.shared.data(for: req)) ?? (Data(), nil)
        guard let http = resp as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            return nil
        }
        return try? JSONDecoder().decode(WeatherInfo.self, from: data)
    }

    /// 我的喜欢：用户点赞过的歌曲列表（上传+采集混合，按点赞时间倒序）
    static func likedTracks(userId: String? = nil) async throws -> [Track] {
        let uid = userId ?? AuthManager.shared.currentUser?.id
        var items: [URLQueryItem] = []
        if let uid, !uid.isEmpty {
            items = [URLQueryItem(name: "userId", value: uid)]
        }
        let data = try await get("likes", query: items, auth: true)
        return try JSONDecoder().decode([Track].self, from: data)
    }

    // MARK: - 我的页面分页数据（my-tracks / my-likes / my-comments）

    /// 分页响应通用结构：{ list, total, hasMore }
    struct PageResult<T: Decodable>: Decodable {
        let list: [T]
        let total: Int
        let hasMore: Bool
    }

    /// 我的评论条目（含对应歌曲信息，点击可直接播放）
    struct MyComment: Codable, Identifiable, Hashable {
        let trackId: String
        let content: String
        let created: Double?
        let title: String?
        let artist: String?

        var id: String { "\(trackId)-\(Int(created ?? 0))" }
    }

    /// 分页请求公共逻辑：拼 page/pageSize 参数 + Bearer 鉴权 + 解码 PageResult
    private static func fetchPage<T: Decodable>(path: String, page: Int?,
                                                pageSize: Int) async throws -> PageResult<T> {
        var items: [URLQueryItem] = []
        if let page {
            items = [URLQueryItem(name: "page", value: String(page)),
                     URLQueryItem(name: "pageSize", value: String(pageSize))]
        }
        let data = try await get(path, query: items, auth: true)
        return try JSONDecoder().decode(PageResult<T>.self, from: data)
    }

    /// 我的上传作品（分页）；page 传 nil 时后端返回全量（兼容旧服务）
    static func myTracks(page: Int? = nil, pageSize: Int = 20) async throws -> PageResult<Track> {
        try await fetchPage(path: "my-tracks", page: page, pageSize: pageSize)
    }

    /// 我的点赞歌曲（分页，上传+采集混合）
    static func myLikes(page: Int? = nil, pageSize: Int = 20) async throws -> PageResult<Track> {
        try await fetchPage(path: "my-likes", page: page, pageSize: pageSize)
    }

    /// 我的评论（分页）
    static func myComments(page: Int? = nil, pageSize: Int = 20) async throws -> PageResult<MyComment> {
        try await fetchPage(path: "my-comments", page: page, pageSize: pageSize)
    }

    /// 我的页统计：累计听歌时长（秒）。统一封装，AppPreloader 与 MineView 共用。
    static func myStats() async throws -> Int {
        var req = URLRequest(url: base.appendingPathComponent("my-stats"))
        req.timeoutInterval = 15
        applyAuth(&req)
        let (data, resp) = try await URLSession.shared.data(for: req)
        try checkAuth(resp)
        guard let http = resp as? HTTPURLResponse, (200...299).contains(http.statusCode),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw URLError(.badServerResponse)
        }
        return (obj["totalSeconds"] as? Int) ?? 0
    }

    struct Comment: Codable, Identifiable, Hashable {
        let id: String
        let userId: String
        let userName: String
        let content: String
        let created: Double
        /// 评论图片（服务端文件名，经 /api/comment-image/{name} 访问；旧评论无图时为 nil）
        let images: [String]?
        /// 评论者是否在线（后端按 WebSocket 活跃连接判断；旧数据无此字段时为 nil）
        let userOnline: Bool?
        /// 评论者头衔/认证/会员（2026-10-19；旧数据无此字段时为 nil）
        let title: String?
        let verified: String?
        let verifiedLabel: String?
        let member: String?
    }

    static func fetchComments(trackId: String) async throws -> [Comment] {
        let data = try await get("comments/\(trackId)")
        return try JSONDecoder().decode([Comment].self, from: data)
    }

    // MARK: - 认证（2026-10-19 会员体系）

    /// 认证类别（两级：level1 第一级，level2 第二级可空）
    static func fetchVerifyCategories() async throws -> [(id: String, level1: String, level2: String)] {
        let data = try await get("verify/categories")
        guard let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let list = obj["list"] as? [[String: Any]] else { return [] }
        return list.compactMap { d in
            guard let id = d["id"] as? String else { return nil }
            return (id, (d["level1"] as? String) ?? "", (d["level2"] as? String) ?? "")
        }
    }

    /// 提交认证申请（level1 必填 + 理由必填；level2/label 可选）
    static func submitVerifyApply(level1: String, level2: String, label: String, reason: String) async throws {
        var req = URLRequest(url: base.appendingPathComponent("verify/apply"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: [
            "level1": level1, "level2": level2, "label": label, "reason": reason
        ])
        applyAuth(&req)
        let (_, resp) = try await URLSession.shared.data(for: req)
        try checkAuth(resp)
        guard let http = resp as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
    }

    static func postComment(trackId: String, userId: String, userName: String, content: String,
                            images: [String] = []) async throws {
        var req = URLRequest(url: base.appendingPathComponent("comment").appendingPathComponent(trackId))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: [
            "userId": userId, "userName": userName, "content": content, "images": images
        ])
        applyAuth(&req)
        let (_, resp) = try await URLSession.shared.data(for: req)
        try checkAuth(resp)
        guard let http = resp as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
    }

    /// 上传评论图片（multipart，服务端校验图片格式/大小），返回服务端文件名
    static func uploadCommentImage(_ imageData: Data) async throws -> String {
        var body = MultipartBody()
        body.file("image", fileName: "comment.jpg", contentType: "image/jpeg", fileData: imageData)
        body.finish()
        var req = URLRequest(url: base.appendingPathComponent("upload-comment-image"))
        req.httpMethod = "POST"
        req.setValue("multipart/form-data; boundary=\(body.boundary)", forHTTPHeaderField: "Content-Type")
        req.httpBody = body.data
        req.timeoutInterval = 30
        applyAuth(&req)
        let (data, resp) = try await URLSession.shared.data(for: req)
        try checkAuth(resp)
        guard let http = resp as? HTTPURLResponse, (200...299).contains(http.statusCode),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let name = obj["name"] as? String, !name.isEmpty else {
            throw URLError(.badServerResponse)
        }
        return name
    }

    /// 评论图片访问 URL
    static func commentImageURL(_ name: String) -> URL {
        base.appendingPathComponent("comment-image").appendingPathComponent(name)
    }

    static func coverURL(_ id: String) -> URL {
        base.appendingPathComponent("cover").appendingPathComponent(id)
    }

    static func lyricURL(_ id: String) -> URL {
        base.appendingPathComponent("lyric").appendingPathComponent(id)
    }

    /// 歌词内存缓存：听歌模式切换恢复、切回同一首歌时歌词秒出，不重复请求。
    /// 设置上限防膨胀（长滑数百首后防止歌词文本常驻内存），内存警告时全清。
    private static let lyricLock = NSLock()
    private static var lyricCache: [String: String] = [:]
    private static let lyricCacheMax = 300

    static func fetchLyric(_ id: String) async throws -> String {
        lyricLock.lock()
        let cached = lyricCache[id]
        lyricLock.unlock()
        if let cached { return cached }
        // auth: true —— 歌词接口已做防护（仅登录态或分享 token 放行）
        let data = try await get("lyric/\(id)", auth: true)
        let text = String(data: data, encoding: .utf8) ?? ""
        lyricLock.lock()
        if lyricCache.count >= lyricCacheMax {
            lyricCache.removeAll()   // 达到上限：整清（歌词可随时重新拉取，无需复杂淘汰）
        }
        lyricCache[id] = text
        lyricLock.unlock()
        return text
    }

    /// 清空歌词缓存（内存警告时调用）
    static func clearLyricCache() {
        lyricLock.lock()
        lyricCache.removeAll()
        lyricLock.unlock()
    }

    static func avatarURL(_ id: String) -> URL {
        base.appendingPathComponent("avatar").appendingPathComponent(id)
    }

    static func backgroundURL(_ id: String) -> URL {
        base.appendingPathComponent("background").appendingPathComponent(id)
    }

    // MARK: - Multipart 表单构建（复用，消除各接口重复粘贴的 field/filePart）

    /// 增量构建 multipart/form-data 请求体
    private struct MultipartBody {
        let boundary: String
        private(set) var data = Data()

        init() {
            boundary = "Boundary-\(UUID().uuidString)"
        }

        mutating func field(_ name: String, _ value: String) {
            data.append(Data("--\(boundary)\r\n".utf8))
            data.append(Data("Content-Disposition: form-data; name=\"\(name)\"\r\n\r\n".utf8))
            data.append(Data("\(value)\r\n".utf8))
        }

        mutating func file(_ name: String, fileName: String, contentType: String, fileData: Data) {
            data.append(Data("--\(boundary)\r\n".utf8))
            data.append(Data("Content-Disposition: form-data; name=\"\(name)\"; filename=\"\(fileName)\"\r\n".utf8))
            data.append(Data("Content-Type: \(contentType)\r\n\r\n".utf8))
            data.append(fileData)
            data.append(Data("\r\n".utf8))
        }

        mutating func finish() {
            data.append(Data("--\(boundary)--\r\n".utf8))
        }
    }

    // MARK: - 用户注册 / 登录（multipart）

    /// 注册：账号由后端自动生成 6 位数字，无需客户端提交
    static func register(password: String, name: String,
                         avatarData: Data?,
                         gender: String = "", age: Int = 0, signature: String = "",
                         backgroundData: Data?) async throws -> User {
        var body = MultipartBody()
        body.field("password", password)
        body.field("name", name)
        body.field("gender", gender)
        body.field("age", String(age))
        body.field("signature", signature)
        if let avatarData {
            body.file("avatar", fileName: "avatar.jpg", contentType: "image/jpeg", fileData: avatarData)
        }
        if let backgroundData {
            body.file("background", fileName: "background.jpg", contentType: "image/jpeg", fileData: backgroundData)
        }
        body.finish()
        return try await submitUser(path: "register", boundary: body.boundary, body: body.data)
    }

    static func login(account: String, password: String) async throws -> User {
        var body = MultipartBody()
        body.field("account", account)
        body.field("password", password)
        body.finish()
        return try await submitUser(path: "login", boundary: body.boundary, body: body.data)
    }

    // MARK: - 邮箱验证码登录

    /// 空响应体：后端成功返回无内容（或仅 err 字段）的接口解码用
    struct EmptyResponse: Decodable {
        init() {}
    }

    /// 通用 POST（multipart/form-data）：提交后解码任意 Decodable 响应体。
    /// - 空响应体（data 为空）时若目标类型为 EmptyResponse 直接视为成功
    /// - authed=true 附加 Bearer token 并做 401 清登录态处理
    private static func post<T: Decodable>(path: String, boundary: String, body: Data,
                                            authed: Bool = false,
                                            timeout: TimeInterval = 30) async throws -> T {
        var req = URLRequest(url: base.appendingPathComponent(path))
        req.httpMethod = "POST"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = timeout
        req.httpBody = body
        if authed {
            applyAuth(&req)
        }
        let startedAt = Date()
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse else {
            logRequest("POST", path, startedAt: startedAt, outcome: "invalid-response")
            throw URLError(.badServerResponse)
        }
        if authed {
            try checkAuth(resp)
        }
        guard (200...299).contains(http.statusCode) else {
            logRequest("POST", path, startedAt: startedAt, outcome: "http-\(http.statusCode)")
            if let err = try? JSONDecoder().decode([String: String].self, from: data),
               let msg = err["err"] {
                throw APIError.message(msg)
            }
            throw URLError(.badServerResponse)
        }
        if data.isEmpty {
            logRequest("POST", path, startedAt: startedAt, outcome: "200-empty")
            // 空响应体：EmptyResponse 直接成功
            if T.self == EmptyResponse.self {
                return EmptyResponse() as! T
            }
            throw APIError.message("服务器返回空数据")
        }
        logRequest("POST", path, startedAt: startedAt, outcome: "200")
        return try JSONDecoder().decode(T.self, from: data)
    }

    /// 发送邮箱验证码
    static func sendEmailCode(email: String) async throws {
        var body = MultipartBody()
        body.field("email", email)
        body.finish()
        // 验证码接口固定 15s 超时，避免异常网络下按钮长期卡 loading
        let _: EmptyResponse = try await post(path: "send-email-code", boundary: body.boundary,
                                              body: body.data, timeout: 15)
    }

    /// 邮箱登录（新用户自动注册）
    static func loginEmail(email: String, code: String) async throws -> User {
        var body = MultipartBody()
        body.field("email", email)
        body.field("code", code)
        body.finish()
        return try await submitUser(path: "login-email", boundary: body.boundary, body: body.data)
    }

    /// 已登录用户绑定邮箱
    static func bindEmail(email: String, code: String) async throws {
        var body = MultipartBody()
        body.field("email", email)
        body.field("code", code)
        body.finish()
        let _: EmptyResponse = try await post(path: "bind-email", boundary: body.boundary, body: body.data, authed: true)
    }

    /// 更新当前用户资料（可选替换头像/背景图）
    static func updateProfile(account: String, name: String, gender: String, age: Int,
                              signature: String, city: String,
                              avatarData: Data?, backgroundData: Data?) async throws -> User {
        var body = MultipartBody()
        body.field("account", account)
        body.field("name", name)
        body.field("gender", gender)
        body.field("age", String(age))
        body.field("signature", signature)
        body.field("city", city)
        if let avatarData {
            body.file("avatar", fileName: "avatar.jpg", contentType: "image/jpeg", fileData: avatarData)
        }
        if let backgroundData {
            body.file("background", fileName: "background.jpg", contentType: "image/jpeg", fileData: backgroundData)
        }
        body.finish()
        return try await submitUser(path: "profile/update", boundary: body.boundary, body: body.data,
                                    authed: true)
    }

    /// 修改密码（需登录）：旧密码校验通过后更新为新密码
    static func changePassword(old: String, new: String) async throws {
        var body = MultipartBody()
        body.field("old_password", old)
        body.field("new_password", new)
        body.finish()

        var req = URLRequest(url: base.appendingPathComponent("change-password"))
        req.httpMethod = "POST"
        req.setValue("multipart/form-data; boundary=\(body.boundary)", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 30
        req.httpBody = body.data
        applyAuth(&req)
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        try checkAuth(resp)
        guard (200...299).contains(http.statusCode) else {
            if let err = try? JSONDecoder().decode([String: String].self, from: data),
               let msg = err["err"] {
                throw APIError.message(msg)
            }
            throw URLError(.badServerResponse)
        }
    }

    /// 版本更新检查结果（/api/check-update）
    struct UpdateCheck: Decodable {
        let hasUpdate: Bool
        let id: String?
        let title: String?
        let content: String?
        let version: String?
        let forceUpdate: Int?
        let downloadUrl: String?
        let downloadUrlIos: String?
    }

    /// 手动检查版本更新（携带本地版本号：同版本后端返回 hasUpdate=false，前端提示已是最新）
    static func checkUpdate() async -> UpdateCheck? {
        var comps = URLComponents(string: base.appendingPathComponent("check-update").absoluteString)
        let ver = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0.0"
        comps?.queryItems = [
            URLQueryItem(name: "v", value: ver),
            URLQueryItem(name: "platform", value: DeviceInfo.platform),
            URLQueryItem(name: "device_id", value: DeviceInfo.deviceId)
        ]
        var req = URLRequest(url: comps?.url ?? base.appendingPathComponent("check-update"))
        req.timeoutInterval = 15
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              let http = resp as? HTTPURLResponse, http.statusCode == 200,
              let obj = try? JSONDecoder().decode(UpdateCheck.self, from: data) else { return nil }
        return obj
    }

    private static func submitUser(path: String, boundary: String, body: Data,
                                   authed: Bool = false) async throws -> User {
        var req = URLRequest(url: base.appendingPathComponent(path))
        req.httpMethod = "POST"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 30
        req.httpBody = body
        if authed {
            applyAuth(&req)
        }
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        if authed {
            try checkAuth(resp)
        }
        guard (200...299).contains(http.statusCode) else {
            if let err = try? JSONDecoder().decode([String: String].self, from: data),
               let msg = err["err"] {
                throw APIError.message(msg)
            }
            throw URLError(.badServerResponse)
        }
        return try JSONDecoder().decode(User.self, from: data)
    }

    static func upload(data: Data, filename: String, title: String, artist: String, album: String,
                       userId: String = "",
                       coverData: Data? = nil, coverFilename: String? = nil) async throws {
        var body = MultipartBody()
        body.field("title", title)
        body.field("artist", artist)
        body.field("album", album)
        body.field("userId", userId)
        body.file("file", fileName: filename, contentType: "audio/mpeg", fileData: data)
        if let coverData, let coverFilename {
            body.file("cover", fileName: coverFilename, contentType: "image/jpeg", fileData: coverData)
        }
        body.finish()

        var req = URLRequest(url: base.appendingPathComponent("upload"))
        req.httpMethod = "POST"
        req.setValue("multipart/form-data; boundary=\(body.boundary)", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 180
        req.httpBody = body.data
        applyAuth(&req)
        let (data, resp) = try await URLSession.shared.data(for: req)
        try checkAuth(resp)
        guard let http = resp as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            if let err = try? JSONDecoder().decode([String: String].self, from: data),
               let msg = err["err"] {
                throw APIError.message(msg)
            }
            throw URLError(.badServerResponse)
        }
    }

    // MARK: - 消息通知

    /// 官方通知消息（聊天记录样式）
    struct OfficialMessage: Codable, Identifiable, Hashable {
        let id: String
        let content: String
        let created: Double
        let sender: String?
        let isNew: Bool?
    }

    /// 用户互动通知（点赞/评论/分享）
    struct Notification: Codable, Identifiable, Hashable {
        let id: String
        let type: String        // like / comment / share / official
        let title: String?
        let content: String?
        let fromUser: String?
        let fromUserId: String?
        let commentId: String?  // 评论类通知对应评论 id（点击定位到那条评论）
        let trackId: String?
        let trackTitle: String?
        let isRead: Int         // 0=未读 1=已读
        let created: Double

        var unread: Bool { isRead == 0 }
    }

    /// WebSocket 实时推送地址（wss://…/music/api/ws?token=xxx）
    static func wsURL(token: String) -> URL? {
        guard var comps = URLComponents(url: base, resolvingAgainstBaseURL: false) else { return nil }
        comps.scheme = "wss"
        var path = comps.path
        if !path.hasSuffix("/") { path += "/" }
        comps.path = path + "ws"
        comps.queryItems = [URLQueryItem(name: "token", value: token)]
        return comps.url
    }

    /// 获取官方通知列表（公共消息，聊天记录只读；返回未读条数 unreadCount 用于红点合计）
    static func officialMessages() async throws -> (list: [OfficialMessage], unreadCount: Int) {
        let url = base.appendingPathComponent("official-messages")
        var req = URLRequest(url: url)
        req.timeoutInterval = 15
        applyAuth(&req)
        let (data, resp) = try await URLSession.shared.data(for: req)
        try checkAuth(resp)
        guard let http = resp as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
        struct MsgResp: Decodable {
            let list: [OfficialMessage]
            let unreadCount: Int?
        }
        let r = try JSONDecoder().decode(MsgResp.self, from: data)
        return (r.list, r.unreadCount ?? 0)
    }

    /// 获取我的互动通知列表（分页：page 从 1 起，size 默认 30，返回 hasMore）
    static func notifications(page: Int = 1, size: Int = 30) async throws -> (list: [Notification], unread: Int, hasMore: Bool) {
        let data = try await get("notifications",
                                 query: [URLQueryItem(name: "page", value: "\(page)"),
                                         URLQueryItem(name: "size", value: "\(size)")],
                                 auth: true)
        struct NotifResp: Decodable {
            let list: [Notification]
            let unread: Int
            let hasMore: Bool
        }
        let r = try JSONDecoder().decode(NotifResp.self, from: data)
        return (r.list, r.unread, r.hasMore)
    }

    /// 标记所有通知为已读
    static func markNotificationsRead() async {
        var req = URLRequest(url: base.appendingPathComponent("notifications/read"))
        req.httpMethod = "POST"
        req.timeoutInterval = 10
        applyAuth(&req)
        _ = try? await URLSession.shared.data(for: req)
    }

    /// 标记官方消息为已读（更新 users.official_read_at，红点=互动未读+官方未读，两者都需清）
    static func markOfficialMessagesRead() async {
        var req = URLRequest(url: base.appendingPathComponent("official-messages/read"))
        req.httpMethod = "POST"
        req.timeoutInterval = 10
        applyAuth(&req)
        _ = try? await URLSession.shared.data(for: req)
    }

    // MARK: - 播放历史
    /// 记录播放历史
    static func recordHistory(trackId: String, title: String, artist: String, cover: String) async {
        var req = URLRequest(url: base.appendingPathComponent("history"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 10
        applyAuth(&req)
        let body: [String: Any] = ["trackId": trackId, "title": title, "artist": artist, "cover": cover]
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        _ = try? await URLSession.shared.data(for: req)
    }

    /// 获取播放历史
    /// ⚠ 必须用 URLComponents 拼 query：appendingPathComponent("history?page=..&size=..")
    ///   会把 ? 和 & 百分号编码成 %3F/%26 → 后端匹配不到 /api/history → 404 → 列表永远为空。
    static func fetchHistory(page: Int = 1, pageSize: Int = 30) async throws -> [HistoryTrack] {
        var comps = URLComponents(url: base.appendingPathComponent("history"),
                                  resolvingAgainstBaseURL: false)!
        comps.queryItems = [
            URLQueryItem(name: "page", value: "\(page)"),
            URLQueryItem(name: "size", value: "\(pageSize)")
        ]
        var req = URLRequest(url: comps.url!)
        req.timeoutInterval = 15
        applyAuth(&req)
        let (data, resp) = try await URLSession.shared.data(for: req)
        try checkAuth(resp)
        struct Resp: Decodable { let list: [HistoryTrack] }
        let r = try JSONDecoder().decode(Resp.self, from: data)
        return r.list
    }

    // MARK: - 搜索历史
    /// 记录搜索历史
    static func recordSearch(keyword: String) async {
        var req = URLRequest(url: base.appendingPathComponent("search/history"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 10
        applyAuth(&req)
        let body: [String: Any] = ["keyword": keyword]
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        _ = try? await URLSession.shared.data(for: req)
    }

    /// 获取搜索历史
    static func fetchSearchHistory() async throws -> [SearchHistoryItem] {
        var req = URLRequest(url: base.appendingPathComponent("search/history"))
        req.timeoutInterval = 15
        applyAuth(&req)
        let (data, resp) = try await URLSession.shared.data(for: req)
        try checkAuth(resp)
        struct Resp: Decodable { let list: [SearchHistoryItem] }
        let r = try JSONDecoder().decode(Resp.self, from: data)
        return r.list
    }

    /// 清空搜索历史
    static func clearSearchHistory() async {
        var req = URLRequest(url: base.appendingPathComponent("search/history"))
        req.httpMethod = "DELETE"
        req.timeoutInterval = 10
        applyAuth(&req)
        _ = try? await URLSession.shared.data(for: req)
    }

    // MARK: - 歌单
    /// 获取我的歌单列表
    static func fetchPlaylists() async throws -> [Playlist] {
        var req = URLRequest(url: base.appendingPathComponent("playlists"))
        req.timeoutInterval = 15
        applyAuth(&req)
        let (data, resp) = try await URLSession.shared.data(for: req)
        try checkAuth(resp)
        struct Resp: Decodable { let list: [Playlist] }
        let r = try JSONDecoder().decode(Resp.self, from: data)
        return r.list
    }

    /// 创建歌单
    static func createPlaylist(name: String, description: String = "") async throws -> String {
        var req = URLRequest(url: base.appendingPathComponent("playlists"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 10
        applyAuth(&req)
        let body: [String: Any] = ["name": name, "description": description]
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
        struct R: Decodable { let playlistId: String }
        let r = try JSONDecoder().decode(R.self, from: data)
        return r.playlistId
    }

    /// 获取歌单歌曲列表
    static func fetchPlaylistTracks(_ playlistId: String) async throws -> (playlist: Playlist, tracks: [HistoryTrack]) {
        var req = URLRequest(url: base.appendingPathComponent("playlists/\(playlistId)/tracks"))
        req.timeoutInterval = 15
        applyAuth(&req)
        let (data, resp) = try await URLSession.shared.data(for: req)
        try checkAuth(resp)
        struct Resp: Decodable { let playlist: Playlist; let list: [HistoryTrack] }
        let r = try JSONDecoder().decode(Resp.self, from: data)
        return (r.playlist, r.list)
    }

    /// 添加歌曲到歌单
    static func addToPlaylist(playlistId: String, trackId: String, title: String, artist: String, cover: String) async throws {
        var req = URLRequest(url: base.appendingPathComponent("playlists/\(playlistId)/tracks"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 10
        applyAuth(&req)
        let body: [String: Any] = ["trackId": trackId, "title": title, "artist": artist, "cover": cover]
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        let (_, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse else {
            throw APIError.message("无响应")
        }
        guard http.statusCode == 200 else {
            throw APIError.message("HTTP \(http.statusCode)")
        }
    }

    /// 从歌单删除歌曲
    static func removeFromPlaylist(playlistId: String, trackId: String) async {
        var req = URLRequest(url: base.appendingPathComponent("playlists/\(playlistId)/tracks/\(trackId)"))
        req.httpMethod = "DELETE"
        req.timeoutInterval = 10
        applyAuth(&req)
        _ = try? await URLSession.shared.data(for: req)
    }

    // MARK: - 通知软删除
    /// 软删除单条通知
    static func deleteNotification(_ id: String) async {
        var req = URLRequest(url: base.appendingPathComponent("notifications/\(id)"))
        req.httpMethod = "DELETE"
        req.timeoutInterval = 10
        applyAuth(&req)
        _ = try? await URLSession.shared.data(for: req)
    }

    // MARK: - 评论点赞
    /// 点赞/取消点赞评论
    static func toggleCommentLike(_ commentId: String) async throws -> (liked: Bool, count: Int) {
        var req = URLRequest(url: base.appendingPathComponent("comments/\(commentId)/like"))
        req.httpMethod = "POST"
        req.timeoutInterval = 10
        applyAuth(&req)
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
        struct R: Decodable { let liked: Bool; let likeCount: Int }
        let r = try JSONDecoder().decode(R.self, from: data)
        return (r.liked, r.likeCount)
    }

    // MARK: - 用户主页
    /// 获取用户主页信息
    static func fetchUserProfile(_ userId: String) async throws -> UserProfile {
        var req = URLRequest(url: base.appendingPathComponent("user/\(userId)/profile"))
        req.timeoutInterval = 15
        applyAuth(&req)
        let (data, resp) = try await URLSession.shared.data(for: req)
        try checkAuth(resp)
        struct Resp: Decodable {
            let userId: String; let account: String; let name: String; let avatar: String
            let signature: String; let created: Double; let gender: String; let age: Int
            let trackCount: Int; let likeCount: Int
            let followerCount: Int; let followingCount: Int; let mutualCount: Int
            let isFollowing: Bool
            let backgroundImage: String; let city: String
            let isOnline: Bool?
            let title: String?; let verified: String?; let verifiedLabel: String?
            let member: String?; let memberExpire: Double?; let svipExpire: Double?
        }
        let r = try JSONDecoder().decode(Resp.self, from: data)
        return UserProfile(userId: r.userId, account: r.account, name: r.name, avatar: r.avatar,
                          signature: r.signature, created: r.created, gender: r.gender, age: r.age,
                          trackCount: r.trackCount, likeCount: r.likeCount,
                          followerCount: r.followerCount, followingCount: r.followingCount,
                          mutualCount: r.mutualCount, isFollowing: r.isFollowing,
                          backgroundImage: r.backgroundImage, city: r.city,
                          isOnline: r.isOnline,
                          title: r.title, verified: r.verified, verifiedLabel: r.verifiedLabel,
                          member: r.member, memberExpire: r.memberExpire, svipExpire: r.svipExpire)
    }

    /// 关注/取关用户
    static func toggleFollow(_ userId: String) async throws -> Bool {
        var req = URLRequest(url: base.appendingPathComponent("user/\(userId)/follow"))
        req.httpMethod = "POST"
        req.timeoutInterval = 10
        applyAuth(&req)
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
        struct R: Decodable { let isFollowing: Bool }
        let r = try JSONDecoder().decode(R.self, from: data)
        return r.isFollowing
    }

    /// 获取用户上传的歌曲
    static func fetchUserTracks(_ userId: String) async throws -> [Track] {
        var req = URLRequest(url: base.appendingPathComponent("user/\(userId)/tracks"))
        req.timeoutInterval = 15
        applyAuth(&req)
        let (data, resp) = try await URLSession.shared.data(for: req)
        try checkAuth(resp)
        struct Resp: Decodable { let list: [Track] }
        let r = try JSONDecoder().decode(Resp.self, from: data)
        return r.list
    }
}

// MARK: - 数据模型
struct HistoryTrack: Decodable, Identifiable {
    let id: String
    let trackId: String
    let title: String
    let artist: String
    let cover: String
    let source: String
    let created: Double
}

struct SearchHistoryItem: Decodable, Identifiable {
    let id: String
    let keyword: String
    let created: Double
}

struct Playlist: Decodable, Identifiable {
    let id: String
    let name: String
    let cover: String
    let description: String
    let trackCount: Int
    let created: Double
}

struct UserProfile: Decodable {
    let userId: String
    let account: String
    let name: String
    let avatar: String
    let signature: String
    let created: Double
    let gender: String
    let age: Int
    let trackCount: Int
    let likeCount: Int
    let followerCount: Int
    let followingCount: Int
    let mutualCount: Int
    let isFollowing: Bool
    let backgroundImage: String
    let city: String
    /// 是否在线（WebSocket 活跃连接；旧数据无此字段时为 nil）
    let isOnline: Bool?
    /// 头衔/认证/会员（2026-10-19 会员体系；旧数据无此字段时为 nil）
    let title: String?
    let verified: String?
    let verifiedLabel: String?
    let member: String?
    let memberExpire: Double?
    let svipExpire: Double?
}

// MARK: - 会员开通（易支付下单）

struct PayCreateResult: Decodable {
    let ok: Bool
    let orderNo: String?
    let amount: Double?
    let plan: String?
    let payUrl: String?
    let note: String?
}

extension API {
    /// 会员开通下单：返回易支付收银台链接（后台未启用时 payUrl 为空 + note 提示）
    /// term: month 月卡 / quarter 季卡 / year 年卡（价格与天数以后台配置为准）
    static func payCreate(plan: String, term: String = "month") async throws -> PayCreateResult {
        var req = URLRequest(url: base.appendingPathComponent("pay/create"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["plan": plan, "term": term])
        req.timeoutInterval = 15
        applyAuth(&req)
        let (data, resp) = try await URLSession.shared.data(for: req)
        try checkAuth(resp)
        return try JSONDecoder().decode(PayCreateResult.self, from: data)
    }
}


// MARK: - 设备信息（平台 + 唯一 ID，上报后端用于更新链接按平台分发）

enum DeviceInfo {
    /// 设备唯一 ID：优先 identifierForVendor，持久化到本地（卸载重装会变，属正常）
    static var deviceId: String {
        if let cached = UserDefaults.standard.string(forKey: "liuying_device_id"),
           !cached.isEmpty { return cached }
        let fresh = UIDevice.current.identifierForVendor?.uuidString ?? UUID().uuidString
        UserDefaults.standard.set(fresh, forKey: "liuying_device_id")
        return fresh
    }
    static var platform: String { "ios" }
}

extension API {
    /// 启动时上报设备平台与唯一 ID（后端记录活跃设备；check-update 亦顺带记录）
    static func reportDevice(userId: String? = nil) async {
        var req = URLRequest(url: base.appendingPathComponent("device"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 10
        // 登录态下自动带 Authorization：后端据此关联设备到用户（后台用户管理可见设备类型）
        applyAuth(&req)
        var body: [String: String] = [
            "device_id": DeviceInfo.deviceId,
            "platform": DeviceInfo.platform,
            "version": (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String) ?? ""
        ]
        if let userId, !userId.isEmpty { body["user_id"] = userId }
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        _ = try? await URLSession.shared.data(for: req)
    }
}
