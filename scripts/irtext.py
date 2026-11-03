"""法說逐字稿的文字處理：詞彙表(投影片自動抽取)、prompt/hotwords、簡繁轉換、幻覺過濾、同音偵測、TSV 術語校正。
純函式，不依賴 whisper，可離線單元測試。"""
import difflib, json, re, subprocess, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# ── 過濾 ────────────────────────────────────────────────
JUNK = re.compile(
    r"字幕|訂閱|订阅|明鏡|明镜|點贊|点赞|打賞|打赏|感謝觀看|感谢观看|謝謝收看|謝謝觀看|下集再見|請不吝|李宗盛|中文字幕志願者|"
    r"Amara|subscribe|Thanks for watching|"
    # 併入 StockAI-DB 的 JUNK_RE
    r"不吝点赞|不吝點讚|转发|轉發|字幕志愿者|字幕由|明镜与点点|明鏡與點點|独播剧场|獨播劇場|YoYo Television|"
    r"谢谢观看|请订阅|請訂閱", re.I)
LOOP = re.compile(r"(.{80,400}?)\1{3,}", re.S)

def norm(s):
    return re.sub(r"[\s、。，,.\-/（）()「」:：;；]+", "", s).lower()

def is_prompt_echo(text, prompt, hotwords=""):
    t = norm(text)
    if len(t) < 6:
        return False
    return t in norm(prompt) or t in norm(hotwords)

def collapse_loop(t):
    return LOOP.sub(r"\1", t)

def clean(segs, prompt="", hotwords=""):
    """segs: [{start,end,text}] (已轉繁體)。回傳過濾後清單。"""
    out, last, rep = [], None, 0
    for s in segs:
        t = collapse_loop(s["text"].strip())
        if not t or JUNK.search(t) or is_prompt_echo(t, prompt, hotwords):
            continue
        if t == last:
            rep += 1
            if rep >= 2:
                continue
        else:
            last, rep = t, 0
        out.append({**s, "text": t})
    return out

# ── 詞彙表 ───────────────────────────────────────────────
EN_STOP = {  # 投影片常見的一般英文字，不當專有詞
    "Consolidated","Net","Operating","Other","Income","Unaudited","Total","Margin","Cash","Trade","Gross","The","These",
    "Thank","Sheet","Share","Selling","Sales","Safe","Revenue","Rate","Questions","Property","Profit","Per","Our","Owners",
    "Investor","Inventories","Intangible","Harbor","Flow","Expenses","Exchange","Effect","Earnings","Discussions","Conference",
    "Balance","Average","Appendix","Administration","Admin","Update","Proprietary","Confidential","Corp","All","Technology",
    "Statement","NT","USD","NTD","Rights","Reserved","Ltd","Inc","Co","And","For","With","Quarter","Year","Q","Million","Billion","Expense",
}
GENERIC_TERMS_ZH = ["營收", "毛利率", "營業利益", "稅後淨利", "每股盈餘", "季增", "年增", "展望"]
GENERIC_TERMS_EN = ["Safe Harbor Statement", "EPS", "YoY", "QoQ"]

