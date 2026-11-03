# 資料模型

`data/conferences.json` 是列表（輕量）；每場的 `detail` 欄位之後指向 `data/detail/<id>.json`（逐字稿流程產出）。

## conferences.json
`{generated_at, sources[], conferences[]}`，每筆：

| 欄位 | 說明 | 來源 |
|---|---|---|
| id | `<代號>-<YYYYMMDD>` | — |
| code, name, date, time | 代號、簡稱、日期、時間 | TWSE VOD / MOPS |
| market, industry | 上市/上櫃…、產業別 | MOPS / TWSE VOD |
| host, host_source | 主辦券商（從 MOPS 擇要訊息比對券商名，沒寫就空） | MOPS |
| place, mops_summary, website | 地點、公告擇要、公司 IR 頁 | MOPS |
| slides.zh/en | 簡報 PDF（mopsov.twse.com.tw/nas/STR/…） | MOPS |
| video.url/kind/source | mp4 或 YouTube 連結（強制 https） | TWSE VOD / MOPS |
| has_video, has_slides, held | 衍生狀態 | — |
| transcript_status | `none`(無影片) / `pending` / `processing` / `done` | 後續流程更新 |
| detail | `detail/<id>.json` 或 null | 後續流程更新 |

## detail/<id>.json（之後由轉文字流程產出）
```json
{
  "id": "6526-20261103",
  "model": {"asr": "whisper-large-v3", "llm": "..."},
  "generated_at": "ISO8601",
  "highlights": [{"text": "…", "ts": "00:02:28", "speaker": "CFO"}],
  "financials": [{"label": "Q3 營收", "value": "NT$69.4 億", "ts": "00:03:10"}],
  "outlook": [{"text": "…", "ts": "00:30:44"}],
  "qa": [{"q": "…", "a": "…", "ts": "00:45:10"}],
  "transcript": [{"speaker": "發言人", "ts": "00:00:07", "zh": "…", "en": null}]
}
```
`detail.html` 已會讀取以上欄位，有檔案就顯示、沒有就顯示「逐字稿尚未產生」。
