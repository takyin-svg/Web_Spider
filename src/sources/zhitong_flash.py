# src/sources/zhitong_flash.py
# 7x24 快讯源：用原生 playwright 滚动加载，突破首屏 20 条上限。
# 停止逻辑沿用旧版三闸门：① 碰头历史记录 break ② 时间 < Cutoff break ③ 触底无新增加载 break；
# 外加 max_scroll_rounds=10 硬上限（约 200 条）。栏目固定为港股 immediately.html?type=ganggu。
#
# 【本轮唯一改动 · 问题二配套】新增 self.last_run_ok 成功/失败信号：
#   - 正常跑完滚动流程（哪怕抓到 0 条 = 确实无新增）      -> last_run_ok = True
#   - 导航/初始化/滚动途中抛异常（Page.goto 超时等硬故障） -> last_run_ok = False
# 目的：让上层能区分「抓到 0 条（无事发生，可推进水位）」与
#       「抓取失败 0 条（超时空转，绝不能推进水位）」，
#       从而实施「按源分记水位」，根治 flash 超时时 Cutoff 仍被 focus 掩护着前移、
#       导致该时段快讯被永久漏采的问题。抓取/解析/停止三闸门逻辑一字未动。
import re
import hashlib
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright
from src.config import HKT
from src.sources.base import BaseSource

