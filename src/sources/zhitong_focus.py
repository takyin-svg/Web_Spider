# src/sources/zhitong_focus.py
import re
import time
import requests
from datetime import datetime
from src.config import HKT
from src.sources.base import BaseSource

class ZhitongFocusSource(BaseSource):
    def __init__(self):
        super().__init__("智通焦點")
        # 電腦版官方非同步分頁接口
        self.api_url = "https://www.zhitongcaijing.com/content/content-list.html"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Referer": "https://www.zhitongcaijing.com/?index=ganggu&page=1",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest"
        }

    def _parse_time_str(self, text: str) -> tuple[int, str]:
        now = datetime.now(HKT)
        text = str(text).strip()
        if not text:
            return 0, ""

        # 匹配 "2026-10-09 20:46" 或 "10-09 20:46"
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

    def fetch(self, cutoff_timestamp: int, state_manager, max_pages: int = 6) -> list[dict]:
        results = []
        should_stop = False

        cutoff_dt_str = datetime.fromtimestamp(cutoff_timestamp, HKT).strftime('%Y-%m-%d %H:%M:%S')
        print(f"  🔍 [{self.name}] 啟動增量採集，目標時間下限: {cutoff_dt_str}")

        for page_num in range(1, max_pages + 1):
            params = {
                "code": "ganggu",
                "page": page_num
            }
            try:
                resp = requests.get(self.api_url, params=params, headers=self.headers, timeout=15)
                if resp.status_code != 200:
                    break

                res_json = resp.json()
                # 判斷回傳結構 (一般為 [status, data] 或字典型態)
                news_items = []
                if isinstance(res_json, list) and len(res_json) >= 2:
                    news_items = res_json[1]
                elif isinstance(res_json, dict):
                    news_items = res_json.get("data", res_json.get("list", []))

                if not news_items:
                    break

                page_added = 0
                for item in news_items:
                    title = item.get("title", "").strip()
                    article_id = item.get("id") or item.get("content_id")
                    full_link = f"https://www.zhitongcaijing.com/content/detail/{article_id}.html"

                    if not title or len(title) < 12:
                        continue

                    # 1. 碰頭已掃描歷史記錄 -> 接軌退出
                    if state_manager.is_news_scanned(full_link):
                        print(f"  🛑 [{self.name}] 碰頭歷史記錄 [{title[:25]}...]")
                        print(f"     已成功接軌上次進度，結束翻頁。")
                        should_stop = True
                        break

                    # 2. 提取時間
                    raw_time = item.get("publish_time") or item.get("pub_time") or item.get("time") or ""
                    pub_ts, pub_time_str = self._parse_time_str(raw_time)

                    # 3. 觸達 16:00 水位線 -> 終止翻頁
                    if pub_ts > 0 and pub_ts <= cutoff_timestamp:
                        print(f"  🛑 [{self.name}] 觸達時間下限 [{pub_time_str}]: [{title[:25]}...]")
                        print(f"     已涵蓋指定時間窗口，結束翻頁。")
                        should_stop = True
                        break

                    results.append({
                        "unique_id": full_link,
                        "title": title,
                        "source": self.name,
                        "link": full_link,
                        "time": pub_time_str
                    })
                    state_manager.add_scanned(full_link)
                    page_added += 1

                if should_stop:
                    break

                if page_added == 0:
                    break

                time.sleep(1)
            except Exception as e:
                print(f"  ⚠️ [{self.name}] 第 {page_num} 頁請求異常: {e}")
                break

        print(f"  └─ [{self.name}] 本次增量共收錄 {len(results)} 條新聞")
        return results
