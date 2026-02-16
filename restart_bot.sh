#!/bin/bash

# ==============================================================================
# 설정 변수
# ==============================================================================
# 프로젝트 경로 (파일 위치)
PROJECT_DIR="/home/ubuntu/coin/binance"

# 실행할 파이썬 파일명
SCRIPT_NAME="coin_bot.py"

# 가상환경 경로 (home/stock 폴더가 가상환경 루트라고 가정)
# 만약 가상환경 이름이 별도로 있다면 "/home/stock/venv" 등으로 수정 필요
VENV_ACTIVATE="/home/ubuntu/coin/bin/activate"

# ==============================================================================
# 1. 기존 프로세스 종료
# ==============================================================================
echo "Checking for running process: $SCRIPT_NAME..."

# 실행 중인 프로세스 ID 찾기 (전체 커맨드 라인 매칭)
PID=$(pgrep -f "python3 $SCRIPT_NAME")

if [ -n "$PID" ]; then
    echo "Found running process (PID: $PID). Killing..."
    kill -9 $PID
    sleep 2 # 프로세스가 완전히 죽을 때까지 잠시 대기
    echo "Process killed."
else
    echo "No running process found."
fi

# ==============================================================================
# 2. 디렉토리 이동 및 가상환경 실행
# ==============================================================================
# 프로젝트 폴더로 이동
cd "$PROJECT_DIR" || { echo "Directory not found! Exiting."; exit 1; }
echo "Moved to: $(pwd)"

# # 가상환경 활성화
if [ -f "$VENV_ACTIVATE" ]; then
    echo "Activating virtual environment..."
    source "$VENV_ACTIVATE"
else
    echo "Error: Virtual environment not found at $VENV_ACTIVATE"
    exit 1
fi

# ==============================================================================
# 3. 프로세스 재실행
# ==============================================================================
echo "Starting $SCRIPT_NAME..."

# nohup으로 백그라운드 실행
nohup python3 "$SCRIPT_NAME" > output.log 2>&1 &

# 새로 실행된 프로세스 ID 출력
NEW_PID=$!
echo "Success! $SCRIPT_NAME started with PID: $NEW_PID"

# ==============================================================================
# 4. 가상환경 종료
# ==============================================================================
deactivate
echo "Virtual environment deactivated."