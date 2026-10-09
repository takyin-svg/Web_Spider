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
            "analyzed_records": []
        }
        self.load()

    def get_cutoff_timestamp(self) -> int:
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
        self._clean_and_normalize()

    def _clean_and_normalize(self):
        """核心修復：強制所有 ID/Link 轉為字串並去重"""
        raw_flash = self.state.get("flash_seen_ids", [])
        raw_news = self.state.get("news_seen_links", [])
        
        # 一律轉為字串且去重
        clean_flash = list(dict.fromkeys(str(x) for x in raw_flash if x))[-1000:]
        clean_news = list(dict.fromkeys(str(x) for x in raw_news if x))[-1000:]
        
        self.state["flash_seen_ids"] = clean_flash
        self.state["news_seen_links"] = clean_news
        self.state["analyzed_records"] = self.state.get("analyzed_records", [])[-1000:]

    def is_news_scanned(self, unique_key) -> bool:
        if not unique_key:
            return False
        key_str = str(unique_key).strip()
        return (key_str in self.state["news_seen_links"] or 
                key_str in self.state["flash_seen_ids"])

    def add_scanned(self, unique_key):
        if not unique_key:
            return
        key_str = str(unique_key).strip()
        
        if key_str.isdigit():
            if key_str not in self.state["flash_seen_ids"]:
                self.state["flash_seen_ids"].append(key_str)
        else:
            if key_str not in self.state["news_seen_links"]:
                self.state["news_seen_links"].append(key_str)

    def add_analyzed_record(self, unique_key, stock: str = "", event: str = ""):
        key_str = str(unique_key).strip()
        # 避免重複追加相同的分析紀錄
        if not any(r.get("key") == key_str for r in self.state["analyzed_records"]):
            self.state["analyzed_records"].append({
                "key": key_str,
                "stock": stock,
                "event": event,
                "timestamp": datetime.now(HKT).strftime('%Y-%m-%d %H:%M')
            })

    def save(self, latest_timestamp: int = 0):
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        if latest_timestamp > self.state.get("last_run_timestamp", 0):
            self.state["last_run_timestamp"] = latest_timestamp
            
        self._clean_and_normalize()
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)
