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

# ===============================================================
# [초기 설정] 로깅 및 거래소 연결
# ===============================================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger()

binance = ccxt.binance({
    'apiKey': config.BINANCE_API_KEY,
    'secret': config.BINANCE_SECRET,
    'options': {'defaultType': 'future'},
    'enableRateLimit': True
})

# [전역 변수 - 상태 공유용]
bot_state = {
    "is_active": True,          
    "temp_pause": False,        
    "period_capital": 0.0,      
    "positions": {sym: False for sym in config.SYMBOLS},
    "targets": {sym: {"long": 0.0, "short": 0.0} for sym in config.SYMBOLS},
    "last_update_id": 0         
}

LOG_FILE = "trade_history.csv"

# ===============================================================
# [유틸리티] 로그 저장 및 상태 동기화
# ===============================================================
def write_trade_log(action, symbol, price, amount, note=""):
    try:
        file_exists = os.path.isfile(LOG_FILE)
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(['Time', 'Action', 'Symbol', 'Price', 'Amount', 'Value', 'Note'])
            writer.writerow([now, action, symbol, price, amount, f"{price*amount:.2f}", note])
    except Exception as e:
        logger.error(f"로그 저장 실패: {e}")

def set_leverage_all():
    """시작 시 모든 코인의 레버리지 설정"""
    for sym in config.SYMBOLS:
        try:
            binance.set_leverage(config.LEVERAGE, sym)
            logger.info(f"✅ {sym} 레버리지 {config.LEVERAGE}배 설정 완료")
        except Exception as e:
            logger.error(f"⚠️ {sym} 레버리지 설정 실패: {e}")

def sync_positions():
    """거래소 실제 포지션과 봇 상태 동기화 (재시작 시 필수)"""
    try:
        exchange_pos = binance.fetch_positions()
        for sym in config.SYMBOLS:
            bot_state["positions"][sym] = False # 초기화

        for pos in exchange_pos:
            sym = pos['symbol']
            if sym in config.SYMBOLS:
                amt = abs(float(pos['contracts']))
                if amt > 0:
                    side = pos['side'].upper()
                    bot_state["positions"][sym] = side
                    logger.info(f"🔄 동기화 완료: {sym} 보유 중 ({side})")
    except Exception as e:
        logger.error(f"❌ 포지션 동기화 실패: {e}")

# ===============================================================
# [기능 1] 텔레그램 리스너 (HTML 모드 메시지 대응)
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
        except: time.sleep(1)

def handle_command(command):
    if command.lower() in ["/info", "info"]:
        send_status_report()
    elif command.lower() in ["/stop", "stop"]:
        bot_state["is_active"] = False
        telegram_notifier.send_telegram_message("⛔ <b>[매수 정지]</b> 신규 진입을 중단합니다.")
    elif command.lower() in ["/start", "start"]:
        bot_state["is_active"] = True
        bot_state["temp_pause"] = False
        telegram_notifier.send_telegram_message("✅ <b>[매수 재개]</b> 봇이 정상 가동됩니다.")
    elif command.lower() in ["/sell", "sell"]:
        telegram_notifier.send_telegram_message("🚨 <b>[긴급 매도]</b> 전량 청산 및 일시 정지")
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
        
        for p in pos_data:
            sym = p['symbol']
            if sym in config.SYMBOLS and abs(float(p['contracts'])) > 0:
                pnl = float(p['unrealizedPnl'])
                total_pnl += pnl
                icon = "🟢" if pnl >= 0 else "🔴"
                pos_msg += f"{icon} <b>{sym.split('/')[0]}</b>: <code>${pnl:+.2f}</code>\n"

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
    except Exception as e:
        logger.error(f"리포트 에러: {e}")

# ===============================================================
# [기능 2] 매매 로직 (숏 거래 포함 + 안전장치)
# ===============================================================
def get_next_start_time():
    """다음 타임프레임 시작 시간(UTC 00:00, 12:00) 계산"""
    now_utc = datetime.datetime.utcnow()
    candidates = [
        now_utc.replace(hour=0, minute=0, second=0, microsecond=0),
        now_utc.replace(hour=12, minute=0, second=0, microsecond=0),
        now_utc.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1),
        now_utc.replace(hour=12, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1)
    ]
    # 현재 시간보다 미래인 가장 가까운 시간 찾기
    for t in sorted(candidates):
        if t > now_utc:
            return t
    return candidates[-1]

def update_targets():
    msg = "🎯 <b>[새로운 타임프레임 시작]</b>\n"
    bot_state["temp_pause"] = False
    
    # 입금 반영을 위해 새로 잔고 조회
    try:
        bal = binance.fetch_balance()
        bot_state["period_capital"] = bal['USDT']['free'] / len(config.SYMBOLS)
    except: pass

    for sym in config.SYMBOLS:
        try:
            ohlcv = binance.fetch_ohlcv(sym, timeframe=config.TIMEFRAME, limit=2)
            rng = (ohlcv[-2][2] - ohlcv[-2][3]) * config.K_VALUE
            bot_state["targets"][sym] = {
                "long": ohlcv[-1][1] + rng,
                "short": ohlcv[-1][1] - rng
            }
            msg += f"- <b>{sym.split('/')[0]}</b>: L <code>{bot_state['targets'][sym]['long']:,.2f}</code> / S <code>{bot_state['targets'][sym]['short']:,.2f}</code>\n"
        except: pass
    telegram_notifier.send_telegram_message(msg)

