# src/scraper.py
import logging
from src.sources.zhitong_focus import ZhitongFocusSource
from src.sources.zhitong_flash import ZhitongFlashSource

logger = logging.getLogger("ScraperManager")

class HKStockScraper:
    def __init__(self):
        # 註冊所有啟用的來源 (未來擴充只需在這裡 append 新的模組實例)
        self.sources = [
            ZhitongFocusSource(),
            ZhitongFlashSource(),
        ]

    def fetch_all(self, cutoff_timestamp: int, state_manager) -> list[dict]:
        all_news = []
        for source in self.sources:
            try:
                items = source.fetch(cutoff_timestamp, state_manager)
                all_news.extend(items)
            except Exception as e:
                logger.error(f"❌ 來源 [{source.name}] 執行異常: {e}", exc_info=True)
                
        logger.info(f"📊 所有來源採集結束，新增去重新聞總量: {len(all_news)} 條")
        return all_news
