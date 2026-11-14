#!/usr/bin/env python3
"""站外影片網址判斷與官網頁 mp4 解析（含中文檔名百分號編碼）。"""
import html, re, urllib.parse, urllib.request

# 這些平台不要自動下載（也不用 cookie）。手動指定場次才可能另外處理。
_STREAM_HOSTS = {
    "youtube.com", "youtu.be", "youtube-nocookie.com",
    "webex.com",
    "gotowebinar.com", "gotomeeting.com", "goto.com", "logmeininc.com",
    "zoom.us", "zoom.com",
    "vimeo.com",
    "zucast.com",
    "webcast-eqs.com",
    "facebook.com", "fb.watch",
    "twitch.tv",
    "teams.microsoft.com", "teams.live.com",
    "meet.google.com",
    "drive.google.com", "docs.google.com",
    "knowledgevision.com",
    "dropbox.com",
}

def is_direct_mp4(url):
    if not url: return False
    path = url.split("?", 1)[0].split("#", 1)[0]
    return path.lower().endswith(".mp4")

def encode_url(url):
    """http->https，並把路徑裡的非 ASCII（例如中文檔名）編成百分號，urllib 才能下載。"""
    url = (url or "").replace("http://", "https://", 1)
    parts = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(urllib.parse.unquote(parts.path), safe="/")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))

def _host(url):
    host = urllib.parse.urlsplit(url or "").netloc.lower()
    if host.startswith("www."): host = host[4:]
    if host.startswith("m."): host = host[2:]
    return host

def is_stream_url(url):
    """YouTube／Webex／GoToWebinar 等串流或需登入的平台，自動排程不下載。"""
    if not url: return False
    host = _host(url)
    if host in _STREAM_HOSTS:
        return True
    return any(host.endswith("." + h) for h in _STREAM_HOSTS)

def is_offsite_page(url):
    """公司／券商官網頁：不是直接 mp4，也不是串流平台。"""
    url = (url or "").strip()
    return bool(url) and not is_direct_mp4(url) and not is_stream_url(url)

def resolve_from_page(page_url, date="", require_date=False):
    """從站外網頁抓直接 mp4；檔名含場次日期（YYYYMMDD）的優先。
    require_date=True 時，沒有日期吻合的 mp4 就視為找不到（避免誤抓舊場次）。"""
    req = urllib.request.Request(encode_url(page_url), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw, base = r.read(), r.geturl()
    text = raw.decode("utf-8", "replace")
    hrefs = re.findall(r'(?:href|src)=["\']([^"\']+)["\']', text, re.I)
    mp4s, seen = [], set()
    for h in hrefs:
        full = urllib.parse.urljoin(base, html.unescape(h))
        if not is_direct_mp4(full): continue
        if full in seen: continue
        seen.add(full)
        mp4s.append(full)
    if not mp4s:
        raise RuntimeError(f"頁面找不到 mp4: {page_url}")
    ymd = (date or "").replace("-", "")
    if ymd:
        dated = [u for u in mp4s if ymd in urllib.parse.unquote(u)]
        if dated:
            return dated[0]
        if require_date:
            raise RuntimeError(f"頁面有 mp4 但沒有符合日期 {ymd} 的檔名: {page_url}")
    return mp4s[0]
