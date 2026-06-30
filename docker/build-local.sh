#!/bin/bash
set -euo pipefail

# ============================================
# 用户配置区域（请根据需要修改以下变量）
# ============================================
COMPOSE_FILE="docker/docker-compose.local.yml"      # docker-compose 配置文件（相对于项目根目录）
BACKEND_PORT=9856                                     # Backend 服务端口
DASHBOARD_PORT=8856                                   # Dashboard 服务端口
AUTO_START=true                                       # 构建完成后是否自动启动容器

# ============================================
# 以下为脚本逻辑，一般无需修改
# ============================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKEND_DOCKERFILE="$PROJECT_DIR/backend/Dockerfile"
DASHBOARD_DOCKERFILE="$PROJECT_DIR/dashboard/Dockerfile"

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

# ─── 1. 前置检查 ───

if [ ! -f "$PROJECT_DIR/$COMPOSE_FILE" ]; then
    log_error "compose 文件不存在: $PROJECT_DIR/$COMPOSE_FILE"
    exit 1
fi
if [ ! -f "$BACKEND_DOCKERFILE" ]; then
    log_error "Backend Dockerfile 不存在: $BACKEND_DOCKERFILE"
    exit 1
fi
if [ ! -f "$DASHBOARD_DOCKERFILE" ]; then
    log_error "Dashboard Dockerfile 不存在: $DASHBOARD_DOCKERFILE"
    exit 1
fi

# .env 处理
ENV_FILE="$PROJECT_DIR/.env"
ENV_EXAMPLE="$PROJECT_DIR/.env.example"

if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$ENV_EXAMPLE" ]; then
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        log_warn ".env 不存在，已从 .env.example 创建，请编辑填入 API Key"
        log_warn "  $ENV_FILE"
    else
        log_error ".env 和 .env.example 均不存在，请手动创建 .env 文件"
        exit 1
    fi
fi

# ─── 2. 确保前端已构建（本地验证用，Docker 内会独立构建） ───
if [ -f "$PROJECT_DIR/dashboard/package.json" ]; then
    log_info "检查前端依赖..."
    if [ ! -d "$PROJECT_DIR/dashboard/node_modules" ]; then
        log_info "安装前端依赖..."
        cd "$PROJECT_DIR/dashboard"
        npm install --legacy-peer-deps
        cd "$PROJECT_DIR"
    fi
    log_info "本地构建前端（验证编译）..."
    cd "$PROJECT_DIR/dashboard"
    npx tsc --noEmit || log_warn "TypeScript 类型检查有警告，Docker 构建将继续"
    npm run build
    cd "$PROJECT_DIR"
    log_info "前端构建完成: $PROJECT_DIR/dashboard/dist/"
fi

# ─── 3. 确保 Docker 在运行 ───
ensure_docker_running

# ─── 4. 构建并启动 ───
log_info "构建镜像并启动服务..."
cd "$PROJECT_DIR"

log_info "停止并移除旧容器（含 orphan 容器）"
docker compose -f "$PROJECT_DIR/$COMPOSE_FILE" down --remove-orphans 2>/dev/null || true

DOCKER_BUILDKIT=1 docker compose -f "$PROJECT_DIR/$COMPOSE_FILE" up -d --build || {
    log_error "镜像构建或容器启动失败"
    exit 1
}

docker image prune -f 2>/dev/null || true

# ─── 5. 健康检查 ───
log_info "等待服务就绪..."

# 等待 backend
for i in $(seq 1 30); do
    if curl -sf -o /dev/null "http://localhost:$BACKEND_PORT/health" 2>/dev/null; then
        log_info "Backend 就绪: http://localhost:$BACKEND_PORT/health"
        break
    fi
    if [ "$i" -eq 30 ]; then
        log_error "Backend 启动超时"
        docker compose -f "$PROJECT_DIR/$COMPOSE_FILE" logs backend
        exit 1
    fi
    sleep 2
done

# 等待 dashboard
for i in $(seq 1 15); do
    if curl -sf -o /dev/null "http://localhost:$DASHBOARD_PORT/" 2>/dev/null; then
        log_info "Dashboard 就绪: http://localhost:$DASHBOARD_PORT"
        break
    fi
    if [ "$i" -eq 15 ]; then
        log_error "Dashboard 启动超时"
        docker compose -f "$PROJECT_DIR/$COMPOSE_FILE" logs dashboard
        exit 1
    fi
    sleep 1
done

# ─── 6. 验证 API 代理 ───
if curl -sf -o /dev/null "http://localhost:$DASHBOARD_PORT/api/dashboard/stats" 2>/dev/null; then
    log_info "API 代理验证通过 (dashboard → backend)"
else
    log_warn "API 代理验证失败，请检查 nginx 配置"
fi

log_info "============================================"
log_info "全部服务已启动:"
log_info "  Backend:    http://localhost:$BACKEND_PORT/health"
log_info "  Dashboard:  http://localhost:$DASHBOARD_PORT"
log_info "  API Proxy:  http://localhost:$DASHBOARD_PORT/api/dashboard/stats"
log_info "============================================"
