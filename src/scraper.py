# src/scraper.py
import traceback
from src.sources.zhitong_focus import ZhitongFocusSource
from src.sources.zhitong_flash import ZhitongFlashSource

class HKStockScraper:
    def __init__(self):
        # 註冊需要運行的模組
        self.sources = [
            ZhitongFocusSource(),
            ZhitongFlashSource(),
        ]

    def fetch_all(self, cutoff_timestamp: int, state_manager) -> list[dict]:
        all_news = []
        source_summary = {}

        print("\n" + "=" * 50)
        print("🌐 開始多源並行採集作業...")
        print("=" * 50)

        for source in self.sources:
            source_name = getattr(source, 'name', source.__class__.__name__)
            print(f"\n▶ 正在執行來源: [{source_name}]")
            
            try:
                items = source.fetch(cutoff_timestamp, state_manager)
                count = len(items) if items else 0
                source_summary[source_name] = count
                
                print(f"  └─ ✅ [{source_name}] 採集成功，新增有效資料: {count} 條")
                
                # 印出該來源的前 2 條標題作為檢查預覽
                if items:
                    for i, it in enumerate(items[:2], 1):
                        preview = it.get('title', '')[:40].replace('\n', ' ')
                        print(f"     [{i}] {preview}...")
                
                all_news.extend(items)
            except Exception as e:
                source_summary[source_name] = 0
                print(f"  └─ ❌ [{source_name}] 採集出錯: {str(e)}")
                traceback.print_exc()

        print("\n" + "-" * 50)
        print("📊 [採集來源數據統計總覽]:")
        for name, count in source_summary.items():
            print(f"  • {name:<12} : {count:>3} 條")
        print(f"  • 總計獲取新聞   : {len(all_news):>3} 條")
        print("-" * 50 + "\n")

        return all_news
