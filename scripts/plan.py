#!/usr/bin/env python3
"""挑出這次要處理的法說會：最近 N 天、有可下載 mp4、尚未轉完(或轉完但還沒整理)、失敗次數未達上限。
直接 mp4，以及「站外官網／券商頁上找得到該日 mp4」的場次會納入；YouTube／Webex／GoToWebinar 等不下載。
站外網頁暫時找不到該日 mp4 時，活動日後 7 天內會再試。
按日期新到舊。輸出 GITHUB_OUTPUT: ids(JSON 陣列) / count。"""
import argparse, datetime, json, os, sys, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from videoutil import encode_url, is_direct_mp4, is_offsite_page, is_stream_url, resolve_from_page

ROOT = Path(__file__).resolve().parent.parent
TPE = datetime.timezone(datetime.timedelta(hours=8))
OFFSITE_RETRY_DAYS = 7
MAX_PAGE_RESOLVE = 30

def retry_of(cid):
    p = ROOT / "data" / "retry" / f"{cid}.json"
    try: return json.loads(p.read_text(encoding="utf-8"))
    except Exception: return {}

def save_retry(cid, data):
    p = ROOT / "data" / "retry" / f"{cid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    cur = retry_of(cid)
    cur.update(data)
    p.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")

def downloadable(url):
    """用 Range 取前 1KB，確認 200/206、是影音/二進位、總長度夠大。"""
    url = encode_url(url)
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

def media_url(c):
    """已是直接 mp4，或先前解析到的官網該日 mp4。"""
    v = c.get("video") or {}
    url = (v.get("url") or "").strip()
    if is_direct_mp4(url):
        return url
    resolved = (v.get("resolved_mp4") or "").strip()
    return resolved if is_direct_mp4(resolved) else ""

def try_resolve_page(c):
    """從官網頁找出該日 mp4。成功回傳網址；失敗回傳空字串並寫下時間以便 7 天內重試。"""
    v = c.get("video") or {}
    page = (v.get("url") or "").strip()
    if not is_offsite_page(page):
        return ""
    cached = (v.get("resolved_mp4") or "").strip()
    if is_direct_mp4(cached):
        return cached
    try:
        url = resolve_from_page(page, c.get("date") or "", require_date=True)
    except Exception as e:
        print(f"RESOLVE-FAIL {c['id']} {c['date']}: {type(e).__name__}: {str(e)[:120]}")
        save_retry(c["id"], {"page_no_mp4_at": datetime.datetime.now(TPE).date().isoformat(),
                             "page_no_mp4_err": str(e)[:200]})
        return ""
    # 寫回記憶體，稍後一併存進 conferences.json
    c.setdefault("video", {})["resolved_mp4"] = url
    print(f"RESOLVE-OK  {c['id']} {c['date']}: {url}")
    return url

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
    retry_since = (today - datetime.timedelta(days=OFFSITE_RETRY_DAYS)).isoformat()
    conf_path = ROOT / "data" / "conferences.json"
    data = json.loads(conf_path.read_text(encoding="utf-8"))
    confs = data["conferences"]
    forced = [x.strip() for x in a.ids.split(",") if x.strip()]
    cands = []
    page_resolves = 0
    resolved_dirty = False
    for c in confs:
        if forced and c["id"] not in forced: continue
        date = c["date"]
        in_window = since <= date <= min(until, today.isoformat())
        # 站外官網頁：活動日後 7 天內即使超出 --days，仍可再試能不能找到該日 mp4
        page = ((c.get("video") or {}).get("url") or "").strip()
        in_offsite_retry = (is_offsite_page(page)
                            and retry_since <= date <= min(until, today.isoformat())
                            and c.get("transcript_status") != "done")
        if not forced and not in_window and not in_offsite_retry:
            continue
        url = media_url(c)
        if not url:
            if is_stream_url(page):
                continue   # YouTube／Webex／GoTo 等不自動下載
            if is_offsite_page(page):
                if c.get("transcript_status") == "done":
                    continue
                if page_resolves >= MAX_PAGE_RESOLVE and not forced:
                    print(f"skip {c['id']}: 本輪官網解析已達 {MAX_PAGE_RESOLVE} 場，留給下一輪")
                    continue
                page_resolves += 1
                url = try_resolve_page(c)
                if url:
                    resolved_dirty = True
                else:
                    continue
            else:
                continue
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
            cands.append((c, need_asr, url))
    cands.sort(key=lambda x: (x[0]["date"], x[0]["time"] or ""), reverse=True)
    pick = []
    for c, need_asr, url in cands:
        if len(pick) >= a.max: break
        if need_asr and not a.no_check:
            ok, info = downloadable(url)
            print(f"{'OK  ' if ok else 'SKIP'} {c['id']} {c['date']} 影片檢查: {info}")
            if not ok: continue
        else:
            print(f"OK   {c['id']} {c['date']} 只需整理")
        pick.append(c["id"])
    print(f"候選 {len(cands)} 場，本次處理 {len(pick)} 場: {pick}")
    if resolved_dirty:
        conf_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print("已把解析到的官網 mp4 寫回 conferences.json（resolved_mp4）")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"ids={json.dumps(pick)}\ncount={len(pick)}\n")

if __name__ == "__main__":
    main()
