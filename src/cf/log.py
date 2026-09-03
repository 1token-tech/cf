"""Logging utilities for cf."""

import hashlib
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

from loguru import logger as global_logger

# 日志格式: INFO  [13:30:28.370] message | key=value
# 把 WARNING/CRITICAL 缩短到 4 字符，其他 level（DEBUG/INFO/ERROR）保持原名，统一按 5 字符左对齐
_SHORT_LEVEL = {"WARNING": "WARN", "CRITICAL": "CRIT"}

# cf 自己塞进 extra 的渲染中间量，不属于调用方传来的业务上下文
_INTERNAL_EXTRA_KEYS = frozenset({"name", "lvl", "ctx", "serialized"})

# 控制台单行上下文的长度上限：超了截断，完整值总是写进 jsonl
_CTX_MAX_LEN = 200


def _business_extra(record) -> dict:
    """调用方传进来的业务字段，两种写法归一成同一层。

    loguru 不像标准库 logging 那样特殊对待 extra= 关键字，它把所有 kwargs 原样塞进
    record["extra"]，于是 logger.info(msg, extra={"chat_id": 1}) 实际拿到的是
    {"extra": {"chat_id": 1}}。仓库里绝大多数调用是这个写法（沿用标准库习惯），
    这里把它摊平，跟 loguru 原生的 logger.info(msg, chat_id=1) 渲染成一致的结果。
    """
    fields = {k: v for k, v in record["extra"].items() if k not in _INTERNAL_EXTRA_KEYS}
    nested = fields.pop("extra", None)
    if isinstance(nested, dict):
        # 同名时直接以 kwargs 传的为准，它写起来更显式
        return {**nested, **fields}
    if nested is not None:
        fields["extra"] = nested
    return fields


def _patch_record(record):
    record["extra"]["lvl"] = _SHORT_LEVEL.get(record["level"].name, record["level"].name)
    # 业务字段渲染成一段紧凑后缀挂在 message 后面。loguru 只有 format 里写了 {extra}
    # 才会输出这些字段，这里不渲染的话 logger.info(..., extra={"chat_id": ...}) 传的
    # 上下文会被静默丢掉，排查时只剩一句干巴巴的 message
    extra = _business_extra(record)
    if not extra:
        record["extra"]["ctx"] = ""
        return
    rendered = " ".join(f"{k}={v}" for k, v in extra.items())
    if len(rendered) > _CTX_MAX_LEN:
        rendered = rendered[:_CTX_MAX_LEN] + "…"
    record["extra"]["ctx"] = f" | {rendered}"


_LOG_FORMAT = (
    "<level>{extra[lvl]:<5}</level> "
    "<dim>[{time:HH:mm:ss.SSS}]</dim> "
    "<level>{message}</level>"
    "<dim>{extra[ctx]}</dim>"
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
    extra = _business_extra(record)
    if extra:
        payload["extra"] = extra
    # default=str：业务字段可能是任意对象，日志序列化失败不该把调用方打挂
    return json.dumps(payload, default=str)


def _formatter(record) -> str:
    # Note this function returns the string to be formatted, not the actual message to be logged
    record["extra"]["serialized"] = _json_serialize(record)
    return "{extra[serialized]}\n"


def format_without_exception(record) -> str:
    """给第三方 sink（典型是 sentry-sdk 的 LoguruIntegration）用的 format 回调。

    loguru 对字符串 format 会自动追加 ``\\n{exception}``，把 traceback 渲染进消息正文；
    而 loguru 自己不管的 sink 又是 diagnose 默认开——traceback 里每一帧的局部变量值
    （数据库 dsn、密码、token）全跟着进了消息。回调形式的 format 不会被追加 ``{exception}``，
    异常本体留给 sink 自己从 record 里拿（sentry 会拿去生成结构化 stacktrace）。
    格式沿用 loguru 默认的前缀，Sentry 上事件标题不变。
    """
    return "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}\n"


# 创建 logger 实例，patcher 写入短 level 名与业务上下文到 extra.lvl / extra.ctx
logger = global_logger.bind(name="cf").patch(_patch_record)

# 初始化 logger 配置
# diagnose 一律关：开着会把 traceback 每一帧的局部变量值打进日志（dsn、密码、token），
# 生产日志 / Sentry 都出过泄露；backtrace 保留，只多帧不多值
logger.remove()
logger.level("DEBUG", color="<dim>")
logger.level("WARNING", color="<red>")
logger.add(sink=sys.stdout, format=_LOG_FORMAT, level="DEBUG", diagnose=False)


@lru_cache(maxsize=1)
def logger_add_path():
    """添加文件日志输出"""
    logger.add(
        _get_log_path(),
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        format=_formatter,
        diagnose=False,
    )


def set_log_level(level: str):
    """设置日志级别"""
    logger.remove()
    logger.add(sink=sys.stdout, format=_LOG_FORMAT, level=level, diagnose=False)
