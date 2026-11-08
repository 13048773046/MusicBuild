import UIKit

/// 从歌曲封面提取主色作为播放页背景（带缓存）
///
/// 管线（2026-10-25 v17.1 定稿，对齐"人眼整体印象色"）：
/// 1. 封面降采样到长边 ~96px；
/// 2. 逐像素统计：过滤近黑（亮度≤0.10）与低饱和（HSV 饱和度≤0.06）像素（计入背景亮度）；
///    其余为彩色像素，按 60° 色相桶计数、亮度加权累计；
/// 3. 灰判定：彩色覆盖 <15%，或覆盖 <30% 且平均饱和度 <0.18 且主色桶占彩色 ≥85%
///    （黑白手绘 / 白底 / 微彩统一色调）→ 返回中性灰；
/// 4. 主体桶 = 亮度加权(1+3·L)最大的 60° 桶，桶内加权平均出主体色相/饱和度/亮度；
/// 5. 环境色规则（同时覆盖所有封面，非逐张打补丁）：
///    - 植被绿：绿系(60°~180°)占比 ≥25% 或平均饱和度 ≥0.30，且平均亮度 ≥0.18 → 取绿系平均色相
///      （户外/绿植封面取"植被绿"而非人物肤色）；
///    - 蓝色：主体为低饱和暖色(肤色感，饱和度≤0.35) 且环境可见(≥12%彩色、亮度≥0.20)
///      且蓝系(180°~300°)占比 ≥30% 且平均饱和度 ≥0.35 → 取蓝系平均色相（牛仔蓝/夜空）；
///    - 否则 → 主体桶色相（黑底亮主体封面如黑底粉心、黑底蓝蝶，直接取主体色）；
/// 6. 亮度锚定：亮度 = 主体亮度×0.6 + 背景亮度×0.4，再钳制到 [0.30, 0.80]
///    （背景亮的封面保持明亮，背景黑的封面保持暗调——平均值稳定）；
/// 7. 输出饱和度托底 ≥0.10（保持"灰X色"柔和感，不出现死灰）。
enum CoverPalette {
    /// NSCache 自动淘汰（内存压力/上限），线程安全无需自加锁
    private static let cache = NSCache<NSString, UIColor>()

    static func color(for id: String, from url: URL) async -> UIColor {
        if let cached = cache.object(forKey: id as NSString) {
            return cached
        }
        // P0-2：优先复用封面缓存（同一 URL 不再重复下载）；取色无需全分辨率，
        // 统一走 decodeAndCache 降采样管线，并顺手写入封面缓存供列表复用
        let uiImage: UIImage?
        if let mem = CoverImageCache.shared.image(for: url) {
            uiImage = mem
        } else if let (data, _) = try? await URLSession.shared.data(from: url) {
            uiImage = CoverImageCache.decodeAndCache(data: data, for: url, maxPixel: 512)
        } else {
            uiImage = nil
        }
        guard let uiImage, let color = extract(from: uiImage) else {
            return .systemGray5
        }
        cache.setObject(color, forKey: id as NSString)
        return color
    }

    /// 清空主色缓存（内存警告时调用）
    static func clearCache() {
        cache.removeAllObjects()
    }

    /// 播放页背景同色相三档渐变（替代"纯色+黑罩"）：顶部提亮(空气感) / 中部本色 / 底部同色相加深。
    /// 色相与饱和度完全保真，只调整亮度——避免黑色叠加把颜色弄脏、发灰。
    /// 返回 (top, mid, bottom) 三个 UIColor，亮度分别为 0.62 / 0.50 / 0.30。
    static func gradientShades(from uiColor: UIColor) -> (top: UIColor, mid: UIColor, bottom: UIColor) {
        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        uiColor.getRed(&r, green: &g, blue: &b, alpha: &a)
        let (h, s, _) = rgbToHsl(r: Double(r), g: Double(g), b: Double(b))
        func shade(_ l: Double) -> UIColor {
            let (sr, sg, sb) = hslToRgb(h: h, s: s, l: l)
            return UIColor(red: CGFloat(sr), green: CGFloat(sg), blue: CGFloat(sb), alpha: a)
        }
        return (shade(0.62), shade(0.50), shade(0.30))
    }

    // MARK: - 入口

