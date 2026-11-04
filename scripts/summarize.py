#!/usr/bin/env python3
"""用 Gemini 把「逐字稿 + 簡報」整理成 重點/財務/展望/問答，寫回 data/detail/<id>.json。
金鑰只從環境變數 GEMINI_API_KEY 讀，走 x-goog-api-key header；不進 URL、不進 log、不寫檔。
每場只打一個 lite、一次 generateContent（429/5xx 最多再試一次）。已有重點則跳過，除非 --force。
JSON 壞掉就不覆蓋既有四區；項目偏少就留下對得上的列，不整份重送。"""
import argparse, datetime, json, os, re, sys, time, urllib.request, urllib.error
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import irtext

ROOT = Path(__file__).resolve().parent.parent
CONF = ROOT / "data" / "conferences.json"
DETAIL = ROOT / "data" / "detail"
WORK = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "ir_work"
API = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-3.5-flash-lite"
SECTIONS = ("highlights", "financials", "outlook", "qa")
TS_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")
FACTORS = (10, 100, 1000, 10000, 100000)   # 萬↔千元、億↔百萬、百萬↔千元、億↔萬、億↔千元

def log(m):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {m}", flush=True)

STATS = {"calls": 0, "retries": 0, "in_tokens": 0, "out_tokens": 0}

def reset_stats():
    for k in STATS: STATS[k] = 0

# ───────── Gemini REST ─────────
class ModelUnavailable(Exception):
    pass

def api(path, key, body=None, timeout=300):
    req = urllib.request.Request(f"{API}/{path}", method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def err_text(e):
    try:
        j = json.loads(e.read().decode())
        return f"{e.code} {j.get('error', {}).get('status', '')} {str(j.get('error', {}).get('message', ''))[:200]}"
    except Exception:
        return f"{e.code}"

def generate(model, key, prompt, schema):
    """一次 generateContent。429 或 5xx 只再試一次，不換模型。回傳 (text, finishReason)。"""
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 32768,
                                 "responseMimeType": "application/json", "responseSchema": schema}}
    for attempt in (1, 2):
        STATS["calls"] += 1
        try:
            j = api(f"models/{model}:generateContent", key, body)
            u = j.get("usageMetadata", {})
            STATS["in_tokens"] += u.get("promptTokenCount", 0)
            STATS["out_tokens"] += u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
            c = (j.get("candidates") or [{}])[0]
            text = "".join(p.get("text", "") for p in c.get("content", {}).get("parts", []))
            return text, c.get("finishReason")
        except urllib.error.HTTPError as e:
            msg = err_text(e)
            if e.code in (404,) or (e.code == 400 and "model" in msg.lower() and "not" in msg.lower()):
                raise ModelUnavailable(f"{model}: {msg}")
            if e.code in (429, 500, 503, 504):
                if attempt == 1:
                    STATS["retries"] += 1
                    log(f"HTTP {msg} -> 等 20s 後再試一次")
                    time.sleep(20); continue
                raise ModelUnavailable(f"{model}: 重試一次後仍失敗 {msg}")
            raise RuntimeError(f"API 錯誤 {msg}")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == 1:
                STATS["retries"] += 1
                log(f"網路錯誤 {type(e).__name__} -> 等 10s 後再試一次"); time.sleep(10); continue
            raise RuntimeError("網路錯誤")

SCHEMA = {"type": "OBJECT", "required": list(SECTIONS), "properties": {
    "highlights": {"type": "ARRAY", "items": {"type": "OBJECT", "required": ["text", "ts"],
                   "properties": {"text": {"type": "STRING"}, "ts": {"type": "STRING"}}}},
    "financials": {"type": "ARRAY", "items": {"type": "OBJECT", "required": ["label", "value", "src"],
                   "properties": {"label": {"type": "STRING"}, "value": {"type": "STRING"}, "src": {"type": "STRING"}}}},
    "outlook": {"type": "ARRAY", "items": {"type": "OBJECT", "required": ["text", "ts"],
                "properties": {"text": {"type": "STRING"}, "ts": {"type": "STRING"}}}},
    "qa": {"type": "ARRAY", "items": {"type": "OBJECT", "required": ["q", "a", "ts"],
           "properties": {"q": {"type": "STRING"}, "a": {"type": "STRING"}, "ts": {"type": "STRING"}}}},
}}

