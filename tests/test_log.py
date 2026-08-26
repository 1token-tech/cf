"""Tests for `cf.log` —— 重点是业务 extra 字段不能被静默丢弃。"""

import json

from cf.log import _business_extra, _json_serialize, _patch_record


def _make_record(extra: dict, message: str = "something happened", level: str = "WARNING") -> dict:
    """构造一个够用的 loguru record 替身（只含被测代码读到的字段）"""

    class _Level:
        name = level

    class _Time:
        @staticmethod
        def timestamp():
            return 1756200000.0

        @staticmethod
        def strftime(fmt):
            return "2026-08-26T15:00:00.000000+0800"

    class _File:
        name = "msg.py"

    return {
        "extra": dict(extra),
        "message": message,
        "level": _Level(),
        "time": _Time(),
        "file": _File(),
        "line": 42,
    }


class TestBusinessExtra:
    def test_internal_keys_excluded(self):
        """cf 自己塞的渲染中间量不算业务上下文"""
        record = _make_record(
            {"name": "cf", "lvl": "WARN", "ctx": " | x=1", "serialized": "{}", "chat_id": "oc_1"}
        )
        assert _business_extra(record) == {"chat_id": "oc_1"}

    def test_empty_when_no_business_field(self):
        record = _make_record({"name": "cf", "lvl": "WARN"})
        assert _business_extra(record) == {}

    def test_nested_extra_kwarg_flattened(self):
        """logger.info(msg, extra={...}) 在 loguru 下会多套一层，要摊平"""
        record = _make_record({"name": "cf", "extra": {"chat_id": "oc_1", "hours_ago": 3}})
        assert _business_extra(record) == {"chat_id": "oc_1", "hours_ago": 3}

    def test_native_kwargs_and_nested_merged(self):
        """两种写法混用时都保留，同名以直接 kwargs 为准"""
        record = _make_record({"name": "cf", "extra": {"a": 1, "b": 2}, "b": 99, "c": 3})
        assert _business_extra(record) == {"a": 1, "b": 99, "c": 3}

    def test_non_dict_extra_kept_as_field(self):
        """extra 传了非 dict（写错了）也别吞掉，原样当一个字段展示"""
        record = _make_record({"name": "cf", "extra": "oops"})
        assert _business_extra(record) == {"extra": "oops"}


class TestPatchRecord:
    def test_renders_business_extra_into_ctx(self):
        """调用方传的 extra 要渲染进 ctx，否则控制台 format 输出不了它们"""
        record = _make_record({"name": "cf", "chat_id": "oc_1", "hours_ago": 3})
        _patch_record(record)

        assert record["extra"]["lvl"] == "WARN"
        assert record["extra"]["ctx"] == " | chat_id=oc_1 hours_ago=3"

    def test_renders_nested_extra_flat(self):
        """仓库主流写法 logger.warning(msg, extra={...}) 渲染成平铺的 k=v"""
        record = _make_record({"name": "cf", "extra": {"message_id": "om_x100abc"}})
        _patch_record(record)

        assert record["extra"]["ctx"] == " | message_id=om_x100abc"

    def test_ctx_is_empty_string_without_business_extra(self):
        """没有业务字段时 ctx 必须存在且为空——format 串引用了它，缺键会让日志直接抛错"""
        record = _make_record({"name": "cf"})
        _patch_record(record)

        assert record["extra"]["ctx"] == ""

    def test_long_ctx_truncated(self):
        """控制台单行有长度上限，避免一条日志刷屏"""
        record = _make_record({"name": "cf", "payload": "x" * 500})
        _patch_record(record)

        assert record["extra"]["ctx"].endswith("…")
        assert len(record["extra"]["ctx"]) < 250

    def test_short_level_kept_for_other_levels(self):
        record = _make_record({"name": "cf"}, level="INFO")
        _patch_record(record)

        assert record["extra"]["lvl"] == "INFO"


class TestJsonSerialize:
    def test_business_extra_written_to_payload(self):
        """jsonl 里要能拿到完整上下文（不截断），这是事后排查的依据"""
        record = _make_record({"name": "cf", "lvl": "WARN", "chat_id": "oc_1", "count": 3})
        payload = json.loads(_json_serialize(record))

        assert payload["extra"] == {"chat_id": "oc_1", "count": 3}
        assert payload["message"] == "something happened"

    def test_nested_extra_flattened_in_payload(self):
        """jsonl 里也要摊平，别写成 extra.extra 这种套娃"""
        record = _make_record({"name": "cf", "lvl": "WARN", "extra": {"chat_id": "oc_1"}})
        payload = json.loads(_json_serialize(record))

        assert payload["extra"] == {"chat_id": "oc_1"}

    def test_long_value_not_truncated_in_payload(self):
        """控制台会截断，jsonl 不截断——排查要的是完整值"""
        record = _make_record({"name": "cf", "extra": {"payload": "x" * 500}})
        payload = json.loads(_json_serialize(record))

        assert payload["extra"]["payload"] == "x" * 500

    def test_no_extra_key_when_nothing_to_report(self):
        record = _make_record({"name": "cf", "lvl": "WARN"})
        payload = json.loads(_json_serialize(record))

        assert "extra" not in payload

    def test_unserializable_value_does_not_raise(self):
        """业务字段可能是任意对象，日志序列化失败不该把调用方打挂"""
        record = _make_record({"name": "cf", "obj": object()})
        payload = json.loads(_json_serialize(record))

        assert "obj" in payload["extra"]
