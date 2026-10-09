# src/sources/zhitong_focus.py
import re
from datetime import datetime
from scrapling.fetchers import DynamicSession
from src.config import HKT
from src.sources.base import BaseSource

class ZhitongFocusSource(BaseSource):
    def __init__(self):
        super().__init__("智通焦點")
        self.entry_url = "https://www.zhitongcaijing.com/?index=ganggu"

    def _parse_time_str(self, text: str) -> tuple[int, str]:
        now = datetime.now(HKT)
        text = str(text).strip()
        if not text:
            return 0, ""

        m = re.search(r'(?:(\d{4})-)?(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{1,2})', text)
        if m:
            year = int(m.group(1)) if m.group(1) else now.year
            month = int(m.group(2))
            day = int(m.group(3))
            hour = int(m.group(4))
            minute = int(m.group(5))
            dt = datetime(year, month, day, hour, minute, 0, tzinfo=HKT)
            return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')

        return 0, ""

    def fetch(self, cutoff_timestamp: int, state_manager, max_pages: int = 5) -> list[dict]:
        results = []
        cutoff_dt_str = datetime.fromtimestamp(cutoff_timestamp, HKT).strftime('%Y-%m-%d %H:%M:%S')
        print(f"  🔍 [{self.name}] 啟動瀏覽器原生通道，時間下限: {cutoff_dt_str}")

        def page_action_handler(page):
            # 阻斷非必要廣告與圖片，防止網絡卡死超時
            page.route("**/*.{png,jpg,jpeg,gif,webp,svg,css,woff,woff2}", lambda route: route.abort())

            # 翻頁並使用網頁原生 window.GET 抓取
            for p in range(1, max_pages + 1):
                raw_data = page.evaluate(f"""async () => {{
                    try {{
                        const res = await window.GET("/content/content-list.html", {{
                            code: "ganggu",
                            page: {p}
                        }});
                        if (res && res[1]) return res[1];
                        if (res && res.data) return res.data;
                    }} catch (e) {{}}
                    return [];
                }}""")

                if not raw_data or not isinstance(raw_data, list):
                    break

                stop_paging = False
                for item in raw_data:
                    title = item.get("title", "").strip()
                    article_id = item.get("id") or item.get("content_id")
                    if not title or not article_id:
                        continue

                    full_link = f"https://www.zhitongcaijing.com/content/detail/{article_id}.html"

                    # 1. 碰頭歷史已掃描記錄 -> 立即終止
                    if state_manager.is_news_scanned(full_link):
                        print(f"  🛑 [{self.name}] 碰頭歷史記錄 [{title[:25]}...]，結束翻頁。")
                        stop_paging = True
                        break

                    # 2. 提取時間
                    raw_time = item.get("publish_time") or item.get("pub_time") or item.get("time") or ""
                    pub_ts, pub_time_str = self._parse_time_str(raw_time)

                    # 3. 觸達水位線 (昨日/週五 16:00) -> 立即終止
                    if pub_ts > 0 and pub_ts <= cutoff_timestamp:
                        print(f"  🛑 [{self.name}] 觸達時間下限 [{pub_time_str}]: [{title[:25]}...]，結束翻頁。")
                        stop_paging = True
                        break

                    results.append({
                        "unique_id": full_link,
                        "title": title,
                        "source": self.name,
                        "link": full_link,
                        "time": pub_time_str
                    })
                    state_manager.add_scanned(full_link)

                if stop_paging:
                    break

        try:
            with DynamicSession(headless=True, stealth=True, timeout=30000) as sess:
                # wait_until 設為 domcontentloaded，只要 DOM 骨架到位立刻執行，不等待廣告
                sess.fetch(
                    self.entry_url,
                    wait_until="domcontentloaded",
                    page_action=page_action_handler
                )
        except Exception as e:
            print(f"  ⚠️ [{self.name}] 調度異常: {e}")

        print(f"  └─ [{self.name}] 本次增量共收錄 {len(results)} 條新聞")
        return results
