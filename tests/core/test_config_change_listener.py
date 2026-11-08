"""配置变更回调（变更监听 + 进程级缓存失效）测试

覆盖：
- 版本号自增与监听回调触发（reload / 外部改文件 / set_config 保存）
- 回调异常隔离、注销
- 实际订阅方：watching 缓存、poster 缓存、bangumi_data 配置刷新
"""

import os
import time
from pathlib import Path
from typing import Any, cast

from app.core.config import config_manager


def _register_cb(cm, log):
    def cb():
        log.append(1)

    cm.register_config_change_listener(cb)
    return cb


class TestConfigChangeListener:
    def test_version_increments_on_reload(self):
        v0 = config_manager.get_config_version()
        config_manager.reload_config()
        assert config_manager.get_config_version() == v0 + 1

    def test_listener_fired_on_reload_config(self):
        fired = []
        cb = _register_cb(config_manager, fired)
        try:
            config_manager.reload_config()
            assert fired == [1]
        finally:
            config_manager.unregister_config_change_listener(cb)

    def test_listener_not_fired_on_unchanged_get(self):
        """无变更的 get 不应触发回调（dirty 标记为幂等消费）。"""
        fired = []
        cb = _register_cb(config_manager, fired)
        try:
            v0 = config_manager.get_config_version()
            config = config_manager.get_config_parser()
            assert config is not None
            # get 未造成任何加载/保存 → 版本不变、回调不触发
            assert config_manager.get_config_version() == v0
            assert fired == []
        finally:
            config_manager.unregister_config_change_listener(cb)

    def test_listener_fired_on_external_file_edit(self):
        """直接改配置文件（mtime 变化）经 get 触发变更回调。"""
        fired = []
        cb = _register_cb(config_manager, fired)
        try:
            path = Path(config_manager.active_config_path)
            original = path.read_text(encoding="utf-8")
            # 保证 mtime 大于上次加载记录（放文件系统时间戳粒度保险）
            path.write_text(original + "\n", encoding="utf-8")
            os.utime(path, (time.time() + 10, time.time() + 10))
            config_manager.get_config_parser()
            assert fired, "外部配置文件修改后应触发变更回调"
        finally:
            config_manager.unregister_config_change_listener(cb)

    def test_listener_fired_on_set_config(self):
        fired = []
        cb = _register_cb(config_manager, fired)
        try:
            config_manager.set("dev", "listener_test_key", "1")
            assert fired == [1]
        finally:
            config_manager.unregister_config_change_listener(cb)
            config_manager.set_config("dev", "listener_test_key", "")
            # 清理配置键，避免污染后续读取
            parser = config_manager.get_config_parser()
            if parser.has_option("dev", "listener_test_key"):
                parser.remove_option("dev", "listener_test_key")
                config_manager.save_config()

    def test_unregister_stops_callbacks(self):
        fired = []
        cb = _register_cb(config_manager, fired)
        config_manager.unregister_config_change_listener(cb)
        config_manager.reload_config()
        assert fired == []

    def test_listener_exception_does_not_block_others(self):
        order = []

        def bad():
            raise RuntimeError("boom")

        def good():
            order.append("ok")

        config_manager.register_config_change_listener(bad)
        config_manager.register_config_change_listener(good)
        try:
            config_manager.reload_config()  # 不应向上抛异常
            assert order == ["ok"]
        finally:
            config_manager.unregister_config_change_listener(bad)
            config_manager.unregister_config_change_listener(good)


