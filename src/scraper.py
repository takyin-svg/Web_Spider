# src/scraper.py
import traceback
from datetime import datetime
from src.config import HKT
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
        # 🆕 本輪統一取一次當下時間，作為「成功源」要推進的分源水位
        now_ts = int(datetime.now(HKT).timestamp())

        print("\n" + "=" * 50)
        print("🌐 開始多源並行採集作業...")
        print("=" * 50)

        for source in self.sources:
            source_name = getattr(source, 'name', source.__class__.__name__)
            print(f"\n▶ 正在執行來源: [{source_name}]")

            # 🆕 各源用自己那條水位線，互不牽連（首次無記錄時自動回退全局默認窗口）
            src_cutoff = state_manager.get_source_cutoff(source_name)

            try:
                items = source.fetch(src_cutoff, state_manager)
                count = len(items) if items else 0
                source_summary[source_name] = count
                
                print(f"  └─ ✅ [{source_name}] 採集成功，新增有效資料: {count} 條")
                
                # 印出該來源的前 2 條標題作為檢查預覽
                if items:
                    for i, it in enumerate(items[:2], 1):
                        preview = it.get('title', '')[:40].replace('\n', ' ')
                        print(f"     [{i}] {preview}...")
                
                all_news.extend(items)

                # 🆕 核心修復：只有該源「本輪抓取成功」才推進它自己的水位線。
                #    last_run_ok=False（如瀏覽器逾時/異常）時凍結該源水位，留待下輪重掃，
                #    不再被其他源的成功掩護著永久漏採。源若未提供該信號則默認成功(向後兼容)。
                if getattr(source, 'last_run_ok', True):
                    state_manager.save_source_cutoff(source_name, now_ts)
                else:
                    print(f"  ⚠️ [{source_name}] 本輪抓取失敗，已凍結該源水位線，下輪將從原時間點重試。")

            except Exception as e:
                source_summary[source_name] = 0
                print(f"  └─ ❌ [{source_name}] 採集出錯: {str(e)}")
                traceback.print_exc()
                # 🆕 拋異常視同該源失敗：不推進其水位線

        print("\n" + "-" * 50)
        print("📊 [採集來源數據統計總覽]:")
        for name, count in source_summary.items():
            print(f"  • {name:<12} : {count:>3} 條")
        print(f"  • 總計獲取新聞   : {len(all_news):>3} 條")
        print("-" * 50 + "\n")

        return all_news
