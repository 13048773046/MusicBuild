# -*- coding: utf-8 -*-
"""P2-13 iOS：PlayerManager.swift 1964 行按职责拆 4 个 extension 文件。
方法按行号区间提取；主文件保留 import/枚举/属性/init/deinit；
private/private(set) 统一去掉（internal），供跨文件 extension 访问。"""
import re

SRC = "MusicApp/Sources/PlayerManager.swift"
OUT = "MusicApp/Sources/"

# 方法名 -> (归属文件, 排序)
# 用 grep 动态取行号，方法体 = 本方法行 .. 下一方法行-1（含方法前注释）
def find_methods():
    lines = open(SRC, encoding="utf-8").read().splitlines(keepends=True)
    # 收集所有方法行（func / deinit 起始行），行号 1-based
    meth = []  # (lineno, name)
    for i, l in enumerate(lines, 1):
        m = re.match(r"\s*(?:private\s+|fileprivate\s+)?(?:static\s+)?func\s+(\w+)", l)
        if m:
            meth.append((i, m.group(1)))
        elif re.match(r"\s*deinit\b", l):
            meth.append((i, "deinit"))
    return lines, meth

# 归属：name -> file
PLAYBACK = {"applyEQ", "engineMode", "reloadCurrent", "trackId", "setTrackId",
            "prepareQueuePlayer", "topUpQueue", "handleBufferingStall",
            "handlePlaybackFailure", "seek", "playbackURL", "playCurrent",
            "loadArtworkIfNeeded", "setupRemoteCommands", "setupInterruptionHandling",
            "restoreOutputVolume", "setupEngineRestoreHandling", "resumePlayback",
            "pausePlayback", "updateNowPlaying"}
QUEUE = {"play", "playInNormalMode", "playNotificationTrack", "toggle", "next",
         "syncIndexWithPlayer", "adoptAutoAdvancedTrack", "loadNextBatchOrLoop",
         "previous", "cyclePlayMode", "applyListenMode", "noteRecommended",
         "startRecommendedStream", "switchToLiked", "switchToWorks",
         "playLikedTrack", "playWorksTrack", "switchToFresh", "randomIndex",
         "trimQueueIfNeeded"}
LISTEN = {"listenTick", "flushListen"}
SESSION = {"saveSessionToDisk", "restoreLastSessionIfNeeded", "saveSession",
           "restoreSession", "handleFailedSwitch", "restoreNormalStream",
           "schedulePrefetchWhenReady", "schedulePrefetch", "warmURLOnly",
           "prewarm", "syncPrefetchedItems", "warmMetadata", "prefetchPalette",
           "prefetchCover", "prefetchLyric"}

def strip_private(text):
    """private/private(set)/fileprivate 修饰去掉 -> internal（跨文件 extension 可访问）。"""
    text = re.sub(r"\bprivate\(set\)\s+", "", text)
    text = re.sub(r"\bfileprivate\s+", "", text)
    text = re.sub(r"\bprivate\s+", "", text)
    return text

def main():
    lines, meth = find_methods()
    n = len(lines)
    # 每个方法区间：起点=方法行（向前吸收紧邻注释/空行），终点=下一方法行-1
    segs = {}  # name -> (start_line, end_line) 1-based
    for idx, (lineno, name) in enumerate(meth):
        end = (meth[idx + 1][0] - 1) if idx + 1 < len(meth) else n - 1  # 末方法排除类尾 }
        start = lineno
        segs[name] = (start, end)

    # 方法区顶层的存储属性块（4 空格缩进、不在方法体内的 var/let/@Published/static let + 初始化表达式）
    # 必须整块留在主类：声明行 -> brace 平衡行（含前导注释）
    prop_blocks = []  # (start_line, end_line) 1-based 含
    for name, (s, e) in segs.items():
        ln = s
        while ln <= e:
            t = lines[ln - 1]
            if re.match(r"^    (?:@Published\s+)?(?:private\s+)?(?:static\s+)?(?:var|let)\b", t) or \
               re.match(r"^    @Published\b", t):
                start = ln
                while start > s and re.match(r"^\s*//", lines[start - 2]):
                    start -= 1
                depth = 0
                end = ln
                for j in range(ln, e + 2):
                    depth += lines[j - 1].count("{") - lines[j - 1].count("}")
                    if depth <= 0:
                        end = j
                        break
                prop_blocks.append((start, end))
                ln = end + 1
            else:
                ln += 1
    top_prop_lines = set()
    for s0, e0 in prop_blocks:
        for ln_no in range(s0, e0 + 1):
            top_prop_lines.add(ln_no)

    groups = {"playback": [], "queue": [], "listen": [], "session": []}
    for name, (s, e) in segs.items():
        if name in PLAYBACK:
            groups["playback"].append((s, e))
        elif name in QUEUE:
            groups["queue"].append((s, e))
        elif name in LISTEN:
            groups["listen"].append((s, e))
        elif name in SESSION:
            groups["session"].append((s, e))
        # 其余（init/deinit/未知）保留在主文件
    print("方法数:", len(meth), "归属:", {k: len(v) for k, v in groups.items()})

    # 提取正文
    body = "".join(lines)
    for key in groups:
        groups[key].sort()
    # 主文件：删除已搬区间（顶层存储属性块保留在主类）
    drop = sorted([r for g in groups.values() for r in g], key=lambda r: r[0])
    keep_lines = []
    prev_end = 0
    for s, e in drop:
        keep_lines.extend(lines[prev_end:s - 1])
        for ln_no in range(s - 1, e):
            if ln_no + 1 in top_prop_lines:
                keep_lines.append(lines[ln_no])
        prev_end = e
    keep_lines.extend(lines[prev_end:])
    main_text = strip_private("".join(keep_lines))
    with open(SRC, "w", encoding="utf-8") as f:
        f.write(main_text)
    print("主文件行数:", len(main_text.splitlines()))

    # extension 文件
    header = ("// PlayerManager+{Title}.swift\n"
              "// P2-13：从 PlayerManager.swift 拆出的 {label} 逻辑（extension 保持零引用改动）\n"
              "import AVFoundation\nimport MediaPlayer\nimport UIKit\nimport Combine\n\n"
              "extension PlayerManager {{\n\n")
    for key, label in (("playback", "播放引擎"), ("queue", "队列与推荐流"),
                       ("listen", "收听行为上报"), ("session", "会话恢复与预取")):
        parts = []
        for s, e in groups[key]:
            seg = []
            for ln_no in range(s - 1, e):
                if ln_no + 1 in top_prop_lines:
                    continue  # 顶层存储属性块留在主类
                seg.append(lines[ln_no])
            parts.append("".join(seg))
        text = strip_private("".join(parts))
        with open(OUT + f"PlayerManager+{key.title()}.swift", "w", encoding="utf-8") as f:
            f.write(header.format(Title=key.title(), label=label) + text + "}\n")
        print(f"生成 PlayerManager+{key.title()}.swift")

if __name__ == "__main__":
    main()
