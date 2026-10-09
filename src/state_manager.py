# src/state_manager.py
import json
import os
from datetime import datetime, timedelta
from src.config import HISTORY_FILE, HKT

class StateManager:
    def __init__(self):
        self.state = {
            "last_run_timestamp": 0,
            "flash_seen_ids": [],
            "news_seen_links": [],
            "analyzed_records": []  # 保留已推送事件的去重記錄
        }
        self.load()

    def get_cutoff_timestamp(self) -> int:
        """
        計算時間窗口下限:
        - 週一 (weekday=0): 自動追溯至上週五 16:00 (覆蓋五收市後及六、日)
        - 週二至週五: 追溯至前一日 16:00
        - 若記錄過上次運行時間且較新，則優先使用上次運行時間（寬限 5 分鐘容錯）
        """
        now = datetime.now(HKT)
        weekday = now.weekday()
        days_back = 3 if weekday == 0 else 1
        
        default_cutoff = (now - timedelta(days=days_back)).replace(
            hour=16, minute=0, second=0, microsecond=0
        )
        cutoff_ts = int(default_cutoff.timestamp())
        
        last_ts = self.state.get("last_run_timestamp", 0)
        if last_ts > cutoff_ts:
            return max(cutoff_ts, last_ts - 300)
        return cutoff_ts

    def load(self):
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self.state.update(data)
            except Exception:
                pass
        self._clean_old_records()

    def _clean_old_records(self):
        """維持快取長度在 1000 條以內"""
        self.state["flash_seen_ids"] = self.state.get("flash_seen_ids", [])[-1000:]
        self.state["news_seen_links"] = self.state.get("news_seen_links", [])[-1000:]
        self.state["analyzed_records"] = self.state.get("analyzed_records", [])[-1000:]

    def is_news_scanned(self, unique_key: str) -> bool:
        """支援一般 URL 或 7x24 的 flash_{id} 唯一鍵判定"""
        if not unique_key:
            return False
        return (unique_key in self.state["news_seen_links"] or 
                unique_key in self.state["flash_seen_ids"])

    def add_scanned(self, unique_key: str):
        if str(unique_key).isdigit():
            self.state["flash_seen_ids"].append(int(unique_key))
        else:
            self.state["news_seen_links"].append(unique_key)

    def add_analyzed_record(self, unique_key: str, stock: str = "", event: str = ""):
        self.state["analyzed_records"].append({
            "key": unique_key,
            "stock": stock,
            "event": event,
            "timestamp": datetime.now(HKT).strftime('%Y-%m-%d %H:%M')
        })

    def save(self, latest_timestamp: int = 0):
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        if latest_timestamp > self.state.get("last_run_timestamp", 0):
            self.state["last_run_timestamp"] = latest_timestamp
            
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)
