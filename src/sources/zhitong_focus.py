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
        self.mobile_url_pattern = "https://m.zhitongcaijing.com/market.html?page={}"

    def _parse_time_str(self, text: str) -> tuple[int, str]:
        """
        支援智通手機版所有奇形怪狀的時間格式:
        1. 2026-10-09 18:30
        2. 10-09 18:30
        3. 10月9日 18:30 或 10月9日
        4. 15分鐘前 / 2小時前
        """
        now = datetime.now(HKT)
        text = str(text).strip()
        if not text:
            return 0, ""

        # 匹配 "2026-10-09 18:30" 或 "10-09 18:30"
        m1 = re.search(r'(?:(\d{4})-)?(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{1,2})', text)
        if m1:
            year = int(m1.group(1)) if m1.group(1) else now.year
            month = int(m1.group(2))
            day = int(m1.group(3))
            hour = int(m1.group(4))
            minute = int(m1.group(5))
            dt = datetime(year, month, day, hour, minute, 0, tzinfo=HKT)
            return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')

        # 匹配 "10月9日" 或 "10月09日 18:30"
        m2 = re.search(r'(\d{1,2})月(\d{1,2})日(?:\s+(\d{1,2}):(\d{1,2}))?', text)
        if m2:
            year = now.year
            month = int(m2.group(1))
            day = int(m2.group(2))
            hour = int(m2.group(3)) if m2.group(3) else 16  # 若只有日期無時分，預設為收市 16:00
            minute = int(m2.group(4)) if m2.group(4) else 0
            dt = datetime(year, month, day, hour, minute, 0, tzinfo=HKT)
            return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')

        # 匹配相對時間
        m_min = re.search(r'(\d+)\s*分鐘前', text)
        if m_min:
            ts = int(now.timestamp()) - int(m_min.group(1)) * 60
            return ts, datetime.fromtimestamp(ts, HKT).strftime('%Y-%m-%d %H:%M:%S')

        m_hr = re.search(r'(\d+)\s*小時前', text)
        if m_hr:
            ts = int(now.timestamp()) - int(m_hr.group(1)) * 3600
            return ts, datetime.fromtimestamp(ts, HKT).strftime('%Y-%m-%d %H:%M:%S')

        return 0, ""

    def fetch(self, cutoff_timestamp: int, state_manager, max_pages: int = 8) -> list[dict]:
        results = []
        cutoff_dt_str = datetime.fromtimestamp(cutoff_timestamp, HKT).strftime('%Y-%m-%d %H:%M:%S')
        print(f"  🔍 [{self.name}] 啟動手機版時間深度識別，目標時間下限: {cutoff_dt_str}")

        should_stop = False

        def page_action_handler(page):
            nonlocal should_stop
            # 阻斷多餘靜態資源，秒級加載
            page.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ico}", lambda route: route.abort())

            for p in range(1, max_pages + 1):
                target_url = self.mobile_url_pattern.format(p)
                try:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
                except Exception as e:
                    print(f"  ⚠️ [{self.name}] 第 {p} 頁開啟超時: {e}")
                    break

                # ⚡ JS 注入核心：利用正則穿透節點內所有純文字，把隱藏嘅時間提煉出嚟
                news_list = page.evaluate("""() => {
                    const items = [];
                    const nodes = document.querySelectorAll('div.list-item, div.item, a[href*="content_id"], a[href*="detail"]');
                    
                    nodes.forEach(el => {
                        const aTag = el.tagName === 'A' ? el : el.querySelector('a');
                        if (!aTag) return;
                        
                        const href = aTag.getAttribute('href') || '';
                        // 提取節點內所有文字
                        const fullText = el.innerText || '';
                        
                        // 正則匹配時間特徵 (例如 "10-09 18:30" 或 "10月9日")
                        const timeMatch = fullText.match(/(?:\\d{4}-)?\\d{1,2}-\\d{1,2}\\s+\\d{1,2}:\\d{1,2}|\\d{1,2}月\\d{1,2}日(?:\\s+\\d{1,2}:\\d{1,2})?|\\d+\\s*(?:分鐘|小時)前/);
                        const timeRaw = timeMatch ? timeMatch[0] : '';
                        
                        const title = (aTag.innerText || '').trim();

                        if (title.length >= 10 && href) {
                            items.push({
                                title: title,
                                href: href,
                                time_raw: timeRaw
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
                    title = re.sub(r'[\r\n\t]+', ' ', title).strip()
                    if any(w in title for w in ["登入", "登錄", "下載", "首頁", "版權所有"]):
                        continue

                    raw_href = item.get("href", "")
                    full_link = urljoin(target_url, raw_href)

                    # 1. 碰頭已掃描歷史紀錄 -> 即刻停止翻頁
                    if state_manager.is_news_scanned(full_link):
                        print(f"  🛑 [{self.name}] 碰頭歷史記錄 [{title[:25]}...]，結束翻頁。")
                        should_stop = True
                        break

                    # 2. 精確時間判定
                    time_raw = item.get("time_raw", "")
                    pub_ts, pub_time_str = self._parse_time_str(time_raw)

                    # 3. 核心：遇到早於 16:00 水位線 -> 立即終止翻頁
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

        try:
            with DynamicSession(headless=True, stealth=True, timeout=30000) as sess:
                sess.fetch(
                    "https://m.zhitongcaijing.com/market.html",
                    wait_until="domcontentloaded",
                    page_action=page_action_handler
                )
        except Exception as e:
            print(f"  ⚠️ [{self.name}] 調度異常: {e}")

        print(f"  └─ [{self.name}] 本次增量共收錄 {len(results)} 條新聞")
        return results
