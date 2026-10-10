# src/filters.py
import re
from typing import List, Dict
from src.config import HK_KEYWORDS, BASIC_BULLISH

class NewsFilter:
    def __init__(self, state_manager):
        # 保留參數避免 orchestrator 初始化報錯
        self.state_manager = state_manager

    def _extract_stock_code(self, text: str) -> str:
        """提取 5 位港股代碼 (例如 03990, 00148, 02498)"""
        m = re.search(r'\((\d{5})(?:\.HK)?\)|（(\d{5})(?:\.HK)?）|\b(\d{5})\.HK\b|\((\d{5})\)', text)
        if m:
            return m.group(1) or m.group(2) or m.group(3) or m.group(4) or ""
        return ""

    def _generate_event_fingerprint(self, item: Dict) -> str:
        """
        生成事件唯一指紋：股票代碼 + 核心動作 + 實體
        例如: "03990_增持_何享健" 或 "02498_銷量"
        """
        title = item.get("title", "")
        content = item.get("content", "")
        full_text = f"{title} {content}"
        
        code = self._extract_stock_code(full_text)
        if not code:
            # 若無明確代碼，取清洗後的標題特徵（去除標點與空格）
            clean_title = re.sub(r'[^\w\u4e00-\u9fa5]', '', title)
            return clean_title[:20]

        # 核心動作比對 (繁簡覆蓋)
        action = "other"
        for act in ["增持", "減持", "减持", "回購", "回购", "配售", "配股", "銷量", "销量", "盈喜", "盈警", "分拆", "收購", "收购", "重組", "重组", "委任"]:
            if act in full_text:
                action = act
                break

        # 重要機構/人物標識（如果有）
        entity = ""
        for ent in ["何享健", "鄭家純", "郑家纯", "梁文青", "Hallgain", "Schroders", "Celestial", "小摩", "高盛", "大摩"]:
            if ent.lower() in full_text.lower():
                entity = ent.lower()
                break

        return f"{code}_{action}_{entity}".strip("_")

    def _deduplicate_by_event(self, items: List[Dict]) -> List[Dict]:
        """
        跨來源同事件去重：
        若焦點新聞與 7x24 同時報導同一事件，優先保留內文更詳盡的「智通焦點」
        """
        seen_events = {}
        unique_list = []

        for it in items:
            fp = self._generate_event_fingerprint(it)
            if fp not in seen_events:
                seen_events[fp] = it
                unique_list.append(it)
            else:
                existing = seen_events[fp]
                # 優先保留資訊密度較高的來源
                if it.get("source") == "智通焦點" and existing.get("source") != "智通焦點":
                    unique_list.remove(existing)
                    seen_events[fp] = it
                    unique_list.append(it)

        return unique_list

    def apply(self, raw_news: list[dict]) -> list[dict]:
        filtered = []
        for news in raw_news:
            title = news.get("title", "")
            content = news.get("content", "")
            text_to_check = f"{title} {content}"
            
            # 1. 港股與利好關鍵字快篩
            is_hk_related = any(kw in text_to_check for kw in HK_KEYWORDS) or bool(self._extract_stock_code(text_to_check))
            has_bullish_hint = any(kw in text_to_check for kw in BASIC_BULLISH)
            
            if is_hk_related and has_bullish_hint:
                filtered.append(news)

        initial_count = len(filtered)
        
        # 2. 執行同股同事件去重 (焦點新聞與 7x24 去重)
        deduped_news = self._deduplicate_by_event(filtered)
        
        print(f"🧹 本地初篩過濾完成：從 {len(raw_news)} 條提煉出 {initial_count} 條，經同事件去重後保留 {len(deduped_news)} 條高潛力新聞")
        return deduped_news
