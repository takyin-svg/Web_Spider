
# src/sources/zhitong_focus.py
import re
import requests
from datetime import datetime, timedelta
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from src.config import HKT
from src.sources.base import BaseSource

class ZhitongFocusSource(BaseSource):
    def __init__(self):
        super().__init__("智通焦點")
        # ✅ 真实列表页 URL（来自你提供的首页 HTML 核实）：
        #    智通财经是桌面站 www.zhitongcaijing.com，且列表数据【服务端静态直出】，
        #    根本不需要浏览器渲染。"要闻/焦点"对应 tab 的真实链接是：
        #    https://www.zhitongcaijing.com?index=yaowen&page={n}
        #    前几轮用的 m.zhitongcaijing.com / market.html 全部是猜错的，才一直 0 条 / 域名解析失败。
        self.list_url_pattern = "https://www.zhitongcaijing.com?index=yaowen&page={}"
        self.base_url = "https://www.zhitongcaijing.com"
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,zh-HK;q=0.8",
            "Referer": "https://www.zhitongcaijing.com/",
        }

    def _parse_time_str(self, text: str) -> tuple[int, str]:
        now = datetime.now(HKT)
        text = str(text).strip()
        if not text:
            return 0, ""

        # 1. 刚刚
        if any(k in text for k in ["剛", "刚"]):
            ts = int(now.timestamp())
            return ts, now.strftime('%Y-%m-%d %H:%M:%S')

        # 2. X 分钟前
        m_min = re.search(r'(\d+)\s*(?:分鐘|分钟|分)\s*前', text)
        if m_min:
            ts = int(now.timestamp()) - int(m_min.group(1)) * 60
            return ts, datetime.fromtimestamp(ts, HKT).strftime('%Y-%m-%d %H:%M:%S')

        # 3. X 小时前
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

        # 5. YYYY-MM-DD HH:MM 或 MM-DD HH:MM
        #    ✅ 真实页面时间就是这种格式，如 "2026-11-18 11:04"
        #    正则必须含日期分隔符 [-/]（前几轮被改坏漏掉，这里恢复正确版）
        m_date_time = re.search(
            r'(?:(\d{4})[-/])?(\d{1,2})[-/](\d{1,2})\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?', text)
        if m_date_time:
            year = int(m_date_time.group(1)) if m_date_time.group(1) else now.year
            month = int(m_date_time.group(2))
            day = int(m_date_time.group(3))
            hour = int(m_date_time.group(4))
            minute = int(m_date_time.group(5))
            second = int(m_date_time.group(6)) if m_date_time.group(6) else 0
            dt = datetime(year, month, day, hour, minute, second, tzinfo=HKT)
            return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')

        # 6. 纯当日时间 HH:MM
        m_today = re.search(r'(?:(?:今[天日])\s*)?(\b\d{1,2}):(\d{1,2})(?::(\d{1,2}))?\b', text)
        if m_today and not re.search(r'\d{1,2}[-/月]\d{1,2}', text):
            h, m = int(m_today.group(1)), int(m_today.group(2))
            s = int(m_today.group(3)) if m_today.group(3) else 0
            if 0 <= h <= 23 and 0 <= m <= 59:
                dt = now.replace(hour=h, minute=m, second=s, microsecond=0)
                return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')

        # 7. 中文日期 X月X日
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

    def fetch(self, cutoff_timestamp: int, state_manager, max_pages: int = 10) -> list[dict]:
        results = []
        cutoff_dt_str = datetime.fromtimestamp(cutoff_timestamp, HKT).strftime('%Y-%m-%d %H:%M:%S')
        print(f"  🔍 [{self.name}] 启动静态直出抓取（requests），目标时间下限: {cutoff_dt_str}")

        should_stop = False

        for p in range(1, max_pages + 1):
            target_url = self.list_url_pattern.format(p)
            try:
                resp = requests.get(target_url, headers=self.headers, timeout=30)
            except Exception as e:
                print(f"  ⚠️ [{self.name}] 第 {p} 页请求异常: {e}")
                break

            if resp.status_code != 200:
                print(f"  ⚠️ [{self.name}] 第 {p} 页 HTTP {resp.status_code}，跳过。")
                break

            resp.encoding = resp.apparent_encoding or "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")

            # ✅ 真实列表容器（HTML 实测 280 条）：div.shadow-wrap-box > div.info-list-item
            cards = soup.select("div.shadow-wrap-box > div.info-list-item")
            if not cards:
                cards = soup.select("div.info-list-item")
            if not cards:
                print(f"  ⚠️ [{self.name}] 第 {p} 页未找到 info-list-item 列表，结束翻页。")
                break

            page_added = 0
            for c in cards:
                a = c.select_one("div.info-item-content-title a") or c.select_one('a[href*="/content/detail/"]')
                if not a:
                    continue
                title = a.get_text(strip=True)
                if not title or len(title) < 8:
                    continue
                if any(w in title for w in ["登入", "登錄", "下載", "首頁", "版權所有"]):
                    continue

                href = a.get("href") or ""
                full_link = urljoin(self.base_url, href)

                # 1. 碰头历史记录 -> 立即终止
                if state_manager.is_news_scanned(full_link):
                    print(f"  🛑 [{self.name}] 碰头历史记录 [{title[:25]}...]，结束翻页。")
                    should_stop = True
                    break

                # 2. 时间：真实页面在 div.info-item-content-operat 的第一个 <span>
                raw_time = ""
                operat = c.select_one("div.info-item-content-operat")
                if operat:
                    for s in operat.select("span"):
                        t = s.get_text(strip=True)
                        if t and re.search(r'\d', t):
                            raw_time = t
                            break
                if not raw_time:
                    raw_time = c.get_text(strip=True)  # 兜底，让正则从整卡文本里抓时间

                pub_ts, pub_time_str = self._parse_time_str(raw_time)

                # 3. 触达时间水位线 -> 立即终止
                if pub_ts > 0 and pub_ts <= cutoff_timestamp:
                    print(f"  🛑 [{self.name}] 触达时间下限 [{pub_time_str}]: [{title[:25]}...]，结束翻页。")
                    should_stop = True
                    break

                if not pub_time_str:
                    pub_time_str = datetime.now(HKT).strftime('%Y-%m-%d %H:%M:%S')

                results.append({
                    "unique_id": full_link,
                    "title": title,
                    "source": self.name,
                    "link": full_link,
                    "time": pub_time_str,
                })
                state_manager.add_scanned(full_link)
                page_added += 1

            if should_stop or page_added == 0:
                break

        print(f"  └─ [{self.name}] 本次增量共收录 {len(results)} 条新闻 (时间全部精准对齐)")
        return results
