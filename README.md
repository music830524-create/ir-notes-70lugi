# Tim 法說逐字稿（手機網頁）

台股法說會列表（依日期分組、搜尋、篩選）與每場的詳細頁。目前是頁面骨架：場次、影片、簡報連結為真實資料，逐字稿／亮點／Q&A 之後接上。
資料模型見 `SCHEMA.md`；重新抓資料：`python3 build_data.py`（需 requests、beautifulsoup4、lxml）。
資料來源：證交所影音平台、公開資訊觀測站。**不要把 token、API key 放進這個 repo。**
