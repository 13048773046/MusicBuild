package app.star2936.guava3291.util

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.compose.ui.graphics.Color
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.util.concurrent.ConcurrentHashMap

// 对应 iOS CoverPalette.swift：封面主色提取（v17.1 肉眼感取色，2026-10-25 定稿）+ 背景三档渐变

object ColorPalette {
    // 与 iOS NSCache 语义近似：ConcurrentHashMap 上限 + 内存压力时清空
    private val cache = ConcurrentHashMap<String, Color>()
    private const val CacheMax = 600

    fun colorFor(id: String, fromUrl: String): Color? = cache[id]

    suspend fun load(id: String, url: String): Color? {
        cache[id]?.let { return it }
        val color = withContext(Dispatchers.IO) {
            try {
                val conn = java.net.URL(url).openConnection()
                conn.connectTimeout = 8000
                conn.readTimeout = 8000
                val input = conn.getInputStream()
                val bitmap = BitmapFactory.decodeStream(input)
                input.close()
                extractV171(bitmap)
            } catch (_: Exception) { null }
        }
        if (color != null) {
            if (cache.size >= CacheMax) cache.clear()
            cache[id] = color
        }
        return color
    }

    /** 提取封面**背景主色**（v17.1 肉眼感取色，对齐 iOS CoverPalette 2026-10-25 定稿）：
     *  1. 封面缩小到长边 ≤96px，大幅减少统计像素量；
     *  2. 逐像素统计：过滤近黑（亮度≤0.10）与低饱和（HSV 饱和度≤0.06）像素（计入背景亮度）；
     *     其余为彩色像素，按 60° 色相桶计数、亮度加权累计；
     *  3. 灰判定：彩色覆盖 <15%，或覆盖 <30% 且平均饱和度 <0.18 且主色桶占彩色 ≥85%
     *     （黑白手绘 / 白底 / 微彩统一色调）→ 返回 null，调用方兜底中性深色；
     *  4. 主体桶 = 亮度加权(1+3·L)最大的 60° 桶，桶内加权平均出主体色相/饱和度/亮度；
     *  5. 环境色规则（同时覆盖所有封面）：
     *     - 植被绿：绿系(60°~180°)占比 ≥25% 或平均饱和度 ≥0.30，且平均亮度 ≥0.18 → 取绿系平均色相；
     *     - 蓝色：主体为低饱和暖色(肤色感，饱和度≤0.35) 且环境可见(≥12%彩色、亮度≥0.20)
     *       且蓝系(180°~300°)占比 ≥30% 且平均饱和度 ≥0.35 → 取蓝系平均色相；
     *     - 否则 → 主体桶色相（黑底亮主体封面直接取主体色）；
     *  6. 亮度锚定：亮度 = 主体亮度×0.6 + 背景亮度×0.4，再钳制到 [0.30, 0.80]；
     *  7. 输出饱和度托底 ≥0.10。 */
    fun extractV171(bitmap: Bitmap): Color? {
        val w = bitmap.width
        val h = bitmap.height
        if (w <= 0 || h <= 0) return null
        // 缩放到长边 ≤96（与 iOS 一致）
        val longSide = maxOf(w, h)
        val scale = if (longSide > 96) 96f / longSide else 1f
        val scaled = if (scale < 1f) Bitmap.createScaledBitmap(bitmap, (w * scale).toInt().coerceAtLeast(1), (h * scale).toInt().coerceAtLeast(1), true)
                     else bitmap
        val sw = scaled.width
        val sh = scaled.height
        val pixels = IntArray(sw * sh)
        scaled.getPixels(pixels, 0, sw, 0, 0, sw, sh)
        if (scaled != bitmap) scaled.recycle()

        // 6 个 60° 色相桶
        val counts = IntArray(6)
        val wSums = DoubleArray(6)      // Σ(1+3·L)
        val hWS = DoubleArray(6)        // Σ(1+3·L)·H
        val sWS = DoubleArray(6)        // Σ(1+3·L)·S(HSV)
        val lWS = DoubleArray(6)        // Σ(1+3·L)·L
        val plainLS = DoubleArray(6)    // ΣL（未加权）
        var bgL = 0.0
        var bgN = 0
        var col = 0
        var sSum = 0.0
        // 绿系(60°~180°) / 蓝系(180°~300°) 未加权统计
        var gCnt = 0; var gHS = 0.0; var gSS = 0.0; var gLS = 0.0
        var bCnt = 0; var bHS = 0.0; var bSS = 0.0; var bLS = 0.0

        for (i in pixels.indices) {
            val px = pixels[i]
            val a = (px shr 24) and 0xFF
            if (a == 0) continue
            val r = ((px shr 16) and 0xFF) / 255f
            val g = ((px shr 8) and 0xFF) / 255f
            val b = (px and 0xFF) / 255f
            val maxC = maxOf(r, g, b)
            val minC = minOf(r, g, b)
            val chroma = maxC - minC
            val lum = (maxC + minC) / 2f
            if (lum <= 0.10f || chroma == 0f) { bgL += lum; bgN += 1; continue }
            val sv = chroma / maxC                     // HSV 饱和度
            if (sv <= 0.06f) { bgL += lum; bgN += 1; continue }
            val (hue, _, _) = rgbToHsl(r, g, b)          // H 0~1
            val deg = hue * 360f
            col += 1
            sSum += sv
            val wi = 1.0 + 3.0 * lum
            val bi = (hue * 6f).toInt() % 6
            counts[bi] += 1
            wSums[bi] += wi
            hWS[bi] += wi * hue
            sWS[bi] += wi * sv
            lWS[bi] += wi * lum
            plainLS[bi] += lum
            if (deg >= 60f && deg < 180f) { gCnt += 1; gHS += hue; gSS += sv; gLS += lum }
            else if (deg >= 180f && deg < 300f) { bCnt += 1; bHS += hue; bSS += sv; bLS += lum }
        }
        val total = sw * sh
        val cover = col.toDouble() / total
        if (col < 6) return null
        val avgS = sSum / col

        // 灰判定 1：覆盖过少
        if (cover < 0.15) return null
        // 灰判定 2：低覆盖 + 低饱和 + 色调高度统一（黑白手绘/微彩灰调）
        if (cover < 0.30 && avgS < 0.18) {
            val mainCount = counts.maxOrNull() ?: 0
            if (mainCount.toDouble() / col >= 0.85) return null
        }

        // 主体桶 = 亮度加权最大的桶
        var mb = 0
        for (i in 1 until 6) { if (wSums[i] > wSums[mb]) mb = i }
        val mh = hWS[mb] / wSums[mb]                    // 0~1
        val ms = maxOf(sWS[mb] / wSums[mb], 0.10)
        val ml = lWS[mb] / wSums[mb]

        // 环境（非主体桶）
        val envN = col - counts[mb]
        var envLS = 0.0
        for (i in 0 until 6) { if (i != mb) envLS += plainLS[i] }
        val envL = if (envN > 0) envLS / envN else ml

        var hue = mh
        var sat = ms
        // 植被绿：绿系占比高 或 饱和度高，且亮度足够（区分真植被 vs 上衣图案）
        var greenUsed = false
        if (gCnt > 0) {
            val gs = gSS / gCnt
            val gl = gLS / gCnt
            val gShare = gCnt.toDouble() / col
            if ((gShare >= 0.25 || gs >= 0.30) && gl >= 0.18) {
                hue = gHS / gCnt
                sat = maxOf(gs, 0.10)
                greenUsed = true
            }
        }
        // 蓝色：主体暖肤 + 环境可见 + 蓝系显著且高饱和
        if (!greenUsed) {
            val warmSkin = mh * 360.0 < 75.0 || mh * 360.0 > 330.0
            if (bCnt > 0 && warmSkin && ms <= 0.35 && envN >= 0.12 * col && envL >= 0.20) {
                val bShare = bCnt.toDouble() / col
                val bs = bSS / bCnt
                if (bShare >= 0.30 && bs >= 0.35) {
                    hue = bHS / bCnt
                    sat = maxOf(bs, 0.10)
                }
            }
        }

        // 亮度锚定：主体×0.6 + 背景×0.4（背景亮的保持亮、背景黑的压暗）
        val bgAvg = if (bgN > 0) bgL / bgN else ml.toDouble()
        val ll = (ml * 0.6 + bgAvg * 0.4).coerceIn(0.30, 0.80)

        val (nr, ng, nb) = hslToRgb(hue.toFloat(), sat.toFloat(), ll.toFloat())
        return Color(nr.coerceIn(0f, 1f), ng.coerceIn(0f, 1f), nb.coerceIn(0f, 1f), 1f)
    }

