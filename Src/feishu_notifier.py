# src/feishu_notifier.py
import requests
from src.config import FEISHU_WEBHOOK

class FeishuNotifier:
    def __init__(self, state_manager=None):
        self.state_manager = state_manager

    def push(self, payload: dict):
        push_time = payload.get("push_time")
        news_list = payload.get("news_list", [])
        
        if not news_list or not FEISHU_WEBHOOK:
            return

        combined_text = f"**📊 港股量化異動監控 (HKT):** {push_time}\n"
        combined_text += f"**🔥 本次命中異動事件: {len(news_list)} 條**\n\n"
        
        for idx, news in enumerate(news_list, 1):
            stock_code = news.get('stock_code', '')
            stock_name = news.get('stock_name', '')
            score = news.get('score', 0)
            original_title = news.get('title', '')
            source_tag = news.get('source', '智通')
            publish_time = news.get('time', '')
            link = news.get('url', news.get('link', ''))

            combined_text += f"**{idx}. {stock_name} ({stock_code})** | 評分: **{score}**\n"
            if publish_time:
                combined_text += f"🕒 **時間:** {publish_time}\n"
            combined_text += f"📌 **標題:** {original_title}\n"
            combined_text += f"🎯 **核心事件:** {news.get('core_event', '')}\n"
            combined_text += f"💡 **利好邏輯:** {news.get('reason', '')}\n"
            
            if link and not link.endswith("#flash_"):
                combined_text += f"🔗 [查看原文鏈接]({link})\n\n"
            else:
                combined_text += f"🏷️ **來源:** {source_tag} (即時快訊)\n\n"
            
        card_payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": "🚀 港股高勝率利好消息提醒"
                    },
                    "template": "carmine"
                },
                "elements": [
                    {
                        "tag": "markdown",
                        "content": combined_text
                    }
                ]
            }
        }
        
        try:
            res = requests.post(FEISHU_WEBHOOK, json=card_payload, timeout=15)
            if res.status_code == 200:
                print(f"✅ 飛書推播成功: {len(news_list)} 條")
            else:
                print(f"❌ 飛書推播失敗: {res.status_code}, {res.text}")
        except Exception as e:
            print(f"❌ 飛書請求異常: {e}")
