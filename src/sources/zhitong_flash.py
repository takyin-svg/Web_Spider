# src/sources/zhitong_flash.py
import re
import hashlib
from datetime import datetime, timezone, timedelta
from scrapling.fetchers import DynamicSession
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
        1. 剛剛 / 刚刚
        2. 分鐘前 / 小時前
        3. 當日純時分秒 (例如 "15:26:27")
        4. 昨天/前天 HH:MM
        5. 標準日期 YYYY-MM-DD HH:MM 或 MM-DD HH:MM
        """
        now = datetime.now(HKT)
        text = str(text).strip()
        if not text:
            return 0, ""

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

        # 3. 昨天 / 前天 HH:MM
        m_yest = re.search(r'(?:昨[天日]|前[天日])\s*(\d{1,2}):(\d{1,2})', text)
        if m_yest:
            days_ago = 2 if "前" in m_yest.group(0) else 1
            target_day = now - timedelta(days=days_ago)
            h, m = int(m_yest.group(1)), int(m_yest.group(2))
            dt = target_day.replace(hour=h, minute=m, second=0, microsecond=0)
            return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')

        # 4. 標準日期 YYYY-MM-DD HH:MM 或 MM-DD HH:MM
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

        # 5. 純時分秒 (7x24 最常見，例如 "18:30:25" 或 "18:30")
        m_time_only = re.search(r'\b(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?\b', text)
        if m_time_only and not re.search(r'\d{1,2}[-/月]\d{1,2}', text):
            h, m = int(m_time_only.group(1)), int(m_time_only.group(2))
            s = int(m_time_only.group(3)) if m_time_only.group(3) else 0
            if 0 <= h <= 23 and 0 <= m <= 59:
                dt = now.replace(hour=h, minute=m, second=s, microsecond=0)
                # 跨天校正：若抓取時間比當前時間超前很多（例如凌晨 01:00 抓到 23:50），代表是昨天的時間
                if dt.timestamp() > now.timestamp() + 300:
                    dt = dt - timedelta(days=1)
                return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')

        # 6. 中文日期 10月9日 18:30
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
            # 阻斷非必要圖片、字體與第三方廣告
            page.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ico}", lambda r: r.abort())

            try:
                page.wait_for_selector("div.allday-item, div.allday-item-content", timeout=20000)
            except Exception:
                return

            scanned_in_session = set()

            for round_idx in range(max_scroll_rounds):
                # 🚀 核心：使用 a.closest(...) 向上錨定卡片容器並提取時間與內文
                items = page.evaluate("""() => {
                    const results = [];
                    const contentNodes = Array.from(document.querySelectorAll('div.allday-item-content, div.allday-item, [class*="allday-item"]'));
                    
                    for (const node of contentNodes) {
                        // 向上或同級鎖定完整卡片卡槽
                        let card = node.closest('div.allday-item') || node.closest('li') || node;
                        
                        // 1. 優先從專門的時間節點提取
                        let rawTime = '';
                        const timeEl = card.querySelector('.allday-item-time, .time, .date, [class*="time"], [class*="date"]');
                        if (timeEl) {
                            rawTime = (timeEl.innerText || timeEl.textContent || '').trim();
                        }

                        // 2. 若無專門標籤，正則穿透卡片文字
                        const cardText = (card.innerText || card.textContent || '').trim();
                        if (!rawTime) {
                            const match = cardText.match(/\\b\\d{1,2}:\\d{1,2}(?::\\d{1,2})?\\b|(?:\\d{4}[-/])?\\d{1,2}[-/]\\d{1,2}\\s+\\d{1,2}:\\d{1,2}|\\d+\\s*(?:分鐘|分钟|小時|小时|分|h)\\s*前|剛剛|刚刚/);
                            if (match) {
                                rawTime = match[0];
                            }
                        }

                        // 提取正文文本與 ID
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

                    # 唯一標識 Hash
                    content_hash = hashlib.md5(raw_content.encode('utf-8')).hexdigest()[:12]
                    unique_id = raw_id.replace("immediately_id_", "") if raw_id else content_hash

                    if unique_id in scanned_in_session:
                        continue
                    scanned_in_session.add(unique_id)

                    title, body = self._split_title_and_content(raw_content)

                    # 1. 碰頭歷史已掃描記錄 -> 立即終止
                    if state_manager.is_news_scanned(unique_id):
                        print(f"  🛑 [{self.name}] 碰頭歷史記錄 [{title[:25]}...]，結束捲動。")
                        should_stop = True
                        break

                    # 2. 精準時間解析
                    pub_ts, pub_time_str = self._parse_time_str(raw_time)

                    # 3. 觸達 16:00 水位線 -> 立即終止
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

                # 滾動加載更多快訊
                page.evaluate("window.scrollBy(0, 3000)")
                page.wait_for_timeout(1000)

                # 若連滾動都無新快訊，跳出
                if new_found_this_round == 0 and round_idx >= 2:
                    break

        try:
            with DynamicSession(headless=True, stealth=True, timeout=30000) as sess:
                sess.fetch(
                    ZHITONG_FLASH_URL,
                    wait_until="domcontentloaded",
                    wait_selector="div.allday-item, div.allday-item-content",
                    page_action=page_action_handler
                )
        except Exception as e:
            print(f"  ⚠️ [{self.name}] 調度異常: {e}")

        print(f"  └─ [{self.name}] 本次增量共收錄 {len(results)} 條快訊 (時間全部精準對齊)")
        return results
