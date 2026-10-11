# src/sources/zhitong_focus.py
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin
from scrapling.fetchers import DynamicSession
from src.config import HKT
from src.sources.base import BaseSource


class ZhitongFocusSource(BaseSource):
    def __init__(self):
        super().__init__("智通焦點")
        self.mobile_url_pattern = "https://m.zhitongcaijing.com/focus.html?page={}"

    def _parse_time_str(self, text: str) -> tuple[int, str]:
        now = datetime.now(HKT)
        text = str(text).strip()
        if not text:
            return 0, ""

        text = re.split(r'\s*[|｜]\s*', text)[0].strip()

        if any(k in text for k in ["剛", "刚"]):
            ts = int(now.timestamp())
            return ts, now.strftime('%Y-%m-%d %H:%M:%S')

        m_min = re.search(r'(\d+)\s*(?:分鐘|分钟|分)\s*前', text)
        if m_min:
            ts = int(now.timestamp()) - int(m_min.group(1)) * 60
            return ts, datetime.fromtimestamp(ts, HKT).strftime('%Y-%m-%d %H:%M:%S')

        m_hr = re.search(r'(\d+)\s*(?:小時|小时|h)\s*前', text)
        if m_hr:
            ts = int(now.timestamp()) - int(m_hr.group(1)) * 3600
            return ts, datetime.fromtimestamp(ts, HKT).strftime('%Y-%m-%d %H:%M:%S')

        m_yest = re.search(r'(?:昨[天日]|前[天日])\s*(\d{1,2}):(\d{1,2})', text)
        if m_yest:
            days_ago = 2 if "前" in m_yest.group(0) else 1
            target_day = now - timedelta(days=days_ago)
            h, m = int(m_yest.group(1)), int(m_yest.group(2))
            dt = target_day.replace(hour=h, minute=m, second=0, microsecond=0)
            return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')

        m_date_time = re.search(r'(?:(\d{4})[-/])?(\d{1,2})[-/](\d{1,2})\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?', text)
        if m_date_time:
            year = int(m_date_time.group(1)) if m_date_time.group(1) else now.year
            month, day = int(m_date_time.group(2)), int(m_date_time.group(3))
            hour, minute = int(m_date_time.group(4)), int(m_date_time.group(5))
            second = int(m_date_time.group(6)) if m_date_time.group(6) else 0
            dt = datetime(year, month, day, hour, minute, second, tzinfo=HKT)
            return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')

        m_today = re.search(r'(?:(?:今[天日])\s*)?(\b\d{1,2}):(\d{1,2})(?::(\d{1,2}))?\b', text)
        if m_today and not re.search(r'\d{1,2}[-/月]\d{1,2}', text):
            h, m = int(m_today.group(1)), int(m_today.group(2))
            s = int(m_today.group(3)) if m_today.group(3) else 0
            if 0 <= h <= 23 and 0 <= m <= 59:
                dt = now.replace(hour=h, minute=m, second=s, microsecond=0)
                return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')

        m_cn = re.search(r'(\d{1,2})月(\d{1,2})日(?:\s*(\d{1,2}):(\d{1,2}))?', text)
        if m_cn:
            year = now.year
            month, day = int(m_cn.group(1)), int(m_cn.group(2))
            hour = int(m_cn.group(3)) if m_cn.group(3) else 22
            minute = int(m_cn.group(4)) if m_cn.group(4) else 0
            dt = datetime(year, month, day, hour, minute, 0, tzinfo=HKT)
            return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')

        return 0, ""

    def fetch(self, state_manager, max_pages: int = 12) -> list[dict]:
        """
        抓取智通焦點新聞。
        ✅ 修正：取消「碰頭即停」，改為整頁掃描 + 單條去重。
        僅在「整頁無新內容」或「觸達72h安全閥」時才停止翻頁。
        """
        if not isinstance(max_pages, int):
            print(f"  ⚠️ [{self.name}] max_pages 類型錯誤 ({type(max_pages).__name__})，已修正為 12")
            max_pages = 12

        results = []
        now = datetime.now(HKT)
        cutoff_dt = now - timedelta(hours=72)
        cutoff_timestamp = int(cutoff_dt.timestamp())
        cutoff_dt_str = cutoff_dt.strftime('%Y-%m-%d %H:%M:%S')

        print(f"  🔍 [{self.name}] 啟動手機版深度識別 (max_pages={max_pages}, 回溯上限: 72小時)")
        print(f"     └─ 目標時間下限: {cutoff_dt_str}")

        should_stop = False

        def page_action_handler(page):
            nonlocal should_stop
            page.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ico}", lambda r: r.abort())

            scanned_in_session = set()

            for p in range(1, max_pages + 1):
                target_url = self.mobile_url_pattern.format(p)

                if p > 1:
                    try:
                        page.goto(target_url, wait_until="domcontentloaded", timeout=18000)
                    except Exception as e:
                        print(f"  ⚠️ [{self.name}] 第 {p} 頁開啟超時: {e}")
                        break

                try:
                    page.wait_for_selector('a[href*="detail"], a[href*="content_id"], .news-item, .list-item', timeout=6000)
                except Exception:
                    pass

                batch = page.evaluate("""() => {
                    const results = [];
                    const seenHrefs = new Set();
                    const containers = Array.from(document.querySelectorAll('.news-item, .list-item, li'));
                    for (const card of containers) {
                        const a = card.querySelector('a[href*="detail"], a[href*="content_id"], a[href*="/content/"]');
                        if (!a) continue;
                        let title = (a.innerText || a.textContent || '').trim();
                        title = title.split(/[|｜]/)[0].trim().replace(/\\s+/g, ' ');
                        const href = a.getAttribute('href') || '';
                        if (title.length < 4 || !href || seenHrefs.has(href)) continue;
                        seenHrefs.add(href);
                        let rawTime = '';
                        const timeEl = card.querySelector('.time, .date, [class*="time"], [class*="date"], span.time, .pubtime');
                        if (timeEl) rawTime = (timeEl.innerText || timeEl.textContent || '').trim();
                        const cardText = card.innerText || card.textContent || '';
                        if (!rawTime) {
                            const match = cardText.match(/(?:\\d{4}[-/])?\\d{1,2}[-/]\\d{1,2}\\s+\\d{1,2}:\\d{1,2}(?::\\d{1,2})?|(?:今[天日]|昨[天日]|前[天日])\\s*\\d{1,2}:\\d{1,2}|\\b\\d{1,2}:\\d{1,2}(?::\\d{1,2})?\\b|\\d{1,2}月\\d{1,2}日(?:\\s*\\d{1,2}:\\d{1,2})?|\\d+\\s*(?:分鐘|分钟|小時|小时|分|h)\\s*前|剛剛|刚刚/);
                            if (match) rawTime = match[0];
                        }
                        results.push({ title, href, raw_time: rawTime });
                    }
                    return results;
                }""")

                if not batch:
                    print(f"  ℹ️ [{self.name}] 第 {p} 頁無內容，停止翻頁。")
                    break

                page_new_count = 0
                page_hit_cutoff = False

                # ✅ 核心修正：遍歷整頁所有条目，不因單條舊聞而中斷
                for item in batch:
                    title = item.get("title", "")
                    if any(w in title for w in ["登入", "登錄", "下載", "首頁", "版權所有"]):
                        continue

                    raw_href = item.get("href", "")
                    full_link = urljoin(target_url, raw_href)

                    if full_link in scanned_in_session:
                        continue
                    scanned_in_session.add(full_link)

                    raw_time = item.get("raw_time", "")
                    pub_ts, pub_time_str = self._parse_time_str(raw_time)

                    # ✅ 72h 安全閥：超過下限直接標記並跳過該條
                    if pub_ts > 0 and pub_ts <= cutoff_timestamp:
                        page_hit_cutoff = True
                        continue

                    if not pub_time_str:
                        pub_time_str = datetime.now(HKT).strftime('%Y-%m-%d %H:%M:%S')

                    # ✅ 單條去重：已處理的新聞跳過，但不中斷整頁掃描
                    if state_manager.is_news_scanned(full_link):
                        continue

                    results.append({
                        "unique_id": full_link,
                        "title": title,
                        "source": self.name,
                        "link": full_link,
                        "time": pub_time_str
                    })
                    state_manager.add_scanned(full_link)
                    page_new_count += 1

                # ✅ 停止條件：整頁都是舊聞（0條新增）且已觸達時間下限
                if page_new_count == 0 and page_hit_cutoff:
                    print(f"  🛑 [{self.name}] 第 {p} 頁全部為歷史內容且觸達72h下限，結束翻頁。")
                    should_stop = True
                    break

                # ✅ 如果整頁都是已處理過的新聞（非時間原因），也繼續往下翻
                # 因為網站可能穿插了新文章在後面幾頁
                if page_new_count == 0 and not page_hit_cutoff:
                    print(f"  ℹ️ [{self.name}] 第 {p} 頁全部為已處理新聞，繼續檢查下一頁...")

                print(f"  📄 [{self.name}] 第 {p} 頁掃描完成，本頁新增: {page_new_count} 條")

        try:
            with DynamicSession(headless=True, stealth=True, timeout=25000) as sess:
                sess.fetch(
                    self.mobile_url_pattern.format(1),
                    wait_until="domcontentloaded",
                    network_idle=False,
                    page_action=page_action_handler
                )
        except Exception as e:
            print(f"  ⚠️ [{self.name}] 調度異常: {e}")

        print(f"  └─ [{self.name}] 本次增量共收錄 {len(results)} 條新聞")
        return results
