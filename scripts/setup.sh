#!/bin/bash

# Moduly 개발 환경 설정 및 의존성 업데이트 스크립트
# 사용법: ./scripts/setup.sh

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}🚀 Moduly 개발 환경 설정 및 의존성 업데이트를 시작합니다...${NC}"
echo "프로젝트 루트: $PROJECT_ROOT"
echo ""

# Python 버전 체크
if ! command -v python3 &> /dev/null && ! command -v py &> /dev/null; then
    echo -e "${RED}❌ Python3가 설치되어 있지 않습니다.${NC}"
    exit 1
fi

PYTHON_311=()

resolve_python_311() {
    local candidate
    local version

    # Windows에서는 App Execution Alias인 python3 대신 Python Launcher를 사용한다.
    if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "win32" ]] || [[ "$OSTYPE" == "cygwin" ]]; then
        if command -v py &> /dev/null && py -3.11 --version &> /dev/null; then
            PYTHON_311=(py -3.11)
            return 0
        fi
    fi

    for candidate in python3.11 python3 python; do
        if ! command -v "$candidate" &> /dev/null; then
            continue
        fi
        version=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2> /dev/null || true)
        if [ "$version" = "3.11" ]; then
            PYTHON_311=("$candidate")
            return 0
        fi
    done

    echo -e "${RED}Python 3.11 interpreter is required. On Windows, install it with the Python Launcher entry 'py -3.11'.${NC}"
    return 1
}

if ! resolve_python_311; then
    exit 1
fi

echo "Python interpreter: ${PYTHON_311[*]}"

# Node.js 버전 체크
if ! command -v npm &> /dev/null; then
    echo -e "${RED}❌ npm이 설치되어 있지 않습니다.${NC}"
    exit 1
fi

setup_python_app() {
    local app_name=$1
    local app_path=$2
    local install_shared=$3

    echo -e "${YELLOW}📦 [$app_name] 설정 중...${NC}"
    
    cd "$PROJECT_ROOT/$app_path"

    if [ ! -d ".venv" ]; then
        echo -e "   - 가상환경(.venv) 생성 중..."
        "${PYTHON_311[@]}" -m venv .venv
    fi

    # OS별 가상환경 경로 설정
    if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "win32" ]] || [[ "$OSTYPE" == "cygwin" ]]; then
        # Windows (Git Bash)
        VENV_PYTHON=".venv/Scripts/python"
        VENV_PIP=".venv/Scripts/pip"
    else
        # Mac/Linux
        VENV_PYTHON=".venv/bin/python"
        VENV_PIP=".venv/bin/pip"
    fi

    local venv_version
    venv_version=$("$VENV_PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2> /dev/null || true)
    if [ "$venv_version" != "3.11" ]; then
        echo -e "${RED}❌ [$app_name] .venv uses Python ${venv_version:-unknown}; Python 3.11 is required.${NC}"
        echo "   Remove $PROJECT_ROOT/$app_path/.venv and run ./scripts/setup.sh again."
        exit 1
    fi

    # pip 업그레이드
    echo -e "   - pip 업그레이드 중..."
    $VENV_PYTHON -m pip install --upgrade pip > /dev/null

    # Shared 패키지 설치 (필요한 경우)
    if [ "$install_shared" = true ]; then
        echo -e "   - Shared 패키지 설치 중..."
        $VENV_PIP install -e "$PROJECT_ROOT/apps/shared" > /dev/null
    fi

    # 의존성 설치
    echo -e "   - 의존성 설치/업데이트 중..."
    $VENV_PIP install -e . > /dev/null

    # 프로젝트 루트로 돌아가기
    cd "$PROJECT_ROOT"

    echo -e "${GREEN}✓ [$app_name] 설정 완료${NC}"
    echo ""
}

setup_node_app() {
    local app_name=$1
    local app_path=$2

    echo -e "${YELLOW}🌐 [$app_name] 설정 중...${NC}"
    
    cd "$PROJECT_ROOT/$app_path"

    # npm 의존성 설치
    echo -e "   - npm install 실행 중..."
    npm install > /dev/null

    echo -e "${GREEN}✓ [$app_name] 설정 완료${NC}"
    echo ""
}

# 1. Gateway 설정
setup_python_app "Gateway" "apps/gateway" true

# 2. Log System 설정
setup_python_app "Log System" "apps/log_system" true

# 3. Workflow Engine 설정
setup_python_app "Workflow Engine" "apps/workflow_engine" true

# 4. Shared (테스트용 등 필요시) - Shared는 보통 다른 앱에 의존성으로 설치되지만, 
# 독립적인 개발을 위해 venv가 필요할 수도 있음. 여기서는 생략하거나 필요시 추가.

# 5. Client 설정
if [ -d "apps/client" ]; then
    setup_node_app "Client" "apps/client"
else
    echo -e "${YELLOW}⚠️ apps/client 디렉토리가 없어 Client 설정을 건너뜁니다.${NC}"
fi

echo -e "${GREEN}✨ 모든 설정이 완료되었습니다!${NC}"
echo -e "이제 ${YELLOW}./scripts/dev.sh${NC}를 실행하여 개발 환경을 시작할 수 있습니다."
