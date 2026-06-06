"""Logging utilities for cf."""

import hashlib
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

from loguru import logger as global_logger

# 日志格式: INFO  [13:30:28.370] message
# 把 WARNING/CRITICAL 缩短到 4 字符，其他 level（DEBUG/INFO/ERROR）保持原名，统一按 5 字符左对齐
_SHORT_LEVEL = {"WARNING": "WARN", "CRITICAL": "CRIT"}


def _patch_short_level(record):
    record["extra"]["lvl"] = _SHORT_LEVEL.get(record["level"].name, record["level"].name)


_LOG_FORMAT = (
    "<level>{extra[lvl]:<5}</level> "
    "<dim>[{time:HH:mm:ss.SSS}]</dim> "
    "<level>{message}</level>"
)


def _sanitize_args(args: list[str]) -> str:
    """把参数列表转成干净的字符串，作为文件名安全的一部分"""
    if not args:
        return "default"

    raw = "_".join(args)
    # 替换非法字符为 _
    cleaned = re.sub(r"[^a-zA-Z0-9_\-]", "_", raw)

    # 可选：截断 + 添加 hash 保证唯一性但不长
    if len(cleaned) > 50:
        hash_suffix = hashlib.md5(raw.encode()).hexdigest()[:6]
        cleaned = cleaned[:40] + "_" + hash_suffix

    return cleaned


def _sanitize_file_name(file_name: str) -> str:
    """把文件名转成干净的字符串，作为文件名安全的一部分"""
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", file_name)


def _get_log_path() -> Path:
    """生成日志文件路径"""
    # 获取入口脚本名，比如 download_image.py
    entry_file = _sanitize_file_name(Path(sys.argv[0]).name)

    # 获取传入参数，拼接为 &arg1_arg2
    args = _sanitize_args(sys.argv[1:])

    # 拼出日志文件路径
    log_file_name = f"{entry_file}_{args}.jsonl"
    log_file_name = log_file_name.lower()
    log_path = Path.home() / "qblog" / log_file_name
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return log_path


def _json_serialize(record) -> str:
    payload = {
        "time": record["time"].timestamp(),
        "time_human": record["time"].strftime("%Y-%m-%dT%H:%M:%S.%f%z"),
        "level": record["level"].name,
        "message": record["message"],
        "file": f"{record['file'].name}:{record['line']}",
    }
    return json.dumps(payload)


def _formatter(record) -> str:
    # Note this function returns the string to be formatted, not the actual message to be logged
    record["extra"]["serialized"] = _json_serialize(record)
    return "{extra[serialized]}\n"


# 创建 logger 实例，patcher 写入短 level 名到 extra.lvl
logger = global_logger.bind(name="cf").patch(_patch_short_level)

# 初始化 logger 配置
logger.remove()
logger.level("DEBUG", color="<dim>")
logger.level("WARNING", color="<red>")
logger.add(sink=sys.stdout, format=_LOG_FORMAT, level="DEBUG")


@lru_cache(maxsize=1)
def logger_add_path():
    """添加文件日志输出"""
    logger.add(
        _get_log_path(),
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        format=_formatter,
    )


def set_log_level(level: str):
    """设置日志级别"""
    logger.remove()
    logger.add(sink=sys.stdout, format=_LOG_FORMAT, level=level)
