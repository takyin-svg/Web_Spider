# src/sources/zhitong_focus.py
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse, parse_qs
from scrapling.fetchers import DynamicSession
from src.config import HKT
from src.sources.base import BaseSource


class ZhitongFocusSource(BaseSource):
    def __init__(self):
        super().__init__("智通焦點")
        self.mobile_url_pattern = "https://m.zhitongcaijing.com/market.html?page={}"

    def _normalize_url(self, url: str) -> str:
        """
        ✅ 核心修復：URL 標準化
        強制將不同格式的文章連結統一轉換為 share.html?content_id=XXX 格式
        確保 state_manager.is_news_scanned() 能正確比對
        """
        if not url:
            return ""
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if 'content_id' in qs:
            return f"https://m.zhitongcaijing.com/article/share.html?content_id={qs['content_id'][0]}"
        match = re.search(r'/content/detail/(\d+)', url)
        if match:
            return f"https://m.zhitongcaijing.com/article/share.html?content_id={match.group(1)}"
        match = re.search(r'content_id=(\d+)', url) or re.search(r'/(\d{6,})(?:\.html)?$', url)
        if match:
            return f"https://m.zhitongcaijing.com/article/share.html?content_id={match.group(1)}"
        return url

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
            month = int(m_cn.group(1))
            day = int(m_cn.group(2))
            hour = int(m_cn.group(3)) if m_cn.group(3) else 22
            minute = int(m_cn.group(4)) if m_cn.group(4) else 0
            dt = datetime(year, month, day, hour, minute, 0, tzinfo=HKT)
            return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')

        return 0, ""

    def fetch(self, cutoff_timestamp: int, state_manager, max_pages: int = 8) -> list[dict]:
        results = []
        cutoff_dt_str = datetime.fromtimestamp(cutoff_timestamp, HKT).strftime('%Y-%m-%d %H:%M:%S')
        print(f"  🔍 [{self.name}] 啟動手機版深度識別，目標時間下限: {cutoff_dt_str}")

        should_stop = False

        def page_action_handler(page):
            nonlocal should_stop
            
            # ✅ 嚴格遵循文檔第4點：資源阻斷必須在最開頭執行
            page.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ico}", lambda r: r.abort())

            for p in range(1, max_pages + 1):
                target_url = self.mobile_url_pattern.format(p)

                if p > 1:
                    try:
                        # ✅ 嚴格使用 domcontentloaded，不等待外部腳本
                        page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                    except Exception as e:
                        print(f"  ⚠️ [{self.name}] 第 {p} 頁開啟超時: {e}")
                        break

                # ✅ 等待新聞列表容器出現（多層級容錯）
                selectors_to_try = [
                    '.info-list-item',
                    '[class*="list-item"]',
                    'a[href*="/content/detail/"]',
                ]
                waited = False
                for sel in selectors_to_try:
                    try:
                        page.wait_for_selector(sel, timeout=5000)
                        waited = True
                        break
                    except Exception:
                        continue
                
                if not waited:
                    print(f"  💤 [{self.name}] 第 {p} 頁未找到新聞容器，當前沒有最新消息更新。")
                    break

                # ✅ 核心演算法：卡片錨定溯源 (.closest() + 父節點穿透)
                # 完全對齊文檔第1點的技術亮點
                batch = page.evaluate("""() => {
                    const results = [];
                    const seenHrefs = new Set();
                    
                    // 先定位所有文章連結
                    const aTags = Array.from(document.querySelectorAll('a[href*="/content/detail/"], a[href*="content_id"]'));
                    
                    for (const a of aTags) {
                        const title = (a.innerText || a.textContent || '').trim();
                        const href = a.getAttribute('href') || '';
                        
                        if (title.length < 8 || !href || seenHrefs.has(href)) continue;
                        seenHrefs.add(href);

                        // ✅ 卡片錨定：先用 closest 鎖定容器
                        let card = a.closest('li') || a.closest('div.item') || a.closest('div.list-item') || 
                                   a.closest('section') || a.closest('div.info-list-item');
                        
                        // ✅ 父節點穿透：若 closest 失敗，逐層向上尋找
                        if (!card) {
                            let parent = a.parentElement;
                            while (parent && parent !== document.body) {
                                if (parent.innerText && parent.innerText.length > title.length + 10) {
                                    card = parent;
                                    break;
                                }
                                parent = parent.parentElement;
                            }
                        }

                        // ✅ 精準時間提取：優先從 .info-item-content-operat 第一個 span
                        let rawTime = '';
                        if (card) {
                            const timeEl = card.querySelector('.info-item-content-operat > span:first-child, [class*="time"], [class*="date"]');
                            if (timeEl) {
                                rawTime = (timeEl.innerText || timeEl.textContent || '').trim();
                            }
                        }

                        // 備用：正則從卡片文字中提取
                        if (!rawTime && card) {
                            const cardText = card.innerText || '';
                            const match = cardText.match(/(?:\\d{4}[-/])?\\d{1,2}[-/]\\d{1,2}\\s+\\d{1,2}:\\d{1,2}(?::\\d{1,2})?|(?:今[天日]|昨[天日]|前[天日])\\s*\\d{1,2}:\\d{1,2}|\\b\\d{1,2}:\\d{1,2}(?::\\d{1,2})?\\b|\\d{1,2}月\\d{1,2}日(?:\\s*\\d{1,2}:\\d{1,2})?|\\d+\\s*(?:分鐘|分钟|小時|小时|分|h)\\s*前|剛剛|刚刚/);
                            if (match) rawTime = match[0];
                        }

                        results.push({
                            title: title.replace(/\\s+/g, ' '),
                            href: href,
                            raw_time: rawTime
                        });
                    }
                    return results;
                }""")

                if not batch:
                    print(f"  💤 [{self.name}] 第 {p} 頁無任何數據，當前沒有最新消息更新。")
                    break

                # === 診斷日誌 ===
                print(f"\n  📋 [{self.name}] 第 {p} 頁抓取結果 (前10條):")
                for idx, item in enumerate(batch[:10], 1):
                    raw_href = item.get("href", "")
                    full_link_raw = urljoin(target_url, raw_href)
                    normalized = self._normalize_url(full_link_raw)
                    # ✅ 信任 state_manager，不再用本地 set 繞過
                    is_scanned = state_manager.is_news_scanned(normalized)
                    status_icon = "🔴已讀" if is_scanned else "🟢新訊"
                    print(f"     {idx:2d}. [{status_icon}] {item.get('raw_time', 'N/A'):>16s} | {item.get('title', '')[:35]}...")
                    print(f"         標準: {normalized}")
                print()

                page_added = 0
                for item in batch:
                    title = item.get("title", "")
                    if any(w in title for w in ["登入", "登錄", "下載", "首頁", "版權所有"]):
                        continue

                    raw_href = item.get("href", "")
                    full_link = self._normalize_url(urljoin(target_url, raw_href))

                    # ✅ 碰頭歷史記錄 -> 立即終止（文檔第3點：Smart Brake）
                    if state_manager.is_news_scanned(full_link):
                        print(f"  🛑 [{self.name}] 碰頭歷史記錄，當前沒有最新消息更新。")
                        print(f"     └─ 標題: {title[:40]}...")
                        should_stop = True
                        break

                    raw_time = item.get("raw_time", "")
                    pub_ts, pub_time_str = self._parse_time_str(raw_time)

                    # ✅ 觸達 Cutoff 水位線 -> 立即終止
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
                    if page_added == 0 and not should_stop:
                        print(f"  💤 [{self.name}] 第 {p} 頁全為歷史記錄，當前沒有最新消息更新。")
                    break

        try:
            with DynamicSession(headless=True, stealth=True, timeout=30000) as sess:
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
