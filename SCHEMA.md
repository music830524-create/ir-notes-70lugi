# 資料模型

`data/conferences.json` 是列表（輕量）。每場的 `detail` 指向已產出的 `data/detail/<id>.json`。

## conferences.json
`{generated_at, sources[], conferences[]}`，每筆：

| 欄位 | 說明 | 來源 |
|---|---|---|
| id | `<代號>-<YYYYMMDD>` | — |
| code, name, date, time | 代號、簡稱、日期、時間 | MOPS 行事曆；影音清單可補名稱與日期 |
| market, industry | 上市/上櫃…、產業別 | MOPS / TWSE WebPro |
| host, host_source | 主辦券商（從 MOPS 擇要訊息比對券商名，沒寫就空） | MOPS |
| place, mops_summary, website | 地點、公告擇要、公司 IR 頁（只顯示連結，不抓頁） | MOPS |
| in_mops, in_twse_vod | 這場是否出現在 MOPS 公告、證交所影音。`detail.html` 用來標來源 | — |
| slides.zh/en | 簡報 PDF（mopsov.twse.com.tw/nas/STR/…） | MOPS |
| video.url/kind/source/offsite | 148 或 101 的直接 mp4：`kind` 為 `mp4`。分類 170 勝出時多 `offsite: true`，介面顯示「有影片 (站外)」並可開 `url`；不是 `.mp4` 時 `kind` 為 `offsite`，不進轉寫 | TWSE WebPro |
| has_video, has_slides, held | 衍生狀態 | — |
| summarized | 該場 detail 已有重點。歷次法說的「整理」標記用這個 | detail 的 highlights |
| transcript_status | `none`(無影片) / `pending` / `processing` / `done` | 後續流程更新 |
| detail | `detail/<id>.json` 或 null | 後續流程更新 |

影片網址只來自證交所 WebPro 這三頁（`vodChannelId=101`）的直接 mp4，同一代號與日期優先 148，其次 101，最後 170：

- https://webpro.twse.com.tw/WebPortal/vod/101/?categoryId=148 （MOPS法說會）
- https://webpro.twse.com.tw/WebPortal/vod/101/?categoryId=101 （法人說明會）
- https://webpro.twse.com.tw/WebPortal/vod/101/?categoryId=170 （公司自辦法說會，站外連結。148/101 沒有直接 mp4 時保留此連結，標「有影片 (站外)」；只有網址本身是直接 `.mp4` 才轉寫）

MOPS 公告決定有哪些法說會，不提供影片網址。

## detail/<id>.json
```json
{
  "id": "6526-20261103",
  "model": {"asr": "faster-whisper-medium", "llm": "gemini-3.5-flash-lite"},
  "generated_at": "ISO8601",
  "audio_duration_s": 0,
  "timing": {},
  "rtf": 0,
  "char_count": 0,
  "highlights": [{"text": "…", "ts": "00:02:28"}],
  "financials": [{"label": "Q3 營收", "value": "69.42 億元（原值 6,942 百萬）", "src": "簡報 p.3；逐字稿 00:01:33"}],
  "outlook": [{"text": "…", "ts": "00:30:44"}],
  "qa": [{"q": "…", "a": "…", "ts": "00:45:10"}],
  "transcript": [{"speaker": "發言人", "ts": "00:00:07", "start": 7.0, "end": 12.5, "zh": "…", "en": null}],
  "pipeline": {"prompt": "…", "hotwords": "…", "opencc": "s2tw+臺->台"},
  "sections_note": "重點／財務／展望／問答由 AI 整理的一句說明",
  "sections_meta": {"model": "gemini-3.5-flash-lite", "calls": 1, "financials_kept": 0, "shortfall": []}
}
```

`highlights` 只有 `text` 與 `ts`，沒有講者。`financials` 是 `label`、`value`、`src`（`detail.html` 讀 `f.src`）。逐字稿每一段的 `speaker` 目前都是「發言人」。`start` / `end` 是秒。`pipeline` 是轉寫用的詞彙與校正紀錄。`sections_note` 顯示在重點區下方；`sections_meta` 記錄該次整理的模型、呼叫次數與剔除的數字。

`detail.html` 有檔案就顯示上述區塊，沒有逐字稿就顯示「逐字稿尚未產生」。
