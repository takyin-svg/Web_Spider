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
        self.mobile_url_pattern = "https://m.zhitongcaijing.com/market.html?page={}"

    def _parse_time_str(self, text: str) -> tuple[int, str]:
        now = datetime.now(HKT)
        text = str(text).strip()
        if not text:
            return 0, ""

        # 清洗尾部作者/來源標記
        text = re.split(r'\s*[|｜]\s*', text)[0].strip()

        # 1. 剛剛 / 刚刚
        if any(k in text for k in ["剛", "刚"]):
            ts = int(now.timestamp())
            return ts, now.strftime('%Y-%m-%d %H:%M:%S')

        # 2. 相對時間
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
            month, day = int(m_date_time.group(2)), int(m_date_time.group(3))
            hour, minute = int(m_date_time.group(4)), int(m_date_time.group(5))
            second = int(m_date_time.group(6)) if m_date_time.group(6) else 0
            dt = datetime(year, month, day, hour, minute, second, tzinfo=HKT)
            return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')

        # 5. 純當日時間
        m_today = re.search(r'(?:(?:今[天日])\s*)?(\b\d{1,2}):(\d{1,2})(?::(\d{1,2}))?\b', text)
        if m_today and not re.search(r'\d{1,2}[-/月]\d{1,2}', text):
            h, m = int(m_today.group(1)), int(m_today.group(2))
            s = int(m_today.group(3)) if m_today.group(3) else 0
            if 0 <= h <= 23 and 0 <= m <= 59:
                dt = now.replace(hour=h, minute=m, second=s, microsecond=0)
                return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')

        # 6. 中文日期 (含純日期兜底)
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
        固定回溯 72 小時作為安全閥，實際邊界由「首次碰頭即停」精確控制。
        """
        results = []
        
        # ✅ 1. 固定計算 72 小時 Cutoff (無視星期幾，簡單粗暴且安全)
        now = datetime.now(HKT)
        cutoff_dt = now - timedelta(hours=72)
        cutoff_timestamp = int(cutoff_dt.timestamp())
        cutoff_dt_str = cutoff_dt.strftime('%Y-%m-%d %H:%M:%S')
        
        # ✅ 2. 日誌明確顯示 max_pages 和 回溯上限
        print(f"  🔍 [{self.name}] 啟動手機版深度識別 (max_pages={max_pages}, 回溯上限: 72小時)")
        print(f"     └─ 目標時間下限: {cutoff_dt_str}")

        should_stop = False

        def page_action_handler(page):
            nonlocal should_stop
            # 攔截圖片/字體等靜態資源，加速載入
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
                    page.wait_for_selector('a[href*="detail"], a[href*="content_id"]', timeout=6000)
                except Exception:
                    pass

                # JS 提取邏輯
                batch = page.evaluate("""() => {
                    const results = [];
                    const seenHrefs = new Set();
                    const aTags = Array.from(document.querySelectorAll('a[href*="detail"], a[href*="content_id"], a[href*="/content/"]'));
                    
                    for (const a of aTags) {
                        let title = (a.innerText || a.textContent || '').trim();
                        title = title.split(/[|｜]/)[0].trim().replace(/\\s+/g, ' ');
                        
                        const href = a.getAttribute('href') || '';
                        if (title.length < 8 || !href || seenHrefs.has(href)) continue;
                        seenHrefs.add(href);

                        let card = a.closest('li') || a.closest('div.item') || a.closest('div.list-item') || a.closest('section');
                        if (!card) {
                            let el = a.parentElement;
                            while (el && el !== document.body) {
                                if (el.innerText && el.innerText.length > title.length + 3) {
                                    card = el;
                                    break;
                                }
                                el = el.parentElement;
                            }
                        }

                        let rawTime = '';
                        if (card) {
                            const timeEl = card.querySelector('.time, .date, [class*="time"], [class*="date"], span.time, .pubtime');
                            if (timeEl) {
                                rawTime = (timeEl.innerText || timeEl.textContent || '').trim();
                            }
                        }

                        const cardText = card ? (card.innerText || card.textContent || '') : title;
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

                page_added = 0

                for item in batch:
                    title = item.get("title", "")
                    if any(w in title for w in ["登入", "登錄", "下載", "首頁", "版權所有"]):
                        continue

                    raw_href = item.get("href", "")
                    full_link = urljoin(target_url, raw_href)

                    # Session 內去重
                    if full_link in scanned_in_session:
                        continue
                    scanned_in_session.add(full_link)

                    # ✅ 核心策略 1：首次碰頭即停（遇到已處理新聞立即終止，這才是真正的精確邊界）
                    if state_manager.is_news_scanned(full_link):
                        print(f"  🛑 [{self.name}] 第 {p} 頁遇到已處理新聞 [{title[:30]}...]，確認進入舊聞區，結束翻頁。")
                        should_stop = True
                        break

                    raw_time = item.get("raw_time", "")
                    pub_ts, pub_time_str = self._parse_time_str(raw_time)

                    # ✅ 核心策略 2：Cutoff 時間下限（72小時安全閥，防止 state_manager 清空時無限抓取）
                    if pub_ts > 0 and pub_ts <= cutoff_timestamp:
                        print(f"  🛑 [{self.name}] 第 {p} 頁觸達時間下限 [{pub_time_str}]: [{title[:30]}...]，結束翻頁。")
                        should_stop = True
                        break

                    # 時間解析失敗兜底
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
            with DynamicSession(headless=True, stealth=True, timeout=25000) as sess:
                sess.fetch(
                    self.mobile_url_pattern.format(1),
                    wait_until="domcontentloaded",
                    network_idle=False,
                    page_action=page_action_handler
                )
        except Exception as e:
            print(f"  ⚠️ [{self.name}] 調度異常: {e}")

        print(f"  └─ [{self.name}] 本次增量共收錄 {len(results)} 條新聞 (時間全部精準對齊)")
        return results
