# src/sources/zhitong_flash.py
import re
import hashlib
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright
from src.config import ZHITONG_FLASH_URL, HKT
from src.sources.base import BaseSource

class ZhitongFlashSource(BaseSource):
    def __init__(self):
        super().__init__("智通7x24")

    def _split_title_and_content(self, raw_text: str) -> tuple[str, str]:
        # 清理多餘的按鈕文字
        text = raw_text.replace("编辑解读", "").replace("添加解读", "").replace("查看解读", "").strip()
        # 嘗試提取 【標題】 格式
        match = re.match(r'^(【.*?】)(.*)', text, re.DOTALL)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        # 若無標準標題，取前 45 個字元作為標題
        return text[:45], text

    def _parse_time_str(self, text: str) -> tuple[int, str]:
        now = datetime.now(HKT)
        text = str(text).strip()
        if not text:
            return 0, ""

        # 1. 剛剛
        if any(k in text for k in ["剛", "刚"]):
            ts = int(now.timestamp())
            return ts, now.strftime('%Y-%m-%d %H:%M:%S')

        # 2. X 分鐘前
        m_min = re.search(r'(\d+)\s*(?:分鐘|分钟|分)\s*前', text)
        if m_min:
            ts = int(now.timestamp()) - int(m_min.group(1)) * 60
            return ts, datetime.fromtimestamp(ts, HKT).strftime('%Y-%m-%d %H:%M:%S')

        # 3. X 小時前
        m_hr = re.search(r'(\d+)\s*(?:小時|小时|h)\s*前', text)
        if m_hr:
            ts = int(now.timestamp()) - int(m_hr.group(1)) * 3600
            return ts, datetime.fromtimestamp(ts, HKT).strftime('%Y-%m-%d %H:%M:%S')

        # 4. 昨天 / 前天 HH:MM
        m_yest = re.search(r'(?:昨[天日]|前[天日])\s*(\d{1,2}):(\d{1,2})', text)
        if m_yest:
            days_ago = 2 if "前" in m_yest.group(0) else 1
            target_day = now - timedelta(days=days_ago)
            h, m = int(m_yest.group(1)), int(m_yest.group(2))
            dt = target_day.replace(hour=h, minute=m, second=0, microsecond=0)
            return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')

        # 5. 完整日期時間 YYYY-MM-DD HH:MM:SS 或 MM-DD HH:MM
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

        # 6. 純時間 HH:MM (需處理跨日邏輯)
        m_time_only = re.search(r'\b(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?\b', text)
        if m_time_only and not re.search(r'\d{1,2}[-/月]\d{1,2}', text):
            h, m = int(m_time_only.group(1)), int(m_time_only.group(2))
            s = int(m_time_only.group(3)) if m_time_only.group(3) else 0
            if 0 <= h <= 23 and 0 <= m <= 59:
                dt = now.replace(hour=h, minute=m, second=s, microsecond=0)
                # 若解析出的時間比當前時間晚超過 1 小時，說明是「昨天」的快讯
                if dt.timestamp() > now.timestamp() + 3600:
                    dt = dt - timedelta(days=1)
                return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')

        # 7. 中文日期 X月X日 HH:MM
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
            # 阻擋靜態資源，提升加載速度
            page.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ico}", lambda r: r.abort())

            try:
                page.goto(ZHITONG_FLASH_URL, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                print(f"  ⚠️ [{self.name}] 頁面開啟超時: {e}")
                return

            # 等待快讯列表加載 (適配智通財經實際 DOM)
            try:
                page.wait_for_selector("div.item, div.immediately-item, div.allday-item", timeout=20000)
            except Exception:
                print(f"  ⚠️ [{self.name}] 未找到快讯列表容器")
                return

            scanned_in_session = set()

            for round_idx in range(max_scroll_rounds):
                # JS 提取邏輯：適配多種可能的容器與時間標籤
                items = page.evaluate("""() => {
                    const results = [];
                    // 擴充選擇器以兼容智通財經不同版本的 DOM
                    const contentNodes = Array.from(document.querySelectorAll('div.item, div.immediately-item, div.allday-item, div[class*="immediately"]'));
                    
                    for (const node of contentNodes) {
                        let card = node;
                        
                        // 1. 提取時間
                        let rawTime = '';
                        const timeEl = card.querySelector('span.time, div.time, .allday-item-time, [class*="time"]');
                        if (timeEl) {
                            rawTime = (timeEl.innerText || timeEl.textContent || '').trim();
                        }

                        // 2. 提取正文內容
                        const contentEl = card.querySelector('.item-content, .allday-item-content, p') || card;
                        const contentText = (contentEl.innerText || contentEl.textContent || '').trim();
                        
                        // 3. 提取唯一 ID (若無則後續用 Hash)
                        const elementId = card.getAttribute('id') || card.getAttribute('data-id') || '';

                        // 過濾太短的無效內容
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

                    # 生成唯一 ID
                    content_hash = hashlib.md5(raw_content.encode('utf-8')).hexdigest()[:12]
                    unique_id = raw_id.replace("immediately_id_", "") if raw_id else content_hash

                    if unique_id in scanned_in_session:
                        continue
                    scanned_in_session.add(unique_id)

                    title, body = self._split_title_and_content(raw_content)

                    # 碰頭歷史記錄，停止滾動
                    if state_manager.is_news_scanned(unique_id):
                        print(f"  🛑 [{self.name}] 碰頭歷史記錄 [{title[:25]}...]，結束捲動。")
                        should_stop = True
                        break

                    pub_ts, pub_time_str = self._parse_time_str(raw_time)

                    # 觸達時間下限，停止滾動
                    if pub_ts > 0 and pub_ts <= cutoff_timestamp:
                        print(f"  🛑 [{self.name}] 觸達時間下限 [{pub_time_str}]: [{title[:25]}...]，結束捲動。")
                        should_stop = True
                        break

                    # 若時間解析失敗，fallback 到當前時間 (避免漏抓)
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

                # 模擬滾動加載下一頁
                page.evaluate("window.scrollBy(0, 3000)")
                page.wait_for_timeout(1200) # 稍微增加等待時間確保 DOM 渲染

                # 連續 3 輪沒有新數據則認為到底
                if new_found_this_round == 0 and round_idx >= 2:
                    break

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                               "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
                    viewport={"width": 390, "height": 844},
                    locale="zh-HK",
                    timezone_id="Asia/Hong_Kong",
                )
                page = context.new_page()
                page.set_default_timeout(30000)
                page_action_handler(page)
                context.close()
                browser.close()
        except Exception as e:
            print(f"  ⚠️ [{self.name}] 調度異常: {e}")

        print(f"  └─ [{self.name}] 本次增量共收錄 {len(results)} 條快訊")
        return results
