#!/bin/bash

# Moduly 로컬 개발 환경 실행 스크립트
# 사용법: ./scripts/dev.sh

set -e

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export NODE_ENV=development

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color
SANDBOX_STARTUP_TIMEOUT_SECONDS="${SANDBOX_STARTUP_TIMEOUT_SECONDS:-180}"
SANDBOX_HEALTH_CONNECT_TIMEOUT_SECONDS="${SANDBOX_HEALTH_CONNECT_TIMEOUT_SECONDS:-2}"
SANDBOX_HEALTH_REQUEST_TIMEOUT_SECONDS="${SANDBOX_HEALTH_REQUEST_TIMEOUT_SECONDS:-5}"
DEV_GATEWAY_RELOAD_QUIET_SECONDS="${DEV_GATEWAY_RELOAD_QUIET_SECONDS:-3}"
export DEV_GATEWAY_RELOAD_QUIET_SECONDS

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

# 개발 환경은 Docker Compose와 고정 포트(3000, 8000)를 공유한다. 두 번째
# dev.sh가 시작되면 기존 환경을 종료할 수 있으므로, 한 번에 하나만 실행한다.
DEV_RUNTIME_LOCK_DIR="$PROJECT_ROOT/.nodease-dev.lock"
DEV_RUNTIME_LOCK_HELD=false

if ! mkdir "$DEV_RUNTIME_LOCK_DIR" 2> /dev/null; then
    echo -e "${RED}이미 Nodease 개발 환경이 실행 중입니다.${NC}"
    echo "기존 환경을 종료한 뒤 다시 실행하세요. 포트 3000/8000을 사용하는 dev.sh를 중복 실행할 수 없습니다."
    exit 1
fi
DEV_RUNTIME_LOCK_HELD=true

release_dev_runtime_lock() {
    if [ "$DEV_RUNTIME_LOCK_HELD" = true ]; then
        rmdir "$DEV_RUNTIME_LOCK_DIR" 2> /dev/null || true
        DEV_RUNTIME_LOCK_HELD=false
    fi
}

echo "🚀 Moduly 개발 환경 시작..."
echo "프로젝트 루트: $PROJECT_ROOT"

# Docker Compose 파일 경로 설정 (dev 환경)
export COMPOSE_FILE="$PROJECT_ROOT/dev/docker-compose.yml"

venv_uses_python_311() {
    local app_path=$1
    local venv_python

    if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "win32" ]] || [[ "$OSTYPE" == "cygwin" ]]; then
        venv_python="$app_path/.venv/Scripts/python"
    else
        venv_python="$app_path/.venv/bin/python"
    fi

    [ -x "$venv_python" ] && "$venv_python" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)' 2> /dev/null
}

# 가상환경 체크 및 자동 설정
if ! venv_uses_python_311 "apps/gateway" || \
   ! venv_uses_python_311 "apps/log_system" || \
   ! venv_uses_python_311 "apps/workflow_engine"; then
    echo -e "${YELLOW}⚠️ 일부 가상환경이 발견되지 않았습니다. 초기 설정을 진행합니다...${NC}"
    ./scripts/setup.sh
    echo -e "${GREEN}✨ 초기 설정 완료! 서비스를 시작합니다.${NC}"
fi

# Windows의 npm/uvicorn은 자식 프로세스를 남길 수 있다. 부모 PID만 종료하면
# 다음 실행에서 Next.js lock 또는 포트 충돌이 발생하므로 Windows에서는 트리 전체를 끝낸다.
stop_managed_process() {
    local pid="${1:-}"

    [ -z "$pid" ] && return 0

    if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "win32" ]] || [[ "$OSTYPE" == "cygwin" ]]; then
        taskkill.exe //PID "$pid" //T //F > /dev/null 2>&1 || true
    else
        kill "$pid" 2>/dev/null || true
    fi
}

# 정리 함수 (Ctrl+C 시 모든 프로세스 종료)
cleanup() {
    local exit_code="${1:-0}"
    trap - EXIT SIGINT SIGTERM
    echo -e "\n${YELLOW}🔥 모든 서비스 종료 중...${NC}"
    
    # 모든 백그라운드 프로세스 종료
    if [ ! -z "$DOCKER_LOG_PID" ]; then
        stop_managed_process "$DOCKER_LOG_PID"
    fi
    if [ ! -z "$DOCKER_WATCHDOG_PID" ]; then
        stop_managed_process "$DOCKER_WATCHDOG_PID"
    fi
    if [ ! -z "$LOG_CELERY_PID" ]; then
        stop_managed_process "$LOG_CELERY_PID"
    fi
    if [ ! -z "$LOG_CELERY_BEAT_PID" ]; then
        stop_managed_process "$LOG_CELERY_BEAT_PID"
    fi
    if [ ! -z "$WORKFLOW_CELERY_PID" ]; then
        stop_managed_process "$WORKFLOW_CELERY_PID"
    fi
    if [ ! -z "$FASTAPI_PID" ]; then
        stop_managed_process "$FASTAPI_PID"
    fi
    if [ ! -z "$CLIENT_PID" ]; then
        stop_managed_process "$CLIENT_PID"
    fi
    
    # Docker Compose 종료
    docker compose -f dev/docker-compose.yml down 2>/dev/null || true
    release_dev_runtime_lock
    
    echo -e "${GREEN}✅ 모든 서비스 종료 완료${NC}"
    exit "$exit_code"
}

