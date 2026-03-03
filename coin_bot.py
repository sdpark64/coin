import ccxt
import time
import datetime
import logging
import threading
import requests
import traceback
import csv
import os
import config
import telegram_notifier
from datetime import timezone

# ===============================================================
# [초기 설정]
# ===============================================================
logger = logging.getLogger()
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')

# 1. 표준 출력 핸들러 (터미널 및 > output.log 명령어로 전달됨)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)
logger.addHandler(stream_handler)

# 2. 파일 직접 저장 핸들러 (코드에서 직접 output.log에 기록)
# 백그라운드 실행 시 파일 쓰기 지연을 방지하기 위해 사용합니다.
file_handler = logging.FileHandler('output.log', encoding='utf-8')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)
binance = ccxt.binance({
    'apiKey': config.BINANCE_API_KEY,
    'secret': config.BINANCE_SECRET,
    'options': {'defaultType': 'future'},
    'enableRateLimit': True
})

# [전역 변수]
bot_state = {
    "is_active": True,          
    "temp_pause": False,        
    "period_capital": 0.0,      
    "positions": {sym: False for sym in config.SYMBOLS},
    "targets": {sym: {"long": 0.0} for sym in config.SYMBOLS}, # short 삭제
    "last_update_id": 0,
    "last_close_slot": None 
}

LOG_FILE = "trade_history.csv"

