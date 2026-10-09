# src/sources/zhitong_flash.py
import logging
import re
from datetime import datetime
from scrapling.fetchers import DynamicSession
from src.config import ZHITONG_FLASH_URL, HKT
from src.sources.base import BaseSource

logger = logging.getLogger("ZhitongFlash")

class ZhitongFlashSource(BaseSource):
    def __init__(self):
        super().__init__("智通7x24")

    def _split_title_and_content(self, raw_text: str) -> tuple[str, str]:
        text = raw_text.replace("编辑解读", "").replace("添加解读", "").strip()
        match = re.match(r'^(【.*?】)(.*)', text, re.DOTALL)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        return text[:45], text

    def fetch(self, cutoff_timestamp: int, state_manager) -> list[dict]:
        results = []

        with DynamicSession(headless=True, stealth=True, timeout=60000) as sess:
            # 1. 抓取首頁 SSR 渲染的最新 20 條
            res = sess.fetch(ZHITONG_FLASH_URL, wait_selector="div.allday-item-content")
            today_str = datetime.now(HKT).strftime('%Y-%m-%d')
            hit_existing_history = False

            for el in res.css("div.allday-item"):
                raw_id = str(el.attrib.get("id", "")).replace("immediately_id_", "").strip()
                content_nodes = el.css("div.allday-item-content")
                if not content_nodes or not raw_id:
                    continue

                # 🌟 核心增量判斷 1: 如果首頁的這條快訊上次已經抓過，說明這條之後的全是舊消息
                if state_manager.is_news_scanned(raw_id):
                    hit_existing_history = True
                    break

                # 提取時間
                time_nodes = el.css("div.allday-item-time")
                pub_time_str = ""
                if time_nodes:
                    node_text = "".join(time_nodes[0].xpath(".//text()").getall()).strip()
                    time_match = re.search(r'\d{2}:\d{2}:\d{2}', node_text)
                    if time_match:
                        pub_time_str = f"{today_str} {time_match.group(0)}"

                # 標題與內文分離
                bold_nodes = content_nodes[0].css("b, strong")
                raw_text = "".join(content_nodes[0].xpath(".//text()").getall()).strip()
                if bold_nodes:
                    bold_text = "".join(bold_nodes[0].xpath(".//text()").getall()).strip()
                    body_text = raw_text.replace(bold_text, "").replace("编辑解读", "").replace("添加解读", "").strip()
                    title = bold_text
                    body = body_text
                else:
                    title, body = self._split_title_and_content(raw_text)

                if len(title) >= 5 or len(body) > 15:
                    results.append({
                        "unique_id": raw_id,
                        "title": title,
                        "content": body,
                        "source": self.name,
                        "link": f"{ZHITONG_FLASH_URL}#flash_{raw_id}",
                        "time": pub_time_str
                    })
                    state_manager.add_scanned(raw_id)

            # 2. 如果首頁 20 條中完全沒有遇到任何舊新聞，才需要透過 JS 繼續往後翻歷史頁面
            if not hit_existing_history:
                cursor = res.attrib.get("data-page") or ""
                # 最多追溯 4 輪
                for round_idx in range(4):
                    if not cursor:
                        break
                        
                    batch = sess.page_evaluate(f"""async () => {{
                        try {{
                            const res = await window.GET("/immediately/content-list.html?type=ganggu", {{
                                last_update_time: "{cursor}"
                            }});
                            if (res && res[1] && res[1].list) {{
                                return res[1].list;
                            }}
                        }} catch (e) {{}}
                        return [];
                    }}""")

                    if not batch:
                        break

                    stop_backward = False
                    for item in batch:
                        f_id = str(item.get("immediately_id", "")).strip()
                        c_time = int(item.get("create_time", 0))

                        # 🌟 觸達時間下限 或 碰到已抓記錄 -> 停止
                        if c_time <= cutoff_timestamp or state_manager.is_news_scanned(f_id):
                            stop_backward = True
                            break

                        raw_content = item.get("content", "")
                        clean_content = re.sub(r'<[^>]+>', '', raw_content).strip()

                        if len(clean_content) > 15:
                            title, body = self._split_title_and_content(clean_content)
                            time_str = datetime.fromtimestamp(c_time, HKT).strftime('%Y-%m-%d %H:%M:%S') if c_time else ""
                            results.append({
                                "unique_id": f_id,
                                "title": title,
                                "content": body,
                                "source": self.name,
                                "link": f"{ZHITONG_FLASH_URL}#flash_{f_id}",
                                "time": time_str
                            })
                            state_manager.add_scanned(f_id)

                    if stop_backward:
                        break
                    cursor = str(batch[-1].get("create_time"))

        return results