PROMPT = """你是台灣股市研究助理。下面是「{name}（{code}）{date} 法人說明會」的【簡報文字】與【逐字稿】。請只根據這兩份原文，整理成四個區塊，輸出 JSON。

嚴格規則：
1. 只能使用原文出現的資訊，不可推測、不可補充外部知識、不可編造數字。原文沒寫就不要寫。
2. 專有名詞以簡報為準：簡報有對應詞就直接寫簡報的詞，不要加「（待確認音檔）」。簡報與逐字稿都無法確定的詞不要放進 highlights；可留在 qa，並在該詞後加「（待確認音檔）」（括號內只能這五個字）。數字不清楚就不要寫。
3. 使用繁體中文、台灣慣用語（例如「營收」「毛利率」「年增」「季增」「晶片」「資料中心」）；英文專有名詞保留原文。不要用簡體字或大陸用語。
4. 各區塊要求：
   - highlights：最重要的重點，每條 1～2 句，原文有則涵蓋營收與獲利、毛利率、產品線、成本、下一季展望。不可出現「待確認音檔」。ts 為逐字稿中出現過的時間（hh:mm:ss）。
   - financials：列出原文有提到的財務數字，有幾項寫幾項，不要為了湊數編造。value 寫數值，以及原文有的年增、季增，例如「69.42 億元（原值 6,942 百萬）；年增 +23.3%、季增 +10.4%」。同一數字可在億、百萬、萬、千元之間換算，不可算出原文沒有的新比率。src：簡報寫「簡報 p.N」，逐字稿寫「逐字稿 hh:mm:ss」，兩者皆有用「；」連接。
   - outlook：管理層對下季與明年的說法、產品時程、毛利率與費用指引、產能與漲價。原文有幾條寫幾條，每條附 ts。
   - qa：提問與回覆依時間順序，不可合併。q 與 a 各不超過 120 字。ts 為該提問開始的時間。主持人流程說明不算問答。
5. 不要輸出投資建議或評價。

【術語提示（來自簡報）】
{hints}

【簡報文字】
{slides}

【逐字稿】
{transcript}
"""

# ───────── 驗證 ─────────
NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
def norm_num(s):
    s = s.replace(",", "")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s

def strip_noise(t):
    t = re.sub(r"\b\d?[1-4]?Q\d{2}\b", " ", t)       # 3Q26 / 2Q26 / Q4
    t = re.sub(r"\bp\.\s*\d+", " ", t)                 # 簡報 p.12
    t = re.sub(r"\d{2}:\d{2}:\d{2}", " ", t)           # 時間戳
    t = re.sub(r"\b(?:19|20)\d{2}\b", " ", t)          # 年份
    return t

def num_set(text):
    """頁碼與時間戳不拿來對數字，避免 42 只因出現在 00:00:42 或 p.42 就被當成原文。"""
    return {norm_num(m) for m in NUM_RE.findall(strip_noise(text or ""))}

def _as_float(n):
    try: return float(n)
    except (TypeError, ValueError): return None

def _near(a, b):
    return abs(a - b) <= 0.05

def number_ok(n, src):
    """直接出現、同單位差 0.05 以內，或億／百萬／萬／千元的一次換算（×/÷10、100、1000、10000）。"""
    if n in src: return True
    f = _as_float(n)
    if f is None: return False
    for s in src:
        sf = _as_float(s)
        if sf is None: continue
        if _near(f, sf): return True
        for k in FACTORS:
            if _near(f, sf * k) or _near(f, sf / k): return True
    return False

def _unit_restatement(n, matched):
    """n 是列裡另一個已對上的數字，換成億／百萬／萬／千元。"""
    f = _as_float(n)
    if f is None: return False
    for m in matched:
        mf = _as_float(m)
        if mf is None: continue
        for k in FACTORS:
            if _near(f, mf * k) or _near(f, mf / k): return True
    return False

def figures_ok(text, src):
    """至少一個數字對上原文；其餘必須是同一數字的單位改寫。沒有數字視為通過。"""
    nums = [norm_num(m) for m in NUM_RE.findall(strip_noise(text))]
    if not nums: return True, []
    good = [n for n in nums if number_ok(n, src)]
    if not good: return False, nums
    bad = [n for n in nums if n not in good and not _unit_restatement(n, good)]
    return not bad, bad

def snap_ts(ts, ts_list):
    if not isinstance(ts, str) or not TS_RE.match(ts) or not ts_list: return None
    if ts in ts_list: return ts
    sec = lambda x: int(x[:2]) * 3600 + int(x[3:5]) * 60 + int(x[6:])
    best = min(ts_list, key=lambda x: abs(sec(x) - sec(ts)))
    return best if abs(sec(best) - sec(ts)) <= 10 else None


