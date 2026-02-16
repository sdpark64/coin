# telegram_notifier.py

import requests
import time
import config

# ==============================================================================
# 📞 텔레그램 알림 함수
# ==============================================================================

def send_telegram_message(message):
    """텔레그램 봇으로 메시지를 전송하는 함수"""
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': config.TELEGRAM_CHAT_ID,
        'text': message,
        # 'parse_mode': 'Markdown'
        'parse_mode': 'HTML'
    }
    try:
        response = requests.post(url, data=payload)
        response.raise_for_status() 
        time.sleep(config.TIME_SLEEP)
        return True
    except Exception as e:
        print(f"[텔레그램] 메시지 전송 실패: {e}")
        return False