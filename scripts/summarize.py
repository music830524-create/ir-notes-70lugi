#!/usr/bin/env python3
"""用 Gemini 把「逐字稿 + 簡報」整理成 重點/財務/展望/問答，寫回 data/detail/<id>.json。
金鑰只從環境變數 GEMINI_API_KEY 讀，走 x-goog-api-key header；不進 URL、不進 log、不寫檔。
失敗(金鑰缺、API 錯、JSON 壞、結果太少)一律不覆蓋既有四區。"""
import argparse, datetime, json, os, re, sys, time, urllib.request, urllib.error
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import irtext

ROOT = Path(__file__).resolve().parent.parent
CONF = ROOT / "data" / "conferences.json"
DETAIL = ROOT / "data" / "detail"
WORK = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "ir_work"
API = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
SECTIONS = ("highlights", "financials", "outlook", "qa")
TS_RE = re.compile(r"^\d{2}:\d{2}:\d{2}$")

def log(m):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {m}", flush=True)

STATS = {"calls": 0, "retries": 0, "in_tokens": 0, "out_tokens": 0}

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
    """單次呼叫，含 429/5xx 退避。回傳 (text, finishReason)。模型不存在/配額用盡 -> ModelUnavailable。"""
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 32768,
                                 "responseMimeType": "application/json", "responseSchema": schema}}
    delays = [20, 45, 90, 150]
    for attempt in range(len(delays) + 1):
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
                body_low = msg.lower()
                if e.code == 429 and ("perday" in body_low.replace(" ", "") or "daily" in body_low):
                    raise ModelUnavailable(f"{model}: daily quota {msg}")
                if attempt < len(delays):
                    STATS["retries"] += 1
                    log(f"HTTP {msg} -> 退避 {delays[attempt]}s 後重試 ({attempt+1}/{len(delays)})")
                    time.sleep(delays[attempt]); continue
                raise ModelUnavailable(f"{model}: 重試用盡 {msg}")
            raise RuntimeError(f"API 錯誤 {msg}")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < len(delays):
                STATS["retries"] += 1
                log(f"網路錯誤 {type(e).__name__} -> 退避重試"); time.sleep(10); continue
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
2. 逐字稿是語音辨識結果，專有名詞與數字可能有誤（例如把公司名、產品名、人名聽錯）。遇到明顯與簡報不一致的詞，以簡報為準。對於「聽起來不確定、疑似辨識錯誤」的字詞：照逐字稿轉寫的字原樣寫出來，並在該詞後面緊接全形括號「（待確認音檔）」，整句其他內容照常保留，不可因此省略整句，也不可自行猜測更正。括號內只能是「待確認音檔」這五個字，不可寫成「逐字稿辨識，待確認」「逐字稿：某某…待確認」等其他格式。數字若不清楚就不要寫。
3. 使用繁體中文、台灣慣用語（例如「營收」「毛利率」「年增」「季增」「晶片」「資料中心」）；英文專有名詞保留原文。不要用簡體字或大陸用語。
4. 各區塊要求：
   - highlights：6 到 8 條最重要的重點，每條 1～2 句，需涵蓋：營收與獲利表現、毛利率變化與原因、各產品線進展、成本壓力、下一季展望。ts 填對應逐字稿時間點（格式 hh:mm:ss，必須是逐字稿中出現過的時間）。
   - financials：凡簡報或逐字稿有提到的財務數字都列，15 到 22 項，涵蓋：營收、營業毛利、毛利率、營業費用、營業利益、營益率、稅後淨利、淨利率、EPS（單季與前三季累計）、前三季毛利率、前三季營業費用年增、現金、存貨與週轉天數、權益、營業現金流、股利政策（payout ratio）。每一項的 value 必須同時寫出「數值＋年增＋季增」（若原文有），例如「69.42 億元（原值 6,942 百萬）；年增 +23.3%、季增 +10.4%」，不要把年增、季增拆成獨立項目。簡報單位為百萬元時，寫成「X.XX 億元（原值 N,NNN 百萬）」以便核對；每個數字必須能在原文逐字找到（或是百萬換算億元），不可自行計算新數字。src 填出處：簡報頁碼寫「簡報 p.N」（N 為下方標示的頁碼），逐字稿寫「逐字稿 hh:mm:ss」，兩者皆有用「；」連接。
   - outlook：管理層對下季、明年的說法、產品時程（Tape-out、量產、各產品線）、毛利率與費用指引、產能與漲價說法，8 到 14 條，每條附 ts。
   - qa：法說會 Q&A 的「每一組」提問與回覆都要列，不可合併、不可遺漏（逐字稿中每個「下一個提問」「線上提問」都是一組；通常 20 組以上）。q 與 a 各一段精簡文字（各不超過 120 字），依時間順序，ts 為該提問開始的時間點（必須是該題實際出現的時間）。主持人宣讀的流程說明不算問答。
   - 逐字稿中不通順、疑似辨識錯誤的詞（例如記憶體種類、公司名、人名），照轉寫的字原樣寫出並在後面加「（待確認音檔）」，不要自行猜測更正，也不要省略那句話。
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
    return {norm_num(m) for m in NUM_RE.findall(text)}

