# -*- coding: utf-8 -*-
"""流萤音乐 · 番茄音乐（番茄畅听音乐版，novelfm）分享链接解析模块。

与 qishui.py 同构：抓分享短链 → 提取 window._ROUTER_DATA → 返回统一歌曲元数据。
分享页自带：api_book_info（歌名/作者/ID/封面 440x440 带签名）、
video_info.main_url（音频直链 audio/mp4，带签名可直接下载）、
lyric_info（lyric_type=2 时带字节 KRC 变体逐字歌词；=0 时无歌词）。
全程免登录、免接口、免密钥。
"""
import json
import re
import urllib.request

from server.config import QISHUI_SHARE_UA  # 复用手机 UA

__all__ = ['_fanqie_fetch_share', '_fanqie_router_data', '_fanqie_lyric_krc', '_fanqie_parse_share']

FANQIE_SHARE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"


def _fanqie_fetch_share(url):
    """抓取番茄音乐分享短链（跟随重定向），返回最终落地页 HTML 文本。"""
    if not url or "://" not in url:
        raise ValueError("无效链接")
    req = urllib.request.Request(url, headers={
        "User-Agent": FANQIE_SHARE_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
    with opener.open(req, timeout=25) as r:
        raw = r.read()
    charset = "utf-8"
    ctype = r.headers.get("Content-Type", "")
    m = re.search(r"charset=([\w-]+)", ctype, re.I)
    if m:
        charset = m.group(1)
    return raw.decode(charset, "replace")


def _fanqie_router_data(html):
    """从分享页 HTML 中提取 window._ROUTER_DATA JSON（与汽水同构，带 window. 前缀）。
    用 find 定位而非正则：JSON 体约 60KB 且结尾分号可有可无，正则边界易失配。"""
    i = html.find("window._ROUTER_DATA = ")
    if i < 0:
        raise ValueError("页面数据解析失败（window._ROUTER_DATA 未找到）")
    j = html.find("</script>", i)
    if j < 0:
        j = html.find("</SCRIPT>", i)
    raw = html[i + len("window._ROUTER_DATA = "):j].strip().rstrip(";")
    if not raw:
        raise ValueError("页面数据解析失败（window._ROUTER_DATA 为空）")
    return json.loads(raw)


def _fanqie_lyric_krc(lyric_info):
    """番茄逐字歌词（字节 KRC 变体）→ 流萤 KRC/标准 LRC。

    输入行格式：[行开始ms,行持续ms]<字开始ms(相对行),字持续ms,0>字<...>字
    输出：
    - 有逐字：KRC 行 [mm:ss.xx]<绝对开始ms,持续ms>字（流萤 parseLyrics 自动识别逐字）
    - 退化：标准 LRC 行 [mm:ss.xx]整行文本
    无歌词（lyric_info 为空）返回空串。
    """
    if not lyric_info or not isinstance(lyric_info, str):
        return ""
    lyric_info = lyric_info.strip()
    if not lyric_info:
        return ""
    lines_out = []
    for raw_line in lyric_info.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        m = re.match(r"^\[(\d+),(\d+)\](.*)$", line)
        if not m:
            continue
        line_start = int(m.group(1))          # 行开始 ms（绝对）
        body = m.group(3)
        # 逐字段：<相对开始ms,持续ms,标志>字
        words = []
        for wm in re.finditer(r"<(\d+),(\d+),(\d+)>([^<]*)", body):
            rel_start, dur, _flag, ch = int(wm.group(1)), int(wm.group(2)), wm.group(3), wm.group(4)
            ch = ch.replace("<", "").strip()
            if not ch:
                continue
            words.append((line_start + rel_start, dur if dur > 0 else 200, ch))
        head = "[%02d:%02d.%02d]" % (line_start // 60000, (line_start // 1000) % 60, (line_start % 1000) // 10)
        if len(words) > 1:
            # 全字时间戳有效 → KRC 逐字
            parts = ["<%d,%d>%s" % (st, dur, ch) for st, dur, ch in words]
            lines_out.append(head + "".join(parts))
        else:
            # 无逐字信息：整行 LRC（时间取行首）
            text = re.sub(r"<\d+,\d+,\d+>[^<]*", "", body).strip()
            lines_out.append(head + (text or ""))
    return "\n".join(lines_out)


def _fanqie_parse_share(url):
    """解析番茄音乐分享链接 → 歌曲元数据 dict（封面 / 逐字歌词 / 音频直链）。

    返回字段：track_id,title,artist,album,cover,cover_original,genre,
              duration,lyric_lrc,audio_url,audio_size
    """
    html = _fanqie_fetch_share(url)
    data = _fanqie_router_data(html)
    pd = ((data.get("loaderData") or {}).get("music-share_page") or {}).get("pageData") or {}
    abi = pd.get("api_book_info") or {}
    track_id = str(abi.get("id") or "").strip()
    if not track_id:
        raise ValueError("未能从链接中解析出歌曲 ID")
    title = str(abi.get("name") or "").strip()
    artist = str(abi.get("author") or "").strip()
    # 作者兜底：author_infos 里的名字
    if not artist:
        ais = abi.get("author_infos") or []
        if ais and isinstance(ais[0], dict):
            artist = str(ais[0].get("name") or "").strip()
    album = title
    # 封面：分享页 440x440 带签名（去掉尺寸段会签名失效 403，直接存 440 高清）
    cover = str(pd.get("audio_thumb_uri_webp") or abi.get("audio_thumb_uri") or "").strip()
    if not cover:
        cover = str(abi.get("audio_thumb_uri") or "").strip()
    # 分类标签：genre_type 为数字分类（200=音乐），op_tag 为运营数字标签，无中文映射 → 留空
    genre = ""
    # 时长：分享页无 duration 字段，下载后由调用方用 ffprobe 补充
    duration = 0
    # 歌词：lyric_type=2 时 lyric_info 为逐字 KRC 变体
    lyric_lrc = _fanqie_lyric_krc(pd.get("lyric_info") or "")
    # 音频直链：video_info.main_url（签名直链，可直接下载 audio/mp4）
    vi = pd.get("video_info") or {}
    audio_url = str(vi.get("main_url") or vi.get("back_url") or "").strip()
    if not audio_url and isinstance(vi, dict) and vi.get("play_api"):
        audio_url = str(vi.get("play_api") or "").strip()
    audio_size = 0
    return {
        "track_id": track_id, "title": title, "artist": artist,
        "album": album, "cover": cover, "cover_original": cover, "genre": genre,
        "duration": duration, "lyric_lrc": lyric_lrc, "audio_url": audio_url,
        "audio_size": audio_size,
    }