    /** 播放页背景同色相三档渐变（对齐 iOS CoverPalette.gradientShades）：
     *  色相/饱和度完全保真，只调整亮度——避免黑色叠加把颜色弄脏、发灰。
     *  返回 (top 0.62 顶部提亮空气感, mid 0.50 中部本色, bottom 0.30 底部同色相加深)。 */
    fun gradientShades(color: Color): Triple<Color, Color, Color> {
        val (hh, ss, _) = rgbToHsl(color.red, color.green, color.blue)
        fun shade(l: Float): Color {
            val (r, g, b) = hslToRgb(hh, ss, l)
            return Color(r.coerceIn(0f, 1f), g.coerceIn(0f, 1f), b.coerceIn(0f, 1f), color.alpha)
        }
        return Triple(shade(0.62f), shade(0.50f), shade(0.30f))
    }

    /** 侧滑栏同色相三档加深（对应 iOS SidebarView "比播放页更暗一档"）：
     *  在 gradientShades 基础上整体再降一档亮度（0.38 / 0.30 / 0.22），
     *  色相/饱和度完全保真——不再用 RGB 乘法压暗（乘法会把颜色压得浑浊发灰），
     *  保证白色文字可读的同时与播放页背景是同一套色相语言。 */
    fun darkenedShades(color: Color): Triple<Color, Color, Color> {
        val (hh, ss, _) = rgbToHsl(color.red, color.green, color.blue)
        fun shade(l: Float): Color {
            val (r, g, b) = hslToRgb(hh, ss, l)
            return Color(r.coerceIn(0f, 1f), g.coerceIn(0f, 1f), b.coerceIn(0f, 1f), color.alpha)
        }
        return Triple(shade(0.38f), shade(0.30f), shade(0.22f))
    }

