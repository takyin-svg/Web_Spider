# src/sources/zhitong_focus.py
import re
from datetime import datetime
from urllib.parse import urljoin
from scrapling.fetchers import DynamicSession
from src.config import HKT
from src.sources.base import BaseSource

class ZhitongFocusSource(BaseSource):
    def __init__(self):
        super().__init__("智通焦點")
        # 手機版港股/焦點頻道分頁 URL
        self.mobile_url_pattern = "https://m.zhitongcaijing.com/market.html?page={}"

    def _parse_time_str(self, text: str) -> tuple[int, str]:
        now = datetime.now(HKT)
        text = str(text).strip()
        if not text:
            return 0, ""

        # 匹配 "2026-10-09 20:46" 或 "10-09 20:46"
        m = re.search(r'(?:(\d{4})-)?(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{1,2})', text)
        if m:
            year = int(m.group(1)) if m.group(1) else now.year
            month = int(m.group(2))
            day = int(m.group(3))
            hour = int(m.group(4))
            minute = int(m.group(5))
            dt = datetime(year, month, day, hour, minute, 0, tzinfo=HKT)
            return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')

        # 匹配 "XX分鐘前" / "XX小時前"
        m_min = re.search(r'(\d+)\s*分鐘前', text)
        if m_min:
            ts = int(now.timestamp()) - int(m_min.group(1)) * 60
            return ts, datetime.fromtimestamp(ts, HKT).strftime('%Y-%m-%d %H:%M:%S')

        m_hr = re.search(r'(\d+)\s*小時前', text)
        if m_hr:
            ts = int(now.timestamp()) - int(m_hr.group(1)) * 3600
            return ts, datetime.fromtimestamp(ts, HKT).strftime('%Y-%m-%d %H:%M:%S')

        return 0, ""

    def fetch(self, cutoff_timestamp: int, state_manager, max_pages: int = 5) -> list[dict]:
        results = []
        cutoff_dt_str = datetime.fromtimestamp(cutoff_timestamp, HKT).strftime('%Y-%m-%d %H:%M:%S')
        print(f"  🔍 [{self.name}] 啟動手機版極速通道，目標時間下限: {cutoff_dt_str}")

        should_stop = False

        def page_action_handler(page):
            nonlocal should_stop
            # 🚀 關鍵優化：徹底攔截圖片、字體與第三方廣告，杜絕 Timeout 45秒！
            page.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ico}", lambda route: route.abort())

            for p in range(1, max_pages + 1):
                target_url = self.mobile_url_pattern.format(p)
                try:
                    # 採用 commit 或 domcontentloaded，骨架一到即刻開始讀取
                    page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
                except Exception as e:
                    print(f"  ⚠️ [{self.name}] 第 {p} 頁打開超時，提前結束: {e}")
                    break

                # 提取手機版新聞列表條目（支援多種常見手機版標籤）
                news_list = page.evaluate("""() => {
                    const items = [];
                    // 尋找列表節點
                    const nodes = document.querySelectorAll('div.list-item, div.news-list a, div.item, a[href*="content_id"], a[href*="detail"]');
                    
                    nodes.forEach(el => {
                        const aTag = el.tagName === 'A' ? el : el.querySelector('a');
                        if (!aTag) return;
                        
                        const href = aTag.getAttribute('href') || '';
                        const title = (aTag.innerText || el.innerText || '').trim();
                        
                        // 尋找條目內包含的時間文字
                        let timeText = '';
                        const timeEl = el.querySelector('.time, .date, span, p');
                        if (timeEl) {
                            timeText = timeEl.innerText.trim();
                        }

                        if (title.length >= 12 && href) {
                            items.push({
                                title: title,
                                href: href,
                                time_raw: timeText
                            });
                        }
                    });
                    return items;
                }""")

                if not news_list:
                    break

                page_added = 0
                for item in news_list:
                    title = item.get("title", "").strip()
                    # 清洗換行符與導航雜訊
                    title = re.sub(r'[\r\n\t]+', ' ', title).strip()
                    if any(w in title for w in ["登入", "登錄", "下載", "首頁", "版權所有", "用戶協議"]):
                        continue

                    raw_href = item.get("href", "")
                    full_link = urljoin(target_url, raw_href)

                    # 1. 碰頭已掃描歷史記錄 -> 立即終止
                    if state_manager.is_news_scanned(full_link):
                        print(f"  🛑 [{self.name}] 碰頭歷史記錄 [{title[:25]}...]，結束翻頁。")
                        should_stop = True
                        break

                    # 2. 提取時間
                    time_raw = item.get("time_raw", "")
                    pub_ts, pub_time_str = self._parse_time_str(time_raw)

                    # 3. 觸達 16:00 水位線 -> 立即終止
                    if pub_ts > 0 and pub_ts <= cutoff_timestamp:
                        print(f"  🛑 [{self.name}] 觸達時間下限 [{pub_time_str}]: [{title[:25]}...]，結束翻頁。")
                        should_stop = True
                        break

                    # 若列表未提取到時間，則補上當前香港時間
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

        try:
            with DynamicSession(headless=True, stealth=True, timeout=30000) as sess:
                # 入口頁極速載入
                sess.fetch(
                    "https://m.zhitongcaijing.com/market.html",
                    wait_until="domcontentloaded",
                    page_action=page_action_handler
                )
        except Exception as e:
            print(f"  ⚠️ [{self.name}] 調度異常: {e}")

        print(f"  └─ [{self.name}] 本次增量共收錄 {len(results)} 條新聞")
        return results
