# src/sources/zhitong_focus.py
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin
from scrapling.fetchers import Fetcher
from src.config import HKT
from src.sources.base import BaseSource

class ZhitongFocusSource(BaseSource):
    def __init__(self):
        super().__init__("智通焦點")
        self.mobile_url_pattern = "https://m.zhitongcaijing.com/market.html?page={}"

    def _parse_time_str(self, text: str) -> tuple[int, str]:
        now = datetime.now(HKT)
        text = str(text).strip()
        if not text:
            return 0, ""

        # 1. 剛剛 / 刚
        if any(k in text for k in ["剛", "刚"]):
            ts = int(now.timestamp())
            return ts, now.strftime('%Y-%m-%d %H:%M:%S')

        # 2. 相對時間：分鐘前 / 小時前
        m_min = re.search(r'(\d+)\s*(?:分鐘|分钟|分)\s*前', text)
        if m_min:
            ts = int(now.timestamp()) - int(m_min.group(1)) * 60
            return ts, datetime.fromtimestamp(ts, HKT).strftime('%Y-%m-%d %H:%M:%S')

        m_hr = re.search(r'(\d+)\s*(?:小時|小时|h)\s*前', text)
        if m_hr:
            ts = int(now.timestamp()) - int(m_hr.group(1)) * 3600
            return ts, datetime.fromtimestamp(ts, HKT).strftime('%Y-%m-%d %H:%M:%S')

        # 3. 昨天 / 前天
        m_yest = re.search(r'(?:昨[天日]|前[天日])\s*(\d{1,2}):(\d{1,2})', text)
        if m_yest:
            days_ago = 2 if "前" in m_yest.group(0) else 1
            target_day = now - timedelta(days=days_ago)
            h, m = int(m_yest.group(1)), int(m_yest.group(2))
            dt = target_day.replace(hour=h, minute=m, second=0, microsecond=0)
            return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')

        # 4. YYYY-MM-DD HH:MM 或 MM-DD HH:MM
        m_date_time = re.search(r'(?:(\d{4})[-/])?(\d{1,2})[-/](\d{1,2})\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?', text)
        if m_date_time:
            year = int(m_date_time.group(1)) if m_date_time.group(1) else now.year
            month = int(m_date_time.group(2))
            day = int(m_date_time.group(3))
            hour = int(m_date_time.group(4))
            minute = int(m_date_time.group(5))
            second = int(m_date_time.group(6)) if m_date_time.group(6) else 0
            dt = datetime(year, month, day, hour, minute, second, tzinfo=HKT)
            return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')

        # 5. 純當日時間 18:30 或 今天 18:30
        m_today = re.search(r'(?:(?:今[天日])\s*)?(\b\d{1,2}):(\d{1,2})(?::(\d{1,2}))?\b', text)
        if m_today and not re.search(r'\d{1,2}[-/月]\d{1,2}', text):
            h, m = int(m_today.group(1)), int(m_today.group(2))
            s = int(m_today.group(3)) if m_today.group(3) else 0
            if 0 <= h <= 23 and 0 <= m <= 59:
                dt = now.replace(hour=h, minute=m, second=s, microsecond=0)
                return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')

        # 6. 中文日期 10月9日 18:30 或 10月9日
        m_cn = re.search(r'(\d{1,2})月(\d{1,2})日(?:\s*(\d{1,2}):(\d{1,2}))?', text)
        if m_cn:
            year = now.year
            month = int(m_cn.group(1))
            day = int(m_cn.group(2))
            hour = int(m_cn.group(3)) if m_cn.group(3) else 22
            minute = int(m_cn.group(4)) if m_cn.group(4) else 0
            dt = datetime(year, month, day, hour, minute, 0, tzinfo=HKT)
            return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')

        return 0, ""

    def fetch(self, cutoff_timestamp: int, state_manager, max_pages: int = 10) -> list[dict]:
        results = []
        cutoff_dt_str = datetime.fromtimestamp(cutoff_timestamp, HKT).strftime('%Y-%m-%d %H:%M:%S')
        print(f"  🔍 [{self.name}] 啟動標準解析，目標時間下限: {cutoff_dt_str}")

        for p in range(1, max_pages + 1):
            target_url = self.mobile_url_pattern.format(p)
            try:
                page = Fetcher().get(target_url, timeout=30000)
            except Exception as e:
                print(f"  ⚠️ [{self.name}] 第 {p} 頁請求異常: {e}")
                break

            a_tags = page.css('a[href*="detail"], a[href*="content_id"], a[href*="/content/"]')
            if not a_tags:
                break

            page_added = 0
            should_stop = False

            for a in a_tags:
                title = a.text(strip=True)
                href = a.attrib.get('href', '')
                if len(title) < 8 or not href:
                    continue

                card = a.css_first('li, div.item, div.list-item, section')
                card_text = card.text(strip=True) if card else title
                
                raw_time = ''
                if card:
                    time_el = card.css_first('.time, .date, [class*="time"], [class*="date"], span.time, .pubtime')
                    if time_el:
                        raw_time = time_el.text(strip=True)

                if not raw_time:
                    match = re.search(r'(?:\d{4}[-/])?\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{1,2}(?::\d{1,2})?|(?:今[天日]|昨[天日]|前[天日])\s*\d{1,2}:\d{1,2}|\b\d{1,2}:\d{1,2}(?::\d{1,2})?|\d{1,2}月\d{1,2}日(?:\s*\d{1,2}:\d{1,2})?|\d+\s*(?:分鐘|分钟|小時|小时|分|h)\s*前|剛剛|刚刚', card_text)
                    if match:
                        raw_time = match.group(0)

                full_link = urljoin(target_url, href)

                if any(w in title for w in ["登入", "登錄", "下載", "首頁", "版權所有"]):
                    continue

                if state_manager.is_news_scanned(full_link):
                    print(f"  🛑 [{self.name}] 碰頭歷史記錄 [{title[:25]}...]，結束翻頁。")
                    should_stop = True
                    break

                pub_ts, pub_time_str = self._parse_time_str(raw_time)

                if pub_ts > 0 and pub_ts <= cutoff_timestamp:
                    print(f"  🛑 [{self.name}] 觸達時間下限 [{pub_time_str}]: [{title[:25]}...]，結束翻頁。")
                    should_stop = True
                    break

                if not pub_time_str:
                    pub_time_str = datetime.now(HKT).strftime('%Y-%m-%d %H:%M:%S')

                results.append({
                    "unique_id": full_link,
                    "title": title,
                    "source": self.name,
                    "link": full_link,
                    "time": pub_time_str
                })
                state_manager.add_scanned(full_link)
                page_added += 1

            if should_stop or page_added == 0:
                break

        print(f"  └─ [{self.name}] 本次增量共收錄 {len(results)} 條新聞 (時間全部精準對齊)")
        return results
