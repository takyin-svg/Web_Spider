# src/config.py
import os
import pytz

# --- 時區與憑證 ---
HKT = pytz.timezone("Asia/Hong_Kong")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")
HISTORY_FILE = "data/history.json"

# --- AI 設定 ---
AI_MODEL = "gemini-3.5-flash-lite"
MIN_SCORE = 88          
MIN_CONFIDENCE = 70      

# --- 港股關鍵字與基礎利好詞庫 ---
HK_KEYWORDS = [
    "港股", "恒指", "恒生指數", "國指", "科指", "南向資金", "北水", "聯交所", "香港交易所", "港交所",
    "增持", "回購", "派息", "股息", "盈喜", "收購", "中標", "配售", "配股", "港股通", ".HK"
]

BASIC_BULLISH = [
    "增持", "回購", "派息", "特別股息", "盈喜", "扭虧為盈", "淨利潤增長", "營收增長",
    "中標", "簽訂合同", "戰略合作", "突破", "獲批", "臨床", "授權", "出海", "收購",
    "重組", "資產注入", "私有化", "配售", "入選", "納入", "超預期", "上調評級", "目標價",
    "AI", "算力", "芯片", "機器人", "降本增效", "自研", "創新藥", "NMPA", "FDA", "ADC"
]

# 智通財經目標網址
ZHITONG_FOCUS_URL = "https://m.zhitongcaijing.com/market.html?page={}"
ZHITONG_FLASH_URL = "https://www.zhitongcaijing.com/immediately.html?type=ganggu"