    fun clear() { cache.clear() }

    // MARK: 颜色工具：RGB ↔ HSL（H 0~1, S 0~1, L 0~1）

    private data class Hsl(val h: Float, val s: Float, val l: Float)

    private fun rgbToHsl(r: Float, g: Float, b: Float): Hsl {
        val maxC = maxOf(r, g, b)
        val minC = minOf(r, g, b)
        val l = (maxC + minC) / 2f
        if (maxC == minC) return Hsl(0f, 0f, l)
        val d = maxC - minC
        val s = if (l > 0.5f) d / (2f - maxC - minC) else d / (maxC + minC)
        var hue = when (maxC) {
            r -> (g - b) / d
            g -> 2f + (b - r) / d
            else -> 4f + (r - g) / d
        }
        hue /= 6f
        if (hue < 0f) hue += 1f
        return Hsl(hue, s, l)
    }

    private fun hslToRgb(h: Float, s: Float, l: Float): Triple<Float, Float, Float> {
        if (s == 0f) return Triple(l, l, l)
        val q = if (l < 0.5f) l * (1f + s) else l + s - l * s
        val p = 2f * l - q
        fun hueToRGB(t0: Float): Float {
            var t = t0
            if (t < 0f) t += 1f
            if (t > 1f) t -= 1f
            if (t < 1f / 6f) return p + (q - p) * 6f * t
            if (t < 1f / 2f) return q
            if (t < 2f / 3f) return p + (q - p) * (2f / 3f - t) * 6f
            return p
        }
        return Triple(hueToRGB(h + 1f / 3f), hueToRGB(h), hueToRGB(h - 1f / 3f))
    }
}