MARK = "（待確認音檔）"
_MARK_PATS = [
    re.compile(r"[（(][^（）()]*待確認[^（）()]*[）)]"),          # （逐字稿辨識，待確認）(待確認)（名稱待確認）
    re.compile(r"[（(]逐字稿[：:][^（）()]*(?:" + MARK + r")[^（）()]*[）)]"),  # （逐字稿：Service（待確認音檔））
]
def norm_mark(t):
    """把各種「待確認」寫法統一成「（待確認音檔）」。"""
    if not isinstance(t, str) or "待確認" not in t: return t
    for _ in range(4):
        n = _MARK_PATS[0].sub(MARK, t)
        n = _MARK_PATS[1].sub(MARK, n)
        if n == t: break
        t = n
    t = re.sub(r"[（(]([^（）()]{0,24})" + MARK + r"(?![^（）()]*[）)])", r"\1" + MARK, t)   # 殘留未閉合的外層括號：（X（待確認音檔）
    t = re.sub(r"(?<![（(])待確認(?!音檔)", MARK, t)       # 括號外的裸「待確認」
    t = re.sub(r"(?:" + MARK + r"\s*){2,}", MARK, t)       # 連續重複
    return t

def clean_str(x):
    return norm_mark(re.sub(r"\s+", " ", x).strip()) if isinstance(x, str) else ""

def validate(data, slides_nums, tr_nums, ts_list):
    src = slides_nums | tr_nums
    rep = {"financials_in": 0, "financials_dropped": [], "highlights_dropped": [], "text_unverified": []}
    out = {k: [] for k in SECTIONS}
    for f in data.get("financials") or []:
        rep["financials_in"] += 1
        label, value, s = clean_str(f.get("label")), clean_str(f.get("value")), clean_str(f.get("src"))
        if not (label and value and s):
            rep["financials_dropped"].append((label or "?", "欄位不全")); continue
        if not NUM_RE.search(strip_noise(value)):
            rep["financials_dropped"].append((label, "無數字")); continue
        ok, bad = figures_ok(value, src)
        if not ok:
            rep["financials_dropped"].append((label, "原文找不到: " + ",".join(bad))); continue
        if not re.search(r"簡報 ?p\.\d+|逐字稿 ?\d{2}:\d{2}:\d{2}", s):
            rep["financials_dropped"].append((label, "出處格式不符")); continue
        out["financials"].append({"label": label, "value": value, "src": s})
    for it in data.get("highlights") or []:
        t = clean_str(it.get("text"))
        if not t: continue
        if MARK in t:
            rep["highlights_dropped"].append((t[:30], "待確認音檔不放重點")); continue
        ok, bad = figures_ok(t, src)
        if not ok:
            rep["highlights_dropped"].append((t[:30], "原文找不到: " + ",".join(bad))); continue
        e = {"text": t}
        ts = snap_ts(it.get("ts"), ts_list)
        if ts: e["ts"] = ts
        out["highlights"].append(e)
    for it in data.get("outlook") or []:
        t = clean_str(it.get("text"))
        if not t: continue
        ok, bad = figures_ok(t, src)
        if not ok: rep["text_unverified"].append(("outlook", t[:30], ",".join(bad)))
        e = {"text": t}
        ts = snap_ts(it.get("ts"), ts_list)
        if ts: e["ts"] = ts
        out["outlook"].append(e)
    for it in data.get("qa") or []:
        q, a = clean_str(it.get("q")), clean_str(it.get("a"))
        if not (q and a): continue
        ok, bad = figures_ok(a, src)
        if not ok: rep["text_unverified"].append(("qa", q[:30], ",".join(bad)))
        e = {"q": q, "a": a}
        ts = snap_ts(it.get("ts"), ts_list)
        if ts: e["ts"] = ts
        out["qa"].append(e)
    out["qa"].sort(key=lambda x: x.get("ts", "99:99:99"))
    return out, rep

def parse_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    d = json.loads(text)
    if not isinstance(d, dict) or not all(isinstance(d.get(k), list) for k in SECTIONS):
        raise ValueError("缺少區塊")
    return d

