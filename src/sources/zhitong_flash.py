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
        """
        精準分離【粗體標題】與後續正文
        """
        text = raw_text.replace("编辑解读", "").replace("添加解读", "").strip()
        match = re.match(r'^(【.*?】)(.*)', text, re.DOTALL)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        return text[:45], text

    def fetch(self, cutoff_timestamp: int, state_manager) -> list[dict]:
        results = []

        def page_action_handler(page):
            page.wait_for_selector("div.allday-item-content", timeout=30000)

            cursor = page.evaluate("""() => {
                const box = document.querySelector('div.allday-box');
                return box ? box.getAttribute('data-page') : null;
            }""")

            # 追溯歷史快訊 (第 21 條之後)
            for round_idx in range(4):
                if not cursor:
                    break
                    
                batch = page.evaluate(f"""async () => {{
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

                stop_loading = False
                for item in batch:
                    f_id = str(item.get("immediately_id", "")).strip()
                    c_time = int(item.get("create_time", 0))

                    if c_time <= cutoff_timestamp:
                        stop_loading = True
                        break

                    if not f_id or state_manager.is_news_scanned(f_id):
                        continue

                    raw_content = item.get("content", "")
                    clean_content = re.sub(r'<[^>]+>', '', raw_content).strip()

                    if len(clean_content) > 15:
                        title, body = self._split_title_and_content(clean_content)
                        # 將 API 的秒級時間戳轉換為標準時間格式
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

                if stop_loading:
                    break

                cursor = str(batch[-1].get("create_time"))

        with DynamicSession(headless=True, stealth=True, timeout=60000) as sess:
            res = sess.fetch(
                ZHITONG_FLASH_URL,
                wait_selector="div.allday-item-content",
                page_action=page_action_handler
            )

            # 補錄首頁 SSR 渲染的最新 20 條
            today_str = datetime.now(HKT).strftime('%Y-%m-%d')
            
            for el in res.css("div.allday-item"):
                raw_id = str(el.attrib.get("id", "")).replace("immediately_id_", "").strip()
                content_nodes = el.css("div.allday-item-content")
                if not content_nodes or not raw_id:
                    continue

                if state_manager.is_news_scanned(raw_id):
                    continue

                # 抓取頁面上的時間標籤：例如 "22:52:37"
                time_nodes = el.css("div.allday-item-time")
                pub_time_str = ""
                if time_nodes:
                    node_text = "".join(time_nodes[0].xpath(".//text()").getall()).strip()
                    # 匹配 HH:MM:SS
                    time_match = re.search(r'\d{2}:\d{2}:\d{2}', node_text)
                    if time_match:
                        pub_time_str = f"{today_str} {time_match.group(0)}"

                # 分離粗體標題與內文
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

        return results
