# src/sources/zhitong_flash.py
# -*- coding: utf-8 -*-
"""
智通财经 7x24 快讯采集（requests + re 静态直出版）

本轮改动要点：
1. 彻底弃用 playwright：focus 已用 requests 成功取到 20 条，证实
   www.zhitongcaijing.com 是服务端静态直出；flash 同样走 requests，
   不会再出现 wait_for_selector 超时后「未找到快讯列表容器」静默 0 条。
2. URL 用「主机常量 + 路径模板」拼装 + 多候选地址自动探测，不依赖聊天复制，
   杜绝前几轮域名被渲染吞字（https:market.html?page=1 / ERR_NAME_NOT_RESOLVED）。
3. 解析器同时兼容 JSON 接口 与 HTML 静态列表，探到哪个用哪个。
4. 一条都没抓到时自动打印诊断（实际 url / HTTP 码 / 正文长度 / 前 200 字 /
   页面出现次数最高的容器类名 Top12），一轮 push 就能定位是反爬还是类名不对。
5. 所有 int()/datetime() 转换包 try/except，不会再像 focus 第 8、9 轮那样
   因 int(None) 把整条采集链路崩掉。
"""
import re
import json
import html as _html
import hashlib
from datetime import datetime, timedelta
from urllib.parse import urljoin

import requests

from src.config import HKT
from src.sources.base import BaseSource

try:  # config 里没有该变量也不报错，直接用内置候选地址
    from src.config import ZHITONG_FLASH_URL
except Exception:
    ZHITONG_FLASH_URL = ""

# 日期分隔符单独拼成常量，避免正则里的 [-/] 在复制过程中被吞字
SEP = r"[-/]"


