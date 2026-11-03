#!/usr/bin/env python3
"""Refresh data/conferences.json from public sources (no keys needed).
 - TWSE WebPro 法人說明會影音清單 (code / name / date / mp4)
 - MOPS 法說會公告 (time / place / 簡報 PDF / host)
Unknown fields stay null/empty -- nothing is guessed.
Usage: python3 build_data.py [--months 115/10,115/11] [--vod 60]
"""
import json, re, sys, argparse, datetime, time, unicodedata
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0"}
VOD_URL = "https://webpro.twse.com.tw/WebPortal/service/vodChannel/categoryMaterialList"
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

def get_vod(n):
    r = requests.post(VOD_URL, data=dict(categoryId=148, vodChannelId=101, returnType="json",
        platform="web", pageNumber=1, pagingSize=n, order="eventDate", sortOrder="desc"), timeout=60, headers=UA)
    r.raise_for_status()
    return r.json()["result"]["materials"]["material"]

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

def video_from_mops(td):
    txt = td.get_text(" ", strip=True)
    links = [a.get("href") for a in td.find_all("a") if a.get("href")]
    links += re.findall(r"https?://[^\s\u4e00-\u9fff]+", txt)
    for u in links:
        if "irconference" in u and re.search(r"\.mp4$", u, re.I): return https(u), "mp4"
    for u in links:
        if re.search(r"youtu", u): return https(u), "youtube"
    return None, None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", default="115/10,115/11")
    ap.add_argument("--vod", type=int, default=60)
    ap.add_argument("--out", default="data/conferences.json")
    a = ap.parse_args()

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
                vurl, vkind = video_from_mops(td[9])
                site = td[8].get_text(strip=True)
                c = dict(id=f"{code}-{date.replace('-', '')}", code=code, name=name, date=date,
                    time=clean(td[3].get_text()) or None, market=MARKETS[mk], industry=None,
                    host=host, host_source="mops_summary" if host else None,
                    place=clean(td[4].get_text(" ")) or None, mops_summary=summary or None,
                    slides=dict(zh=pdf_of(td[6]), en=pdf_of(td[7])),
                    video=dict(url=vurl, kind=vkind, source="mops" if vurl else None),
                    website=https(site) if site.startswith("http") else None,
                    in_mops=True, in_twse_vod=False)
                confs[c["id"]] = c
    # 2) TWSE VOD list
    for x in get_vod(a.vod):
        code, name = x["agentUserName"], x["agentSimpleName"]
        date = x["eventDate"][:10]
        cid = f"{code}-{date.replace('-', '')}"
        c = confs.setdefault(cid, dict(id=cid, code=code, name=name, date=date, time=None, market=None,
            industry=None, host=None, host_source=None, place=None, mops_summary=None,
            slides=dict(zh=None, en=None), video=dict(url=None, kind=None, source=None),
            website=None, in_mops=False, in_twse_vod=True))
        c["in_twse_vod"] = True
        c["industry"] = x.get("industryName1") or c["industry"]
        if x.get("webLinkPath"):
            c["video"] = dict(url=https(x["webLinkPath"]), kind="mp4", source="twse_vod")
    # 3) status fields + detail placeholders
    today = datetime.date.today().isoformat()
    for c in confs.values():
        c["held"] = c["date"] <= today
        c["has_video"] = bool(c["video"]["url"])
        c["has_slides"] = bool(c["slides"]["zh"] or c["slides"]["en"])
        # transcript_status: none | pending | processing | done   (set by later pipeline)
        c["transcript_status"] = "pending" if c["has_video"] else "none"
        c["detail"] = None  # later: "detail/<id>.json" (see SCHEMA.md)
    rows = sorted(confs.values(), key=lambda c: (c["date"], c["time"] or "", c["code"]), reverse=True)
    # keep prior transcript fields if a refresh runs after the pipeline exists
    try:
        old = {c["id"]: c for c in json.load(open(a.out))["conferences"]}
        for c in rows:
            o = old.get(c["id"])
            if o and o.get("transcript_status") in ("processing", "done"):
                c["transcript_status"], c["detail"] = o["transcript_status"], o.get("detail")
    except Exception:
        pass
    json.dump(dict(generated_at=datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        sources=["TWSE WebPro 法人說明會影音", "MOPS 法人說明會公告 (t100sb02_1)"], conferences=rows),
        open(a.out, "w"), ensure_ascii=False, indent=1)
    print(len(rows), "conferences", rows[-1]["date"], "->", rows[0]["date"])

if __name__ == "__main__":
    main()
