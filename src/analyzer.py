# src/analyzer.py
import json
import time
from google import genai
from google.genai import types
from src.config import GEMINI_API_KEY, AI_MODEL, MIN_SCORE, MIN_CONFIDENCE

class GeminiAnalyzer:
    def __init__(self):
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY 未設定")
        self.client = genai.Client(api_key=GEMINI_API_KEY)

    def analyze(self, news_list: list[dict]) -> list[dict] | None:
        if not news_list:
            return []

        # 動態將 MIN_SCORE 注入提示詞規則中
        prompt = f"""
        你是港股事件驅動對沖基金經理，專門從新聞中篩選未來48小時-2周可能引發股價大幅上漲的重大催化劑。
        
        【判斷標準】
        1. 事件已正式落實，實質改變公司盈利或估值。
        2. 評分 (score): 95-100(極重大)；{MIN_SCORE}-94(明確重大利好)；<{MIN_SCORE}(忽略，設為 is_major_bullish: false)。
        3. 置信度 (confidence): 95=官方；90=權威媒體；60=市場消息。
        4. 實體補全 (Entity Resolution): 
           - 若新聞僅提及公司名稱，請補齊對應的港股代號 (stock_code，如 00700.HK)。
           - 若新聞僅提及代號，請補齊其個股中文名稱 (stock_name)。
           - 若為大盤宏觀、行業泛指且無具體受惠個股，請在代號與名稱皆填「無」。

        【🚨 硬性排除項 (Kill List)】
        遇到以下情況直接淘汰，給予低分(<{MIN_SCORE})並標記 is_major_bullish: false：
        - 新聞標題或描述含有「股價拉升、狂飆、異動、急升、漲超、漲逾、漲近、尾盤走高」等字眼 (代表盤中已被消化，失去提前埋伏價值)。
        - 超出指定時間範圍嘅舊聞、盤中已完全消化嘅消息。
        - 缺乏實質數字支持嘅常規合作、輕微業績增長（<25%）。
        - 老千股炒作、股吧/論壇傳聞。
        - 泛泛行業分析或宏觀評論（缺乏明確受惠個股）。
        - 純粹的股價預測、成交額統計、技術指標分析、買入建議。
        
        返回嚴格的 JSON 陣列格式，必須包含 time 欄位並原樣保留爬蟲抓取的時間：
        [
          {{
            "title": "原新聞標題", 
            "url": "原新聞連結", 
            "time": "原新聞發布時間",
            "source": "原來源",
            "stock_code": "代號或無", 
            "stock_name": "個股中文或無", 
            "core_event": "核心事件限10字",
            "is_major_bullish": true/false, 
            "score": {MIN_SCORE}, 
            "confidence": 90,
            "category": "利好分類", 
            "urgency": "Immediate/1-3 Days/Long Term",
            "reason": "80字以內點評，若觸發硬性排除項請明確說明原因", 
            "risk_warning": "風險提示(若有暗藏利空以🔴開頭，若無填'未見明顯利空')"
          }}
        ]
        """
        
        print(f"🧠 開始將批次新聞送入 Gemini ({AI_MODEL}) 進行深度分析與硬性排除過濾...")
        analyzed_results = []
        
        max_retries = 3
        wait_times = [10, 30, 60]
        
        for attempt in range(1, max_retries + 1):
            try:
                res = self.client.models.generate_content(
                    model=AI_MODEL,
                    contents=prompt + "\n資料:\n" + json.dumps(news_list, ensure_ascii=False),
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1 
                    )
                )
                
                batch_results = json.loads(res.text)
                
                for item in batch_results:
                    title_preview = item.get("title", "")[:20].replace("\n", "")
                    stock_code = item.get("stock_code", "")
                    stock_name = item.get("stock_name", "")
                    is_bullish = item.get("is_major_bullish", False)
                    score = item.get("score", 0)
                    confidence = item.get("confidence", 0)
                    reason = item.get("reason", "無詳細理由")
                    
                    is_macro_news = (stock_code in ["", "無", "None"]) and (stock_name in ["", "無", "None"])
                    display_name = f"{stock_name}({stock_code})" if not is_macro_news else "宏觀無個股"
                    
                    # 統一使用 MIN_SCORE 與 MIN_CONFIDENCE 動態判定
                    if is_macro_news or not is_bullish or score < MIN_SCORE or confidence < MIN_CONFIDENCE:
                        print(f"  ❌ [淘汰] {display_name} | 評分: {score} (置信度: {confidence}) | {title_preview}... | 原因: {reason}")
                    else:
                        print(f"  ✅ [達標] {display_name} | 評分: {score} (置信度: {confidence}) | {title_preview}... | 理由: {reason}")
                        analyzed_results.append(item)
                        
                return analyzed_results
                
            except Exception as e:
                error_msg = str(e)
                print(f"  ⚠️ [第 {attempt} 次嘗試失敗] AI 解析或連線異常: {error_msg}")
                
                if attempt < max_retries:
                    wait_time = wait_times[attempt - 1]
                    print(f"  ⏳ 系統將於 {wait_time} 秒後要求 AI 重新分析...")
                    time.sleep(wait_time)
                else:
                    print(f"  ❌ 連續 {max_retries} 次分析失敗，觸發安全回滾機制，留待下輪處理。")
                    return None
                    
        return None
