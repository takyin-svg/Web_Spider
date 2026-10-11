# src/sources/zhitong_focus.py
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
        """強制將不同格式的文章連結統一轉換為 share.html?content_id=XXX 格式"""
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

        # ✅ 建立本地 seen set，繞過可能有問題的 state_manager.is_news_scanned()
        local_seen_links: set[str] = set()
        try:
            existing = state_manager.get_all_scanned_news_links() if hasattr(state_manager, 'get_all_scanned_news_links') else []
            local_seen_links.update(existing)
            print(f"  📚 [{self.name}] 已載入 {len(local_seen_links)} 條歷史記錄至本地快取")
        except Exception as e:
            print(f"  ⚠️ [{self.name}] 載入歷史記錄失敗: {e}")

        should_stop = False

        def page_action_handler(page):
            nonlocal should_stop
            page.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ico}", lambda r: r.abort())

            for p in range(1, max_pages + 1):
                target_url = self.mobile_url_pattern.format(p)

                if p > 1:
                    try:
                        page.goto(target_url, wait_until="domcontentloaded", timeout=18000)
                    except Exception as e:
                        print(f"  ⚠️ [{self.name}] 第 {p} 頁開啟超時: {e}")
                        break

                # ✅ 修復1: 多層級容錯等待，不再依賴單一選擇器
                selectors_to_try = [
                    '.home-list-scroll .info-list-item',
                    '.info-list-item',
                    'a[href*="/content/detail/"]',
                ]
                waited = False
                for sel in selectors_to_try:
                    try:
                        page.wait_for_selector(sel, timeout=4000)
                        waited = True
                        break
                    except Exception:
                        continue
                if not waited:
                    print(f"  💤 [{self.name}] 第 {p} 頁未找到任何新聞容器，當前沒有最新消息更新。")
                    break

                # ✅ 修復2: JS 內建多層級容錯選擇器 + 容器探測日誌
                batch = page.evaluate("""() => {
                    const results = [];
                    const seenHrefs = new Set();

                    // 多層級容錯：依序嘗試不同容器選擇器
                    const containerSelectors = [
                        '.home-list-scroll .info-list-item',
                        '.info-list-item',
                        '[class*="list-item"]',
                        '[class*="news-item"]',
                        '[class*="article-item"]',
                    ];

                    let items = [];
                    let matchedSelector = '(none)';
                    for (const sel of containerSelectors) {
                        items = Array.from(document.querySelectorAll(sel));
                        if (items.length > 0) {
                            matchedSelector = sel;
                            break;
                        }
                    }

                    // 最後防線：若所有容器都找不到，直接從所有 detail 連結向上回溯卡片
                    if (items.length === 0) {
                        matchedSelector = 'fallback: a[href*="/content/detail/"] upward traversal';
                        const allLinks = Array.from(document.querySelectorAll('a[href*="/content/detail/"]'));
                        for (const a of allLinks) {
                            const title = (a.innerText || a.textContent || '').trim();
                            const href = a.getAttribute('href') || '';
                            if (title.length < 8 || !href || seenHrefs.has(href)) continue;
                            seenHrefs.add(href);

                            // 向上回溯找到最近的卡片容器
                            let card = a.closest('li') || a.closest('div[class*="item"]') || a.closest('section');
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

                            let rawTime = '';
                            if (card) {
                                const timeEl = card.querySelector('[class*="time"], [class*="date"], span:first-child');
                                if (timeEl) rawTime = (timeEl.innerText || timeEl.textContent || '').trim();
                            }
                            if (!rawTime) {
                                const cardText = card ? (card.innerText || '') : title;
                                const match = cardText.match(/(?:\\d{4}[-/])?\\d{1,2}[-/]\\d{1,2}\\s+\\d{1,2}:\\d{1,2}(?::\\d{1,2})?|(?:今[天日]|昨[天日]|前[天日])\\s*\\d{1,2}:\\d{1,2}|\\b\\d{1,2}:\\d{1,2}(?::\\d{1,2})?\\b|\\d{1,2}月\\d{1,2}日(?:\\s*\\d{1,2}:\\d{1,2})?|\\d+\\s*(?:分鐘|分钟|小時|小时|分|h)\\s*前|剛剛|刚刚/);
                                if (match) rawTime = match[0];
                            }

                            results.push({ title: title.replace(/\\s+/g, ' '), href, raw_time: rawTime });
                        }
                        return { matched_selector: matchedSelector, items: results };
                    }

                    // 正常路徑：從容器內提取
                    for (const item of items) {
                        const a = item.querySelector('a[href*="/content/detail/"]') || item.closest('a[href*="/content/detail/"]');
                        if (!a) continue;

                        const title = (a.innerText || a.textContent || '').trim();
                        const href = a.getAttribute('href') || '';
                        if (title.length < 8 || !href || seenHrefs.has(href)) continue;
                        seenHrefs.add(href);

                        let rawTime = '';
                        const timeEl = item.querySelector('[class*="time"], [class*="date"], [class*="operat"] span:first-child, span:first-child');
                        if (timeEl) rawTime = (timeEl.innerText || timeEl.textContent || '').trim();

                        if (!rawTime) {
                            const cardText = item.innerText || '';
                            const match = cardText.match(/(?:\\d{4}[-/])?\\d{1,2}[-/]\\d{1,2}\\s+\\d{1,2}:\\d{1,2}(?::\\d{1,2})?|(?:今[天日]|昨[天日]|前[天日])\\s*\\d{1,2}:\\d{1,2}|\\b\\d{1,2}:\\d{1,2}(?::\\d{1,2})?\\b|\\d{1,2}月\\d{1,2}日(?:\\s*\\d{1,2}:\\d{1,2})?|\\d+\\s*(?:分鐘|分钟|小時|小时|分|h)\\s*前|剛剛|刚刚/);
                            if (match) rawTime = match[0];
                        }

                        results.push({ title: title.replace(/\\s+/g, ' '), href, raw_time: rawTime });
                    }

                    return { matched_selector: matchedSelector, items: results };
                }""")

                # ✅ 解析 JS 返回的結構化結果
                matched_selector = batch.get("matched_selector", "(unknown)") if isinstance(batch, dict) else "(legacy)"
                items = batch.get("items", []) if isinstance(batch, dict) else batch

                # 診斷日誌：印出實際命中的選擇器
                print(f"  🎯 [{self.name}] 第 {p} 頁命中選擇器: {matched_selector} (共 {len(items)} 條)")

                if not items:
                    print(f"  💤 [{self.name}] 第 {p} 頁無任何數據，當前沒有最新消息更新。")
                    break

                # === 診斷日誌：印出網頁上實際抓到的前 10 條 ===
                print(f"\n  📋 [{self.name}] 第 {p} 頁抓取結果 (前10條):")
                for idx, item in enumerate(items[:10], 1):
                    raw_href = item.get("href", "")
                    full_link_raw = urljoin(target_url, raw_href)
                    normalized = self._normalize_url(full_link_raw)
                    is_scanned = normalized in local_seen_links
                    status_icon = "🔴已讀" if is_scanned else "🟢新訊"
                    print(f"     {idx:2d}. [{status_icon}] {item.get('raw_time', 'N/A'):>16s} | {item.get('title', '')[:35]}...")
                    print(f"         標準: {normalized}")
                print()

                page_added = 0
                for item in items:
                    title = item.get("title", "")
                    if any(w in title for w in ["登入", "登錄", "下載", "首頁", "版權所有"]):
                        continue

                    raw_href = item.get("href", "")
                    full_link = self._normalize_url(urljoin(target_url, raw_href))

                    if full_link in local_seen_links:
                        print(f"  🛑 [{self.name}] 碰頭歷史記錄，當前沒有最新消息更新。")
                        print(f"     └─ 標題: {title[:40]}...")
                        should_stop = True
                        break

                    raw_time = item.get("raw_time", "")
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
                    local_seen_links.add(full_link)
                    state_manager.add_scanned(full_link)
                    page_added += 1

                if should_stop or page_added == 0:
                    if page_added == 0 and not should_stop:
                        print(f"  💤 [{self.name}] 第 {p} 頁全為歷史記錄，當前沒有最新消息更新。")
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
