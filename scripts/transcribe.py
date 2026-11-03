#!/usr/bin/env python3
"""下載法說影片 -> 16kHz 單聲道音檔 -> faster-whisper(CPU, int8) -> 繁體 -> data/detail/<id>.json + .txt"""
import argparse, json, os, re, subprocess, sys, time, datetime, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONF = ROOT / "data" / "conferences.json"
DETAIL = ROOT / "data" / "detail"
WORK = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "ir_work"

JUNK = re.compile(r"字幕|訂閱|订阅|明鏡|明镜|點贊|点赞|打賞|打赏|感謝觀看|感谢观看|謝謝收看|謝謝觀看|下集再見|"
                  r"請不吝|李宗盛|中文字幕志願者|Amara|subscribe|Thanks for watching", re.I)

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

def clean(segs):
    out, last, rep = [], None, 0
    for s in segs:
        t = s["text"].strip()
        if not t or JUNK.search(t):
            continue
        if t == last:
            rep += 1
            if rep >= 2:  # 同一句連續重複超過 2 次 => 幻覺
                continue
        else:
            last, rep = t, 0
        s["text"] = t
        out.append(s)
    return out

def run_one(c, model_name, model_obj, cc):
    cid = c["id"]
    WORK.mkdir(parents=True, exist_ok=True)
    mp4, wav = WORK / f"{cid}.mp4", WORK / f"{cid}.wav"
    T = {}
    t0 = time.time()
    set_status(cid, "processing")
    download(c["video"]["url"], mp4)
    T["download_s"] = round(time.time() - t0, 1)
    log(f"STAGE download done {T['download_s']}s size={mp4.stat().st_size/1e6:.1f}MB")

    t1 = time.time()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4), "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True)
    T["extract_s"] = round(time.time() - t1, 1)
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(wav)]).decode().strip())
    log(f"STAGE extract done {T['extract_s']}s audio_duration={dur:.1f}s ({hms(dur)})")

    t2 = time.time()
    prompt = (f"以下是台灣上市公司{c['name']}（股票代號{c['code']}）的法人說明會，使用繁體中文。"
              "內容包含營收、毛利率、營業費用、營業利益、稅後淨利、每股盈餘EPS、季增、年增、庫存、資本支出、"
              "AI、半導體、客戶、產品組合、展望與指引，以及法人問答。")
    seg_iter, info = model_obj.transcribe(str(wav), language="zh", beam_size=5, vad_filter=True,
                                          initial_prompt=prompt, condition_on_previous_text=False)
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
        s["text"] = cc.convert(s["text"])
    segs = clean(raw)
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
    (DETAIL / f"{cid}.json").write_text(json.dumps(det, ensure_ascii=False, indent=1), encoding="utf-8")
    txt = "\n".join(f"[{s['ts']}] {s['zh']}" for s in transcript)
    (DETAIL / f"{cid}.txt").write_text(txt + "\n", encoding="utf-8")
    set_status(cid, "done", f"detail/{cid}.json")
    log(f"STAGE postprocess done {T['postprocess_s']}s chars={chars} total={T['total_s']}s")
    log("SUMMARY " + json.dumps({"id": cid, "model": model_name, "audio_s": round(dur, 1), **T, "rtf": det["rtf"], "chars": chars}, ensure_ascii=False))
    mp4.unlink(missing_ok=True); wav.unlink(missing_ok=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True)
    ap.add_argument("--model", default="medium")
    a = ap.parse_args()
    ids = [x.strip() for x in a.ids.split(",") if x.strip()]
    confs = {c["id"]: c for c in load()["conferences"]}
    from faster_whisper import WhisperModel
    from opencc import OpenCC
    cc = OpenCC("s2twp")
    t = time.time()
    model = WhisperModel(a.model, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 4)
    log(f"STAGE model_load done {time.time()-t:.1f}s (cpu_threads={os.cpu_count()})")
    fail = 0
    for cid in ids:
        c = confs.get(cid)
        if not c or not c.get("video", {}).get("url"):
            log(f"skip {cid}: no video"); fail += 1; continue
        try:
            run_one(c, a.model, model, cc)
        except Exception as e:
            log(f"FAILED {cid}: {e}"); set_status(cid, "pending"); fail += 1
    sys.exit(1 if fail else 0)

if __name__ == "__main__":
    main()
