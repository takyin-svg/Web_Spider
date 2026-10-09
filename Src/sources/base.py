# src/sources/base.py
from abc import ABC, abstractmethod

class BaseSource(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def fetch(self, cutoff_timestamp: int, state_manager) -> list[dict]:
        """
        所有新聞模組必須統一返回格式:
        [
          {
            "unique_id": str/int,      # 唯一鍵 (URL 或 immediately_id)
            "title": str,              # 新聞內文或標題
            "source": str,             # 來源名稱
            "link": str,               # 鏈接 (若無則為錨點或空)
            "time": int/str            # 時間戳或時間字串
          },
          ...
        ]
        """
        pass