def pdf_text(url, dst):
    if not url:
        return ""
    try:
        req = urllib.request.Request(url.replace("http://", "https://", 1), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            Path(dst).write_bytes(r.read())
        return subprocess.run(["pdftotext", "-layout", str(dst), "-"], capture_output=True, text=True, timeout=120).stdout
    except Exception as e:
        print("slides fetch failed:", e)
        return ""

def english_terms(text):
    """從投影片文字抽英文專有詞：全大寫縮寫、含數字/連字號的型號、標題頁品牌字。"""
    found = []
    for m in re.finditer(r"\b[A-Za-z][A-Za-z0-9+\-]{1,}\b", text):
        w = m.group(0)
        if w in EN_STOP or w.lower() in {x.lower() for x in EN_STOP}:
            continue
        if (w.isupper() and 2 <= len(w) <= 8) or re.search(r"\d", w) or re.search(r"[a-z][A-Z]", w):
            if w.upper() in {"AIROHA"} :
                continue
            found.append(w)
    seen, out = set(), []
    for w in found:
        if w not in seen:
            seen.add(w); out.append(w)
    return out

def brand_from_title(zh_text, en_text):
    """標題頁(前 12 行)找公司全名與英文品牌。"""
    head_zh = "\n".join(zh_text.splitlines()[:12])
    head_en = "\n".join(en_text.splitlines()[:12])
    full = re.search(r"([\u4e00-\u9fff]{2,10}(?:科技|股份有限公司|電子|半導體|集團|控股|光電|材料|電腦|通訊))", head_zh)
    brand = re.search(r"\b([A-Z][a-z]{3,})\b(?=\s+(?:Technology|Corp|Semiconductor|Electronics|Inc))", head_en)
    return (full.group(1) if full else ""), (brand.group(1) if brand else "")

def speakers_from_slides(text):
    """投影片若有「發言人/協理/副總 XXX」才抽得到；沒有就回空。"""
    names = []
    for m in re.finditer(r"(?:發言人|協理|副總經理|副總|總經理|財務長|處長)\s*[:：]?\s*([\u4e00-\u9fff]{2,4})", text):
        if m.group(1) not in {"暨發言", "發言人"}:
            names.append(m.group(1))
    return names

def load_company(code):
    p = DATA / "company" / f"{code}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

def build_glossary(conf, zh_text, en_text, industry_terms=None, company=None):
    short = conf["name"]
    full, brand = brand_from_title(zh_text, en_text)
    names = list(dict.fromkeys(speakers_from_slides(zh_text + "\n" + en_text) + (company or {}).get("names", [])))
    terms = []
    terms += (company or {}).get("terms", [])
    terms += industry_terms or []
    terms += english_terms(en_text + "\n" + zh_text)
    terms += GENERIC_TERMS_EN
    if brand:
        terms.insert(0, brand)
    terms = list(dict.fromkeys(terms))
    return {"short": short, "full": full or short, "brand": brand, "names": names, "terms": terms}

def build_prompt_hotwords(g, prompt_max=110, hot_max=130):
    """prompt：只用「、」「。」，無括號/股票代號，不以「以下是」開頭；hotwords：空白分隔。"""
    items = [g["short"], g["brand"], *g["names"]]
    items += [t for t in g["terms"] + GENERIC_TERMS_ZH if not re.fullmatch(r"Q\d", t)]
    items = [x for x in dict.fromkeys(items) if x and x != g["full"]]
    prompt = f"{g['full']}法說會。"
    body = ""
    for t in items:
        add = ("、" if body else "") + t
        if len(prompt) + len(body) + len(add) + 1 > prompt_max:
            continue
        body += add
    prompt += body + "。"
    hw = ""
    for t in dict.fromkeys(x for x in [g["full"], *items] if x and not re.fullmatch(r"Q\d", x)):
        if len(hw) + len(t) + 1 > hot_max:
            continue
        hw += (" " if hw else "") + t
    return prompt, hw

# ── 同音偵測 ─────────────────────────────────────────────
_FUZZY = [("zh", "z"), ("ch", "c"), ("sh", "s"), ("ing", "in"), ("eng", "en"), ("ang", "an")]
_CJK = re.compile(r"[\u4e00-\u9fff]")
FOLLOW = ("科技", "公司", "持續", "的", "也", "將", "已", "預期", "目前", "在", "與", "和", "是", "今年")
TITLE_BEFORE = ("副總", "發言人", "協理", "經理", "財務長", "處長", "總經理", "董事長", "執行長", "長官")

def _fz(s):
    for a, b in _FUZZY:
        s = s.replace(a, b)
    return s

def _syl(ch):
    from pypinyin import lazy_pinyin, Style
    return lazy_pinyin(ch, style=Style.NORMAL)[0]

def _fin(ch):
    from pypinyin import lazy_pinyin, Style
    return _fz(lazy_pinyin(ch, style=Style.FINALS)[0])

def fix_cn_name(text, name, stats=None, allow_near=True):
    """把與 name 同音/近音的字串換成 name。name 為中文 2–4 字。"""
    n = len(name)
    if n < 2 or not all(_CJK.match(c) for c in name):
        return text
    key = [_fz(_syl(c)) for c in name]
    out, i = [], 0
    while i < len(text):
        w = text[i:i + n]
        rep = False
        if len(w) == n and w != name and all(_CJK.match(c) for c in w):
            wk = [_fz(_syl(c)) for c in w]
            before, after = text[max(0, i - 4):i], text[i + n:i + n + 3]
            if n == 2:
                same = wk == key
                near = w[0] == name[0] and _fin(w[1]) == _fin(name[1])
                rep = (same or near) and after.startswith(FOLLOW)
            else:
                mism = sum(a != b for a, b in zip(wk, key))
                if mism == 0:
                    rep = True
                elif mism == 1 and n >= 3 and allow_near:
                    rep = any(t in before for t in TITLE_BEFORE) or after.startswith("先生")
        if rep:
            if stats is not None: stats[f"{w}->{name}"] = stats.get(f"{w}->{name}", 0) + 1
            out.append(name); i += n
        else:
            out.append(text[i]); i += 1
    return "".join(out)

def fix_latin_name(text, name, stats=None, thr=0.8):
    def sub(m):
        w = m.group(0)
        if w.lower() != name.lower() and difflib.SequenceMatcher(None, w.lower(), name.lower()).ratio() >= thr:
            if stats is not None: stats[f"{w}->{name}"] = stats.get(f"{w}->{name}", 0) + 1
            return name
        return w
    return re.sub(r"\b[A-Za-z]{%d,}\b" % max(4, len(name) - 2), sub, text)

def fix_homophones(text, g, stats=None):
    # 公司名：只做同音(+2字名的近音+後綴語境)；人名：另允許「職稱語境下 3 字中 2 字相符」
    text = fix_cn_name(text, g["short"], stats, allow_near=False) if re.fullmatch(r"[\u4e00-\u9fff]+", g["short"]) else text
    for nm in g["names"]:
        if re.fullmatch(r"[A-Za-z]+", nm):
            text = fix_latin_name(text, nm, stats)
        elif re.fullmatch(r"[\u4e00-\u9fff]+", nm):
            text = fix_cn_name(text, nm, stats, allow_near=True)
    return text

# ── TSV 術語校正 ──────────────────────────────────────────
_NUM = re.compile(r"\d+(?:\.\d+)?")

def load_termfix(path):
    rules = []
    p = Path(path)
    if not p.exists():
        return rules
    for ln in p.read_text(encoding="utf-8-sig").splitlines():
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        f = ln.split("\t")
        if len(f) < 2:
            continue
        mode = (f[2].strip().lower() if len(f) > 2 and f[2].strip() else "exact")
        rules.append((f[0], f[1], mode))
    return rules

def apply_termfix(text, rules, stats=None):
    for wrong, right, mode in rules:
        waive = mode.endswith("!")  # 模式後加 ! = 此規則本來就會新增型號數字(如 PAM4)，豁免數字檢查
        mode = mode.rstrip("!")
        if mode == "re":
            new = re.sub(wrong, right, text)
        elif mode == "ci":
            new = re.sub(re.escape(wrong), lambda m: right, text, flags=re.I)
        else:
            new = text.replace(wrong, right)
        if new == text:
            continue
        if not waive and _NUM.findall(new) != _NUM.findall(text):  # 數字被動到 => 跳過這條
            if stats is not None:
                stats["skipped_digit_change"] = stats.get("skipped_digit_change", 0) + 1
                stats.setdefault("skipped_examples", []).append(f"{wrong} | {text[:40]}")
            continue
        if stats is not None: stats[wrong] = stats.get(wrong, 0) + 1
        text = new
    return text

def to_tw(cc, t):
    return cc.convert(t).replace("臺", "台")

def postfix(segs, g, global_rules, company_rules, homophone=True, stats=None):
    out = []
    for s in segs:
        t = s["text"]
        if homophone:
            t = fix_homophones(t, g, stats)
        t = apply_termfix(t, global_rules, stats)
        t = apply_termfix(t, company_rules, stats)
        out.append({**s, "text": t})
    return out
