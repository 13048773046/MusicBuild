package app.star2936.guava3291.util

// 对应 iOS LyricView.swift：KRC / LRC 歌词解析（毫秒级逐字时间戳）
// 2026-10-22 对齐 iOS：KRCLine 增加 isKaraoke 标志；[ti:]/[ar:] 标签行与元信息内容行强制整句显示；
// 新增 foliaEnhancedLRC()（Folia 全貌版 enhanced-lrc 逐字格式转换）。

data class KRWord(val text: String, val start: Double, var end: Double)

data class KRCLine(
    val words: List<KRWord>,
    val start: Double,
    val end: Double,
    /** 是否为逐字卡拉OK行（多字且带逐字时间戳）；false=整句行（元信息/标签/标准LRC行） */
    val isKaraoke: Boolean = false,
) {
    val text: String get() = words.joinToString("") { it.text }
}

private val rowRegex = Regex("""\[(\d+):(\d+)(?:[.:](\d+))?(?:,(\d+))?\]""")
private val wordRegex = Regex("""<(\d+),(\d+)>([^<]*)""")

/** 元信息内容行关键词（作词/作曲/演唱等非唱词，不参与逐字渲染） */
private val metaKeywords = listOf(
    "作词", "作曲", "编曲", "演唱", "原唱", "翻唱", "制作人",
    "录音", "混音", "母带", "和声", "监制", "企划", "发行", "出品",
    "专辑", "版权", "词曲", "翻译",
)

fun parseLyrics(text: String): List<KRCLine> {
    if (text.isEmpty()) return emptyList()
    val lines = mutableListOf<KRCLine>()

    for (raw in text.lines()) {
        val rowMatch = rowRegex.find(raw) ?: continue
        if (rowMatch.range.first != 0) continue
        val mm = rowMatch.groupValues[1].toDoubleOrNull() ?: 0.0
        val ss = rowMatch.groupValues[2].toDoubleOrNull() ?: 0.0
        var frac = 0.0
        if (rowMatch.groupValues[3].isNotEmpty()) {
            val f = rowMatch.groupValues[3].toDoubleOrNull() ?: 0.0
            frac = f / (if (f >= 100) 1000.0 else 100.0)   // 三位=毫秒，两位=百分秒
        }
        val rowStart = mm * 60 + ss + frac
        val body = raw.substring(rowMatch.range.last + 1)

        val words = mutableListOf<KRWord>()
        var wordFound = false
        for (m in wordRegex.findAll(body)) {
            val stMs = m.groupValues[1].toDoubleOrNull() ?: 0.0
            val durMs = m.groupValues[2].toDoubleOrNull() ?: 0.0
            val txt = m.groupValues[3]
            if (txt.isEmpty()) continue
            words.add(KRWord(txt, stMs / 1000.0, (stMs + durMs) / 1000.0))
            wordFound = true
        }

        if (!wordFound) {
            val txt = body.trim()
            if (txt.isEmpty()) continue
            words.add(KRWord(txt, rowStart, rowStart))
        }
        if (words.isEmpty()) continue

        // 过滤平台水印/贡献者等非歌词元信息行
        val joined = words.joinToString("") { it.text }
        if (joined.contains("滚动歌词") || joined.contains("翻译贡献者") || joined.contains("贡献者")) continue

        // 是否逐字卡拉OK行：多个带逐字时间戳的字 且 非标签/元信息行
        val isTagLine = joined.startsWith("[ti:") || joined.startsWith("[ar:") || joined.startsWith("[al:")
        val isMetaLine = metaKeywords.any { joined.contains(it) }
        val isKaraoke = wordFound && words.size > 1 && !isTagLine && !isMetaLine

        lines.add(KRCLine(words, words.first().start, words.last().end, isKaraoke))
    }

    if (lines.isEmpty()) return emptyList()
    lines.sortBy { it.start }
    // LRC 行（end == start）的结束时间取下一行开始时间
    for (i in lines.indices) {
        if (lines[i].end <= lines[i].start) {
            val nextStart = if (i + 1 < lines.size) lines[i + 1].start else lines[i].start + 3.0
            if (nextStart > lines[i].start) {
                val words = lines[i].words.toMutableList()
                val last = words.last()
                words[words.size - 1] = KRWord(last.text, last.start, nextStart)
                lines[i] = KRCLine(words, lines[i].start, nextStart, lines[i].isKaraoke)
            }
        }
    }
    return lines
}

/**
 * 结构化歌词行 → Folia enhanced-lrc 逐字格式（卡拉OK逐字高亮），对应 iOS FoliaWebSync.foliaEnhancedLRC。
 *
 * `[mm:ss.xx]<mm:ss.xx>字<mm:ss.xx>字...`（逐字标签为绝对时间，字的结束=下一字标签/行结束）。
 * - 行时间戳：mm:ss.xx（百分秒，与标准 LRC 一致）
 * - 逐字行（isKaraoke 且有逐字 words）：`<mm:ss.xx>` 绝对开始时间依次拼接
 * - 整句行（标准 LRC/元信息行）：直接输出 `[mm:ss.xx]整句文本`，Folia 自动按行时长均分逐字
 * - 标题/作者行与元信息内容行（作词/作曲/演唱等）过滤，避免污染歌词区
 */
fun foliaEnhancedLRC(lines: List<KRCLine>, title: String, artist: String): String {
    fun stamp(t: Double): String {
        val mm = t.toInt() / 60
        val ss = t.toInt() % 60
        val cs = ((t - t.toInt()) * 100).toInt()   // 百分秒（与 Folia mm:ss.xx 兼容）
        return String.format("%02d:%02d.%02d", mm, ss, cs)
    }
    val out = mutableListOf<String>()
    for (line in lines) {
        val t = line.start
        if (t < 0) continue
        val text = line.text
        if (text.isEmpty()) continue
        // 标题/作者行（[ti:]/[ar:] 转成的纯文本，与歌曲元数据比对）
        if (text == title || (artist.isNotEmpty() && text == artist)) continue
        // 兜底：形如 "[ti:xxx]" / "[ar:xxx]" 的标签行
        if (text.startsWith("[") && text.endsWith("]")) {
            val inner = text.substring(1, text.length - 1)
            val colon = inner.indexOf(':')
            if (colon > 0 && inner.substring(0, colon).all { it.isLetter() }) continue
        }
        // 元信息内容行：作词/作曲/演唱/…（非唱词，不输出）
        if (metaKeywords.any { text.contains(it) }) continue

        val body: String
        if (line.isKaraoke && line.words.size > 1) {
            val sb = StringBuilder()
            for (word in line.words) {
                sb.append('<').append(stamp(word.start)).append('>').append(word.text)
            }
            body = sb.toString()
        } else {
            body = text // 整句行：Folia 自动按行时长均分逐字
        }
        out.add("[${stamp(t)}]$body")
    }
    return out.joinToString("\n")
}
