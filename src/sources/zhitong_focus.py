# src/sources/zhitong_focus.py
import re
import time
from urllib.parse import urljoin
from datetime import datetime
from scrapling.fetchers import StealthyFetcher
from src.config import ZHITONG_FOCUS_URL, HKT
from src.sources.base import BaseSource

class ZhitongFocusSource(BaseSource):
    def __init__(self):
        super().__init__("智通焦點")

    def _parse_time_str(self, text: str) -> tuple[int, str]:
        """解析常見時間字串為 (秒級時間戳, 格式化字串)"""
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

        # 匹配 "XX分鐘前" / "XX小時前"
        m_min = re.search(r'(\d+)\s*分鐘前', text)
        if m_min:
            ts = int(now.timestamp()) - int(m_min.group(1)) * 60
            return ts, datetime.fromtimestamp(ts, HKT).strftime('%Y-%m-%d %H:%M:%S')

        m_hr = re.search(r'(\d+)\s*小時前', text)
        if m_hr:
            ts = int(now.timestamp()) - int(m_hr.group(1)) * 3600
            return ts, datetime.fromtimestamp(ts, HKT).strftime('%Y-%m-%d %H:%M:%S')

        return 0, ""

    def fetch(self, cutoff_timestamp: int, state_manager, max_pages: int = 6) -> list[dict]:
        results = []
        should_stop = False

        cutoff_dt_str = datetime.fromtimestamp(cutoff_timestamp, HKT).strftime('%Y-%m-%d %H:%M:%S')
        print(f"  🔍 [{self.name}] 啟動增量採集，目標時間下限: {cutoff_dt_str}")

        for page_num in range(1, max_pages + 1):
            url = ZHITONG_FOCUS_URL.format(page_num)
            try:
                page = StealthyFetcher.fetch(url, headless=True, timeout=45000)
                # 手機版列表項目的通用節點
                elements = page.css(".list-item a, .news-list a, .item a, a")
                
                page_added = 0
                for el in elements:
                    raw_title = el.xpath(".//text()").getall()
                    title = "".join(raw_title).strip()

                    # 過濾導航雜訊
                    if len(title) < 14 or any(w in title for w in ["登入", "登錄", "下載", "首頁", "版權所有", "用戶協議", "關於我們"]):
                        continue

                    href = el.attrib.get("href", "") if el.attrib else ""
                    if not href or href.startswith("javascript:"):
                        continue
                        
                    full_link = urljoin(url, href)

                    # 🌟 核心增量判斷 1: 遇見已掃描記錄，立即結束翻頁
                    if state_manager.is_news_scanned(full_link):
                        print(f"  🛑 [{self.name}] 碰頭歷史記錄 [{title[:25]}...]")
                        print(f"     已成功接軌上次進度，僅採集最新增量，結束翻頁。")
                        should_stop = True
                        break

                    # 嘗試從兄弟節點或父層提取發布時間標籤
                    time_raw = ""
                    time_nodes = el.xpath("./following-sibling::*//text() | ../..//span[contains(@class, 'time')]//text() | .//span//text()").getall()
                    if time_nodes:
                        joined_text = "".join(time_nodes).strip()
                        if re.search(r'\d{1,2}-\d{1,2}|\d{1,2}:\d{1,2}|前', joined_text):
                            time_raw = joined_text

                    pub_ts, pub_time_str = self._parse_time_str(time_raw)

                    # 🌟 核心增量判斷 2: 若提取到時間且早於 16:00 水位線，觸發終止
                    if pub_ts > 0 and pub_ts <= cutoff_timestamp:
                        print(f"  🛑 [{self.name}] 觸達時間下限 [{pub_time_str}]: [{title[:25]}...]")
                        print(f"     已涵蓋指定時間窗口，結束翻頁。")
                        should_stop = True
                        break

                    # 若未提取到時間字串，預設為當前執行時間
                    if not pub_time_str:
                        pub_time_str = datetime.now(HKT).strftime('%Y-%m-%d %H:%M:%S')

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

                if page_added == 0 and page_num >= 2:
                    break

                time.sleep(1.5)
            except Exception as e:
                print(f"  ⚠️ [{self.name}] 第 {page_num} 頁擷取出錯: {e}")
                break

        print(f"  └─ [{self.name}] 本次增量共收錄 {len(results)} 條新聞")
        return results
