# src/orchestrator.py
import os
import time
import random
from datetime import datetime, timedelta
from src.config import HKT, MIN_SCORE
from src.state_manager import StateManager
from src.scraper import HKStockScraper
from src.filters import NewsFilter
from src.analyzer import GeminiAnalyzer
from src.feishu_notifier import FeishuNotifier

class Orchestrator:
    def _get_window(self):
        """依據香港時間判定當前運作時段與結束時間"""
        now = datetime.now(HKT)
        hour, minute = now.hour, now.minute

        # 夜間時段: 22:00 - 02:00
        if hour >= 22 or hour < 2:
            end = now.replace(hour=2, minute=0, second=0, microsecond=0)
            if hour >= 22:
                end += timedelta(days=1)
            return "夜間 (昨收16:00後至此刻)", end

        # 早盤前時段: 05:00 - 10:00
        elif 5 <= hour < 10:
            end = now.replace(hour=10, minute=0, second=0, microsecond=0)
            return "早盤前 (橫跨週末/昨夜至今日開盤)", end

        # 盤中交易時段: 10:30 - 15:30
        elif (hour == 10 and minute >= 30) or (11 <= hour <= 14) or (hour == 15 and minute < 30):
            end = now.replace(hour=15, minute=30, second=0, microsecond=0)
            return "盤中 (10:30-15:30)", end

        # 自訂/測試時段
        return "自訂/測試時段", now + timedelta(minutes=60)

    def _execute_pipeline(self, state_mgr, scraper, news_filter, analyzer, notifier):
        """執行單次完整的增量採集、初篩、Gemini 分析與飛書推送流程"""
        cutoff_ts = state_mgr.get_cutoff_timestamp()
        now = datetime.now(HKT)

        print("\n==================================================")
        print(f"⏰ [{now.strftime('%H:%M:%S')} HKT] 開始執行增量資訊採集流程！")
        print(f"⏱️ 本次採集時間下限 (Cutoff): {datetime.fromtimestamp(cutoff_ts, HKT).strftime('%Y-%m-%d %H:%M:%S')}")
        print("==================================================")

        # 1. 多源增量爬取
        raw_news = scraper.fetch_all(cutoff_ts, state_mgr)
        
        # 🚨 關鍵防護：若本次因網路逾時或異常導致抓取量為 0，絕對不更新 history.json 的 Cutoff 時間戳！
        if not raw_news:
            print("📭 本次採集為空（可能遇網路逾時或暫無更新），保留原 Cutoff 留待下輪重試，不更新狀態。")
            state_mgr.save()  # 不傳入新時間戳，保持原有水位線
            return

        # 2. 關鍵字初篩與同事件去重
        filtered_news = news_filter.apply(raw_news)
        if not filtered_news:
            print("⚡ 新聞皆未命中港股或利好關鍵字。")
            state_mgr.save(int(now.timestamp()))
            return

        # 3. AI 批次分析
        print(f"🤖 共有 {len(filtered_news)} 條新聞進入 Gemini 深度量化分析...")
        analyzed_results = []
        batch_size = 20

        for i in range(0, len(filtered_news), batch_size):
            batch = filtered_news[i:i + batch_size]
            print(f"🧠 開始將第 {i+1} 至 {min(i+batch_size, len(filtered_news))} 條新聞送入 AI 分析...")
            
            batch_res = analyzer.analyze(batch)
            if batch_res is not None:
                for item in batch:
                    state_mgr.add_analyzed_record(item.get("unique_id"), event="AI_PROCESSED")
                analyzed_results.extend(batch_res)
            else:
                print(f"⚠️ 第 {i+1} 批次 AI 分析異常，將留待下輪重試。")

        # 4. 飛書推送 (動態讀取 MIN_SCORE)
        if analyzed_results:
            push_time = datetime.now(HKT).strftime('%Y-%m-%d %H:%M:%S')
            print(f"🚀 AI 共篩選出 {len(analyzed_results)} 條達標重磅訊號，準備合併推送...")
            notifier.push({"push_time": push_time, "news_list": analyzed_results})
        else:
            print(f"📉 經 AI 研判，本次無符合 >= {MIN_SCORE} 分之高確定性利好事件。")

        # 5. 確保順利完成後，才安全推進時間水位線
        state_mgr.save(int(now.timestamp()))
        print("🏁 當輪採集完畢，狀態已寫入 data/history.json。")

    def run(self):
        run_mode = os.getenv("RUN_MODE", "daemon").lower()
        
        state_mgr = StateManager()
        scraper = HKStockScraper()
        news_filter = NewsFilter(state_mgr)
        analyzer = GeminiAnalyzer()
        notifier = FeishuNotifier(state_mgr)

        # 單次手動模式（manual / once）
        if run_mode in ["manual", "once"]:
            print("🚀 [手動/單次模式] 立即啟動單次採集任務，不進入隨機循環...")
            self._execute_pipeline(state_mgr, scraper, news_filter, analyzer, notifier)
            return

        # 隨機排程常駐模式 (Daemon Mode)
        session_name, end_time = self._get_window()
        print(f"🌟 啟動時段：【{session_name}】，預計運行至 HKT: {end_time.strftime('%H:%M:%S')}")

        initial_sleep = random.randint(0, 5 * 60)
        first_run_time = datetime.now(HKT) + timedelta(seconds=initial_sleep)
        print(f"🎲 [首次排程] 第一波隨機爬取時間定於 HKT: {first_run_time.strftime('%H:%M:%S')}")
        print(f"💤 [狀態] 系統正在 Sleep 待機中... (預計等待 {initial_sleep} 秒 / 約 {initial_sleep // 60} 分鐘)")
        time.sleep(initial_sleep)

        while True:
            now = datetime.now(HKT)
            if now >= end_time:
                break

            self._execute_pipeline(state_mgr, scraper, news_filter, analyzer, notifier)

            now = datetime.now(HKT)
            if now >= end_time:
                break

            sleep_seconds = random.randint(5 * 60, 12 * 60)
            if now + timedelta(seconds=sleep_seconds) > end_time:
                remaining = (end_time - now).total_seconds()
                if remaining > 180:
                    next_run_time = now + timedelta(seconds=remaining)
                    print(f"🎲 [末次排程] 本時段即將結束，最後一次動作時間定於 HKT: {next_run_time.strftime('%H:%M:%S')}")
                    print(f"💤 [狀態] 系統休眠中... (剩餘 {int(remaining)} 秒至時段結束)")
                    time.sleep(remaining)
                break

            next_run_time = now + timedelta(seconds=sleep_seconds)
            print(f"\n🎲 [下一輪排程] 隨機執行時間定於 HKT: {next_run_time.strftime('%H:%M:%S')}")
            print(f"💤 [狀態] 系統休眠中... (等待 {sleep_seconds} 秒 / 約 {sleep_seconds // 60} 分鐘)")
            time.sleep(sleep_seconds)

        print("🏁 當前排程時段結束，程式平穩退出。")
