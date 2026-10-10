# src/feishu_notifier.py
import requests
from opencc import OpenCC
from src.config import FEISHU_WEBHOOK

class FeishuNotifier:
    def __init__(self, state_manager=None):
        self.state_manager = state_manager
        # 初始化簡轉繁轉換器 (s2t: Simplified Chinese to Traditional Chinese)
        self.cc = OpenCC('s2t')

    def to_traditional(self, text: str) -> str:
        """安全轉換為繁體字"""
        if not text:
            return ""
        try:
            return self.cc.convert(str(text))
        except Exception:
            return str(text)

    def push(self, payload: dict):
        push_time = payload.get("push_time", "")
        news_list = payload.get("news_list", [])
        
        if not news_list or not FEISHU_WEBHOOK:
            return

        date_str = push_time.split()[0] if " " in push_time else push_time
        card_title = f"📊 港股新聞監控快訊 | {date_str}"

        # 構建飛書互動卡片的 elements 陣列
        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"=== 【個股重大利好】 ===\n🔥 **本次高勝率命中異動事件: {len(news_list)} 條**"
                }
            },
            {"tag": "hr"}
        ]

        # 逐條建構結構化新聞卡片區塊
        for idx, news in enumerate(news_list, 1):
            stock_code = news.get('stock_code', '')
            stock_name = self.to_traditional(news.get('stock_name', ''))
            score = news.get('score', 0)
            confidence = news.get('confidence', 0)
            
            original_title = self.to_traditional(news.get('title', ''))
            core_event = self.to_traditional(news.get('core_event', ''))
            reason = self.to_traditional(news.get('reason', ''))
            source_tag = self.to_traditional(news.get('source', '智通'))
            
            publish_time = news.get('time', '')
            link = news.get('url', news.get('link', ''))

            # 股票顯示處理
            stock_display = f"{stock_name} ({stock_code})" if stock_code not in ["", "無", "None"] else stock_name

            # 組裝單條新聞 Markdown 內容
            news_md = f"**{idx}. 📰 {original_title}**\n"
            news_md += f"🏷️ **股票:** {stock_display} | 評分: **{score}** (置信度: {confidence})\n"
            if publish_time:
                news_md += f"⏰ **發布時間:** {publish_time} HKT\n"
            news_md += f"📌 **來源:** {source_tag}\n"
            
            if link and not link.endswith("#flash_"):
                news_md += f"🔗 **連結:** [點擊查看原文]({link})\n"
            else:
                news_md += f"🏷️ **來源:** {source_tag} (即時快訊)\n"
                
            news_md += f"🎯 **核心事件:** {core_event}\n"
            news_md += f"💡 **利好摘要:** {reason}\n"

            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": news_md
                }
            })
            elements.append({"tag": "hr"})

        # 底部推送時間
        elements.append({
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": f"⏰ 推送時間：{push_time} HKT"
                }
            ]
        })

        # 封裝飛書互動卡片 Payload
        card_payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": card_title
                    },
                    "template": "carmine"
                },
                "elements": elements
            }
        }
        
        try:
            res = requests.post(FEISHU_WEBHOOK, json=card_payload, timeout=15)
            if res.status_code == 200:
                print(f"✅ 飛書推播成功 (全繁體卡片樣式): {len(news_list)} 條")
            else:
                print(f"❌ 飛書推播失敗: {res.status_code}, {res.text}")
        except Exception as e:
            print(f"❌ 飛書請求異常: {e}")
