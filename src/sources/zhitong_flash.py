# src/sources/zhitong_flash.py
import re
import hashlib
from datetime import datetime, timezone, timedelta
from scrapling.fetchers import StealthyFetcher
from src.config import ZHITONG_FLASH_URL, HKT
from src.sources.base import BaseSource

class ZhitongFlashSource(BaseSource):
    def __init__(self):
        super().__init__("智通7x24")

    def _split_title_and_content(self, raw_text: str) -> tuple[str, str]:
        """清洗按鈕噪聲並拆分快訊【標題】與正文"""
        text = raw_text.replace("编辑解读", "").replace("添加解读", "").replace("查看解读", "").strip()
        match = re.match(r'^(【.*?】)(.*)', text, re.DOTALL)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        return text[:45], text

    def _parse_time_str(self, text: str) -> tuple[int, str]:
        """
        支援 7x24 快訊全格式時間解析：
        1. 剛剛 /