def check_entry():
    if not bot_state["is_active"] or bot_state["temp_pause"]: return

    for sym in config.SYMBOLS:
        if bot_state["positions"][sym]: continue

        try:
            ticker = binance.fetch_ticker(sym)
            curr = ticker['last']
            tg = bot_state["targets"][sym]
            
            # 1. 진입 방향 결정
            enter_side = None
            if curr > tg['long']: enter_side = "LONG"
            elif curr < tg['short']: enter_side = "SHORT"

            if enter_side:
                # 2. [안전장치] 실제 주문 전 가용 잔고(USDT) 확인
                bal = binance.fetch_balance()
                free_usdt = bal['USDT']['free']
                
                # 주문 예정 금액 (레버리지 적용 전 증거금)
                order_cost = bot_state["period_capital"]
                
                # 잔고가 할당액보다 적으면, 잔고만큼만 진입 (수수료 여유 1% 제외)
                if free_usdt < order_cost:
                    logger.warning(f"⚠️ {sym} 잔고 부족 ({free_usdt} < {order_cost}). 가용 잔고로 조정.")
                    order_cost = free_usdt * 0.99 
                
                # 3. 최소 주문 금액 체크 ($5 미만이면 스킵)
                if order_cost < 5.0:
                    logger.warning(f"⛔ {sym} 주문액 $5 미만. 진입 취소.")
                    continue

                # 4. 최종 수량 계산
                amount_usdt = order_cost * config.LEVERAGE
                amount = binance.amount_to_precision(sym, amount_usdt / curr)
                
                # 5. 주문 실행
                if enter_side == "LONG":
                    binance.create_market_buy_order(sym, amount)
                    bot_state["positions"][sym] = "LONG"
                    write_trade_log("BUY_LONG", sym, curr, amount)
                    telegram_notifier.send_telegram_message(f"⚡ <b>[LONG 진입]</b> {sym} @ <code>{curr}</code>")
                
                elif enter_side == "SHORT":
                    binance.create_market_sell_order(sym, amount)
                    bot_state["positions"][sym] = "SHORT"
                    write_trade_log("SELL_SHORT", sym, curr, amount)
                    telegram_notifier.send_telegram_message(f"📉 <b>[SHORT 진입]</b> {sym} @ <code>{curr}</code>")

        except Exception as e:
            logger.error(f"{sym} 진입 에러: {e}")

def close_all_positions(reason="Time End"):
    msg = f"👋 <b>[청산 실행]</b> 사유: {reason}\n"
    has_trade = False
    try:
        # 봇의 상태가 아닌, 거래소의 실제 포지션을 조회하여 청산 (동기화 보장)
        exchange_pos = binance.fetch_positions()
        for p in exchange_pos:
            sym = p['symbol']
            if sym in config.SYMBOLS:
                amt = abs(float(p['contracts']))
                if amt > 0:
                    side = p['side'].upper()
                    
                    # [핵심 수정] params={'reduceOnly': True} 추가
                    # 이 옵션이 있어야 포지션이 0일 때 반대 포지션이 잡히는 것을 막아줍니다.
                    params = {'reduceOnly': True}
                    
                    try:
                        if side == 'LONG': 
                            binance.create_market_sell_order(sym, amt, params=params)
                        else: 
                            binance.create_market_buy_order(sym, amt, params=params)
                        
                        write_trade_log("EXIT", sym, 0, amt, reason)
                        msg += f"- {sym.split('/')[0]} {side} 청산\n"
                        has_trade = True
                        
                    except Exception as order_err:
                        # 이미 청산된 경우 등 주문 실패 시 로그만 남기고 넘어감
                        logger.warning(f"{sym} 청산 주문 스킵/실패 (이미 청산됨?): {order_err}")

                    # 봇 상태 업데이트
                    bot_state["positions"][sym] = False
                    
        if has_trade:
            telegram_notifier.send_telegram_message(msg)
            
    except Exception as e:
        logger.error(f"청산 전체 로직 오류: {e}")

# ===============================================================
# [메인 루프]
# ===============================================================
def main():
    # 1. 봇 가동 준비
    set_leverage_all()
    sync_positions()
    
    # 텔레그램 리스너 시작
    threading.Thread(target=telegram_listener, daemon=True).start()
    
    # 2. 시작 대기 로직 (설명과 일치시킨 부분)
    next_start = get_next_start_time()
    next_kst = next_start + datetime.timedelta(hours=9)
    
    msg = "🤖 <b>봇 가동 시작</b> (대기 모드)\n"
    msg += f"⏳ 다음 시작 시간(KST {next_kst.strftime('%H:%M')})까지 대기합니다."
    telegram_notifier.send_telegram_message(msg)
    
    # 시간이 될 때까지 1초씩 대기
    while datetime.datetime.utcnow() < next_start:
        time.sleep(1)
        
    # 3. 타임프레임 시작! (목표가 갱신)
    telegram_notifier.send_telegram_message("🚀 <b>타임프레임 시작!</b> 목표가를 갱신하고 매매를 시작합니다.")
    update_targets() 
    
    # 4. 무한 감시 루프
    while True:
        try:
            now_utc = datetime.datetime.utcnow()
            
            # [청산 로직] 11:50, 23:50 (마감 10분 전)
            if now_utc.minute == 50 and (now_utc.hour == 11 or now_utc.hour == 23):
                close_all_positions(reason="Timeframe End")
                
                telegram_notifier.send_telegram_message("💤 <b>휴식</b> 다음 봉 시작까지 대기...")
                time.sleep(601) # 10분 10초 대기 (정각 넘기기)
                
                # 새 봉 시작 후 처리
                update_targets() 
                sync_positions()
            
            # [진입 로직]
            check_entry()
            time.sleep(1) # API 과부하 방지 (0.1초는 너무 빠름)
            
        except Exception as e:
            logger.error(f"메인 루프 에러: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()