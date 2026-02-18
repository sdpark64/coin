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
K_VALUES = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]   # 비교할 K값
FETCH_DAYS = 365            # 2년치 데이터
TOTAL_CAPITAL = 10000.0
LEVERAGE = 3.0
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

# def run_single_backtest(tf, k, data_map):
#     """특정 TF와 K값으로 백테스트 1회 수행"""
#     # 1. 공통 시간축 생성 (데이터 교집합)
#     sample_df = list(data_map.values())[0]
#     time_index = sample_df.index
    
#     # 2. 지갑 초기화
#     per_coin_capital = TOTAL_CAPITAL / len(data_map)
#     wallet = {sym: per_coin_capital for sym in data_map.keys()}
    
#     equity_curve = []
    
#     # 3. 루프 실행
#     for current_time in time_index:
#         current_total_equity = 0
        
#         for sym, df in data_map.items():
#             if current_time not in df.index:
#                 current_total_equity += wallet[sym]
#                 continue
                
#             row = df.loc[current_time]
#             bal = wallet[sym]
            
#             # 목표가 계산
#             target_long = row['open'] + row['range'] * k
#             target_short = row['open'] - row['range'] * k
            
#             # [가상 매매 로직]
#             # 1. 진입했다고 가정 (Entry)
#             position = None
#             if row['high'] > target_long:
#                 position = 'long'
#                 entry_price = target_long
#             elif row['low'] < target_short:
#                 position = 'short'
#                 entry_price = target_short
                
#             # 2. 포지션이 있었다면 청산 및 정산 (Exit at Close/Open of next)
#             # 여기서는 보수적으로 '종가 청산'으로 계산
#             if position:
#                 exit_price = row['close']
#                 amount = (bal * LEVERAGE) / entry_price
                
#                 # 수수료 & 펀딩비
#                 fee = (entry_price * amount * FEE_RATE) + (exit_price * amount * FEE_RATE)
#                 fund = (entry_price * amount * FUNDING_RATE) # 1회 부과 가정
                
#                 # PnL
#                 if position == 'long':
#                     pnl = (exit_price - entry_price) * amount
#                 else:
#                     pnl = (entry_price - exit_price) * amount
                    
#                 bal += (pnl - fee - fund)
            
#             wallet[sym] = bal
#             current_total_equity += bal
            
#         equity_curve.append(current_total_equity)
        
#     return equity_curve

# def run_single_backtest(tf, k, data_map):
#     """특정 TF와 K값으로 백테스트 수행 (양방향 터치 시 손실 가정)"""
#     sample_df = list(data_map.values())[0]
#     time_index = sample_df.index
    
#     per_coin_capital = TOTAL_CAPITAL / len(data_map)
#     wallet = {sym: per_coin_capital for sym in data_map.keys()}
    
#     equity_curve = []
    
#     for current_time in time_index:
#         current_total_equity = 0
        
#         for sym, df in data_map.items():
#             if current_time not in df.index:
#                 current_total_equity += wallet[sym]
#                 continue
                
#             row = df.loc[current_time]
#             bal = wallet[sym]
            
#             target_long = row['open'] + row['range'] * k
#             target_short = row['open'] - row['range'] * k
            
#             pnl = 0
#             fee_and_fund = 0
            
#             # [수정된 매매 로직: 보수적 접근]
#             # Case 1: 양방향 터치 (가장 위험한 상황) -> 손절 처리
#             if (row['high'] > target_long) and (row['low'] < target_short):
#                 entry_price = target_long # 혹은 target_short
#                 exit_price = target_short # 반대 방향으로 손절
#                 amount = (bal * LEVERAGE) / entry_price
                
#                 # 진입가와 손절가 사이의 차액만큼 손실
#                 pnl = -abs(target_long - target_short) * amount
#                 fee_and_fund = (entry_price * amount * FEE_RATE) + (exit_price * amount * FEE_RATE) + (entry_price * amount * FUNDING_RATE)

#             # Case 2: 롱 목표가만 터치
#             elif row['high'] > target_long:
#                 entry_price = target_long
#                 exit_price = row['close']
#                 amount = (bal * LEVERAGE) / entry_price
#                 pnl = (exit_price - entry_price) * amount
#                 fee_and_fund = (entry_price * amount * FEE_RATE) + (exit_price * amount * FEE_RATE) + (entry_price * amount * FUNDING_RATE)

#             # Case 3: 숏 목표가만 터치
#             elif row['low'] < target_short:
#                 entry_price = target_short
#                 exit_price = row['close']
#                 amount = (bal * LEVERAGE) / entry_price
#                 pnl = (entry_price - exit_price) * amount
#                 fee_and_fund = (entry_price * amount * FEE_RATE) + (exit_price * amount * FEE_RATE) + (entry_price * amount * FUNDING_RATE)

