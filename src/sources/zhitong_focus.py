# src/sources/zhitong_focus.py
import logging
import time
from urllib.parse import urljoin
from scrapling.fetchers import StealthyFetcher
from src.config import ZHITONG_FOCUS_URL
from src.sources.base import BaseSource

logger = logging.getLogger("ZhitongFocus")

class ZhitongFocusSource(BaseSource):
    def __init__(self):
        super().__init__("智通焦點")

    def fetch(self, cutoff_timestamp: int, state_manager, max_pages: int = 4) -> list[dict]:
        results = []
        logger.info(f"📱 正在爬取 [{self.name}] (手機版靜態分頁)...")

        for page_num in range(1, max_pages + 1):
            url = ZHITONG_FOCUS_URL.format(page_num)
            try:
                page = StealthyFetcher.fetch(url, headless=True, timeout=45000)
                elements = page.css(".list-item a, .news-list a, .item a, a")
                page_added = 0

                for el in elements:
                    raw_title = el.xpath(".//text()").getall()
                    title = "".join(raw_title).strip()
                    
                    if len(title) < 15 or "登入" in title or "下載" in title:
                        continue

                    href = el.attrib.get("href", "") if el.attrib else ""
                    if not href or href.startswith("javascript:"):
                        continue

                    full_link = urljoin(url, href)
                    
                    # 狀態去重
                    if state_manager.is_news_scanned(full_link):
                        continue

                    results.append({
                        "unique_id": full_link,
                        "title": title,
                        "source": self.name,
                        "link": full_link,
                        "time": ""
                    })
                    state_manager.add_scanned(full_link)
                    page_added += 1

                logger.info(f"   ↳ [{self.name}] 第 {page_num} 頁採集到 {page_added} 條新增新聞")
                if page_added == 0:
                    break
                time.sleep(2)
            except Exception as e:
                logger.error(f"❌ [{self.name}] 第 {page_num} 頁失敗: {e}")
                break

        return results