def number_ok(n, src):
    """n 在原文，或 n 為「億元」由原文「百萬」換算而來。"""
    if n in src: return True
    try: f = float(n)
    except ValueError: return False
    for k in (100, 10000):                      # 億→百萬(×100)、億→萬(×10000)
        v = round(f * k, 4)
        if norm_num(f"{v:.4f}") in src: return True
    return False

def check_numbers(text, src):
    bad = []
    for m in NUM_RE.findall(strip_noise(text)):
        n = norm_num(m)
        if not number_ok(n, src):
            bad.append(m)
    return bad

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
    rep = {"financials_in": 0, "financials_dropped": [], "text_unverified": []}
    out = {k: [] for k in SECTIONS}
    for f in data.get("financials") or []:
        rep["financials_in"] += 1
        label, value, s = clean_str(f.get("label")), clean_str(f.get("value")), clean_str(f.get("src"))
        if not (label and value and s):
            rep["financials_dropped"].append((label or "?", "欄位不全")); continue
        if not NUM_RE.search(strip_noise(value)):
            rep["financials_dropped"].append((label, "無數字")); continue
        bad = check_numbers(value, src)
        if bad:
            rep["financials_dropped"].append((label, "原文找不到: " + ",".join(bad))); continue
        if not re.search(r"簡報 ?p\.\d+|逐字稿 ?\d{2}:\d{2}:\d{2}", s):
            rep["financials_dropped"].append((label, "出處格式不符")); continue
        out["financials"].append({"label": label, "value": value, "src": s})
    for sec in ("highlights", "outlook"):
        for it in data.get(sec) or []:
            t = clean_str(it.get("text"))
            if not t: continue
            bad = check_numbers(t, src)
            if bad: rep["text_unverified"].append((sec, t[:30], ",".join(bad)))
            e = {"text": t}
            ts = snap_ts(it.get("ts"), ts_list)
            if ts: e["ts"] = ts
            out[sec].append(e)
    for it in data.get("qa") or []:
        q, a = clean_str(it.get("q")), clean_str(it.get("a"))
        if not (q and a): continue
        bad = check_numbers(a, src)
        if bad: rep["text_unverified"].append(("qa", q[:30], ",".join(bad)))
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
def summarize_one(c, key, models, opt):
    cid = c["id"]
    txt_p, js_p = DETAIL / f"{cid}.txt", DETAIL / f"{cid}.json"
    if not txt_p.exists() or not js_p.exists():
        log(f"{cid}: 找不到逐字稿，略過"); return False
    t0 = time.time()
    transcript = txt_p.read_text(encoding="utf-8")
    ts_list = re.findall(r"^\[(\d{2}:\d{2}:\d{2})\]", transcript, flags=re.M)
    WORK.mkdir(parents=True, exist_ok=True)
    raw = irtext.pdf_text((c.get("slides") or {}).get("zh"), WORK / f"{cid}_sum_zh.pdf")
    pages = raw.split("\f")
    slides = "\n".join(f"=== 簡報 p.{i} ===\n{p.strip()}" for i, p in enumerate(pages, 1) if p.strip())
    if not slides:
        log(f"{cid}: 簡報讀不到，僅用逐字稿（財務數字會僅依逐字稿驗證）")
    det = json.loads(js_p.read_text(encoding="utf-8"))
    hints = (det.get("pipeline") or {}).get("hotwords", "") or "（無）"
    prompt = PROMPT.format(name=c["name"], code=c["code"], date=c["date"], hints=hints, slides=slides or "（無）", transcript=transcript)
    slides_nums, tr_nums = num_set(slides), num_set(transcript)
    log(f"{cid}: slides={len(slides)}字 transcript={len(transcript)}字 prompt≈{len(prompt)}字 模型候選={models}")

    result = used = rep = None
    for model in models:
        try:
            for attempt in (1, 2):
                p = prompt if attempt == 1 else prompt + "\n\n（上一次輸出不是合法 JSON 或缺少區塊，請嚴格依 schema 只輸出 JSON。）"
                text, fin = generate(model, key, p, SCHEMA)
                try:
                    data = parse_json(text)
                except Exception as e:
                    log(f"{model}: JSON 無效 ({type(e).__name__}, finish={fin}, {len(text)}字)")
                    if attempt == 2: raise ModelUnavailable(f"{model}: JSON 兩次皆無效")
                    STATS["retries"] += 1; continue
                out, rep = validate(data, slides_nums, tr_nums, ts_list)
                if len(out["highlights"]) >= 3 and len(out["financials"]) >= 5 and len(out["qa"]) >= 3:
                    result, used = out, model; break
                log(f"{model}: 結果太少 h={len(out['highlights'])} f={len(out['financials'])} qa={len(out['qa'])}")
                if attempt == 2: raise ModelUnavailable(f"{model}: 結果過少")
                STATS["retries"] += 1
            if result: break
        except ModelUnavailable as e:
            log(f"模型不可用，換下一個: {e}")
    if not result:
        log(f"{cid}: ✗ 失敗，保留既有四區不變"); return False

    secs = round(time.time() - t0, 1)
    meta = {"model": used, "generated_at": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec="seconds"),
            "calls": STATS["calls"], "retries": STATS["retries"], "seconds": secs,
            "in_tokens": STATS["in_tokens"], "out_tokens": STATS["out_tokens"],
            "financials_in": rep["financials_in"], "financials_kept": len(result["financials"]),
            "financials_dropped": [f"{a}（{b}）" for a, b in rep["financials_dropped"]],
            "text_numbers_unverified": len(rep["text_unverified"])}
    log("REPORT " + json.dumps({**meta, "unverified_detail": rep["text_unverified"]}, ensure_ascii=False))
    if opt.dry:
        log("dry-run：不寫檔"); print("RESULT " + json.dumps(result, ensure_ascii=False)); return True
    for k in SECTIONS: det[k] = result[k]
    det["model"] = {**(det.get("model") or {}), "llm": used}
    det["sections_meta"] = meta
    det["sections_note"] = f"重點／財務／展望／問答由 AI（{used}）依簡報與逐字稿整理；財務數字已自動比對原文、找不到的已剔除。逐字稿為語音辨識，專有名詞可能有誤。"
    js_p.write_text(json.dumps(det, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"{cid}: ✓ 已寫入 h={len(result['highlights'])} f={len(result['financials'])} o={len(result['outlook'])} qa={len(result['qa'])} ({secs}s, {STATS['calls']} 次呼叫)")
    return True

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="")
    ap.add_argument("--model", default="", help="指定模型(逗號分隔=依序備援)；預設 " + ",".join(DEFAULT_MODELS))
    ap.add_argument("--dry", action="store_true", help="只跑不寫檔")
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
    models = [m.strip() for m in (opt.model or os.environ.get("GEMINI_MODEL", "") or ",".join(DEFAULT_MODELS)).split(",") if m.strip()]
    confs = {c["id"]: c for c in json.loads(CONF.read_text(encoding="utf-8"))["conferences"]}
    ok = True
    for cid in [x.strip() for x in opt.ids.split(",") if x.strip()]:
        if cid not in confs: log(f"{cid}: 不在 conferences.json"); ok = False; continue
        try:
            ok = summarize_one(confs[cid], key, models, opt) and ok
        except Exception as e:
            log(f"{cid}: ✗ {type(e).__name__}: {str(e)[:200]}，保留既有四區"); ok = False
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
