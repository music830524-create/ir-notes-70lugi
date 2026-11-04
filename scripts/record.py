#!/usr/bin/env python3
"""記錄單場處理結果到 data/retry/<id>.json（失敗次數，plan.py 用來避免無限重試）。
成功(有逐字稿且有整理)就刪除紀錄；沒逐字稿 -> asr_fails+1；有逐字稿但沒整理 -> sum_fails+1。"""
import datetime, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
cid = sys.argv[1]
det = ROOT / "data" / "detail" / f"{cid}.json"
rp = ROOT / "data" / "retry" / f"{cid}.json"
rp.parent.mkdir(parents=True, exist_ok=True)
try: d = json.loads(det.read_text(encoding="utf-8"))
except Exception: d = {}
r = {}
if rp.exists():
    try: r = json.loads(rp.read_text(encoding="utf-8"))
    except Exception: r = {}
now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec="seconds")
if not d.get("transcript"):
    r["asr_fails"] = r.get("asr_fails", 0) + 1; r["last_fail"] = now; r["stage"] = "asr"
elif not d.get("highlights"):
    r["sum_fails"] = r.get("sum_fails", 0) + 1; r["last_fail"] = now; r["stage"] = "summarize"
else:
    rp.unlink(missing_ok=True); print(f"{cid}: OK，清除失敗紀錄"); sys.exit(0)
rp.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"{cid}: 失敗已記錄 {r}")