# ===============================================================
# [유틸리티]
# ===============================================================
def write_trade_log(action, symbol, price, amount, note=""):
    try:
        file_exists = os.path.isfile(LOG_FILE)
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        val_price = float(price)
        val_amount = float(amount)
        total_value = val_price * val_amount

        with open(LOG_FILE, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(['Time', 'Action', 'Symbol', 'Price', 'Amount', 'Value', 'Note'])
            writer.writerow([now, action, symbol, val_price, val_amount, f"{total_value:.2f}", note])
    except Exception as e:
        logger.error(f"로그 저장 실패: {e}")

def set_leverage_all():
    for sym in config.SYMBOLS:
        try:
            binance.set_leverage(config.LEVERAGE, sym)
            logger.info(f"✅ {sym} 레버리지 {config.LEVERAGE}배 설정 완료")
        except Exception as e:
            logger.error(f"⚠️ {sym} 레버리지 설정 실패: {e}")

def sync_positions():
    try:
        for sym in config.SYMBOLS:
            bot_state["positions"][sym] = False 

        exchange_pos = binance.fetch_positions()
        for pos in exchange_pos:
            market_sym = pos['symbol'].split(':')[0] 
            if market_sym in config.SYMBOLS:
                amt = float(pos['contracts'])
                if abs(amt) > 0.00001: # 먼지 잔고 필터링
                    side = pos['side'].upper()
                    bot_state["positions"][market_sym] = side
                    logger.info(f"🔄 동기화 확인: {market_sym} 보유 중 ({side})")
    except Exception as e:
        logger.error(f"❌ 포지션 동기화 실패: {e}")

# ===============================================================
# [텔레그램]
# ===============================================================
def get_telegram_updates(offset=None):
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates"
    try:
        response = requests.get(url, params={'timeout': 10, 'offset': offset}).json()
        return response.get("result", [])
    except: return []

def telegram_listener():
    logger.info("📡 텔레그램 리스너 시작")
    while True:
        try:
            updates = get_telegram_updates(bot_state["last_update_id"] + 1)
            for update in updates:
                bot_state["last_update_id"] = update["update_id"]
                if "message" in update and "text" in update["message"]:
                    text = update["message"]["text"].strip()
                    if str(update["message"]["chat"]["id"]) == str(config.TELEGRAM_CHAT_ID):
                        handle_command(text)
            time.sleep(1)
        except Exception as e:
            # 에러 발생 시 파일에 로그를 반드시 기록하도록 수정
            logger.error(f"❌ 텔레그램 리스너 오류: {e}")
            logger.error(traceback.format_exc()) # 상세 에러 스택 기록
            time.sleep(5) # 에러 시 잠시 대기

def handle_command(command):
    if command.lower() in ["/info", "info"]:
        send_status_report()
    elif command.lower() in ["/stop", "stop"]:
        bot_state["is_active"] = False
        telegram_notifier.send_telegram_message("⛔ <b>[매수 정지]</b>")
    elif command.lower() in ["/start", "start"]:
        bot_state["is_active"] = True
        bot_state["temp_pause"] = False
        telegram_notifier.send_telegram_message("✅ <b>[매수 재개]</b>")
    elif command.lower() in ["/sell", "sell"]:
        telegram_notifier.send_telegram_message("🚨 <b>[긴급 매도]</b>")
        close_all_positions(reason="User Command")
        bot_state["temp_pause"] = True

def send_status_report():
    try:
        bal = binance.fetch_balance()
        wallet_bal = bal['USDT']['total']
        free_bal = bal['USDT']['free']
        pos_data = binance.fetch_positions()
        
        total_pnl = 0.0
        pos_msg = ""
        has_position = False
        
        for p in pos_data:
            amt = float(p['contracts'])
            if abs(amt) > 0:
                sym = p['symbol']
                pnl = float(p.get('unrealizedPnl', 0))
                side = p['side'].upper()
                
                raw_leverage = p.get('leverage')
                if raw_leverage is None:
                    if 'info' in p and 'leverage' in p['info']:
                        leverage = int(float(p['info']['leverage']))
                    else:
                        leverage = config.LEVERAGE
                else:
                    leverage = int(float(raw_leverage))
                
                notional = float(p.get('notional', 0))
                
                if leverage > 0 and abs(notional) > 0:
                    margin = abs(notional) / leverage
                    roi = (pnl / margin) * 100 if margin > 0 else 0
                else:
                    roi = 0.0 # 레버리지나 노셔널 값이 비정상일 때 대비

                total_pnl += pnl
                icon = "🔴" if pnl < 0 else "🟢"
                pos_msg += f"{icon} <b>{sym.split('/')[0]}</b> ({side}): <code>${pnl:+.2f}</code> (<code>{roi:+.1f}%</code>)\n"
                has_position = True

        total_equity = wallet_bal + total_pnl
        status = "🟢 가동중" if bot_state["is_active"] else "🔴 정지됨"
        if bot_state["temp_pause"]: status = "🔒 일시잠금"

        msg = f"📊 <b>[자산 현황]</b>\n상태: {status}\n"
        msg += f"💰 <b>총 자산: <code>${total_equity:,.2f}</code></b>\n"
        msg += f"💵 주문가능: <code>${free_bal:,.2f}</code>\n"
        msg += "-" * 20 + "\n"
        msg += pos_msg if pos_msg else "💤 보유 포지션 없음\n"
        msg += f"💼 프레임 할당액: <code>${bot_state['period_capital']:,.2f}</code>"
        telegram_notifier.send_telegram_message(msg)
    except Exception as e: logger.error(f"리포트 에러: {e}")

# ===============================================================
# [매매 로직]
# ===============================================================

def get_next_start_time():
    now_utc = datetime.datetime.now(timezone.utc)
    # 오늘 자정(UTC 00:00) 기준
    base_date = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    # 다음 날 자정
    next_start = base_date + datetime.timedelta(days=1)
    return next_start

def update_targets(is_restart=False):
    if is_restart:
        msg = "♻️ <b>[시스템 복구 모드]</b> 롱 목표가를 재계산하고 매매를 재개합니다.\n"
    else:
        msg = "🎯 <b>[새로운 타임프레임 시작]</b>\n"
        bot_state["temp_pause"] = False
    
    try:
        bal = binance.fetch_balance()
        bot_state["period_capital"] = bal['USDT']['total'] / len(config.SYMBOLS)
    except: pass

    for sym in config.SYMBOLS:
        try:
            ohlcv = binance.fetch_ohlcv(sym, timeframe=config.TIMEFRAME, limit=2)
            # 변동성 계산 (전일 고가 - 전일 저가) * K
            rng = (ohlcv[-2][2] - ohlcv[-2][3]) * config.K_VALUE
            
            # 롱 타겟만 저장 (Short 제거)
            bot_state["targets"][sym] = {
                "long": ohlcv[-1][1] + rng
            }
            msg += f"- {sym.split('/')[0]}: Long Target {bot_state['targets'][sym]['long']:,.2f}\n"
        except Exception as e:
            logger.error(f"{sym} 타겟 계산 실패: {e}")
    
    telegram_notifier.send_telegram_message(msg)
    sync_positions()

def check_entry():
    if not bot_state["is_active"] or bot_state["temp_pause"]: return

    for sym in config.SYMBOLS:
        # 이미 포지션이 있으면 스킵
        if bot_state["positions"][sym]: continue

        try:
            ticker = binance.fetch_ticker(sym)
            curr = ticker['last']
            tg_long = bot_state["targets"][sym]['long']
            
            # 롱 진입 조건만 확인
            if curr > tg_long:
                # [안전장치] 중복 진입 방지
                is_duplicate = False
                positions = binance.fetch_positions()
                for p in positions:
                    market_sym = p['symbol'].split(':')[0]
                    if market_sym == sym:
                        if abs(float(p['contracts'])) > 0.00001: 
                            is_duplicate = True
                            bot_state["positions"][sym] = p['side'].upper()
                            break
                
                if is_duplicate:
                    continue

                # 주문 수량 계산
                bal = binance.fetch_balance()
                free_usdt = bal['USDT']['free']
                order_cost = bot_state["period_capital"]
                if free_usdt < order_cost: order_cost = free_usdt * 0.99 
                if order_cost < 5.0: continue

                amount_usdt = order_cost * config.LEVERAGE
                amount = binance.amount_to_precision(sym, amount_usdt / curr)
                
                # 시장가 매수 주문
                binance.create_market_buy_order(sym, amount)
                bot_state["positions"][sym] = "LONG"
                write_trade_log("BUY_LONG", sym, curr, amount)
                telegram_notifier.send_telegram_message(f"⚡ <b>[LONG 진입]</b> {sym} @ {curr}")

        except Exception as e:
            logger.error(f"{sym} 진입 에러: {e}")

def close_all_positions(reason="Time End"):
    msg = f"👋 <b>[청산 실행]</b> 사유: {reason}\n"
    has_trade = False
    try:
        exchange_pos = binance.fetch_positions()
        for p in exchange_pos:
            order_symbol = p['symbol'] # 주문용 (BTC/USDT:USDT)
            market_sym = order_symbol.split(':')[0] # 내부용 (BTC/USDT)
            
            if market_sym in config.SYMBOLS:
                amt = abs(float(p['contracts']))
                if amt > 0.00001: # 먼지 잔고 무시
                    side = p['side'].upper()
                    params = {'reduceOnly': True}
                    try:
                        if side == 'LONG': binance.create_market_sell_order(order_symbol, amt, params=params)
                        else: binance.create_market_buy_order(order_symbol, amt, params=params)
                        
                        write_trade_log("EXIT", market_sym, 0, amt, reason)
                        msg += f"- {market_sym.split('/')[0]} {side} 청산\n"
                        bot_state["positions"][market_sym] = False
                        has_trade = True
                    except Exception as order_err:
                        logger.warning(f"{market_sym} 청산 주문 실패: {order_err}")
        if has_trade:
            telegram_notifier.send_telegram_message(msg)
    except Exception as e:
        logger.error(f"청산 오류: {e}")

# ===============================================================
# [메인 루프] - 핵심 수정 부분
# ===============================================================
# ===============================================================
# [메인 루프] - 재실행 시 대기 로직 최적화
# ===============================================================
def main():
    set_leverage_all()
    threading.Thread(target=telegram_listener, daemon=True).start()
    
    telegram_notifier.send_telegram_message("🤖 <b>봇 재가동</b> 시간 동기화 중...")

    # [1] 현재 시간 체크
    now_utc = datetime.datetime.now(timezone.utc)
    is_break_time = False
    
    # UTC 23:50 ~ 23:59 (일봉 마감 10분 전) 인지 확인
    if now_utc.hour == 23 and now_utc.minute >= 50:
        is_break_time = True
    
    # [2] 분기 처리
    if is_break_time:
        # (A) 휴식 시간에 켜졌다면: 아무것도 안 하고 청산 후 대기
        bot_state["last_close_slot"] = f"{now_utc.date()}_{now_utc.hour}"
        
        next_start = get_next_start_time()
        next_kst = next_start + datetime.timedelta(hours=9)
        
        msg = f"💤 <b>[휴식 시간 재시작]</b> 마감 임박({now_utc.strftime('%H:%M')})으로 인해 매매를 쉬고,\n"
        msg += f"다음 시작 시간(KST {next_kst.strftime('%H:%M')})까지 대기합니다."
        telegram_notifier.send_telegram_message(msg)
        
        # 혹시 들고 있을 포지션 정리
        close_all_positions(reason="Restart inside Break Time")
        
        # 12:00 / 00:00 될 때까지 무한 대기
        while datetime.datetime.now(timezone.utc) < next_start:
            time.sleep(1)
            
        time.sleep(10) # 캔들 생성 대기
        telegram_notifier.send_telegram_message("🚀 <b>새로운 타임프레임 시작!</b>")
        update_targets(is_restart=False)

    else:
        # (B) 매매 시간에 켜졌다면: 즉시 복구 및 매매 재개
        update_targets(is_restart=True)
        telegram_notifier.send_telegram_message("✅ <b>[매매 재개]</b> 기존 포지션이 있다면 유지하고, 신규 진입을 감시합니다.")
    
    # [3] 메인 감시 루프 진입
    while True:
        try:
            now_utc = datetime.datetime.now(timezone.utc)
            
            # UTC 23:50 ~ 23:59 사이: 휴식 및 청산 로직
            if now_utc.hour == 23 and now_utc.minute >= 50:
                current_slot = f"{now_utc.date()}_{now_utc.hour}"

                # 이미 이번 타임 청산을 완료했다면, 추가 청산 없이 대기만 함
                if bot_state["last_close_slot"] == current_slot:
                    time.sleep(10) # 루프 과부하 방지
                    continue

                # 청산 실행
                bot_state["last_close_slot"] = current_slot
                close_all_positions(reason="Timeframe End")
                telegram_notifier.send_telegram_message("💤 <b>휴식</b> 다음 봉 시작까지 대기...")
                
                # 10분+알파 대기 (다음 봉 시작 12:00/00:00 넘길 때까지)
                time.sleep(601) 
                
                time.sleep(10) 
                update_targets(is_restart=False) 
            
            else:
                # 평상시: 진입 감시
                check_entry()
                time.sleep(1)
            
        except Exception as e:
            logger.error(f"메인 루프 에러: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()