class ZhitongFlashSource(BaseSource):
    # 桌面站主机名（已证实 requests 可直连，focus 正常取数）
    HOST = "https://www.zhitongcaijing.com"

    # 候选列表地址，按顺序探测，第一个能抓到卡片的即固定下来
    CANDIDATE_URLS = [
        "{host}/immediately.html?type=ganggu",   # 本轮日志实测命中(html)且卡片最多，置顶
        "{host}/immediately.html",
        "{host}/?index=yaowen&page={page}",
        "{host}/content/immediately.html",        # 曾稳定 500，仅作末尾兜底
        "{host}/7x24.html?page={page}",
    ]

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,zh-HK;q=0.8",
        "Referer": "https://www.zhitongcaijing.com/",
    }

    def __init__(self):
        super().__init__("智通7x24")
        # config 里的地址若完整可用则优先；残缺值（被吞字的）自动忽略
        ok = bool(ZHITONG_FLASH_URL) and "://" in ZHITONG_FLASH_URL and \
            re.search(r"[a-z0-9][a-z0-9.-]*\.[a-z]{2,}", ZHITONG_FLASH_URL)
        self.custom_url = ZHITONG_FLASH_URL if ok else ""
        self._work_url = ""  # 探测成功后记住真正可用的地址

    # ------------------------------------------------------------ 小工具
    @staticmethod
    def _strip_tags(s: str) -> str:
        s = re.sub(r"<[^>]+>", " ", s or "")
        s = _html.unescape(s)
        return re.sub(r"\s+", " ", s).strip()

    @staticmethod
    def _clean_text(s: str) -> str:
        """剥离 split/strip 后残留的 HTML 属性碎片、孤立尖括号与多余空白。
        例：'id="immediately_id_8116899"> ' -> ''；'> 【港股...' -> '【港股...'"""
        if not s:
            return ""
        s = re.sub(r'\b[\w-]+="[^"]*"', " ", s)   # 残留的 HTML 属性（id= / class= ...）
        s = re.sub(r"[<>]", " ", s)                 # 孤立的 < >
        return re.sub(r"\s+", " ", s).strip()

    def _split_title_and_content(self, raw_text: str):
        text = (raw_text or "").replace("编辑解读", "").replace("添加解读", "") \
                               .replace("查看解读", "").strip()
        text = self._clean_text(text)
        m = re.match(r"^(【.*?】)(.*)", text, re.DOTALL)
        if m:
            return m.group(1).strip(), self._clean_text(m.group(2))
        return text[:45], text

    # ------------------------------------------------------ 时间解析（加固版）
    def _parse_time_str(self, text: str):
        """返回 (timestamp:int, 'YYYY-mm-dd HH:MM:SS'|'')；解析不出 -> (0, '')"""
        now = datetime.now(HKT)
        text = str(text).strip()
        if not text:
            return 0, ""

        # 0) 接口直接返回的绝对时间戳（秒 / 毫秒）
        if re.fullmatch(r"\d{10}", text):
            ts = int(text)
            return ts, datetime.fromtimestamp(ts, HKT).strftime("%Y-%m-%d %H:%M:%S")
        if re.fullmatch(r"\d{13}", text):
            ts = int(text) // 1000
            return ts, datetime.fromtimestamp(ts, HKT).strftime("%Y-%m-%d %H:%M:%S")

        # 1) 刚刚
        if any(k in text for k in ["剛", "刚"]):
            ts = int(now.timestamp())
            return ts, now.strftime("%Y-%m-%d %H:%M:%S")

        # 2) X 分钟前
        m = re.search(r"(\d+)\s*(?:分鐘|分钟|分)\s*前", text)
        if m:
            ts = int(now.timestamp()) - int(m.group(1)) * 60
            return ts, datetime.fromtimestamp(ts, HKT).strftime("%Y-%m-%d %H:%M:%S")

        # 3) X 小时前
        m = re.search(r"(\d+)\s*(?:小時|小时|h)\s*前", text)
        if m:
            ts = int(now.timestamp()) - int(m.group(1)) * 3600
            return ts, datetime.fromtimestamp(ts, HKT).strftime("%Y-%m-%d %H:%M:%S")

        # 4) 昨天 / 前天 HH:MM
        m = re.search(r"(?:昨[天日]|前[天日])\s*(\d{1,2}):(\d{1,2})", text)
        if m:
            days_ago = 2 if "前" in m.group(0) else 1
            dt = (now - timedelta(days=days_ago)).replace(
                hour=int(m.group(1)), minute=int(m.group(2)),
                second=0, microsecond=0)
            return int(dt.timestamp()), dt.strftime("%Y-%m-%d %H:%M:%S")

        # 5) YYYY-MM-DD HH:MM(:SS) —— 分组严格为 年/月/日/时/分/秒
        m = re.search(
            r"(?:(\d{4})" + SEP + r")?(\d{1,2})" + SEP + r"(\d{1,2})"
            r"\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?", text)
        if m:
            try:
                dt = datetime(
                    int(m.group(1)) if m.group(1) else now.year,
                    int(m.group(2)) if m.group(2) else 1,
                    int(m.group(3)) if m.group(3) else 1,
                    int(m.group(4)) if m.group(4) else 0,
                    int(m.group(5)) if m.group(5) else 0,
                    int(m.group(6)) if m.group(6) else 0,
                    tzinfo=HKT)
                return int(dt.timestamp()), dt.strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                pass   # 越界值继续走后面的分支，绝不抛异常

        # 6) MM-DD HH:MM（缺年份）
        m = re.search(r"(\d{1,2})" + SEP + r"(\d{1,2})\s+(\d{1,2}):(\d{1,2})", text)
        if m:
            try:
                dt = datetime(now.year, int(m.group(1)), int(m.group(2)),
                              int(m.group(3)), int(m.group(4)), 0, tzinfo=HKT)
                return int(dt.timestamp()), dt.strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                pass

        # 7) 纯时间 HH:MM —— 带跨日修正（比现在晚 1h 以上判为昨天）
        if not re.search(r"\d{1,2}" + SEP + r"\d{1,2}", text) and \
                not re.search(r"\d{1,2}月\d{1,2}日", text):
            m = re.search(r"\b(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?\b", text)
            if m:
                try:
                    dt = now.replace(hour=int(m.group(1)), minute=int(m.group(2)),
                                     second=int(m.group(3) or 0), microsecond=0)
                    if dt.timestamp() > now.timestamp() + 3600:
                        dt = dt - timedelta(days=1)
                    return int(dt.timestamp()), dt.strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, TypeError):
                    pass

        # 8) 中文日期 X月X日 HH:MM
        m = re.search(r"(\d{1,2})月(\d{1,2})日(?:\s*(\d{1,2}):(\d{1,2}))?", text)
        if m:
            try:
                dt = datetime(now.year, int(m.group(1)), int(m.group(2)),
                              int(m.group(3) or 22), int(m.group(4) or 0),
                              0, tzinfo=HKT)
                return int(dt.timestamp()), dt.strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                pass

        return 0, ""

    # ------------------------------------------------------------ HTTP
    def _get(self, url: str):
        try:
            resp = requests.get(url, headers=self.HEADERS, timeout=30)
        except Exception as e:
            print(f"  ⚠️ [{self.name}] 请求异常: {url} | {e}")
            return None
        try:
            resp.encoding = resp.apparent_encoding or "utf-8"
        except Exception:
            pass
        return resp

    # ----------------------------------------------- 解析：JSON 接口形式
    @staticmethod
    def _dig(obj, keys):
        for k in keys:
            if isinstance(obj, dict) and obj.get(k) not in (None, ""):
                return obj[k]
        return None

    def _parse_json_items(self, data) -> list:
        rows = None
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            for container in ("data", "result", "res", "payload"):
                node = data.get(container)
                if isinstance(node, list):
                    rows = node
                    break
                if isinstance(node, dict):
                    for sub in ("list", "items", "rows", "content", "data"):
                        if isinstance(node.get(sub), list):
                            rows = node[sub]
                            break
                if rows:
                    break
        if not rows:
            return []

        out = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            content = self._strip_tags(str(self._dig(
                r, ["content", "text", "brief", "summary", "message", "title"]) or ""))
            raw_time = ""
            for tk in ("time", "ctime", "created_at", "create_time", "publish_time",
                       "pubtime", "show_time", "display_time"):
                if r.get(tk) not in (None, ""):
                    raw_time = str(r[tk])
                    break
            rid = str(self._dig(r, ["id", "content_id", "news_id", "item_id"]) or "")
            if len(content) >= 10:
                out.append({"raw_id": rid, "content": content, "raw_time": raw_time})
        return out

    # ----------------------------------------------- 解析：HTML 静态形式
    def _parse_html_items(self, page_html: str) -> list:
        """基于 finditer 精准切卡：容器类名必须是完整 class token（后跟空格或引号），
        避免把 allday-item-time / allday-item-content 等子类误切成卡片；
        id 从容器开标签本身提取，正文用 content 子节点优先、整块 clean 兜底。"""
        out = []
        container = (r"allday-item|immediately-item|immediately_list|flash-item"
                     r"|realtime-item|time-list-item|info-list-item")
        opentag = re.compile(
            r'<[^>]*class="[^"]*(?:' + container + r')(?=[ "])[^>]*>')
        ms = list(opentag.finditer(page_html))
        for i, m in enumerate(ms):
            end = ms[i + 1].start() if i + 1 < len(ms) else len(page_html)
            blk = page_html[m.end():end]

            rid = ""
            idm = re.search(r'(?:id|data-id|data-news-id)="([^"]+)"', m.group(0))
            if idm:
                rid = idm.group(1)

            cm = re.search(r'class="[^"]*(?:content|text|desc|brief)[^"]*"[^>]*>'
                           r'(.{10,}?)</div>', blk, re.DOTALL)
            content = self._clean_text(
                self._strip_tags(cm.group(1)) if cm else self._strip_tags(blk))
            for junk in ("编辑解读", "添加解读", "查看解读", "查看详情"):
                content = content.replace(junk, " ")
            content = re.sub(r"\s+", " ", content).strip()

            raw_time = ""
            tm = re.search(r'class="[^"]*(?:time|date)[^"]*"[^>]*>([^<>]*\d[^<>]*)<', blk)
            if tm:
                raw_time = self._strip_tags(tm.group(1))
            if not raw_time:
                tm = re.search(
                    r"(\d{4}" + SEP + r"\d{1,2}" + SEP + r"\d{1,2}\s+\d{1,2}:\d{2}"
                    r"|\d{1,2}:\d{2}(?::\d{2})?"
                    r"|\d+\s*(?:分鐘|分钟|小時|小时|分|h)\s*前|刚刚|剛剛)", blk)
                raw_time = tm.group(1) if tm else ""

            if len(content) >= 10:
                out.append({"raw_id": rid, "content": content, "raw_time": raw_time})
        return out

    def _parse_any(self, resp):
        """自动判定 JSON / HTML，返回 (items, 类型)"""
        ctype = (resp.headers.get("Content-Type") or "").lower()
        body = resp.text or ""
        if "json" in ctype or body[:200].lstrip()[:1] in ("{", "["):
            try:
                return self._parse_json_items(json.loads(body)), "json"
            except Exception:
                pass
        return self._parse_html_items(body), "html"

    # ------------------------------------------------------------ 诊断
    def _dump_diag(self, url: str, resp):
        try:
            body = resp.text if resp is not None else ""
            print(f"  🔎 [{self.name}] 诊断 | url={url} | "
                  f"http={getattr(resp, 'status_code', 'NA')} | body_len={len(body)}")
            print(f"  🔎 [{self.name}] 正文前200字: {self._strip_tags(body)[:200]!r}")
            freq = {}
            for cls in re.findall(r'class="([^"]+)"', body or ""):
                for c in cls.split():
                    if re.search(r"item|list|flash|time|news|content", c):
                        freq[c] = freq.get(c, 0) + 1
            top = sorted(freq.items(), key=lambda x: -x[1])[:12]
            print(f"  🔎 [{self.name}] 疑似容器类名 Top: "
                  f"{top if top else '（无 —— 疑似被反爬/返回空壳页）'}")
        except Exception as e:
            print(f"  🔎 [{self.name}] 诊断输出失败: {e}")

    # ------------------------------------------------------------- fetch
    def fetch(self, cutoff_timestamp: int, state_manager,
              max_scroll_rounds: int = 10) -> list:
        results = []
        cutoff_dt_str = datetime.fromtimestamp(cutoff_timestamp, HKT) \
            .strftime("%Y-%m-%d %H:%M:%S")
        print(f"  🔍 [{self.name}] 启动静态直出抓取（requests+re），"
              f"目标时间下限: {cutoff_dt_str}")

        cands = []
        if self._work_url:
            cands.append(self._work_url)
        if self.custom_url and self.custom_url not in cands:
            cands.append(self.custom_url)
        for tpl in self.CANDIDATE_URLS:
            for p in (1, 2):
                cands.append(tpl.format(host=self.HOST, page=p))

        scanned_in_session = set()
        should_stop = False
        diagnosed = False

        for url in cands:
            if should_stop:
                break
            resp = self._get(url)
            if resp is None or resp.status_code != 200:
                if resp is not None:
                    print(f"  ⚠️ [{self.name}] {url} -> HTTP {resp.status_code}")
                continue

            items, kind = self._parse_any(resp)
            if not items:
                if not diagnosed:
                    self._dump_diag(url, resp)   # 只详细打印一次，避免日志爆炸
                    diagnosed = True
                continue

            if not self._work_url:
                self._work_url = url
                print(f"  ✅ [{self.name}] 命中可用地址({kind}): {url} | 卡片 {len(items)} 条")

            for it in items:
                raw_content = it["content"]
                raw_time = it["raw_time"]
                raw_id = (it["raw_id"] or "").replace("immediately_id_", "")

                content_hash = hashlib.md5(raw_content.encode("utf-8")).hexdigest()[:12]
                unique_id = raw_id if raw_id else content_hash

                if unique_id in scanned_in_session:
                    continue
                scanned_in_session.add(unique_id)

                title, body = self._split_title_and_content(raw_content)

                # 1) 碰头历史记录 -> 结束
                if state_manager.is_news_scanned(unique_id):
                    print(f"  🛑 [{self.name}] 碰头历史记录 [{title[:25]}...]，结束翻页。")
                    should_stop = True
                    break

                # 2) 时间解析 + 触达水位线 -> 结束
                pub_ts, pub_time_str = self._parse_time_str(raw_time)
                if pub_ts > 0 and pub_ts <= cutoff_timestamp:
                    print(f"  🛑 [{self.name}] 触达时间下限 [{pub_time_str}]: "
                          f"[{title[:25]}...]，结束翻页。")
                    should_stop = True
                    break

                if not pub_time_str:
                    pub_time_str = datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S")

                results.append({
                    "unique_id": unique_id,
                    "title": title,
                    "content": body,
                    "source": self.name,
                    "link": urljoin(self.HOST + "/", "") + f"#flash_{unique_id}",
                    "time": pub_time_str,
                })
                state_manager.add_scanned(unique_id)

            if len(results) >= max_scroll_rounds * 20:
                break

        print(f"  └─ [{self.name}] 本次增量共收录 {len(results)} 条快讯")
        return results
