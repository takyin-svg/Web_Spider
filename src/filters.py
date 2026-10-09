from src.config import HK_KEYWORDS, BASIC_BULLISH

class NewsFilter:
    def __init__(self, state_manager):
        # 保留這個接收參數，以免 orchestrator.py 初始化時報錯
        # 但我們在此類別中不再讓它去讀寫記憶體
        self.state_manager = state_manager

    def apply(self, raw_news: list[dict]) -> list[dict]:
        filtered = []
        for news in raw_news:
            title = news.get("title", "")
            
            # 🚨 內鬼已拔除：刪除了 URL 去重與 add_record 的邏輯
            # 這些動作已經在 orchestrator.py 裡面完美處理了
            
            # 關鍵字初篩 (剔除無關/垃圾新聞)
            is_hk_related = any(kw in title for kw in HK_KEYWORDS)
            has_bullish_hint = any(kw in title for kw in BASIC_BULLISH)
            
            if is_hk_related and has_bullish_hint:
                filtered.append(news)
                
        print(f"🧹 本地初篩過濾完成：從 {len(raw_news)} 條提煉出 {len(filtered)} 條高潛力新聞")
        return filtered
