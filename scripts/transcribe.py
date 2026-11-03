#!/usr/bin/env python3
"""下載法說影片 -> 16kHz 單聲道音檔 -> faster-whisper(CPU, int8) -> 繁體 -> data/detail/<id>.json + .txt"""
import argparse, json, os, re, subprocess, sys, time, datetime, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import irtext

ROOT = Path(__file__).resolve().parent.parent
CONF = ROOT / "data" / "conferences.json"
DETAIL = ROOT / "data" / "detail"
WORK = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "ir_work"

def log(msg):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}", flush=True)

def hms(sec):
    sec = int(sec)
    return f"{sec//3600:02d}:{sec%3600//60:02d}:{sec%60:02d}"

def load():
    return json.loads(CONF.read_text(encoding="utf-8"))

def set_status(cid, status, detail=None):
    d = load()
    for c in d["conferences"]:
        if c["id"] == cid:
            c["transcript_status"] = status
            if detail is not None:
                c["detail"] = detail
    CONF.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")

def download(url, dst, tries=5):
    url = url.replace("http://", "https://", 1)
    for i in range(1, tries + 1):
        try:
            log(f"download try {i}: {url}")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r, open(dst, "wb") as f:
                while True:
                    b = r.read(1 << 20)
                    if not b: break
                    f.write(b)
            if dst.stat().st_size > 100000:
                return
        except Exception as e:
            log(f"download failed: {e}")
        time.sleep(5 * i)
    raise RuntimeError("download failed")