#             # 지갑 업데이트
#             bal += (pnl - fee_and_fund)
#             wallet[sym] = bal
#             current_total_equity += bal
            
#         equity_curve.append(current_total_equity)
        
#     return equity_curve

# def run_single_backtest(tf, k, data_map):
#     """특정 TF와 K값으로 백테스트 수행 (8분할 및 양방향 동시 진입)"""
#     sample_df = list(data_map.values())[0]
#     time_index = sample_df.index
    
#     # [수정] 자산 관리 방식 변경: 전체 자산을 8개(4코인 x 2방향)로 나눔
#     # 처음에는 고정된 슬롯 자금으로 시작하고, 매 루프 끝에 전체 자산을 합쳐 재분배(복리)
#     total_equity = TOTAL_CAPITAL
#     equity_curve = []
    
#     for current_time in time_index:
#         # 매 봉 시작 시점에 전체 자산을 8등분하여 각 슬롯에 배분 (복리 적용)
#         slot_capital = total_equity / 8
#         new_total_equity = 0
        
#         for sym, df in data_map.items():
#             if current_time not in df.index:
#                 new_total_equity += (slot_capital * 2) # 데이터 없으면 롱/숏 슬롯 유지
#                 continue
                
#             row = df.loc[current_time]
#             target_long = row['open'] + row['range'] * k
#             target_short = row['open'] - row['range'] * k
            
#             # --- [1. 롱 슬롯 정산] ---
#             bal_l = slot_capital
#             if row['high'] > target_long:
#                 entry_p = target_long
#                 exit_p = row['close']
#                 amount = (bal_l * LEVERAGE) / entry_p
#                 pnl = (exit_p - entry_p) * amount
#                 fee = (entry_p + exit_p) * amount * FEE_RATE
#                 fund = (entry_p * amount * FUNDING_RATE)
#                 bal_l += (pnl - fee - fund)
            
#             # --- [2. 숏 슬롯 정산] ---
#             # elif 대신 if를 사용하여 롱/숏 중복 진입 허용
#             bal_s = slot_capital
#             if row['low'] < target_short:
#                 entry_p = target_short
#                 exit_p = row['close']
#                 amount = (bal_s * LEVERAGE) / entry_p
#                 pnl = (entry_p - exit_p) * amount
#                 fee = (entry_p + exit_p) * amount * FEE_RATE
#                 fund = (entry_p * amount * FUNDING_RATE)
#                 bal_s += (pnl - fee - fund)

#             # 해당 코인의 롱/숏 슬롯 결과를 전체 자산에 합산
#             new_total_equity += (bal_l + bal_s)
            
#         # 전체 자산 업데이트 및 곡선 기록
#         total_equity = new_total_equity
#         equity_curve.append(total_equity)
        
#     return equity_curve

def run_single_backtest(tf, k, data_map):
    """특정 TF와 K값으로 백테스트 수행 (4개 코인 롱 전용 분산 투자)"""
    sample_df = list(data_map.values())[0]
    time_index = sample_df.index
    
    # [수정] 자산 관리: 4개 코인 각각의 롱 슬롯에 1/4씩 배분
    total_equity = TOTAL_CAPITAL
    equity_curve = []
    
    for current_time in time_index:
        # 매 봉 시작 시점에 전체 자산을 4등분하여 각 코인 롱 슬롯에 배분 (복리)
        slot_capital = total_equity / len(data_map)
        new_total_equity = 0
        
        for sym, df in data_map.items():
            if current_time not in df.index:
                new_total_equity += slot_capital
                continue
                
            row = df.loc[current_time]
            # 롱 목표가만 계산
            target_long = row['open'] + row['range'] * k
            
            bal_l = slot_capital
            
            # --- [롱 포지션 진입 및 정산] ---
            if row['high'] > target_long:
                entry_p = target_long
                exit_p = row['close']
                amount = (bal_l * LEVERAGE) / entry_p
                
                # 수익금 계산
                pnl = (exit_p - entry_p) * amount
                # 수수료 및 펀딩비 (진입 + 청산)
                fee = (entry_p + exit_p) * amount * FEE_RATE
                fund = (entry_p * amount * FUNDING_RATE)
                
                bal_l += (pnl - fee - fund)
            
            # 숏 로직은 완전히 제외됨
            new_total_equity += bal_l
            
        # 봉 종료 후 전체 자산 갱신
        total_equity = new_total_equity
        equity_curve.append(total_equity)
        
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

