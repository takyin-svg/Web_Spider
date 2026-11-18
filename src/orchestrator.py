#src/orchestrator.py
import os
import json
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
    # AI 分析最大重試次數（主模型）
    MAX_AI_RETRIES = 3
    # 主模型連續失敗後降級的備援模型
    # gemini-2.5-flash 已於 2026-11-02 棄用，12/1 起舊名請求會自動改道至
    # gemini-3.5-flash-lite 並按新模型計費——屆時請把此行改成真正的備援（如 gemini-3.5-flash）
    FALLBACK_MODEL = "gemini-3.8-flash"

    # 🔧 本次修復新增：「斷點重試佇列」——凡是 AI 分析未成功（軟失敗或硬停）的新聞，
    #   一律暫存到此檔，下一輪無論 scraper 抓取水位是否已越過它們，都會優先重試，
    #   徹底堵死「分析失敗→水位已推進→歷史記錄擋住→未分析新聞永久遺失」的漏洞。
    PENDING_FILE = "data/pending_analyze.json"
    # 佇列上限，超過則先進先出丟棄最舊（催化劑時效已過，避免無限堆積）
    MAX_PENDING = 1000

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

        # 早盤前時段: 05:00 - 10:30
        elif 5 <= hour < 10 or (hour == 10 and minute < 30):
            end = now.replace(hour=10, minute=30, second=0, microsecond=0)
            return "早盤前 (橫跨週末/昨夜至今日開盤)", end

        # 盤中交易時段: 10:30 - 16:00
        elif (hour == 10 and minute >= 30) or (11 <= hour <= 15):
            end = now.replace(hour=16, minute=0, second=0, microsecond=0)
            return "盤中 (10:30-16:00)", end

        # 自訂/測試時段
        return "自訂/測試時段", now + timedelta(minutes=60)

    def _calculate_backoff(self, attempt: int) -> float:
        """
        503(容量不足) 的退避要對症下藥：用「分鐘級」而非秒級，並加隨機抖動。
        - 第1次失敗後等 60s + rand(0~30)
        - 第2次失敗後等 120s + rand(0~30)
        - 第3次失敗後等 180s + rand(0~30)
        抖動避免多進程/多批次在同一刻同步重試（雷群效應）。
        """
        base = [60.0, 120.0, 180.0][min(attempt, 3) - 1]
        return base + random.uniform(0, 30)

    def _call_with_model(self, analyzer: GeminiAnalyzer, batch: list, model: str) -> list | None:
        """
        以指定模型調用 analyzer.analyze。
        優先走 model= 入參（上一輪已改好的 analyzer 支援）；
        若舊版 analyze 不接受 model 關鍵字，則臨時覆寫 analyzer.model 屬性再調，調完還原。
        """
        try:
            return analyzer.analyze(batch, model=model)
        except TypeError:
            old = getattr(analyzer, "model", None)
            try:
                analyzer.model = model
                return analyzer.analyze(batch)
            finally:
                if old is not None:
                    analyzer.model = old

    def _analyze_with_retry(self, analyzer: GeminiAnalyzer, batch: list, batch_num: int) -> tuple[list | None, bool]:
        """
        帶「主模型 3 次分鐘級退避 + 降級 1 次」策略的 AI 分析包裝器。

        回傳 (batch_res, hard_stop):
          - batch_res 為列表：分析成功（可能為空列表=該批無達標）。
          - batch_res 為 None 且 hard_stop=False：軟失敗，僅該批留待下輪，不中斷其餘批次。
          - batch_res 為 None 且 hard_stop=True：主模型與降級模型皆失敗，判定進入容量高峰，
            呼叫方應立即結束本輪剩餘批次、重新排下一輪。
        """
        # ---- 主模型：最多 MAX_AI_RETRIES 次，分鐘級退避+抖動 ----
        for attempt in range(1, self.MAX_AI_RETRIES + 1):
            try:
                result = analyzer.analyze(batch)
                if result is not None:
                    if attempt > 1:
                        print(f"✅ [批次 {batch_num}] 第 {attempt} 次嘗試成功！")
                    return result, False
                err = "AI 返回空結果"
            except Exception as e:
                err = f"{type(e).__name__}: {e}"

            if attempt < self.MAX_AI_RETRIES:
                wait = self._calculate_backoff(attempt)
                print(f"⚠️ [批次 {batch_num}] {err}（{attempt}/{self.MAX_AI_RETRIES}），{wait:.0f}s 後重試...")
                time.sleep(wait)
            else:
                print(f"⚠️ [批次 {batch_num}] 主模型連續 {self.MAX_AI_RETRIES} 次失敗：{err}")

        # ---- 主模型 3 次皆敗 → 降級到備援模型打一次 ----
        print(f"🔄 [批次 {batch_num}] 降級至 {self.FALLBACK_MODEL} 嘗試一次...")
        try:
            result = self._call_with_model(analyzer, batch, self.FALLBACK_MODEL)
            if result is not None:
                print(f"✅ [批次 {batch_num}] 降級模型成功！")
                return result, False
        except Exception as e:
            print(f"💀 [批次 {batch_num}] 降級模型亦失敗：{type(e).__name__}: {e}")

        # ---- 主模型 + 降級都失敗：硬停，結束本輪 ----
        print(f"🛑 [批次 {batch_num}] 主模型+降級皆失敗，結束本輪分析，留待下輪重排。")
        return None, True

    # ------------------------------------------------------------------
    # 🔧 新增：斷點重試佇列的讀寫（本輪修復的核心，獨立於 scraper 抓取水位）
    # ------------------------------------------------------------------
    def _load_pending(self) -> list:
        """載入上一輪未成功分析而暫存的新聞。檔不存在或損毀一律回空清單。"""
        try:
            with open(self.PENDING_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []

    def _save_pending(self, items: list):
        """暫存本輪仍未成功分析的新聞，供下一輪斷點重試；超出上限則先進先出丟棄最舊。"""
        items = items if isinstance(items, list) else []
        if len(items) > self.MAX_PENDING:
            dropped = len(items) - self.MAX_PENDING
            print(f"⚠️ 待重試佇列超過上限 {self.MAX_PENDING}，丟棄最舊 {dropped} 條（催化劑時效已過）。")
            items = items[-self.MAX_PENDING:]
        try:
            os.makedirs(os.path.dirname(self.PENDING_FILE) or ".", exist_ok=True)
            with open(self.PENDING_FILE, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
        except OSError as e:
            # 佇列寫入失敗不該讓整輪崩潰，但必須高調——否則仍會遺失
            print(f"❌ 待重試佇列寫入失敗（{self.PENDING_FILE}）：{e}。該批新聞下輪可能需靠抓取水位重撿。")

    def _merge_dedup(self, fresh: list, pending: list) -> list:
        """合併「本輪新抓取」與「上輪暫存佇列」，依 unique_id 去重，新料優先（時間戳較近）。"""
        seen = set()
        merged = []
        for it in (fresh + pending):
            uid = it.get("unique_id")
            if uid in seen:
                continue
            seen.add(uid)
            merged.append(it)
        return merged

    def _execute_pipeline(self, state_mgr, scraper, news_filter, analyzer, notifier):
        """執行單次完整的增量採集、初篩、Gemini 分析與飛書推送流程"""
        cutoff_ts = state_mgr.get_cutoff_timestamp()
        now = datetime.now(HKT)

        print("\n==================================================")
        print(f"⏰ [{now.strftime('%H:%M:%S')} HKT] 開始執行增量資訊採集流程！")
        print(f"⏱️ 本次採集時間下限 (Cutoff): {datetime.fromtimestamp(cutoff_ts, HKT).strftime('%Y-%m-%d %H:%M:%S')}")
        print("==================================================")

        # 載入上輪未分析成功的暫存佇列（斷點重試來源，不受抓取水位影響）
        pending = self._load_pending()

        # 1. 多源增量爬取
        raw_news = scraper.fetch_all(cutoff_ts, state_mgr)

        # 🚨 關鍵防護：若本次因網路逾時或異常導致抓取量為 0，且佇列也空，
        #    絕對不更新 history.json 的 Cutoff 時間戳（若佇列非空則仍需繼續把佇列吃掉）。
        if not raw_news and not pending:
            print("📭 本次採集為空（可能遇網路逾時或暫無更新），保留原 Cutoff 留待下輪重試，不更新狀態。")
            state_mgr.save()
            return

        # 2. 關鍵字初篩與同事件去重
        filtered_news = news_filter.apply(raw_news) if raw_news else []
        if not filtered_news and not pending:
            print("⚡ 新聞皆未命中港股或利好關鍵字。")
            state_mgr.save()   # 分源水位已由 scraper 推進並落盤，此處不再推進全局時間戳
            return

        # 2.5 🔧 併入暫存佇列：新料在前、暫存佇列在後，依 unique_id 去重
        worklist = self._merge_dedup(filtered_news, pending)
        if pending:
            print(f"♻️ 併入上輪未分析成功的暫存 {len(pending)} 條；本輪待分析清單去重後共 {len(worklist)} 條。")

        # 3. AI 批次分析 (使用帶重試機制的包裝器)
        total_count = len(worklist)
        print(f"🤖 共有 {total_count} 條新聞進入 Gemini 深度量化分析...")
        analyzed_results = []
        batch_size = 20

        # 🔧 本輪未能成功分析、需暫存到下輪的新聞清單
        retry_items = []

        for i in range(0, total_count, batch_size):
            batch = worklist[i:i + batch_size]
            batch_num = i // batch_size + 1
            end_idx = min(i + batch_size, total_count)

            print(f"🧠 [批次 {batch_num}] 開始將第 {i+1} 至 {end_idx} 條新聞 (共 {len(batch)} 條) 送入 AI 分析...")

            # ✅ 核心變更：主模型 3 次分鐘級退避 + 降級 1 次，回傳 (結果, 是否硬停)
            batch_res, hard_stop = self._analyze_with_retry(analyzer, batch, batch_num)

            if batch_res is not None:
                for item in batch:
                    state_mgr.add_analyzed_record(item.get("unique_id"), event="AI_PROCESSED")
                analyzed_results.extend(batch_res)
            else:
                # 🔧 該批失敗：整批進暫存佇列，下輪優先重試（不再依賴抓取水位能否重撿）
                retry_items.extend(batch)
                print(f"⚠️ [批次 {batch_num}] AI 分析最終失敗，該批次 {len(batch)} 條新聞已進入待重試佇列。")
                # 🛑 硬停：主模型與降級皆失敗，判定容量高峰，結束本輪剩餘批次、重排下一輪
                if hard_stop:
                    # 🔧 硬停之後尚未處理的所有批次，一併進佇列，絕不遺漏
                    tail = worklist[i + batch_size:]
                    if tail:
                        retry_items.extend(tail)
                        print(f"🛑 本輪提前結束，其餘 {len(tail)} 條尚未分析的新聞一併暫存佇列。")
                    break

        # 4. 飛書推送 (動態讀取 MIN_SCORE)
        if analyzed_results:
            push_time = datetime.now(HKT).strftime('%Y-%m-%d %H:%M:%S')
            print(f"🚀 AI 共篩選出 {len(analyzed_results)} 條達標重磅訊號，準備合併推送...")
            notifier.push({"push_time": push_time, "news_list": analyzed_results})
        else:
            print(f"📉 經 AI 研判，本次無符合 >= {MIN_SCORE} 分之高確定性利好事件。")

        # 5. 🔧 落盤斷點重試佇列：有失敗就寫回，全部處理完就清空佇列檔
        self._save_pending(retry_items)
        if retry_items:
            print(f"♻️ 已暫存 {len(retry_items)} 條未分析成功的新聞至 {self.PENDING_FILE}，下一輪將優先重試。")
        elif pending:
            print(f"♻️ 待重試佇列已全部消化，清空 {self.PENDING_FILE}。")

        # 6. 確保順利完成後落盤。時間水位線改由 scraper 按源推進（save_source_cutoff）：
        #    某源超時/失敗只凍結它自己那條，不再被其他源的成功掩護著一路前移。
        state_mgr.save()   # 不再無腦推進全局 last_run_timestamp
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

            sleep_seconds = random.randint(3 * 60, 8 * 60)
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
