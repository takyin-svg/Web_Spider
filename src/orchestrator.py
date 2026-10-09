# src/orchestrator.py
import time
from datetime import datetime
from src.config import HKT
from src.state_manager import StateManager
from src.scraper import HKStockScraper
from src.filters import NewsFilter
from src.analyzer import GeminiAnalyzer
from src.feishu_notifier import FeishuNotifier

class Orchestrator:
    def run(self):
        state_mgr = StateManager()
        cutoff_ts = state_mgr.get_cutoff_timestamp()
        
        now = datetime.now(HKT)
        print("==================================================")
        print(f"🚀 啟動港股資訊採集流程 [{now.strftime('%Y-%m-%d %H:%M:%S')} HKT]")
        print(f"⏱️ 本次採集時間下限 (Cutoff): {datetime.fromtimestamp(cutoff_ts, HKT).strftime('%Y-%m-%d %H:%M:%S')}")
        print("==================================================")

        scraper = HKStockScraper()
        news_filter = NewsFilter(state_mgr)
        analyzer = GeminiAnalyzer()
        notifier = FeishuNotifier(state_mgr)

        # 1. 調度所有模組抓取增量新聞
        raw_news = scraper.fetch_all(cutoff_ts, state_mgr)
        
        if not raw_news:
            print("📭 本次無新增或未掃描之新聞。")
            state_mgr.save()
            return

        # 2. 關鍵字初篩
        filtered_news = news_filter.apply(raw_news)
        if not filtered_news:
            print("⚡ 新聞皆未命中港股或利好關鍵字，結束分析。")
            state_mgr.save()
            return

        # 3. AI 批次分析
        print(f"🤖 共有 {len(filtered_news)} 條新聞進入 Gemini 深度量化分析...")
        analyzed_results = []
        batch_size = 20

        for i in range(0, len(filtered_news), batch_size):
            batch = filtered_news[i:i + batch_size]
            batch_res = analyzer.analyze(batch)
            if batch_res is not None:
                for item in batch:
                    state_mgr.add_analyzed_record(item.get("unique_id"), event="AI_PROCESSED")
                analyzed_results.extend(batch_res)

        # 4. 飛書推送
        if analyzed_results:
            push_time = datetime.now(HKT).strftime('%Y-%m-%d %H:%M:%S')
            notifier.push({"push_time": push_time, "news_list": analyzed_results})
        else:
            print("📉 經 AI 研判，本次無符合 >= 80 分之高確定性利好事件。")

        # 5. 保存最新進度
        state_mgr.save(int(now.timestamp()))
        print("🏁 全流程執行完畢，狀態已寫入 data/history.json。")