# ───────── 主流程 ─────────
def summarize_one(c, key, model, opt):
    reset_stats()
    cid = c["id"]
    txt_p, js_p = DETAIL / f"{cid}.txt", DETAIL / f"{cid}.json"
    if not txt_p.exists() or not js_p.exists():
        log(f"{cid}: 找不到逐字稿，略過"); return False
    det = json.loads(js_p.read_text(encoding="utf-8"))
    if det.get("highlights") and not opt.force:
        log(f"{cid}: 已有重點，略過（要重跑請加 --force）"); return True
    t0 = time.time()
    transcript = txt_p.read_text(encoding="utf-8")
    ts_list = re.findall(r"^\[(\d{2}:\d{2}:\d{2})\]", transcript, flags=re.M)
    WORK.mkdir(parents=True, exist_ok=True)
    raw = irtext.pdf_text((c.get("slides") or {}).get("zh"), WORK / f"{cid}_sum_zh.pdf")
    pages = raw.split("\f")
    slides = "\n".join(f"=== 簡報 p.{i} ===\n{p.strip()}" for i, p in enumerate(pages, 1) if p.strip())
    if not slides:
        log(f"{cid}: 簡報讀不到，僅用逐字稿（財務數字會僅依逐字稿驗證）")
    hints = (det.get("pipeline") or {}).get("hotwords", "") or "（無）"
    prompt = PROMPT.format(name=c["name"], code=c["code"], date=c["date"], hints=hints, slides=slides or "（無）", transcript=transcript)
    slides_nums, tr_nums = num_set(slides), num_set(transcript)
    log(f"{cid}: slides={len(slides)}字 transcript={len(transcript)}字 prompt≈{len(prompt)}字 模型={model}")

    try:
        text, fin = generate(model, key, prompt, SCHEMA)
    except ModelUnavailable as e:
        log(f"{cid}: ✗ {e}，保留既有四區"); return False
    try:
        data = parse_json(text)
    except Exception as e:
        log(f"{model}: JSON 無效 ({type(e).__name__}, finish={fin}, {len(text)}字)，不重送")
        return False
    result, rep = validate(data, slides_nums, tr_nums, ts_list)
    shortfall = []
    if len(result["highlights"]) < 3: shortfall.append(f"重點{len(result['highlights'])}")
    if len(result["financials"]) < 5: shortfall.append(f"財務{len(result['financials'])}")
    if len(result["qa"]) < 3: shortfall.append(f"問答{len(result['qa'])}")
    if shortfall:
        log(f"{model}: 項目偏少 {' '.join(shortfall)}，留下對得上的列，不重送")

    secs = round(time.time() - t0, 1)
    meta = {"model": model, "generated_at": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec="seconds"),
            "calls": STATS["calls"], "retries": STATS["retries"], "seconds": secs,
            "in_tokens": STATS["in_tokens"], "out_tokens": STATS["out_tokens"],
            "financials_in": rep["financials_in"], "financials_kept": len(result["financials"]),
            "financials_dropped": [f"{a}（{b}）" for a, b in rep["financials_dropped"]],
            "highlights_dropped": [f"{a}（{b}）" for a, b in rep["highlights_dropped"]],
            "text_numbers_unverified": len(rep["text_unverified"]),
            "shortfall": shortfall}
    log("REPORT " + json.dumps({**meta, "unverified_detail": rep["text_unverified"]}, ensure_ascii=False))
    if opt.dry:
        log("dry-run：不寫檔"); print("RESULT " + json.dumps(result, ensure_ascii=False)); return True
    for k in SECTIONS: det[k] = result[k]
    det["model"] = {**(det.get("model") or {}), "llm": model}
    det["sections_meta"] = meta
    note = f"重點／財務／展望／問答由 AI（{model}）依簡報與逐字稿整理；財務與重點數字已比對原文（含億／百萬／萬／千元換算），對不上的已剔除。逐字稿為語音辨識，專有名詞可能有誤。"
    if shortfall: note += " 這次列出的項目偏少，未再重送。"
    det["sections_note"] = note
    js_p.write_text(json.dumps(det, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"{cid}: ✓ 已寫入 h={len(result['highlights'])} f={len(result['financials'])} o={len(result['outlook'])} qa={len(result['qa'])} ({secs}s, {STATS['calls']} 次呼叫)")
    return True

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="")
    ap.add_argument("--model", default="", help="只使用這一個模型；預設 " + DEFAULT_MODEL + "。逗號清單只取第一個，不備援")
    ap.add_argument("--dry", action="store_true", help="只跑不寫檔")
    ap.add_argument("--force", action="store_true", help="已有重點也重跑（預設略過；GitHub workflow 不要加）")
    ap.add_argument("--list-models", action="store_true", help="列出可用的 flash 模型名稱後離開")
    opt = ap.parse_args()
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        log("沒有 GEMINI_API_KEY，略過整理（既有四區不變）"); return 0
    if opt.list_models:
        j = api("models?pageSize=200", key)
        for m in j.get("models", []):
            if "flash" in m["name"] and "generateContent" in m.get("supportedGenerationMethods", []):
                print(m["name"])
        return 0
    picked = [m.strip() for m in (opt.model or "").split(",") if m.strip()]
    model = picked[0] if picked else DEFAULT_MODEL
    if len(picked) > 1:
        log(f"忽略其餘模型，只使用 {model}")
    confs = {c["id"]: c for c in json.loads(CONF.read_text(encoding="utf-8"))["conferences"]}
    ok = True
    for cid in [x.strip() for x in opt.ids.split(",") if x.strip()]:
        if cid not in confs: log(f"{cid}: 不在 conferences.json"); ok = False; continue
        try:
            ok = summarize_one(confs[cid], key, model, opt) and ok
        except Exception as e:
            log(f"{cid}: ✗ {type(e).__name__}: {str(e)[:200]}，保留既有四區"); ok = False
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
