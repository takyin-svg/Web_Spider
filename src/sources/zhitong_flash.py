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
        text = raw_text.replace("编辑解读", "").replace("添加解读", "").replace("查看解读", "").strip()
        match = re.match(r'^(【.*?】)(.*)', text, re.DOTALL)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        return text[:45], text

    def _parse_time_str(self, text: str) -> tuple[int, str]:
        now = datetime.now(HKT)
        text = str(text).strip()
        if not text:
            return 0, ""

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
            month = int(m_date_time.group(2))
            day = int(m_date_time.group(3))
            hour = int(m_date_time.group(4))
            minute = int(m_date_time.group(5))
            second = int(m_date_time.group(6)) if m_date_time.group(6) else 0
            dt = datetime(year, month, day, hour, minute, second, tzinfo=HKT)
            return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')

        m_time_only = re.search(r'\b(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?\b', text)
        if m_time_only and not re.search(r'\d{1,2}[-/月]\d{1,2}', text):
            h, m = int(m_time_only.group(1)), int(m_time_only.group(2))
            s = int(m_time_only.group(3)) if m_time_only.group(3) else 0
            if 0 <= h <= 23 and 0 <= m <= 59:
                dt = now.replace(hour=h, minute=m, second=s, microsecond=0)
                if dt.timestamp() > now.timestamp() + 300:
                    dt = dt - timedelta(days=1)
                return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')

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

    def fetch(self, cutoff_timestamp: int, state_manager, max_scroll_rounds: int = 10) -> list[dict]:
        results = []
        cutoff_dt_str = datetime.fromtimestamp(cutoff_timestamp, HKT).strftime('%Y-%m-%d %H:%M:%S')
        print(f"  🔍 [{self.name}] 啟動卡片錨定識別，目標時間下限: {cutoff_dt_str}")

        should_stop = False

        def page_action_handler(page):
            nonlocal should_stop
            page.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ico}", lambda r: r.abort())

            try:
                page.wait_for_selector("div.allday-item, div.allday-item-content", timeout=20000)
            except Exception:
                return

            scanned_in_session = set()

            for round_idx in range(max_scroll_rounds):
                items = page.evaluate("""() => {
                    const results = [];
                    const contentNodes = Array.from(document.querySelectorAll('div.allday-item-content, div.allday-item, [class*="allday-item"]'));
                    
                    for (const node of contentNodes) {
                        let card = node.closest('div.allday-item') || node.closest('li') || node;
                        
                        let rawTime = '';
                        const timeEl = card.querySelector('.allday-item-time, .time, .date, [class*="time"], [class*="date"]');
                        if (timeEl) {
                            rawTime = (timeEl.innerText || timeEl.textContent || '').trim();
                        }

                        const cardText = (card.innerText || card.textContent || '').trim();
                        if (!rawTime) {
                            const match = cardText.match(/\\b\\d{1,2}:\\d{1,2}(?::\\d{1,2})?\\b|(?:\\d{4}[-/])?\\d{1,2}[-/]\\d{1,2}\\s+\\d{1,2}:\\d{1,2}|\\d+\\s*(?:分鐘|分钟|小時|小时|分|h)\\s*前|剛剛|刚刚/);
                            if (match) {
                                rawTime = match[0];
                            }
                        }

                        const contentEl = card.querySelector('.allday-item-content') || card;
                        const contentText = (contentEl.innerText || contentEl.textContent || '').trim();
                        const elementId = card.getAttribute('id') || card.getAttribute('data-id') || '';

                        if (contentText.length >= 10) {
                            results.push({
                                raw_id: elementId,
                                content: contentText,
                                raw_time: rawTime
                            });
                        }
                    }
                    return results;
                }""")

                if not items:
                    break

                new_found_this_round = 0
                for it in items:
                    raw_content = it.get("content", "")
                    raw_time = it.get("raw_time", "")
                    raw_id = it.get("raw_id", "")

                    content_hash = hashlib.md5(raw_content.encode('utf-8')).hexdigest()[:12]
                    unique_id = raw_id.replace("immediately_id_", "") if raw_id else content_hash

                    if unique_id in scanned_in_session:
                        continue
                    scanned_in_session.add(unique_id)

                    title, body = self._split_title_and_content(raw_content)

                    if state_manager.is_news_scanned(unique_id):
                        print(f"  🛑 [{self.name}] 碰頭歷史記錄 [{title[:25]}...]，結束捲動。")
                        should_stop = True
                        break

                    pub_ts, pub_time_str = self._parse_time_str(raw_time)

                    if pub_ts > 0 and pub_ts <= cutoff_timestamp:
                        print(f"  🛑 [{self.name}] 觸達時間下限 [{pub_time_str}]: [{title[:25]}...]，結束捲動。")
                        should_stop = True
                        break

                    if not pub_time_str:
                        pub_time_str = datetime.now(HKT).strftime('%Y-%m-%d %H:%M:%S')

                    results.append({
                        "unique_id": unique_id,
                        "title": title,
                        "content": body,
                        "source": self.name,
                        "link": f"{ZHITONG_FLASH_URL}#flash_{unique_id}",
                        "time": pub_time_str
                    })
                    state_manager.add_scanned(unique_id)
                    new_found_this_round += 1

                if should_stop:
                    break

                page.evaluate("window.scrollBy(0, 3000)")
                page.wait_for_timeout(1000)

                if new_found_this_round == 0 and round_idx >= 2:
                    break

        try:
            fetcher = StealthyFetcher(headless=True, stealth=True, timeout=30000)
            fetcher.fetch(
                ZHITONG_FLASH_URL,
                wait_until="domcontentloaded",
                page_action=page_action_handler
            )
        except Exception as e:
            print(f"  ⚠️ [{self.name}] 調度異常: {e}")

        print(f"  └─ [{self.name}] 本次增量共收錄 {len(results)} 條快訊 (時間全部精準對齊)")
        return results
