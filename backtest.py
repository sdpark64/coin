import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time

# ===============================================================
# [설정] 파라미터 범위 지정
# ===============================================================
SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]
TIMEFRAMES = ["6h", "12h", "1d"]  # 비교할 시간대
K_VALUES = [0.1, 0.2, 0.3, 0.4]   # 비교할 K값
FETCH_DAYS = 30            # 2년치 데이터
TOTAL_CAPITAL = 10000.0
LEVERAGE = 2.0
FEE_RATE = 0.0004
FUNDING_RATE = 0.0001

def fetch_all_data(symbols, timeframes, days):
    """모든 코인, 모든 타임프레임의 데이터를 미리 수집"""
    binance = ccxt.binance()
    all_data = {}
    
    print(f"📡 데이터 수집 시작 (기간: {days}일, 대상: {len(symbols)}개 코인)")
    
    for tf in timeframes:
        all_data[tf] = {}
        for sym in symbols:
            print(f"   ㄴ 수집중: {sym} [{tf}]...", end="\r")
            
            since = binance.milliseconds() - (days * 24 * 60 * 60 * 1000)
            ohlcv_list = []
            while since < binance.milliseconds():
                data = binance.fetch_ohlcv(sym, tf, since, limit=1000)
                if not data: break
                since = data[-1][0] + 1
                ohlcv_list += data
                time.sleep(0.1) # API 제한 방지
            
            df = pd.DataFrame(ohlcv_list, columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])
            df['datetime'] = pd.to_datetime(df['datetime'], unit='ms')
            df.set_index('datetime', inplace=True)
            
            # 기본 지표 계산 (Range)
            df['range'] = df['high'].shift(1) - df['low'].shift(1)
            all_data[tf][sym] = df
            
    print("\n✅ 데이터 수집 완료!")
    return all_data

def run_single_backtest(tf, k, data_map):
    """특정 TF와 K값으로 백테스트 1회 수행"""
    # 1. 공통 시간축 생성 (데이터 교집합)
    sample_df = list(data_map.values())[0]
    time_index = sample_df.index
    
    # 2. 지갑 초기화
    per_coin_capital = TOTAL_CAPITAL / len(data_map)
    wallet = {sym: per_coin_capital for sym in data_map.keys()}
    
    equity_curve = []
    
    # 3. 루프 실행
    for current_time in time_index:
        current_total_equity = 0
        
        for sym, df in data_map.items():
            if current_time not in df.index:
                current_total_equity += wallet[sym]
                continue
                
            row = df.loc[current_time]
            bal = wallet[sym]
            
            # 목표가 계산
            target_long = row['open'] + row['range'] * k
            target_short = row['open'] - row['range'] * k
            
            # [가상 매매 로직]
            # 1. 진입했다고 가정 (Entry)
            position = None
            if row['high'] > target_long:
                position = 'long'
                entry_price = target_long
            elif row['low'] < target_short:
                position = 'short'
                entry_price = target_short
                
            # 2. 포지션이 있었다면 청산 및 정산 (Exit at Close/Open of next)
            # 여기서는 보수적으로 '종가 청산'으로 계산
            if position:
                exit_price = row['close']
                amount = (bal * LEVERAGE) / entry_price
                
                # 수수료 & 펀딩비
                fee = (entry_price * amount * FEE_RATE) + (exit_price * amount * FEE_RATE)
                fund = (entry_price * amount * FUNDING_RATE) # 1회 부과 가정
                
                # PnL
                if position == 'long':
                    pnl = (exit_price - entry_price) * amount
                else:
                    pnl = (entry_price - exit_price) * amount
                    
                bal += (pnl - fee - fund)
            
            wallet[sym] = bal
            current_total_equity += bal
            
        equity_curve.append(current_total_equity)
        
    return equity_curve

def analyze_results(timeframes, k_values):
    # 1. 데이터 준비
    raw_data = fetch_all_data(SYMBOLS, TIMEFRAMES, FETCH_DAYS)
    results = []

    print("\n🔄 시뮬레이션 진행 중...")
    
    # 2. 이중 루프 (Grid Search)
    for tf in timeframes:
        # 해당 TF의 데이터만 추출
        tf_data = raw_data[tf]
        
        for k in k_values:
            print(f"   👉 Testing: Timeframe=[{tf}] / K=[{k}]...", end="\r")
            
            curve = run_single_backtest(tf, k, tf_data)
            
            # 성과 분석
            final_equity = curve[-1]
            total_ret = (final_equity - TOTAL_CAPITAL) / TOTAL_CAPITAL
            
            # CAGR
            years = FETCH_DAYS / 365.0
            cagr = (final_equity / TOTAL_CAPITAL) ** (1/years) - 1
            
            # MDD
            s = pd.Series(curve)
            peak = s.cummax()
            drawdown = (s - peak) / peak
            mdd = drawdown.min()
            
            # Calmar Ratio (수익/위험 비율)
            calmar = cagr / abs(mdd) if mdd != 0 else 0
            
            results.append({
                "TF": tf,
                "K": k,
                "Final Balance": final_equity,
                "Return": total_ret * 100,
                "CAGR": cagr * 100,
                "MDD": mdd * 100,
                "Score (Calmar)": calmar
            })
            
    # 3. 결과 출력
    df_res = pd.DataFrame(results)
    # Score(칼마 비율) 순으로 정렬
    df_res = df_res.sort_values(by="Score (Calmar)", ascending=False)
    
    print("\n\n" + "="*80)
    print(f"🏆 전략 파라미터 비교 결과 (Top 5)")
    print("="*80)
    # 보기 좋게 출력
    print(df_res.to_string(index=False, formatters={
        "Final Balance": "${:,.0f}".format,
        "Return": "{:+.2f}%".format,
        "CAGR": "{:+.2f}%".format,
        "MDD": "{:.2f}%".format,
        "Score (Calmar)": "{:.2f}".format
    }))
    print("="*80)
    
    # 최적 조합 추천
    best = df_res.iloc[0]
    print(f"\n✅ 추천 설정: 타임프레임 [{best['TF']}] / K값 [{best['K']}]")
    print(f"   (이유: 수익률 대비 MDD가 가장 우수함)")

if __name__ == "__main__":
    analyze_results(TIMEFRAMES, K_VALUES)