    static func extract(from image: UIImage) -> UIColor? {
        guard let cgImage = image.cgImage else { return nil }
        guard let small = downsample(cgImage, maxSide: 96) else { return nil }
        guard let rgb = paletteVote(cgImage: small) else {
            // 灰判定：黑白手绘 / 白底 / 微彩统一色调 → 中性灰（视觉"灰色"，不偏色）
            return UIColor(white: 0.32, alpha: 1)
        }
        return UIColor(red: rgb.r, green: rgb.g, blue: rgb.b, alpha: 1)
    }

    // MARK: - 降采样

    private static func downsample(_ cgImage: CGImage, maxSide: Int) -> CGImage? {
        let w = cgImage.width, h = cgImage.height
        let long = max(w, h)
        guard long > maxSide else { return cgImage }
        let scale = CGFloat(maxSide) / CGFloat(long)
        let tw = Int(CGFloat(w) * scale), th = Int(CGFloat(h) * scale)
        guard tw > 0, th > 0, let space = cgImage.colorSpace else { return nil }
        guard let ctx = CGContext(data: nil, width: tw, height: th,
                                  bitsPerComponent: 8, bytesPerRow: tw * 4,
                                  space: space,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
        ctx.interpolationQuality = .high
        ctx.draw(cgImage, in: CGRect(x: 0, y: 0, width: tw, height: th))
        return ctx.makeImage()
    }

    // MARK: - Step 2-7：v17.1 肉眼感取色（核心）

    /// 60° 色相桶（6 桶）
    private struct Bucket {
        var count = 0
        var wSum = 0.0          // Σ(1+3·L)
        var hWSum = 0.0         // Σ(1+3·L)·H
        var sWSum = 0.0         // Σ(1+3·L)·S(HSV)
        var lWSum = 0.0         // Σ(1+3·L)·L
        var plainLSum = 0.0     // ΣL（未加权，用于环境平均亮度）
    }

    private static func paletteVote(cgImage: CGImage) -> (r: Double, g: Double, b: Double)? {
        let w = cgImage.width, h = cgImage.height
        guard let space = cgImage.colorSpace else { return nil }
        guard let ctx = CGContext(data: nil, width: w, height: h,
                                  bitsPerComponent: 8, bytesPerRow: w * 4,
                                  space: space,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
        ctx.draw(cgImage, in: CGRect(x: 0, y: 0, width: w, height: h))
        guard let data = ctx.data else { return nil }
        let ptr = data.bindMemory(to: UInt8.self, capacity: w * h * 4)

        var buckets = Array(repeating: Bucket(), count: 6)
        var bgL = 0.0, bgN = 0
        var col = 0
        var sSum = 0.0
        // 绿系(60°~180°) / 蓝系(180°~300°) 未加权统计
        var gCnt = 0, gHS = 0.0, gSS = 0.0, gLS = 0.0
        var bCnt = 0, bHS = 0.0, bSS = 0.0, bLS = 0.0

        for y in 0..<h {
            for x in 0..<w {
                let i = (y * w + x) * 4
                guard ptr[i + 3] > 128 else { continue }   // 跳过透明像素
                let r = Double(ptr[i]) / 255.0
                let g = Double(ptr[i + 1]) / 255.0
                let b = Double(ptr[i + 2]) / 255.0
                let (hue, _, lum) = rgbToHsl(r: r, g: g, b: b)
                let maxC = max(r, max(g, b))
                let minC = min(r, min(g, b))
                let chroma = maxC - minC
                if lum <= 0.10 || chroma == 0 {
                    bgL += lum; bgN += 1; continue
                }
                let sv = chroma / maxC                     // HSV 饱和度
                if sv <= 0.06 {
                    bgL += lum; bgN += 1; continue
                }
                let deg = hue * 360.0
                col += 1
                sSum += sv
                let wi = 1.0 + 3.0 * lum
                let bi = Int(hue * 6.0) % 6
                buckets[bi].count += 1
                buckets[bi].wSum += wi
                buckets[bi].hWSum += wi * hue
                buckets[bi].sWSum += wi * sv
                buckets[bi].lWSum += wi * lum
                buckets[bi].plainLSum += lum
                if deg >= 60.0 && deg < 180.0 {
                    gCnt += 1; gHS += hue; gSS += sv; gLS += lum
                } else if deg >= 180.0 && deg < 300.0 {
                    bCnt += 1; bHS += hue; bSS += sv; bLS += lum
                }
            }
        }
        let total = w * h
        let cover = Double(col) / Double(total)
        guard col >= 6 else { return nil }
        let avgS = sSum / Double(col)

        // 灰判定 1：覆盖过少
        if cover < 0.15 { return nil }
        // 灰判定 2：低覆盖 + 低饱和 + 色调高度统一（黑白手绘/微彩灰调）
        if cover < 0.30 && avgS < 0.18 {
            let mainCount = buckets.map(\.count).max() ?? 0
            if Double(mainCount) / Double(col) >= 0.85 { return nil }
        }

        // 主体桶 = 亮度加权最大的桶
        var mb = 0
        for i in 1..<6 where buckets[i].wSum > buckets[mb].wSum { mb = i }
        let main = buckets[mb]
        let mh = main.hWSum / main.wSum                    // 0~1
        let ms = max(main.sWSum / main.wSum, 0.10)
        let ml = main.lWSum / main.wSum

        // 环境（非主体桶）
        let envN = col - main.count
        var envLS = 0.0
        for i in 0..<6 where i != mb { envLS += buckets[i].plainLSum }
        let envL = envN > 0 ? envLS / Double(envN) : ml

        var hue = mh, sat = ms
        // 植被绿：绿系占比高 或 饱和度高，且亮度足够（区分真植被 vs 上衣图案）
        if gCnt > 0 {
            let gs = gSS / Double(gCnt)
            let gl = gLS / Double(gCnt)
            let gShare = Double(gCnt) / Double(col)
            if (gShare >= 0.25 || gs >= 0.30) && gl >= 0.18 {
                hue = gHS / Double(gCnt)
                sat = max(gs, 0.10)
            }
        }
        // 蓝色：主体暖肤 + 环境可见 + 蓝系显著且高饱和（牛仔蓝/夜空的肉眼感）
        if gCnt == 0 || !(Double(gCnt) / Double(col) >= 0.25 || gSS / Double(gCnt) >= 0.30) || (gLS / Double(gCnt) < 0.18) {
            let warmSkin = mh * 360.0 < 75.0 || mh * 360.0 > 330.0
            if bCnt > 0 && warmSkin && ms <= 0.35 && Double(envN) >= 0.12 * Double(col) && envL >= 0.20 {
                let bShare = Double(bCnt) / Double(col)
                let bs = bSS / Double(bCnt)
                if bShare >= 0.30 && bs >= 0.35 {
                    hue = bHS / Double(bCnt)
                    sat = max(bs, 0.10)
                }
            }
        }

        // 亮度锚定：主体×0.6 + 背景×0.4（背景亮的保持亮、背景黑的压暗）
        let bgAvg = bgN > 0 ? bgL / Double(bgN) : ml
        let ll = max(0.30, min(0.80, ml * 0.6 + bgAvg * 0.4))

        let (r, g, b) = hslToRgb(h: hue, s: sat, l: ll)
        return (r, g, b)
    }

    // MARK: - 颜色工具：RGB ↔ HSL（H 0~1, S 0~1, L 0~1）

    private static func rgbToHsl(r: Double, g: Double, b: Double) -> (h: Double, s: Double, l: Double) {
        let maxV = max(r, max(g, b)), minV = min(r, min(g, b))
        let l = (maxV + minV) / 2.0
        if maxV == minV { return (0, 0, l) }
        let d = maxV - minV
        let s = l > 0.5 ? d / (2 - maxV - minV) : d / (maxV + minV)
        var h: Double
        switch maxV {
        case r: h = ((g - b) / d + (g < b ? 6 : 0)) / 6
        case g: h = ((b - r) / d + 2) / 6
        default: h = ((r - g) / d + 4) / 6
        }
        return (h, s, l)
    }

    private static func hslToRgb(h: Double, s: Double, l: Double) -> (r: Double, g: Double, b: Double) {
        if s == 0 { return (l, l, l) }
        let q = l < 0.5 ? l * (1 + s) : l + s - l * s
        let p = 2 * l - q
        func hue2rgb(_ t0: Double) -> Double {
            var t = t0
            if t < 0 { t += 1 }
            if t > 1 { t -= 1 }
            if t < 1.0 / 6.0 { return p + (q - p) * 6 * t }
            if t < 1.0 / 2.0 { return q }
            if t < 2.0 / 3.0 { return p + (q - p) * (2.0 / 3.0 - t) * 6 }
            return p
        }
        return (hue2rgb(h + 1.0 / 3.0), hue2rgb(h), hue2rgb(h - 1.0 / 3.0))
    }
}
