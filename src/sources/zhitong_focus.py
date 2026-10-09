# src/sources/zhitong_focus.py
import re
import time
from datetime import datetime
from urllib.parse import urljoin
from scrapling.fetchers import StealthyFetcher
from src.config import ZHITONG_FOCUS_URL, HKT
from src.sources.base import BaseSource

class ZhitongFocusSource(BaseSource):
    def __init__(self):
        super().__init__("智通焦點")

    def _parse_pub_time(self, time_raw_str: str) -> int:
        now = datetime.now(HKT)
        text = time_raw_str.strip()
        if not text:
            return int(now.timestamp())

        m = re.search(r'(?:(\d{4})-)?(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{1,2})', text)
        if m:
            year = int(m.group(1)) if m.group(1) else now.year
            month = int(m.group(2))
            day = int(m.group(3))
            hour = int(m.group(4))
            minute = int(m.group(5))
            dt = datetime(year, month, day, hour, minute, 0, tzinfo=HKT)
            return int(dt.timestamp())

        m_min = re.search(r'(\d+)\s*分鐘前', text)
        if m_min:
            return int(now.timestamp()) - int(m_min.group(1)) * 60

        m_hr = re.search(r'(\d+)\s*小時前', text)
        if m_hr:
            return int(now.timestamp()) - int(m_hr.group(1)) * 3600

        return int(now.timestamp())

    def fetch(self, cutoff_timestamp: int, state_manager, max_pages: int = 15) -> list[dict]:
        results = []
        should_stop = False

        cutoff_dt_str = datetime.fromtimestamp(cutoff_timestamp, HKT).strftime('%Y-%m-%d %H:%M:%S')
        print(f"  🔍 [{self.name}] 啟動增量採集，目標時間下限: {cutoff_dt_str}")

        for page_num in range(1, max_pages + 1):
            url = ZHITONG_FOCUS_URL.format(page_num)
            try:
                page = StealthyFetcher.fetch(url, headless=True, timeout=45000)
                items = page.css(".list-item, .news-item, .item, li")
                if not items:
                    items = page.css("a")

                for item in items:
                    link_node = item.css("a") if item.tag != "a" else [item]
                    if not link_node:
                        continue
                    
                    href = link_node[0].attrib.get("href", "")
                    if not href or href.startswith("javascript:"):
                        continue
                        
                    full_link = urljoin(url, href)
                    title = "".join(link_node[0].xpath(".//text()").getall()).strip()

                    if len(title) < 12 or any(w in title for w in ["登入", "登錄", "下載", "首頁", "版權所有"]):
                        continue

                    # 🌟 核心增量判斷 1: 遇到上次已抓過的新聞 -> 說明已接軌最新進度，立即中斷退出！
                    if state_manager.is_news_scanned(full_link):
                        print(f"  🛑 [{self.name}] 碰頭歷史記錄: [{title[:25]}...]")
                        print(f"     已成功接軌上次進度，僅採集最新增量，結束翻頁。")
                        should_stop = True
                        break

                    # 提取時間
                    time_nodes = item.css(".time, .date, span, p")
                    time_raw = ""
                    for tn in time_nodes:
                        t_text = "".join(tn.xpath(".//text()").getall()).strip()
                        if re.search(r'\d{1,2}-\d{1,2}|\d{1,2}:\d{1,2}|前', t_text):
                            time_raw = t_text
                            break

                    pub_ts = self._parse_pub_time(time_raw)
                    pub_time_str = datetime.fromtimestamp(pub_ts, HKT).strftime('%Y-%m-%d %H:%M:%S')

                    # 🌟 核心增量判斷 2: 遇到早於截止時間(前日/週五 16:00)的新聞 -> 終止翻頁
                    if pub_ts <= cutoff_timestamp:
                        print(f"  🛑 [{self.name}] 觸達時間下限 [{pub_time_str}]: [{title[:25]}...]")
                        print(f"     已涵蓋目標時間窗口，結束翻頁。")
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

                if should_stop:
                    break

                time.sleep(1.5)
            except Exception as e:
                print(f"  ⚠️ [{self.name}] 第 {page_num} 頁擷取出錯: {e}")
                break

        return results
