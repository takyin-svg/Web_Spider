# src/sources/zhitong_focus.py 
import time
import re
from datetime import datetime, timedelta
from scrapling.fetchers import DynamicSession

from src.base_source import BaseSource


class ZhitongFocusSource(BaseSource):
    def __init__(self, state_manager):
        super().__init__(state_manager)
        self.name = "智通焦點"
        self.base_url = "https://www.zhitongcaijing.com/content/market.html?page={}"
        self.fetcher = DynamicSession(
            headless=True,
            disable_images=True,
        )

    def _parse_time_str(self, time_str):
        """
        解析智通財經的時間字符串，支援多種格式：
        - "剛剛" / "刚才"
        - "X分鐘前" / "X小時前"
        - "昨天 HH:MM" / "前天 HH:MM"
        - "YYYY-MM-DD HH:MM" / "MM-DD HH:MM"
        - "今天 HH:MM" / 純時間 "HH:MM"
        - "X月X日 HH:MM" / "X月X日"
        """
        if not time_str:
            return None

        time_str = time_str.strip()
        now = datetime.now()

        # 1. "剛剛" / "刚才"
        if time_str in ("剛剛", "刚才"):
            return int(now.timestamp())

        # 2. "X分鐘前" / "X小時前"
        m = re.match(r'(\d+)\s*分鐘前', time_str)
        if m:
            return int((now - timedelta(minutes=int(m.group(1)))).timestamp())
        m = re.match(r'(\d+)\s*小時前', time_str)
        if m:
            return int((now - timedelta(hours=int(m.group(1)))).timestamp())

        # 3. "昨天/前天 HH:MM"
        m = re.match(r'(昨天|前天)\s+(\d{1,2}:\d{2})', time_str)
        if m:
            days_ago = 1 if m.group(1) == '昨天' else 2
            target_date = now - timedelta(days=days_ago)
            dt = datetime.strptime(f"{target_date.strftime('%Y-%m-%d')} {m.group(2)}", "%Y-%m-%d %H:%M")
            return int(dt.timestamp())

        # 4. "YYYY-MM-DD HH:MM" 或 "MM-DD HH:MM"
        m = re.match(r'(\d{2,4}-\d{1,2}-\d{1,2})\s+(\d{1,2}:\d{2})', time_str)
        if m:
            date_part = m.group(1)
            if len(date_part.split('-')[0]) == 2:
                date_part = f"{now.year}-{date_part}"
            dt = datetime.strptime(f"{date_part} {m.group(2)}", "%Y-%m-%d %H:%M")
            return int(dt.timestamp())

        # 5. "今天 HH:MM" 或 純時間 "HH:MM"
        m = re.match(r'(?:今天\s+)?(\d{1,2}:\d{2})', time_str)
        if m:
            dt = datetime.strptime(f"{now.strftime('%Y-%m-%d')} {m.group(1)}", "%Y-%m-%d %H:%M")
            return int(dt.timestamp())

        # 6. "X月X日 HH:MM" 或 "X月X日"
        m = re.match(r'(\d{1,2})月(\d{1,2})日(?:\s+(\d{1,2}:\d{2}))?', time_str)
        if m:
            month, day = int(m.group(1)), int(m.group(2))
            time_part = m.group(3) if m.group(3) else "00:00"
            dt = datetime.strptime(f"{now.year}-{month:02d}-{day:02d} {time_part}", "%Y-%m-%d %H:%M")
            return int(dt.timestamp())

        return None

    def fetch(self, cutoff_ts, existing_links):
        results = []
        should_stop = False

        # 🔧 修復：新增連續空頁計數器，防止因單頁無新消息而過早終止翻頁
        consecutive_empty_pages = 0
        MAX_CONSECUTIVE_EMPTY_PAGES = 2

        for page in range(1, 9):  # 最多爬取8頁
            url = self.base_url.format(page)
            print(f"  🌐 [{self.name}] 正在爬取第 {page} 頁: {url}")

            try:
                response = self.fetcher.get(url, timeout=30)

                # 透過 JS 提取新聞卡片數據
                js_script = """
                return Array.from(document.querySelectorAll('.news-list li, .news_item, .list_item')).map(item => {
                    const a = item.querySelector('a');
                    const timeEl = item.querySelector('.time, .date, .news-time');
                    return {
                        title: a ? a.innerText.trim() : '',
                        link: a ? a.getAttribute('href') : '',
                        time: timeEl ? timeEl.innerText.trim() : ''
                    };
                }).filter(item => item.title && item.link);
                """
                cards = response.js.execute(js_script)

                if not cards:
                    print(f"  ⚠️ [{self.name}] 第 {page} 頁未找到新聞卡片。")
                    break

                page_added = 0
                for card in cards:
                    title = card.get('title', '').strip()
                    link = card.get('link', '').strip()
                    pub_time_str = card.get('time', '').strip()

                    if not title or not link:
                        continue

                    full_link = link if link.startswith('http') else f"https://www.zhitongcaijing.com{link}"

                    # 🔧 修復：碰頭已掃描歷史記錄 -> 跳過該條，繼續檢查同頁剩餘新聞 (原為 break)
                    if self.state_manager.is_news_scanned(full_link):
                        print(f"  ⏭️ [{self.name}] 跳過已掃描記錄 [{title[:25]}...]")
                        continue  # 原為: should_stop = True; break

                    # 解析時間
                    pub_ts = self._parse_time_str(pub_time_str)
                    if pub_ts is None:
                        print(f"  ⚠️ [{self.name}] 無法解析時間: '{pub_time_str}'，標題: {title[:20]}...")
                        pub_ts = time.time()

                    # 觸達時間下限 -> 終止
                    if pub_ts <= cutoff_ts:
                        print(f"  ⏱️ [{self.name}] 觸達時間下限 [{title[:25]}...]，結束翻頁。")
                        should_stop = True
                        break

                    # 加入結果並標記為已掃描
                    results.append({
                        'title': title,
                        'link': full_link,
                        'timestamp': pub_ts,
                        'source': self.name
                    })
                    self.state_manager.mark_news_scanned(full_link)
                    page_added += 1

                # 🔧 修復：翻頁終止條件邏輯重構
                if should_stop:
                    break

                if page_added == 0:
                    consecutive_empty_pages += 1
                    if consecutive_empty_pages >= MAX_CONSECUTIVE_EMPTY_PAGES:
                        print(f"  ⚠️ [{self.name}] 連續 {MAX_CONSECUTIVE_EMPTY_PAGES} 頁無新消息，結束翻頁。")
                        break
                else:
                    consecutive_empty_pages = 0  # 有新消息則重置計數

            except Exception as e:
                print(f"  ❌ [{self.name}] 爬取第 {page} 頁出錯: {e}")
                break

        return results
