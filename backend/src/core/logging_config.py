"""
日志配置模块 — 参考 LQConstPlanReviewAgent 的日志设计。

格式: 时间 - 模块 - 级别 - 文件:行号 - 消息
输出: 控制台(DEBUG) + 按级别拆分到 logs/ 子目录的滚动文件。
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler


# ---------------------------------------------------------------------------
# 级别过滤器 — 只放行指定级别及以上的日志
# ---------------------------------------------------------------------------


class _RobustStreamHandler(logging.StreamHandler):
    """StreamHandler that auto-flushes and recovers from closed streams.

    When capsys replaces sys.stdout between tests, the old stream may be
    closed.  This handler detects that and re-binds to the current sys.stdout.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
            self.flush()
        except (ValueError, OSError):
            # stream closed (capsys cleanup) — rebind and retry
            self.stream = sys.stdout  # type: ignore[assignment]
            try:
                super().emit(record)
                self.flush()
            except Exception:
                pass  # last resort: drop the message


class _LevelFilter(logging.Filter):
    """只放行指定级别及以上的日志记录（向上兼容）。"""

    def __init__(self, level: int):
        super().__init__()
        self.level = level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= self.level


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

_LOG_FORMAT = (
    "%(asctime)s - %(name)s - %(levelname)s - "
    "%(filename)s:%(lineno)d - %(message)s"
)
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_LEVEL_DIRS: dict[int, str] = {
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warn",
    logging.ERROR: "error",
}


def setup_logging(
    level: str = "DEBUG",
    log_dir: str = "logs",
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
) -> None:
    """配置日志系统：控制台 + 按级别拆分的滚动文件。

    Args:
        level: 日志级别 (DEBUG / INFO / WARNING / ERROR / CRITICAL).
        log_dir: 日志根目录.
        max_bytes: 单文件最大字节数.
        backup_count: 保留的备份数量.
    """
    log_level: int = getattr(logging, level.upper(), logging.DEBUG)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    root = logging.getLogger()
    root.setLevel(log_level)

    has_file_handlers = any(
        isinstance(h, RotatingFileHandler) for h in root.handlers
    )

    # 每次调用都重建控制台 handler（capsys 会替换 sys.stdout，
    # 旧的 stream 可能已关闭）
    for h in root.handlers[:]:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler):
            root.removeHandler(h)

    console = _RobustStreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG)
    console.setFormatter(formatter)
    root.addHandler(console)

    # 文件 handler 只创建一次
    if has_file_handlers:
        return

    os.makedirs(log_dir, exist_ok=True)

    all_file = RotatingFileHandler(
        os.path.join(log_dir, "lensgate.log"),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    all_file.setLevel(logging.DEBUG)
    all_file.setFormatter(formatter)
    root.addHandler(all_file)

    for lvl, subdir in _LEVEL_DIRS.items():
        level_dir = os.path.join(log_dir, subdir)
        os.makedirs(level_dir, exist_ok=True)
        lvl_handler = RotatingFileHandler(
            os.path.join(level_dir, "lensgate.log"),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        lvl_handler.setLevel(logging.DEBUG)
        lvl_handler.setFormatter(formatter)
        lvl_handler.addFilter(_LevelFilter(lvl))
        root.addHandler(lvl_handler)


# ---------------------------------------------------------------------------
# 兼容层
# ---------------------------------------------------------------------------


def get_logger(name: str = __name__) -> logging.Logger:
    """返回标准 logging.Logger，与之前 structlog API 兼容。"""
    return logging.getLogger(name)


# ---------------------------------------------------------------------------
# 便捷 helper
# ---------------------------------------------------------------------------


def log_request(
    logger: logging.Logger,
    method: str,
    path: str,
    status_code: int,
    duration_ms: float,
) -> None:
    logger.info(
        "request | method=%s path=%s status=%d duration_ms=%.1f",
        method, path, status_code, duration_ms,
    )


def log_vision(
    logger: logging.Logger,
    image_count: int,
    vision_duration_ms: float,
) -> None:
    logger.info(
        "vision_complete | image_count=%d vision_duration_ms=%.1f",
        image_count, vision_duration_ms,
    )