def run_one(c, model_name, model_obj, cc, opt):
    cid = c["id"]
    WORK.mkdir(parents=True, exist_ok=True)
    mp4, wav = WORK / f"{cid}.mp4", WORK / f"{cid}.wav"
    T = {}
    t0 = time.time()
    if not opt.tag: set_status(cid, "processing")
    download(c["video"]["url"], mp4)
    T["download_s"] = round(time.time() - t0, 1)
    log(f"STAGE download done {T['download_s']}s size={mp4.stat().st_size/1e6:.1f}MB")

    t1 = time.time()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4), *(["-t", str(opt.max_seconds)] if opt.max_seconds else []), "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True)
    T["extract_s"] = round(time.time() - t1, 1)
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(wav)]).decode().strip())
    log(f"STAGE extract done {T['extract_s']}s audio_duration={dur:.1f}s ({hms(dur)})")

    # ── 詞彙表：投影片(zh/en) -> 名詞/英文術語 -> prompt + hotwords ──
    tg = time.time()
    sl = c.get("slides") or {}
    zh_txt = irtext.pdf_text(sl.get("zh"), WORK / f"{cid}_zh.pdf")
    en_txt = irtext.pdf_text(sl.get("en"), WORK / f"{cid}_en.pdf")
    company = {} if opt.no_company else irtext.load_company(c["code"])
    ind = json.loads((ROOT / "data" / "terms_industry.json").read_text(encoding="utf-8")).get(c.get("industry"), [])
    g = irtext.build_glossary(c, zh_txt, en_txt, ind, company)
    prompt, hotwords = irtext.build_prompt_hotwords(g)
    log(f"GLOSSARY slides_zh={len(zh_txt)}ch slides_en={len(en_txt)}ch company_file={bool(company)}")
    log(f"PROMPT({len(prompt)}) {prompt}")
    log(f"HOTWORDS({len(hotwords)}) {hotwords}")
    T["glossary_s"] = round(time.time() - tg, 1)

    t2 = time.time()
    seg_iter, info = model_obj.transcribe(str(wav), language="zh", beam_size=5, vad_filter=True,
                                          initial_prompt=prompt, hotwords=hotwords,
                                          condition_on_previous_text=False)
    raw, n = [], 0
    for s in seg_iter:
        raw.append({"start": s.start, "end": s.end, "text": s.text})
        n += 1
        if n % 20 == 0:
            log(f"  progress {s.end:.0f}/{dur:.0f}s ({s.end/dur*100:.0f}%) elapsed {time.time()-t2:.0f}s")
    T["transcribe_s"] = round(time.time() - t2, 1)
    log(f"STAGE transcribe done {T['transcribe_s']}s segments={len(raw)} RTF={T['transcribe_s']/dur:.3f}")

    t3 = time.time()
    for s in raw:
        s["text"] = irtext.to_tw(cc, s["text"])
    n_raw = len(raw)
    cleaned = irtext.clean(raw, prompt, hotwords)
    log(f"CLEAN dropped {n_raw - len(cleaned)} of {n_raw} segments (junk/echo/repeat)")
    stats = {}
    gl = [] if opt.no_termfix else irtext.load_termfix(ROOT / "data" / "termfix.tsv")
    cl = [] if (opt.no_termfix or opt.no_company) else irtext.load_termfix(ROOT / "data" / "termfix.d" / f"{c['code']}.tsv")
    segs = irtext.postfix(cleaned, g, gl, cl, homophone=not opt.no_homophone, stats=stats)
    log("FIXSTATS " + json.dumps(stats, ensure_ascii=False))
    transcript = [{"speaker": "發言人", "ts": hms(s["start"]), "start": round(s["start"], 2),
                   "end": round(s["end"], 2), "zh": s["text"], "en": None} for s in segs]
    chars = sum(len(s["zh"]) for s in transcript)
    T["postprocess_s"] = round(time.time() - t3, 1)
    T["total_s"] = round(time.time() - t0, 1)

    DETAIL.mkdir(parents=True, exist_ok=True)
    det = {"id": cid, "model": {"asr": f"faster-whisper-{model_name}", "llm": None},
           "generated_at": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec="seconds"),
           "audio_duration_s": round(dur, 1), "timing": T, "rtf": round(T["transcribe_s"] / dur, 3),
           "char_count": chars, "highlights": [], "financials": [], "outlook": [], "qa": [], "transcript": transcript}
    det["pipeline"] = {"prompt": prompt, "hotwords": hotwords, "opencc": "s2tw+臺->台", "company_file": bool(company),
                       "termfix_global": len(gl), "termfix_company": len(cl), "homophone": not opt.no_homophone,
                       "fixstats": stats, "dropped_segments": n_raw - len(cleaned)}
    suffix = f".{opt.tag}" if opt.tag else ""
    (DETAIL / f"{cid}{suffix}.json").write_text(json.dumps(det, ensure_ascii=False, indent=1), encoding="utf-8")
    txt = "\n".join(f"[{s['ts']}] {s['zh']}" for s in transcript)
    (DETAIL / f"{cid}{suffix}.txt").write_text(txt + "\n", encoding="utf-8")
    # 修正前的原始(已轉繁體、已過濾)分段，供離線比較各修正階段的貢獻
    (DETAIL / f"{cid}{suffix}.raw.json").write_text(json.dumps({"glossary": g, "segments": cleaned}, ensure_ascii=False), encoding="utf-8")
    if not opt.tag:
        set_status(cid, "done", f"detail/{cid}.json")
    log(f"STAGE postprocess done {T['postprocess_s']}s chars={chars} total={T['total_s']}s")
    log("SUMMARY " + json.dumps({"id": cid, "model": model_name, "audio_s": round(dur, 1), **T, "rtf": det["rtf"], "chars": chars}, ensure_ascii=False))
    mp4.unlink(missing_ok=True); wav.unlink(missing_ok=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True)
    ap.add_argument("--model", default="medium", help="small|medium|large-v3|large-v3-turbo")
    ap.add_argument("--tag", default="", help="輸出加後綴(不改 conferences.json 狀態)，例如 medium-v2")
    ap.add_argument("--no-company", action="store_true", help="不讀 data/company/<code>.json 與 data/termfix.d/<code>.tsv")
    ap.add_argument("--no-termfix", action="store_true", help="不套用 TSV 術語校正")
    ap.add_argument("--max-seconds", type=int, default=0, help="測試用：只轉前 N 秒")
    ap.add_argument("--no-homophone", action="store_true", help="不做 pypinyin 同音校正")
    a = ap.parse_args()
    ids = [x.strip() for x in a.ids.split(",") if x.strip()]
    confs = {c["id"]: c for c in load()["conferences"]}
    from faster_whisper import WhisperModel
    from opencc import OpenCC
    cc = OpenCC("s2tw")
    t = time.time()
    model = WhisperModel(a.model, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 4)
    log(f"STAGE model_load done {time.time()-t:.1f}s (cpu_threads={os.cpu_count()})")
    fail = 0
    for cid in ids:
        c = confs.get(cid)
        if not c or not c.get("video", {}).get("url"):
            log(f"skip {cid}: no video"); fail += 1; continue
        try:
            run_one(c, a.model, model, cc, a)
        except Exception as e:
            log(f"FAILED {cid}: {e}"); fail += 1
            if not a.tag: set_status(cid, "pending")
    sys.exit(1 if fail else 0)

if __name__ == "__main__":
    main()
