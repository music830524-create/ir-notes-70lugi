#!/usr/bin/env python3
"""Refresh data/conferences.json from public sources (no keys needed).
 - MOPS 法說會公告：行事曆（時間、地點、簡報、主辦、公司頁）。不讀公告欄的影片。
 - TWSE WebPro：148、101 只收直接 mp4；170（站外）保留 webLinkPath，介面標「有影片 (站外)」
Unknown fields stay null/empty -- nothing is guessed.
Usage: python3 build_data.py [--months 115/10,115/11] [--vod 60]
"""
import json, re, sys, argparse, datetime, time, unicodedata
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0"}
VOD_URL = "https://webpro.twse.com.tw/WebPortal/service/vodChannel/categoryMaterialList"
# 瀏覽頁（vodChannelId 都是 101）。同一代號+日期優先 148、101 的直接 mp4；都沒有才用 170。
# 170 勝出時保留任何 webLinkPath，並標 offsite（介面「有影片 (站外)」）。
# 148 MOPS法說會 https://webpro.twse.com.tw/WebPortal/vod/101/?categoryId=148
# 101 法人說明會 https://webpro.twse.com.tw/WebPortal/vod/101/?categoryId=101
# 170 公司自辦法說會(站外連結) https://webpro.twse.com.tw/WebPortal/vod/101/?categoryId=170
VOD_CATEGORIES = (148, 101, 170)
VOD_OFFSITE = 170
MOPS_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t100sb02_1"
PDF_BASE = "https://mopsov.twse.com.tw/nas/STR/"
MARKETS = {"sii": "上市", "otc": "上櫃", "rotc": "興櫃", "pub": "公開發行"}
BROKERS = ["大和國泰","中國信託","華南永昌","摩根士丹利","摩根大通","花旗環球","群益金鼎","永豐金","合作金庫","法銀巴黎","瑞士信貸",
    "凱基","台新","元大","永豐","富邦","兆豐","統一","福邦","國泰","康和","玉山","國票","中信","第一金","麥格理","大和","瑞銀","美銀",
    "亞東","台中銀","宏遠","高盛","野村","里昂","匯豐","星展","德意志","日盛","犇亞","口袋","安泰","京城","新光","彰銀","臺銀","土銀","德信","大展","致和","statestreet"]
HOST_RE = re.compile("(" + "|".join(BROKERS) + r")(?:綜合)?(證券|投顧)")

def find_host(summary):
    """Broker name quoted from the MOPS 擇要訊息 text (whitelist match); None if not stated."""
    m = HOST_RE.search(unicodedata.normalize("NFKC", summary or ""))
    return m.group(1) + m.group(2) if m else None

def https(u):
    return re.sub(r"^http://", "https://", u) if u else u

def is_direct_mp4(url):
    """只接受網址路徑以 .mp4 結尾的直接檔。YouTube、雲端硬碟、官網頁、mp3 都不是。"""
    if not url: return False
    path = url.split("?", 1)[0].split("#", 1)[0]
    return path.lower().endswith(".mp4")

def get_vod(n, category_id):
    r = requests.post(VOD_URL, data=dict(categoryId=category_id, vodChannelId=101, returnType="json",
        platform="web", pageNumber=1, pagingSize=n, order="eventDate", sortOrder="desc"), timeout=60, headers=UA)
    r.raise_for_status()
    return r.json()["result"]["materials"]["material"]

