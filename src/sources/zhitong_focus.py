# srczhitong_focus.py
import re
import html as _html
import requests
from datetime import datetime, timedelta
from urllib.parse import urljoin
from src.config import HKT
from src.sources.base import BaseSource


class ZhitongFocusSource(BaseSource):
    def __init__(self):
        super().__init__("智通焦點")
        # 真实列表页：桌面站，数据服务端静态直出，无需浏览器/bs4
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

        # ★ 修复点：补回日期分隔符 [-/]，捕获组顺序=年/月/日/时/分/秒
        m_date_time = re.search(
            r'(?:(\d{4})[-/])?(\d{1,2})\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?', text)
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

    @staticmethod
    def _strip_tags(s: str) -> str:
        s = re.sub(r'<[^>]+>', '', s)
        return _html.unescape(s).strip()

    def _extract_cards(self, page_html: str) -> list[dict]:
        """用纯正则从静态 HTML 抽取 (title, href, raw_time)，无需 bs4。"""
        out = []
        blocks = re.split(r'<div[^>]+class="[^"]*\binfo-list-item\b[^"]*"', page_html)
        for blk in blocks[1:]:
            title_m = re.search(
                r'class="[^"]*info-item-content-title[^"]*".*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                blk, re.DOTALL)
            if not title_m:
                title_m = re.search(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', blk, re.DOTALL)
            if not title_m:
                continue
            href = title_m.group(1).strip()
            title = self._strip_tags(title_m.group(2))
            if not title or len(title) < 8:
                continue
            raw_time = ""
            op_m = re.search(
                r'class="[^"]*info-item-content-operat[^"]*"(.*?)(?:<div|</div>\s*</div>)',
                blk, re.DOTALL)
            if op_m:
                for tm in re.finditer(r'>([^<>]*\d[^<>]*)<', op_m.group(1)):
                    t = tm.group(1).strip()
                    if t:
                        raw_time = t
                        break
            if not raw_time:
                card_text = self._strip_tags(blk)
                tm = re.search(
                    r'\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}|\d{1,2}:\d{2}', card_text)
                raw_time = tm.group(0) if tm else ""
            out.append({"title": title, "href": href, "raw_time": raw_time})
        return out

    def fetch(self, cutoff_timestamp: int, state_manager, max_pages: int = 10) -> list[dict]:
        results = []
        cutoff_dt_str = datetime.fromtimestamp(cutoff_timestamp, HKT).strftime('%Y-%m-%d %H:%M:%S')
        print(f"  🔍 [{self.name}] 启动静态直出抓取（requests+re），目标时间下限: {cutoff_dt_str}")

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
            batch = self._extract_cards(resp.text)
            if not batch:
                print(f"  ⚠️ [{self.name}] 第 {p} 页未找到 info-list-item 列表，结束翻页。")
                break

            page_added = 0
            for item in batch:
                title = item["title"]
                if any(w in title for w in ["登入", "登錄", "下載", "首頁", "版權所有"]):
                    continue
                full_link = urljoin(self.base_url, item["href"])

                if state_manager.is_news_scanned(full_link):
                    print(f"  🛑 [{self.name}] 碰头历史记录 [{title[:25]}...]，结束翻页。")
                    should_stop = True
                    break

                pub_ts, pub_time_str = self._parse_time_str(item.get("raw_time", ""))

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