# 港股 7x24 栏目（旧版 ZHITONG_FLASH_URL 是不带 type 的全市场页，这里硬编码港股）
FLASH_URL = "https://www.zhitongcaijing.com/immediately.html?type=ganggu"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class ZhitongFlashSource(BaseSource):
    def __init__(self):
        super().__init__("智通7x24")
        # 本轮抓取是否成功（供 scraper/orchestrator 判定是否推进该源水位）。默认成功。
        self.last_run_ok = True

    # ---------- 文本处理 ----------
    def _split_title_and_content(self, raw_text: str) -> tuple[str, str]:
        text = (raw_text.replace("编辑解读", "").replace("添加解读", "")
                .replace("查看解读", "").strip())
        match = re.match(r'^(【.*?】)(.*)', text, re.DOTALL)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        return text[:45], text

    def _is_footer_block(self, content: str) -> bool:
        """拦掉页脚/友链脏块：友链、关于我们、版权声明，或同时出现 >=3 个财经站名。"""
        if not content:
            return False
        for kw in ["友链", "友情链接", "关于我们", "版权声明", "版权所有", "联系我们",
                   "ICP备", "京公网安备"]:
            if kw in content:
                return True
        sites = ["新浪港股", "同花顺", "东方财富", "和讯", "金融界", "全景网",
                 "凤凰财经", "腾讯财经", "雪球", "格隆汇"]
        hit = sum(1 for s in sites if s in content)
        return hit >= 3

    # ---------- 时间解析（加固版） ----------
    def _parse_time_str(self, text: str) -> tuple[int, str]:
        now = datetime.now(HKT)
        text = str(text).strip()
        if not text:
            return 0, ""

        if any(k in text for k in ["剛", "刚"]):
            return int(now.timestamp()), now.strftime('%Y-%m-%d %H:%M:%S')

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
            try:
                h, m = int(m_yest.group(1)), int(m_yest.group(2))
                dt = target_day.replace(hour=h, minute=m, second=0, microsecond=0)
                return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')
            except (ValueError, TypeError):
                pass

        m_date_time = re.search(
            r'(?:(\d{4})[-/])?(\d{1,2})[-/](\d{1,2})\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?', text)
        if m_date_time:
            try:
                year = int(m_date_time.group(1)) if m_date_time.group(1) else now.year
                month = int(m_date_time.group(2))
                day = int(m_date_time.group(3))
                hour = int(m_date_time.group(4))
                minute = int(m_date_time.group(5))
                second = int(m_date_time.group(6)) if m_date_time.group(6) else 0
                dt = datetime(year, month, day, hour, minute, second, tzinfo=HKT)
                return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')
            except (ValueError, TypeError):
                pass

        m_time_only = re.search(r'\b(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?\b', text)
        if m_time_only and not re.search(r'\d{1,2}[-/月]\d{1,2}', text):
            try:
                h, m = int(m_time_only.group(1)), int(m_time_only.group(2))
                s = int(m_time_only.group(3)) if m_time_only.group(3) else 0
                if 0 <= h <= 23 and 0 <= m <= 59:
                    dt = now.replace(hour=h, minute=m, second=s, microsecond=0)
                    if dt.timestamp() > now.timestamp() + 300:
                        dt = dt - timedelta(days=1)
                    return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')
            except (ValueError, TypeError):
                pass

        m_cn = re.search(r'(\d{1,2})月(\d{1,2})日(?:\s*(\d{1,2}):(\d{1,2}))?', text)
        if m_cn:
            try:
                month = int(m_cn.group(1))
                day = int(m_cn.group(2))
                hour = int(m_cn.group(3)) if m_cn.group(3) else 22
                minute = int(m_cn.group(4)) if m_cn.group(4) else 0
                dt = datetime(now.year, month, day, hour, minute, 0, tzinfo=HKT)
                return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')
            except (ValueError, TypeError):
                pass

        return 0, ""

    def fetch(self, cutoff_timestamp: int, state_manager, max_scroll_rounds: int = 10) -> list[dict]:
        results = []
        # 每次进入抓取先重置为成功，只有真正抛异常才置 False
        self.last_run_ok = True
        cutoff_dt_str = datetime.fromtimestamp(cutoff_timestamp, HKT).strftime('%Y-%m-%d %H:%M:%S')
        print(f"  🔍 [{self.name}] 启动浏览器滚动抓取，目标时间下限: {cutoff_dt_str}，最大滚动轮次: {max_scroll_rounds}")

        should_stop = False
        scanned_in_session = set()

        def handle_batch(items):
            """把一批 DOM 卡片按三闸门逻辑入库，返回本轮新增数。"""
            nonlocal should_stop
            new_found = 0
            for it in items:
                raw_content = (it.get("content") or "").strip()
                raw_time = it.get("raw_time", "")
                raw_id = it.get("raw_id", "")

                # 模板骨架 / JS 占位污染：含 ${ 的直接丢
                if "${" in raw_content:
                    continue
                content_hash = hashlib.md5(raw_content.encode("utf-8")).hexdigest()[:12]
                unique_id = raw_id.replace("immediately_id_", "") if raw_id else content_hash

                # 正文过短或页脚脏块
                if len(raw_content) < 10 or self._is_footer_block(raw_content):
                    continue

                if unique_id in scanned_in_session:
                    continue
                scanned_in_session.add(unique_id)

                title, body = self._split_title_and_content(raw_content)

                # ① 碰头历史记录 -> break（首屏时间倒序，碰头即其后更旧皆已收）
                if state_manager.is_news_scanned(unique_id):
                    print(f"  🛑 [{self.name}] 碰头历史记录 [{title[:25]}...]，结束滚动。")
                    should_stop = True
                    return new_found

                # ② 时间 < Cutoff -> break（主力退出）
                pub_ts, pub_time_str = self._parse_time_str(raw_time)
                if pub_ts > 0 and pub_ts <= cutoff_timestamp:
                    print(f"  🛑 [{self.name}] 触达时间下限 [{pub_time_str}]: [{title[:25]}...]，结束滚动。")
                    should_stop = True
                    return new_found

                if not pub_time_str:
                    pub_time_str = datetime.now(HKT).strftime('%Y-%m-%d %H:%M:%S')

                results.append({
                    "unique_id": unique_id,
                    "title": title,
                    "content": body,
                    "source": self.name,
                    "link": f"{FLASH_URL}#flash_{unique_id}",
                    "time": pub_time_str,
                })
                state_manager.add_scanned(unique_id)
                new_found += 1
            return new_found

        EXTRACT_JS = r"""() => {
            const out = [];
            const nodes = Array.from(document.querySelectorAll(
                'div.allday-item, div.allday-item-content, [class*="allday-item"]'));
            const seen = new Set();
            for (const node of nodes) {
                let card = node.closest('div.allday-item') || node;
                const contentEl = card.querySelector('.allday-item-content') || card;
                const contentText = (contentEl.innerText || contentEl.textContent || '').trim();
                if (contentText.length < 10) continue;

                let rawTime = '';
                const timeEl = card.querySelector('.allday-item-time, .time, .date, [class*="time"], [class*="date"]');
                if (timeEl) rawTime = (timeEl.innerText || timeEl.textContent || '').trim();
                if (!rawTime) {
                    const mm = contentText.match(/\b\d{1,2}:\d{1,2}(?::\d{1,2})?\b|(?:\d{4}[-/])?\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{1,2}|\d+\s*(?:分鐘|分钟|小時|小时|分|h)\s*前|剛剛|刚刚/);
                    if (mm) rawTime = mm[0];
                }
                const elementId = card.getAttribute('id') || card.getAttribute('data-id') || '';
                const key = elementId + '|' + contentText.slice(0, 40);
                if (seen.has(key)) continue;
                seen.add(key);
                out.push({ raw_id: elementId, content: contentText, raw_time: rawTime });
            }
            return out;
        }"""

        browser = None
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
                page = browser.new_page(user_agent=_UA)
                page.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ico}", lambda r: r.abort())
                page.goto(FLASH_URL, wait_until="domcontentloaded", timeout=45000)
                try:
                    page.wait_for_selector("div.allday-item, div.allday-item-content", timeout=20000)
                except Exception:
                    print(f"  ⚠️ [{self.name}] 未等到 allday-item 卡片，返回已抓取 {len(results)} 条。")

                for round_idx in range(max_scroll_rounds):
                    items = page.evaluate(EXTRACT_JS)
                    if not items:
                        break

                    new_found = handle_batch(items)
                    if should_stop:
                        break

                    # 触底：再滚一屏，等站点 JS 现算 sign 拉下一批
                    page.evaluate("window.scrollBy(0, 3000)")
                    page.wait_for_timeout(1200)

                    # ③ 触底无新增加载（连续零新增后比对 scrollHeight 是否增长）-> break
                    if new_found == 0 and round_idx >= 2:
                        before = page.evaluate("() => document.body.scrollHeight")
                        page.evaluate("window.scrollBy(0, 3000)")
                        page.wait_for_timeout(1500)
                        after = page.evaluate("() => document.body.scrollHeight")
                        if after <= before + 50:
                            print(f"  ℹ️ [{self.name}] 触底无新增加载，结束滚动。")
                            break
                else:
                    print(f"  ℹ️ [{self.name}] 已达最大滚动轮次 {max_scroll_rounds}，提前结束（如需更多请临时调大）。")
        except Exception as e:
            # 导航超时 / 浏览器初始化失败等硬故障：标记本轮该源抓取失败，
            # 上层据此【不推进 flash 水位】，下轮从旧水位重试，杜绝永久漏采。
            self.last_run_ok = False
            print(f"  ⚠️ [{self.name}] 调度异常: {e}")
        finally:
            # 无论成功/异常，确保浏览器进程被回收（长驻守护下防止 chromium 僵尸进程堆积）。
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass

        if not self.last_run_ok:
            print(f"  └─ [{self.name}] 本次抓取失败（异常/超时），共收录 {len(results)} 条；该源水位保持不变。")
        else:
            print(f"  └─ [{self.name}] 本次增量共收录 {len(results)} 条快讯 (时间全部精准对齐)")
        return results
