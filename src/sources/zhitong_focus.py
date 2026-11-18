
# src/sources/zhitong_focus.py
# 栏目正名 + 翻页破 20：改用智通「港股」栏目 JSON 接口
#   https://www.zhitongcaijing.com/content/hkstock.html?data_type=1&page=N
#   - 必须带 data_type=1 才会切到返回 JSON 的 AJAX 接口；光拼 ?page=N 拿回的是同一份首屏 HTML（这是此前无论 Cutoff 拉多宽都卡在 20 条、第 2 页必空的根因）
#   - JSON 字段：title / url(/content/detail/xxx.html) / content_id / create_time(Unix秒, 与 create_time_desc 完全对齐) / create_time_desc(YYYY-MM-DD HH:MM)
#   - 首屏(page=1)与翻页(page>=2)都走同一 JSON 解析；若站点某页回落成 HTML，则降级用正则切卡，逻辑不中断
import re
import json
import html as _html
import time
import requests
from datetime import datetime, timedelta
from urllib.parse import urljoin
from src.config import HKT
from src.sources.base import BaseSource


class ZhitongFocusSource(BaseSource):
    def __init__(self):
        # 注：源名沿用「智通焦點」以免牵动 main.py/飞书推送等处对该名称的引用；实际抓取栏目已正名为智通「港股」(hkstock)
        super().__init__("智通焦點")
        # 真正的港股栏目 + data_type=1 触发 JSON 分页接口；page 从 1 开始
        self.list_url_pattern = "https://www.zhitongcaijing.com/content/hkstock.html?data_type=1&page={}"
        self.base_url = "https://www.zhitongcaijing.com"
        self.headers = {
            'User-Agent': (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            'Accept-Language': "zh-CN,zh;q=0.9,zh-HK;q=0.8",
            'Accept': "application/json, text/html;q=0.9,*/*;q=0.8",
            'Referer': "https://www.zhitongcaijing.com/content/hkstock.html",
        }
        # 日期分隔符用变量拼接，规避聊天渲染吞字的老坑
        self.SEP = r"[-/]"

    # ---------- 时间解析（保留，作为 HTML 降级 / create_time 缺失时的兜底） ----------
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
            try:
                h, m = int(m_yest.group(1)), int(m_yest.group(2))
                dt = target_day.replace(hour=h, minute=m, second=0, microsecond=0)
                return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')
            except (ValueError, TypeError):
                pass

        pat = rf'(?:(\d{{4}}){self.SEP})?(\d{{1,2}}){self.SEP}(\d{{1,2}})\s+(\d{{1,2}}):(\d{{1,2}})(?::(\d{{1,2}}))?'
        m_date_time = re.search(pat, text)
        if m_date_time:
            try:
                year = int(m_date_time.group(1)) if m_date_time.group(1) else now.year
                month = int(m_date_time.group(2))
                day = int(m_date_time.group(3))
                hour = int(m_date_time.group(4))
                minute = int(m_date_time.group(5)) if m_date_time.group(5) is not None else 0
                second = int(m_date_time.group(6)) if m_date_time.group(6) else 0
                dt = datetime(year, month, day, hour, minute, second, tzinfo=HKT)
                return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')
            except (ValueError, TypeError):
                pass

        m_today = re.search(r'(?:(?:今[天日])\s*)?(\b\d{1,2}):(\d{1,2})(?::(\d{1,2}))?\b', text)
        if m_today and not re.search(r'\d{1,2}[-/月]\d{1,2}', text):
            try:
                h, m = int(m_today.group(1)), int(m_today.group(2))
                s = int(m_today.group(3)) if m_today.group(3) else 0
                if 0 <= h <= 23 and 0 <= m <= 59:
                    dt = now.replace(hour=h, minute=m, second=s, microsecond=0)
                    return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')
            except (ValueError, TypeError):
                pass

        m_cn = re.search(r'(\d{1,2})月(\d{1,2})日(?:\s*(\d{1,2}):(\d{1,2}))?', text)
        if m_cn:
            try:
                year = now.year
                month = int(m_cn.group(1))
                day = int(m_cn.group(2))
                hour = int(m_cn.group(3)) if m_cn.group(3) else 22
                minute = int(m_cn.group(4)) if m_cn.group(4) else 0
                dt = datetime(year, month, day, hour, minute, 0, tzinfo=HKT)
                return int(dt.timestamp()), dt.strftime('%Y-%m-%d %H:%M:%S')
            except (ValueError, TypeError):
                pass

        return 0, ""

    # ---------- HTML 降级解析（保留，仅当某页未返回 JSON 时使用） ----------
    @staticmethod
    def _strip_tags(s: str) -> str:
        s = re.sub(r'<[^>]+>', '', s)
        return _html.unescape(s).strip()

    def _extract_cards(self, page_html: str) -> list[dict]:
        """纯正则从静态 HTML 抽取卡片；过滤含 ${} 的 JS 模板骨架脏块。"""
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
            # 过滤 JS 模板骨架（href="${data.url}" / title 含 ${...}）
            if "${" in href or "${" in title:
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
                tm = re.search(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}|\d{1,2}:\d{2}', card_text)
                raw_time = tm.group(0) if tm else ""
            out.append({"title": title, "href": href, "raw_time": raw_time, "ts_hint": None})
        return out

    # ---------- JSON 解析（主路径） ----------
    def _extract_items_json(self, payload: dict) -> list[dict]:
        out = []
        data = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(data, list):
            return out
        for it in data:
            if not isinstance(it, dict):
                continue
            title = str(it.get("title", "")).strip()
            href = str(it.get("url", "")).strip()
            if not title or len(title) < 8 or not href:
                continue
            raw_time = str(it.get("create_time_desc", "")).strip()
            ts_hint = None
            for key in ("create_time", "publish_time"):
                v = it.get(key)
                try:
                    if v not in (None, "", 0, "0"):
                        iv = int(v)
                        if iv > 1_000_000_000:  # 合理 Unix 秒区间
                            ts_hint = iv
                            break
                except (ValueError, TypeError):
                    continue
            out.append({"title": title, "href": href, "raw_time": raw_time, "ts_hint": ts_hint})
        return out

    @staticmethod
    def _looks_like_json(resp) -> bool:
        ct = (resp.headers.get("Content-Type") or "").lower()
        if "json" in ct:
            return True
        body = resp.text.lstrip()[:1]
        return body in ("{", "[")

    def fetch(self, cutoff_timestamp: int, state_manager, max_pages: int = 10) -> list[dict]:
        # ★ max_pages 硬上限 = 10：最多翻 10 页（约 200 条），与四闸门并存
        results = []
        cutoff_dt_str = datetime.fromtimestamp(cutoff_timestamp, HKT).strftime('%Y-%m-%d %H:%M:%S')
        print(f"  🔍 [{self.name}] 启动港股栏目 JSON 抓取（data_type=1），目标时间下限: {cutoff_dt_str}，最大翻页: {max_pages} 页")

        hit_cutoff = False          # ① 时间下限：唯一能终止整轮翻页的条件
        empty_streak = 0            # ③ 连续空收页防呆：避免历史深处空翻

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

            # 主路径：JSON 接口；站点若回落 HTML 则降级正则切卡
            batch = []
            if self._looks_like_json(resp):
                try:
                    payload = resp.json()
                    if isinstance(payload, dict) and payload.get("status") not in (None, "success"):
                        print(f"  ⚠️ [{self.name}] 第 {p} 页接口状态异常: {payload.get('msg')}")
                        break
                    batch = self._extract_items_json(payload)
                except Exception as e:
                    print(f"  ⚠️ [{self.name}] 第 {p} 页 JSON 解析失败({e})，降级 HTML 切卡。")
                    resp.encoding = resp.apparent_encoding or "utf-8"
                    batch = self._extract_cards(resp.text)
            else:
                resp.encoding = resp.apparent_encoding or "utf-8"
                batch = self._extract_cards(resp.text)

            if not batch:
                print(f"  ⚠️ [{self.name}] 第 {p} 页无有效条目，结束翻页。")
                break

            page_added = 0
            for item in batch:
                title = item["title"]
                if any(w in title for w in ["登入", "登錄", "下載", "首頁", "版權所有"]):
                    continue
                full_link = urljoin(self.base_url, item["href"])

                # ② 碰头历史记录：只跳过该条，绝不 break —— 这是修复 20 条上限的关键
                if state_manager.is_news_scanned(full_link):
                    continue

                # 时间：JSON 用精确 Unix 秒戳(与 create_time_desc 对齐)，HTML 降级走正则解析
                if item.get("ts_hint"):
                    pub_ts = int(item["ts_hint"])
                    pub_time_str = datetime.fromtimestamp(pub_ts, HKT).strftime('%Y-%m-%d %H:%M:%S')
                else:
                    pub_ts, pub_time_str = self._parse_time_str(item.get("raw_time", ""))

                # ① 触达时间下限：主力退出条件，命中即终止整轮翻页
                if pub_ts > 0 and pub_ts <= cutoff_timestamp:
                    print(f"  🛑 [{self.name}] 触达时间下限 [{pub_time_str}]: [{title[:25]}...]，结束翻页。")
                    hit_cutoff = True
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

            if hit_cutoff:
                break

            # ③ 整页都是碰头/过滤掉的页：累计连续空收，达到 2 页才停（继续往后翻以越过边界旧闻）
            if page_added == 0:
                empty_streak += 1
                if empty_streak >= 2:
                    print(f"  ℹ️ [{self.name}] 连续 {empty_streak} 页无新增，结束翻页。")
                    break
            else:
                empty_streak = 0

            time.sleep(0.6)  # 逐页之间稍作停顿，防反爬

        else:
            # for 正常跑完（未被任何 break 打断）= 触及 max_pages 硬上限
            print(f"  ℹ️ [{self.name}] 已达最大翻页上限 {max_pages} 页，提前结束（如需更多请临时调大 max_pages）。")

        print(f"  └─ [{self.name}] 本次增量共收录 {len(results)} 条新闻 (时间全部精准对齐)")
        return results