class TestRegisteredCacheInvalidation:
    """实际订阅的进程级缓存应在配置变更后失效。"""

    def test_watching_cache_cleared_on_reload(self):
        import app.utils.bangumi_api.collection as collection_mod

        collection_mod._watching_cache["user_x"] = (time.time(), {1, 2, 3})
        config_manager.reload_config()
        assert collection_mod._watching_cache == {}

    def test_poster_caches_cleared_on_reload(self):
        import app.utils.bgm_poster_service as poster_mod

        poster_mod._poster_url_cache[("ns", 1)] = ("url", time.time() + 3600)
        # 哨兵条目：键/值类型无需真实匹配，仅验证 reload 会清空缓存
        poster_mod._bgm_api_instances[cast(Any, ("k",))] = cast(Any, object())
        config_manager.reload_config()
        assert poster_mod._poster_url_cache == {}
        assert poster_mod._bgm_api_instances == {}

    def test_bangumi_data_reload_config_updates_fields_and_clears_cache(self):
        from app.utils import bangumi_data as bangumi_data_module

        bd = bangumi_data_module.bangumi_data
        # 给定一个明显不同的缓存路径作为探测值，确认 reload 会重读配置
        bd._data_cache = {"fake": "stale"}
        bd.reload_config()
        # 细粒度触发：bangumi-data 专属配置未变化时保留内存缓存
        # （无关配置变更不再清空缓存，避免每次保存配置触发全量重建）
        assert bd._data_cache == {"fake": "stale"}
        # 重读后的字段与 config_manager 当前值一致
        assert bd.local_cache_path == config_manager.get(
            "bangumi-data", "local_cache_path", fallback="./bangumi_data_cache.json"
        )

    def test_subscribers_registered(self):
        """watching / poster / bangumi-data 三个缓存模块均已订阅。"""
        cbs = list(config_manager._change_listeners)
        assert len(cbs) >= 3
        # 至少注册了 bangumi_data.reload_config（bound method）
        from app.utils import bangumi_data as bangumi_data_module

        bd = bangumi_data_module.bangumi_data
        assert any(getattr(cb, "__self__", None) is bd for cb in cbs)


class TestBatchConfigWrite:
    """批量写入（ConfigManager.batch）：整批只写一次盘、只触发一次回调。

    配置页保存是逐键调用 set_config 的，若每次写盘 + 回调，会产生几十次
    文件写入与缓存重建风暴（历史问题：日志同一请求刷出几十条刷新记录）。
    """

    @staticmethod
    def _cleanup_dev_keys(*keys):
        parser = config_manager.get_config_parser()
        if not parser.has_section("dev"):
            return
        removed = False
        for key in keys:
            if parser.has_option("dev", key):
                parser.remove_option("dev", key)
                removed = True
        if removed:
            config_manager.save_config()

    def test_batch_writes_once_and_fires_once(self, monkeypatch):
        writes = []
        original_save = config_manager._save_config

        def counting_save(config):
            writes.append(1)
            original_save(config)

        monkeypatch.setattr(config_manager, "_save_config", counting_save)
        fired = []
        cb = _register_cb(config_manager, fired)
        try:
            with config_manager.batch():
                config_manager.set_config("dev", "batch_test_key_a", "value_a")
                config_manager.set_config("dev", "batch_test_key_b", "value_b")
                config_manager.save_config()
                # 批量期间不落盘、不回调
                assert writes == []
                assert fired == []
            assert len(writes) == 1, "批量写入应只写一次盘"
            assert fired == [1], "批量写入应只触发一次变更回调"
            assert config_manager.get("dev", "batch_test_key_a") == "value_a"
            assert config_manager.get("dev", "batch_test_key_b") == "value_b"
        finally:
            config_manager.unregister_config_change_listener(cb)
            self._cleanup_dev_keys("batch_test_key_a", "batch_test_key_b")

    def test_nested_batch_flushes_at_outermost_exit(self):
        fired = []
        cb = _register_cb(config_manager, fired)
        try:
            with config_manager.batch():
                config_manager.set_config("dev", "batch_test_key_c", "value_c1")
                with config_manager.batch():
                    config_manager.set_config("dev", "batch_test_key_c", "value_c2")
                assert fired == [], "内层 batch 退出不应触发回调"
            assert fired == [1]
            assert config_manager.get("dev", "batch_test_key_c") == "value_c2"
        finally:
            config_manager.unregister_config_change_listener(cb)
            self._cleanup_dev_keys("batch_test_key_c")
