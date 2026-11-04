#!/usr/bin/env python3
"""從證交所/公開資訊觀測站公開資料，建立各公司「歷次法說」的公開連結(約 2 年)：data/history/<代號>.json。
只放日期、簡報 PDF 連結、影片連結(證交所 mp4 或公司公告的 YouTube)，不下載、不轉檔舊影片。
用法: python3 scripts/build_history.py [--months 25] [--vod 6000]"""
import argparse, datetime, json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import build_data as b

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "history"

def month_list(n):
    t = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    y, m = t.year, t.month
    out = []
    for _ in range(n):
        out.append((str(y - 1911), str(m)))
        m -= 1
        if m == 0: y, m = y - 1, 12
    return out

def load_existing(rows):
    if not OUT.exists(): return
    for f in OUT.glob("*.json"):
        try: L = json.loads(f.read_text(encoding="utf-8"))
        except Exception: continue
        for e in L:
            rows[(f.stem, e["d"])] = e

def save(rows):
    by = {}
    for (code, date), e in rows.items():
        by.setdefault(code, []).append({k: v for k, v in e.items() if v})
    OUT.mkdir(parents=True, exist_ok=True)
    for code, L in by.items():
        L.sort(key=lambda x: x["d"], reverse=True)
        (OUT / f"{code}.json").write_text(json.dumps(L, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return len(by)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=25)
    ap.add_argument("--vod", type=int, default=6000)
    ap.add_argument("--budget", type=float, default=40, help="MOPS 最多花幾分鐘(來源會擋連線，抓不到的月份下次排程補)")
    a = ap.parse_args()
    rows = {}
    load_existing(rows)
    try:                                              # 1) 證交所影音清單：穩定，涵蓋約 2 年的影片連結
        for x in b.get_vod(a.vod):
            code, date = x["agentUserName"], x["eventDate"][:10]
            e = rows.setdefault((code, date), {"name": x["agentSimpleName"], "d": date, "t": None})
            if x.get("webLinkPath"): e["v"], e["k"] = b.https(x["webLinkPath"]), "mp4"
        print(f"VOD 後共 {len(rows)} 筆", flush=True)
    except Exception as ex:
        print("VOD 失敗:", ex, file=sys.stderr)
    save(rows)
    t0 = time.time(); fails = 0                       # 2) 公開資訊觀測站(簡報 PDF)：盡力而為、限時、連線被擋就停
    for y, m in month_list(a.months):
        if time.time() - t0 > a.budget * 60 or fails >= 6:
            print(f"MOPS 停止於 {y}/{m}（逾時或連續失敗 {fails}），其餘月份留待下次", flush=True); break
        n = 0
        for mk in b.MARKETS:
            try: got = b.get_mops(mk, y, m)
            except Exception as ex:
                got = []; fails += 1; print(f"  {mk} {y}/{m} 失敗: {str(ex)[:80]}", flush=True); time.sleep(5)
            if not got and mk in ("sii", "otc"): fails += 1
            time.sleep(1.5)
            for _, td in got:
                code = td[0].get_text(strip=True)
                date = b.roc_to_iso(td[2].get_text(strip=True))
                if not date: continue
                vurl, vkind = b.video_from_mops(td[9])
                e = rows.setdefault((code, date), {"name": td[1].get_text(strip=True), "d": date, "t": None})
                e["t"] = e.get("t") or b.clean(td[3].get_text()) or None
                e["z"] = e.get("z") or b.pdf_of(td[6]); e["e"] = e.get("e") or b.pdf_of(td[7])
                if vurl and not e.get("v"): e["v"], e["k"] = vurl, vkind
                n += 1
        if n: fails = 0
        print(f"{y}/{m}: {n}", flush=True)
        save(rows)
    print(f"{save(rows)} 家公司、{len(rows)} 筆")

if __name__ == "__main__":
    main()
