# src/sources/zhitong_focus.py
import time
from urllib.parse import urljoin
from scrapling.fetchers import StealthyFetcher
from src.config import ZHITONG_FOCUS_URL
from src.sources.base import BaseSource

class ZhitongFocusSource(BaseSource):
    def __init__(self):
        super().__init__("智通焦點")

    def fetch(self, cutoff_timestamp: int, state_manager, max_pages: int = 8) -> list[dict]:
        """
        智通焦點新聞（手機版）
        - 增量機制：若歷史已有記錄，遇到已掃描過的連結立即終止翻頁（秒級接軌最新）
        - 跨週末/首次運行：翻頁多達 8 頁（約 80 條新聞），保證涵蓋週五 16:00 至週一全部內容
        """
        results = []
        should_stop = False

        print(f"  🔍 [{self.name}] 開始抓取手機版分頁...")

        for page_num in range(1, max_pages + 1):
            url = ZHITONG_FOCUS_URL.format(page_num)
            try:
                page = StealthyFetcher.fetch(url, headless=True, timeout=45000)
                # 使用經實測成功提取到 10 條/頁的通用選擇器
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

                    # 🌟 增量接軌判定：如果碰頭歷史已掃描記錄 -> 立即煞車退出
                    if state_manager.is_news_scanned(full_link):
                        print(f"  🛑 [{self.name}] 碰頭歷史記錄 [{title[:25]}...]")
                        print(f"     已成功接軌上次進度，僅採集最新增量，結束翻頁。")
                        should_stop = True
                        break

                    results.append({
                        "unique_id": full_link,
                        "title": title,
                        "source": self.name,
                        "link": full_link,
                        "time": ""
                    })
                    state_manager.add_scanned(full_link)
                    page_added += 1

                if should_stop:
                    break

                # 若整頁都無新增資料，退出翻頁
                if page_added == 0 and page_num >= 2:
                    break

                time.sleep(1.5)
            except Exception as e:
                print(f"  ⚠️ [{self.name}] 第 {page_num} 頁擷取出錯: {e}")
                break

        print(f"  └─ [{self.name}] 本次增量共收錄 {len(results)} 條新聞")
        return results
