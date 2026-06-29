#!/bin/bash
set -euo pipefail

# ============================================
# 用户配置区域（请根据需要修改以下变量）
# ============================================
IMAGE_NAME="tlma-gateway"                          # 镜像名称
IMAGE_TAG="local"                                   # 镜像标签
DOCKERFILE="backend/Dockerfile"                     # Dockerfile 路径（相对于项目根目录）
COMPOSE_FILE="docker/docker-compose.local.yml"      # docker-compose 配置文件（相对于项目根目录）
AUTO_START=true                                     # 构建完成后是否自动启动容器

# ============================================
# 以下为脚本逻辑，一般无需修改
# ============================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

log_info() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] $*"
}

log_error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] $*" >&2
}

log_warn() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WARN] $*"
}

# 确保 Docker 守护进程已启动
ensure_docker_running() {
    if docker info >/dev/null 2>&1; then
        return 0
    fi

    log_warn "Docker 守护进程未运行，尝试启动..."

    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*)
            local docker_exe="/c/Program Files/Docker/Docker/Docker Desktop.exe"
            if [ -f "$docker_exe" ]; then
                start "" "$docker_exe" 2>/dev/null &
                log_info "正在启动 Docker Desktop..."
            else
                log_error "未找到 Docker Desktop，请手动启动"
                exit 1
            fi
            ;;
        Linux)
            if command -v systemctl >/dev/null 2>&1; then
                sudo systemctl start docker 2>/dev/null || {
                    log_error "无法启动 Docker 服务，请手动启动"
                    exit 1
                }
                log_info "正在启动 Docker 服务..."
            elif command -v service >/dev/null 2>&1; then
                sudo service docker start 2>/dev/null || {
                    log_error "无法启动 Docker 服务，请手动启动"
                    exit 1
                }
                log_info "正在启动 Docker 服务..."
            fi
            ;;
        Darwin)
            open -a Docker 2>/dev/null &
            log_info "正在启动 Docker Desktop..."
            ;;
    esac

    local waited=0
    while ! docker info >/dev/null 2>&1; do
        if [ $waited -ge 60 ]; then
            log_error "等待 Docker 启动超时，请检查 Docker Desktop 状态"
            exit 1
        fi
        sleep 2
        waited=$((waited + 2))
    done
    log_info "Docker 已就绪"
}

# 检查必要文件
if [ ! -f "$PROJECT_DIR/$DOCKERFILE" ]; then
    log_error "Dockerfile 不存在: $PROJECT_DIR/$DOCKERFILE"
    exit 1
fi
if [ ! -f "$PROJECT_DIR/$COMPOSE_FILE" ]; then
    log_error "compose 文件不存在: $PROJECT_DIR/$COMPOSE_FILE"
    exit 1
fi

# .env 缺失时从 .env.example 创建
ENV_FILE="$PROJECT_DIR/.env"
ENV_EXAMPLE="$PROJECT_DIR/backend/.env.example"
BACKEND_ENV="$PROJECT_DIR/backend/.env"

if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$ENV_EXAMPLE" ]; then
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        log_warn ".env 不存在，已从 .env.example 创建，请编辑填入 API Key"
        log_warn "  $ENV_FILE"
    else
        log_warn ".env 和 .env.example 均不存在，请手动创建 .env 文件"
    fi
fi

# Ensure backend/.env exists for Docker build context
if [ ! -f "$BACKEND_ENV" ] && [ -f "$ENV_FILE" ]; then
    cp "$ENV_FILE" "$BACKEND_ENV"
    log_info "已从项目根目录同步 .env → backend/.env（供 Docker 构建使用）"
fi

# 1. 确保 Docker 在运行
ensure_docker_running

# 2. 构建 Docker 镜像
log_info "开始构建镜像: ${IMAGE_NAME}:${IMAGE_TAG}"
cd "$PROJECT_DIR"
DOCKER_BUILDKIT=1 docker build \
    -t "${IMAGE_NAME}:${IMAGE_TAG}" \
    -f "$DOCKERFILE" \
    ./backend || {
    log_error "镜像构建失败"
    exit 1
}

log_info "构建完成: ${IMAGE_NAME}:${IMAGE_TAG}"
    # Remove dangling images from previous builds
    docker image prune -f 2>/dev/null || true


# 3. 自动启动容器
if [ "$AUTO_START" = true ]; then
    log_info "停止并移除旧容器"
    docker compose -f "$PROJECT_DIR/$COMPOSE_FILE" down 2>/dev/null || true
    log_info "启动容器"
    docker compose -f "$PROJECT_DIR/$COMPOSE_FILE" up -d || {
        log_error "容器启动失败"
        exit 1
    }
    log_info "容器已启动: tlma-gateway"
    log_info "健康检查: http://localhost:9856/health"
fi