def fetch_vod_videos(n):
    """三個分類各抓一頁。回傳 (by_cid, all_failed)。分類全數連線失敗時 all_failed 為 True。
    148、101 只留直接 mp4。170 在前面都沒有時留下 webLinkPath（含 YouTube、雲端、官網頁）。"""
    by = {}
    ok = 0
    for cat in VOD_CATEGORIES:
        try:
            mats = get_vod(n, cat) or []
            ok += 1
        except Exception as e:
            print(f"VOD categoryId={cat} 失敗: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        added = 0
        for x in mats:
            code = x.get("agentUserName")
            date = (x.get("eventDate") or "")[:10]
            if not code or len(date) != 10: continue
            url = https(x.get("webLinkPath"))
            if not url: continue
            direct = is_direct_mp4(url)
            if cat != VOD_OFFSITE and not direct: continue
            cid = f"{code}-{date.replace('-', '')}"
            if cid in by: continue  # 148 → 101 的 mp4 已佔住，170 不覆蓋
            by[cid] = {"code": code, "name": x.get("agentSimpleName"), "date": date,
                       "url": url, "industry": x.get("industryName1"),
                       "kind": "mp4" if direct else "offsite", "offsite": cat == VOD_OFFSITE}
            added += 1
        print(f"VOD categoryId={cat}: 本類新增 {added}，累計 {len(by)}", flush=True)
    return by, ok == 0

def get_mops(mk, y, m):
    for _ in range(3):
        try:
            r = requests.post(MOPS_URL, data={"encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1",
                "TYPEK": mk, "year": y, "month": m}, timeout=60, headers=UA)
            r.encoding = "utf-8"
            t = BeautifulSoup(r.text, "lxml").find("table")
            if t: break
        except Exception as e:
            print("retry", mk, y, m, e, file=sys.stderr)
        time.sleep(2)
    else:
        return []
    out = []
    for tr in t.find_all("tr"):
        td = tr.find_all("td")
        if len(td) < 11 or not re.fullmatch(r"\w{4,6}", td[0].get_text(strip=True)):
            continue
        out.append((mk, td))
    return out

def roc_to_iso(s):
    m = re.fullmatch(r"(\d{2,3})/(\d{2})/(\d{2})", s.strip())
    return f"{int(m[1])+1911}-{m[2]}-{m[3]}" if m else None

def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()

def pdf_of(td):
    t = td.get_text(strip=True)
    return PDF_BASE + t if re.fullmatch(r"[\w\-]+\.pdf", t, re.I) else None

def derive(c, root=None):
    """衍生欄位。逐字稿狀態以 data/detail/<id>.json 是否存在且有逐字稿為準(不靠暫存狀態，避免平行作業互相覆蓋)。"""
    import os
    root = root or os.path.dirname(os.path.abspath(__file__))
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date().isoformat()
    c["held"] = c["date"] <= today
    v = c.get("video") or {}
    c["has_video"] = bool(v.get("url"))
    c["has_slides"] = bool(c["slides"]["zh"] or c["slides"]["en"])
    # 只有直接 mp4 才待轉。站外的 YouTube／網頁有連結，但不進轉寫佇列。
    c["transcript_status"] = "pending" if v.get("kind") == "mp4" else "none"
    c["detail"] = None
    c["summarized"] = False
    f = os.path.join(root, "data", "detail", c["id"] + ".json")
    if os.path.exists(f):
        try:
            dj = json.load(open(f, encoding="utf-8"))
            if dj.get("transcript"):
                c["transcript_status"], c["detail"] = "done", f"detail/{c['id']}.json"
                c["summarized"] = bool(dj.get("highlights"))
        except Exception:
            pass

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", default="auto", help="auto=本月與上月(民國年/月，台北時間)，或 115/10,115/11")
    ap.add_argument("--no-fetch", action="store_true", help="不連網，只依 data/detail 重算既有 conferences.json 的狀態")
    ap.add_argument("--vod", type=int, default=60)
    ap.add_argument("--out", default="data/conferences.json")
    a = ap.parse_args()

    if a.no_fetch:
        d = json.load(open(a.out))
        for c in d["conferences"]: derive(c)
        d["conferences"] = sorted(d["conferences"], key=lambda c: (c["date"], c["time"] or "", c["code"]), reverse=True)
        json.dump(d, open(a.out, "w"), ensure_ascii=False, indent=1)
        print("status refreshed", len(d["conferences"])); return
    if a.months == "auto":
        t = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
        pm = (t.replace(day=1) - datetime.timedelta(days=1))
        a.months = f"{pm.year-1911}/{pm.month},{t.year-1911}/{t.month}"
    confs = {}
    # 1) MOPS announcements
    for ym in a.months.split(","):
        y, m = ym.split("/")
        for mk in MARKETS:
            for _, td in get_mops(mk, y, m):
                code, name = td[0].get_text(strip=True), td[1].get_text(strip=True)
                date = roc_to_iso(td[2].get_text(strip=True))
                if not date: continue
                summary = clean(td[5].get_text(" "))
                host = find_host(summary)
                site = td[8].get_text(strip=True)
                c = dict(id=f"{code}-{date.replace('-', '')}", code=code, name=name, date=date,
                    time=clean(td[3].get_text()) or None, market=MARKETS[mk], industry=None,
                    host=host, host_source="mops_summary" if host else None,
                    place=clean(td[4].get_text(" ")) or None, mops_summary=summary or None,
                    slides=dict(zh=pdf_of(td[6]), en=pdf_of(td[7])),
                    video=dict(url=None, kind=None, source=None),
                    website=https(site) if site.startswith("http") else None,
                    in_mops=True, in_twse_vod=False)
                confs[c["id"]] = c
    # 2) 三個 WebPro 分類的直接 mp4。公告欄的 YouTube／官網連結不進 video。
    vod_by, vod_all_failed = fetch_vod_videos(a.vod)
    for cid, x in vod_by.items():
        c = confs.setdefault(cid, dict(id=cid, code=x["code"], name=x["name"], date=x["date"], time=None, market=None,
            industry=None, host=None, host_source=None, place=None, mops_summary=None,
            slides=dict(zh=None, en=None), video=dict(url=None, kind=None, source=None),
            website=None, in_mops=False, in_twse_vod=False))
        c["in_twse_vod"] = True
        c["industry"] = x.get("industry") or c["industry"]
        video = dict(url=x["url"], kind=x["kind"], source="twse_vod")
        if x["offsite"]: video["offsite"] = True   # 分類 170：介面顯示「有影片 (站外)」
        c["video"] = video
    # 3) 併入既有清單裡這次沒抓到的場次(較舊的、不在本月/上月/最近 N 筆影音的)，不讓它們消失
    try:
        old = {c["id"]: c for c in json.load(open(a.out))["conferences"]}
    except Exception:
        old = {}
    def _keep_saved_video(v):
        """斷線時可留的舊連結：證交所直接 mp4，或已標過的分類 170 站外連結。"""
        v = v or {}
        if not v.get("url"): return False
        if v.get("offsite") or v.get("kind") == "offsite": return True
        return v.get("source") == "twse_vod" and is_direct_mp4(v.get("url"))
    for cid, o in old.items():
        if cid not in confs:
            ov = o.get("video") or {}
            if ov.get("url") and not _keep_saved_video(ov):
                o = dict(o)
                o["video"] = dict(url=None, kind=None, source=None)
            confs[cid] = o
        else:  # 新抓到的缺的欄位用舊值補(例如舊的簡報連結)。不沿用 MOPS 公告欄的影片。
            n = confs[cid]
            for k in ("slides",):
                for kk in ("zh", "en"):
                    if not n[k].get(kk) and (o.get(k) or {}).get(kk): n[k][kk] = o[k][kk]
            if not n["video"]["url"] and vod_all_failed and _keep_saved_video(o.get("video")):
                n["video"] = o["video"]
                n["in_twse_vod"] = True
            for k in ("time", "market", "industry", "host", "host_source", "place", "mops_summary", "website"):
                if not n.get(k) and o.get(k): n[k] = o[k]
    for c in confs.values(): derive(c)
    rows = sorted(confs.values(), key=lambda c: (c["date"], c["time"] or "", c["code"]), reverse=True)
    json.dump(dict(generated_at=datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        sources=["TWSE WebPro 影音 categoryId 148/101 的 mp4，170 為站外連結", "MOPS 法人說明會公告 (t100sb02_1，行事曆與簡報，不含影片)"], conferences=rows),
        open(a.out, "w"), ensure_ascii=False, indent=1)
    print(len(rows), "conferences", rows[-1]["date"], "->", rows[0]["date"])

if __name__ == "__main__":
    main()
