#!/usr/bin/env python3
"""挑出這次要處理的法說會：最近 N 天、有 mp4 影片且確實可下載、尚未轉完(或轉完但還沒整理)、失敗次數未達上限。
按日期新到舊。輸出 GITHUB_OUTPUT: ids(JSON 陣列) / count。"""
import argparse, datetime, json, os, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPE = datetime.timezone(datetime.timedelta(hours=8))

def retry_of(cid):
    p = ROOT / "data" / "retry" / f"{cid}.json"
    try: return json.loads(p.read_text(encoding="utf-8"))
    except Exception: return {}

def downloadable(url):
    """用 Range 取前 1KB，確認 200/206、是影音/二進位、總長度夠大。"""
    url = url.replace("http://", "https://", 1)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Range": "bytes=0-1023"})
        with urllib.request.urlopen(req, timeout=30) as r:
            ct = (r.headers.get("Content-Type") or "").lower()
            cr = r.headers.get("Content-Range") or ""
            total = int(cr.split("/")[-1]) if "/" in cr and cr.split("/")[-1].isdigit() else int(r.headers.get("Content-Length") or 0)
            head = r.read(16)
            ok = r.status in (200, 206) and total > 1_000_000 and (ct.startswith(("video/", "application/octet-stream")) or b"ftyp" in head)
            return ok, f"{r.status} {ct} {total/1e6:.0f}MB"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:80]}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="只看最近幾天(含今天)")
    ap.add_argument("--max", type=int, default=6, help="每次最多處理幾場")
    ap.add_argument("--max-fails", type=int, default=3)
    ap.add_argument("--ids", default="", help="手動指定(略過日期範圍與下載檢查以外的條件)")
    ap.add_argument("--skip-recent", type=int, default=0, help="略過最近幾天(留給每 2 小時排程)")
    ap.add_argument("--asr-only", action="store_true", help="只挑還沒轉逐字稿的")
    ap.add_argument("--no-check", action="store_true", help="不檢查影片可下載(只計數)")
    a = ap.parse_args()
    today = datetime.datetime.now(TPE).date()
    since = (today - datetime.timedelta(days=a.days)).isoformat()
    until = (today - datetime.timedelta(days=a.skip_recent)).isoformat()
    confs = json.loads((ROOT / "data" / "conferences.json").read_text(encoding="utf-8"))["conferences"]
    forced = [x.strip() for x in a.ids.split(",") if x.strip()]
    cands = []
    for c in confs:
        if forced and c["id"] not in forced: continue
        if not forced and not (since <= c["date"] <= min(until, today.isoformat())): continue
        if not (c.get("video") or {}).get("url") or c["video"].get("kind") != "mp4": continue
        det = ROOT / "data" / "detail" / f"{c['id']}.json"
        r = retry_of(c["id"])
        need_asr = c["transcript_status"] != "done"
        need_sum = False
        if not need_asr:
            try:
                d = json.loads(det.read_text(encoding="utf-8"))
                need_sum = not d.get("highlights")
            except Exception:
                need_asr = True
        if need_asr and r.get("asr_fails", 0) >= a.max_fails: print(f"skip {c['id']}: 轉寫已失敗 {r['asr_fails']} 次"); continue
        if need_sum and not need_asr and r.get("sum_fails", 0) >= a.max_fails: print(f"skip {c['id']}: 整理已失敗 {r['sum_fails']} 次"); continue
        if a.asr_only and not need_asr: continue
        if need_asr or need_sum:
            cands.append((c, need_asr))
    cands.sort(key=lambda x: (x[0]["date"], x[0]["time"] or ""), reverse=True)
    pick = []
    for c, need_asr in cands:
        if len(pick) >= a.max: break
        if need_asr and not a.no_check:
            ok, info = downloadable(c["video"]["url"])
            print(f"{'OK  ' if ok else 'SKIP'} {c['id']} {c['date']} 影片檢查: {info}")
            if not ok: continue
        else:
            print(f"OK   {c['id']} {c['date']} 只需整理")
        pick.append(c["id"])
    print(f"候選 {len(cands)} 場，本次處理 {len(pick)} 場: {pick}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"ids={json.dumps(pick)}\ncount={len(pick)}\n")

if __name__ == "__main__":
    main()
