# src/sources/zhitong_flash.py
import logging
import re
from scrapling.fetchers import DynamicSession
from src.config import ZHITONG_FLASH_URL
from src.sources.base import BaseSource

logger = logging.getLogger("ZhitongFlash")

class ZhitongFlashSource(BaseSource):
    def __init__(self):
        super().__init__("智通7x24")

    def fetch(self, cutoff_timestamp: int, state_manager) -> list[dict]:
        results = []
        logger.info(f"⚡ 正在爬取 [{self.name}] (原生 JS 注入雙通道)...")

        def page_action_handler(page):
            logger.info("⏳ [7x24] 等待首頁節點載入...")
            page.wait_for_selector("div.allday-item-content", timeout=30000)

            # 提取首頁分頁時間戳記
            cursor = page.evaluate("""() => {
                const box = document.querySelector('div.allday-box');
                return box ? box.getAttribute('data-page') : null;
            }""")

            # 最多調用 4 次 window.GET (覆蓋約 100 條快訊，週末充足)
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
                    f_id = item.get("immediately_id")
                    c_time = int(item.get("create_time", 0))

                    if c_time <= cutoff_timestamp:
                        stop_loading = True
                        break

                    if state_manager.is_news_scanned(str(f_id)):
                        continue

                    raw_content = item.get("content", "")
                    clean_content = re.sub(r'<[^>]+>', '', raw_content)
                    clean_content = clean_content.replace("编辑解读", "").replace("添加解读", "").strip()

                    if len(clean_content) > 15:
                        results.append({
                            "unique_id": str(f_id),
                            "title": clean_content,
                            "source": self.name,
                            "link": f"{ZHITONG_FLASH_URL}#flash_{f_id}",
                            "time": c_time
                        })
                        state_manager.add_scanned(str(f_id))

                if stop_loading:
                    logger.info("🛑 [7x24] 已追溯到 16:00 邊界時間，停止調用 API。")
                    break

                cursor = str(batch[-1].get("create_time"))

        with DynamicSession(headless=True, stealth=True, timeout=60000) as sess:
            res = sess.fetch(
                ZHITONG_FLASH_URL,
                wait_selector="div.allday-item-content",
                page_action=page_action_handler
            )

            # 補錄首頁 SSR 渲染的前 20 條
            for el in res.css("div.allday-item"):
                raw_id = el.attrib.get("id", "").replace("immediately_id_", "")
                content_nodes = el.css("div.allday-item-content")
                if not content_nodes:
                    continue

                raw_text = "".join(content_nodes[0].xpath(".//text()").getall()).strip()
                clean_text = raw_text.replace("编辑解读", "").replace("添加解读", "").strip()

                if raw_id and state_manager.is_news_scanned(raw_id):
                    continue

                if len(clean_text) > 15:
                    results.append({
                        "unique_id": raw_id,
                        "title": clean_text,
                        "source": self.name,
                        "link": f"{ZHITONG_FLASH_URL}#flash_{raw_id}" if raw_id else "",
                        "time": ""
                    })
                    if raw_id:
                        state_manager.add_scanned(raw_id)

        logger.info(f"✅ [{self.name}] 採集完畢，新增有效快訊: {len(results)} 條")
        return results