wait_for_first_service_exit() {
    local watched_pid
    local running_pids

    while true; do
        running_pids="$(jobs -pr)"
        for watched_pid in "$@"; do
            if ! printf '%s\n' "$running_pids" | grep -qx "$watched_pid"; then
                wait "$watched_pid"
                return $?
            fi
        done
        sleep 1
    done
}

sandbox_is_healthy() {
    local request_timeout="${1:-$SANDBOX_HEALTH_REQUEST_TIMEOUT_SECONDS}"

    curl --connect-timeout "$SANDBOX_HEALTH_CONNECT_TIMEOUT_SECONDS" \
        --max-time "$request_timeout" -fsS \
        http://localhost:8194/health > /dev/null 2>&1
}

monitor_docker_services() {
    local consecutive_failures=0

    while true; do
        if docker compose -f dev/docker-compose.yml exec -T postgres \
                pg_isready -U admin -d moduly_local > /dev/null 2>&1 && \
           docker compose -f dev/docker-compose.yml exec -T redis \
                redis-cli ping > /dev/null 2>&1 && \
           sandbox_is_healthy; then
            consecutive_failures=0
        else
            consecutive_failures=$((consecutive_failures + 1))
            if [ "$consecutive_failures" -ge 3 ]; then
                echo -e "${RED}A required Docker service failed its health check.${NC}"
                return 1
            fi
        fi
        sleep 5
    done
}

wait_for_sandbox_ready() {
    local started_at
    local deadline
    local now
    local remaining
    local request_timeout

    started_at="$(date +%s)"
    deadline=$((started_at + SANDBOX_STARTUP_TIMEOUT_SECONDS))

    echo "Waiting for Sandbox readiness..."
    while true; do
        now="$(date +%s)"
        remaining=$((deadline - now))
        if [ "$remaining" -le 0 ]; then
            break
        fi
        request_timeout="$SANDBOX_HEALTH_REQUEST_TIMEOUT_SECONDS"
        if [ "$remaining" -lt "$request_timeout" ]; then
            request_timeout="$remaining"
        fi
        if sandbox_is_healthy "$request_timeout"; then
            echo -e "${GREEN}Sandbox is ready.${NC}"
            return 0
        fi
        if ! docker compose -f dev/docker-compose.yml ps --status running -q sandbox | grep -q .; then
            echo -e "${RED}Sandbox container exited before becoming ready.${NC}"
            return 1
        fi
        sleep 1
    done

    echo -e "${RED}Sandbox startup timed out after ${SANDBOX_STARTUP_TIMEOUT_SECONDS}s.${NC}"
    return 1
}

trap 'cleanup $?' EXIT
trap 'cleanup 130' SIGINT
trap 'cleanup 143' SIGTERM


# 1. Docker Compose (PostgreSQL + Redis + Sandbox) - detached 모드로 시작
echo -e "${GREEN}📦 인프라 시작 (PostgreSQL + Redis + Sandbox)...${NC}"
docker compose -f dev/docker-compose.yml up -d postgres redis pgadmin
docker compose -f dev/docker-compose.yml up -d --build sandbox # 최신 코드를 반영하기 위해 빌드

# PostgreSQL이 준비될 때까지 대기 (최대 30초)
echo "⏳ PostgreSQL 준비 대기 중..."
for i in {1..30}; do
    if docker compose -f dev/docker-compose.yml exec -T postgres pg_isready -U admin -d moduly_local > /dev/null 2>&1; then
        echo -e "${GREEN}✓ PostgreSQL 준비 완료${NC}"
        break
    fi
    if [ $i -eq 30 ]; then
        echo -e "${RED}❌ PostgreSQL 시작 실패${NC}"
        exit 1
    fi
    sleep 1
done

# Redis가 준비될 때까지 대기 (최대 10초)
echo "⏳ Redis 준비 대기 중..."
for i in {1..10}; do
    if docker compose -f dev/docker-compose.yml exec -T redis redis-cli ping > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Redis 준비 완료${NC}"
        break
    fi
    if [ $i -eq 10 ]; then
        echo -e "${RED}❌ Redis 시작 실패${NC}"
        exit 1
    fi
    sleep 1
done

if ! wait_for_sandbox_ready; then
    exit 1
fi

