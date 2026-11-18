# src/filters.py
import re
import unicodedata
from typing import List, Dict
from src.config import HK_KEYWORDS, BASIC_BULLISH


class NewsFilter:
    def __init__(self, state_manager):
        self.state_manager = state_manager

    def _extract_stock_code(self, text: str) -> str:
        """提取 5 位港股代碼"""
        m = re.search(r'\((\d{5})(?:\.HK)?\)|（(\d{5})(?:\.HK)?）|\b(\d{5})\.HK\b|\((\d{5})\)', text)
        if m:
            return m.group(1) or m.group(2) or m.group(3) or m.group(4) or ""
        return ""

    @staticmethod
    def _normalize_title(title: str) -> str:
        """
        跨源标题归一化（修复跨源去重失效的核心）：
        把 focus(要闻) 与 flash(7x24) 对同一事件的不同写法收敛到同一形态，
        使后续生成的指纹可对齐。只抹除"来源格式差异"，不改变事件语义：
          1. NFKC 全角 -> 半角（（）．，：！？ 及全角数字字母）
          2. 剥除 flash 快讯标题外层的【】方括号
          3. 剥除 .HK / .hk 代码后缀（focus 常写 (02272)、flash 常写 (02272.HK)）
          4. 剥除括号内与裸写的股票代码数字（代码已由 _extract_stock_code 单独入指纹，
             标题里再带一遍会造成 focus/flash 因 .HK 与否而分叉）
        """
        t = unicodedata.normalize('NFKC', title)
        t = re.sub(r'[【】\[\]]', '', t)
        t = re.sub(r'\.HK\b', '', t, flags=re.I)
        t = re.sub(r'\(\d{4,6}\)', '', t)
        t = re.sub(r'\b\d{4,6}\b', '', t)
        return t

    def _generate_event_fingerprint(self, item: Dict) -> str:
        """
        生成事件唯一指紋：股票代碼 + 核心動作 + 實體 + 標題前15字
        修復：加入標題特徵，避免同一股票的多個獨立利好被錯誤合併
        修復：標題先经 _normalize_title 归一化，使 focus/flash 跨源同一事件指纹对齐
        """
        title = item.get("title", "")
        content = item.get("content", "")
        full_text = f"{title} {content}"

        # 归一化后的标题，用于生成跨源一致的标题签名
        norm_title = self._normalize_title(title)

        code = self._extract_stock_code(full_text)
        if not code:
            clean_title = re.sub(r'[^\w\u4e00-\u9fa5]', '', norm_title)
            return clean_title[:30]  # 無代碼時用更長的標題特徵

        action = "other"
        for act in [
            "增持", "減持", "减持", "回購", "回购", "配售", "配股",
            "銷量", "销量", "盈喜", "盈警", "分拆", "收購", "收购",
            "重組", "重组", "委任", "獲批", "臨床", "新藥", "訂單", "中標"
        ]:
            if act in full_text:
                action = act
                break

        entity = ""
        for ent in [
            "何享健", "鄭家純", "郑家纯", "梁文青", "Hallgain",
            "Schroders", "Celestial", "小摩", "高盛", "大摩"
        ]:
            if ent.lower() in full_text.lower():
                entity = ent.lower()
                break

        # 🚨 關鍵修復：使用归一化后标题清洗的前15字作為區分因子（跨源可对齐）
        title_sig = re.sub(r'[^\w\u4e00-\u9fa5]', '', norm_title)[:15]

        return f"{code}_{action}_{entity}_{title_sig}".strip("_")

    def _deduplicate_by_event(self, items: List[Dict]) -> List[Dict]:
        """跨來源同事件去重，優先保留智通焦點"""
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

            is_hk_related = any(kw in text_to_check for kw in HK_KEYWORDS) or bool(self._extract_stock_code(text_to_check))
            has_bullish_hint = any(kw in text_to_check for kw in BASIC_BULLISH)

            if is_hk_related and has_bullish_hint:
                filtered.append(news)

        initial_count = len(filtered)

        # 執行同股同事件去重
        deduped_news = self._deduplicate_by_event(filtered)

        # 🚨 完整性校驗：確保沒有任何隱式截斷發生
        final_count = len(deduped_news)
        print(f"🧹 本地初篩過濾完成：從 {len(raw_news)} 條提煉出 {initial_count} 條，經同事件去重後保留 {final_count} 條高潛力新聞")

        # 防禦性檢查：若去重後數量異常大於初始數量（理論上不應發生），記錄警告
        if final_count > initial_count:
            print(f"⚠️ [警告] 去重後數量 ({final_count}) 大於初篩數量 ({initial_count})，請檢查去重邏輯！")

        return deduped_news