# Docker Compose 로그를 백그라운드에서 표시
docker compose -f dev/docker-compose.yml logs -f postgres redis sandbox &
DOCKER_LOG_PID=$!

monitor_docker_services &
DOCKER_WATCHDOG_PID=$!

if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "win32" ]] || [[ "$OSTYPE" == "cygwin" ]]; then
    LOG_SYSTEM_PYTHON="apps/log_system/.venv/Scripts/python"
else
    LOG_SYSTEM_PYTHON="apps/log_system/.venv/bin/python"
fi

# 2. Celery Worker (Log-System)
# 로그 시스템은 로컬 안정성을 위해 solo pool을 사용한다.
echo -e "${GREEN}📝 Log-System Celery Worker 시작...${NC}"
(
    export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
    PYTHONPATH="$PROJECT_ROOT" "$LOG_SYSTEM_PYTHON" -m celery -A apps.log_system.main worker -n log@%h -Q log -l info -P solo
) &
LOG_CELERY_PID=$!

sleep 1

# 3. Celery Beat (Log-System)
echo -e "${GREEN}⏱️ Log-System Celery Beat 시작...${NC}"
(
    PYTHONPATH="$PROJECT_ROOT" "$LOG_SYSTEM_PYTHON" -m celery -A apps.log_system.main beat -l info
) &
LOG_CELERY_BEAT_PID=$!

sleep 1

# 4. Celery Worker (Workflow-Engine)
echo -e "${GREEN}⚙️ Workflow-Engine Celery Worker 시작...${NC}"
(
    # OS별 Python 경로 설정
    if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "win32" ]] || [[ "$OSTYPE" == "cygwin" ]]; then
        VENV_PYTHON="apps/workflow_engine/.venv/Scripts/python"
    else
        VENV_PYTHON="apps/workflow_engine/.venv/bin/python"
    fi
    export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
    PYTHONPATH="$PROJECT_ROOT" $VENV_PYTHON -m celery -A apps.workflow_engine.main worker -n workflow@%h -Q workflow -l info -P gevent --concurrency="${WORKFLOW_CELERY_CONCURRENCY:-100}" --without-gossip --without-mingle --without-heartbeat
) &
WORKFLOW_CELERY_PID=$!

sleep 1

# 5. Gateway API 서버
echo -e "${GREEN}🖥️ Gateway API 서버 시작...${NC}"
(
    # OS별 Python 경로 설정
    if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "win32" ]] || [[ "$OSTYPE" == "cygwin" ]]; then
        VENV_PYTHON="apps/gateway/.venv/Scripts/python"
    else
        VENV_PYTHON="apps/gateway/.venv/bin/python"
    fi
    WATCHFILES_FORCE_POLLING="${WATCHFILES_FORCE_POLLING:-true}" \
        AGENT_BUILDER_DRAFT_MODEL_ID="${AGENT_BUILDER_DRAFT_MODEL_ID:-gpt-5-mini}" \
        PYTHONPATH="$PROJECT_ROOT" \
        $VENV_PYTHON scripts/dev_gateway.py
) &
FASTAPI_PID=$!

sleep 2

if ! kill -0 "$FASTAPI_PID" 2>/dev/null; then
    echo -e "${RED}Gateway API failed to start. Check whether port 8000 is already in use.${NC}"
    exit 1
fi

# 5. Next.js 클라이언트 (선택)
if [ -d "apps/client" ]; then
    echo -e "${GREEN}🌐 Next.js 클라이언트 시작...${NC}"
    (
        cd apps/client
        npm run dev
    ) &
    CLIENT_PID=$!
fi

echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}✅ 모듈리 개발 환경이 시작되었습니다!${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo "📌 접속 URL:"
echo "   - API:        http://localhost:8000"
echo "   - API 문서:   http://localhost:8000/docs"
echo "   - 프론트엔드: http://localhost:3000"
echo "   - Sandbox:    http://localhost:8194"
echo "   - pgAdmin:    http://localhost:5050"
echo ""
echo -e "${YELLOW}Ctrl+C를 누르면 모든 서비스가 종료됩니다.${NC}"
echo ""

# Stop the local environment when the first required service exits. A plain
# wait hides a dead Gateway while unrelated workers continue running.
SERVICE_PIDS=(
    "$DOCKER_WATCHDOG_PID"
    "$LOG_CELERY_PID"
    "$LOG_CELERY_BEAT_PID"
    "$WORKFLOW_CELERY_PID"
    "$FASTAPI_PID"
)
if [ -n "${CLIENT_PID:-}" ]; then
    SERVICE_PIDS+=("$CLIENT_PID")
fi

set +e
wait_for_first_service_exit "${SERVICE_PIDS[@]}"
service_exit_code=$?
set -e

if [ "$service_exit_code" -eq 0 ]; then
    service_exit_code=1
fi
echo -e "${RED}A required development service exited; cleaning up.${NC}"
cleanup "$service_exit_code"
