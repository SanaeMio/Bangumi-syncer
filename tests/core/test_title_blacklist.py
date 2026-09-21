"""负样本黑名单（reject 学习）仓库测试

验证：
1. 批量写入与读取
2. INSERT OR IGNORE 幂等（重复写入不新增）
3. 标题键归一化（大小写/空白差异命中同一键）
4. 空标题返回空集
5. 单条移除与整标题清空
"""

from app.core.database import database_manager


class TestTitleBlacklist:
    TITLE = "测试番剧"

    def teardown_method(self):
        database_manager.clear_title_blacklist(self.TITLE)

    def test_add_and_get(self):
        database_manager.clear_title_blacklist(self.TITLE)
        database_manager.bulk_add_title_blacklist(self.TITLE, ["111", "222"])
        assert database_manager.get_title_blacklist(self.TITLE) == {"111", "222"}

    def test_idempotent(self):
        database_manager.clear_title_blacklist(self.TITLE)
        database_manager.bulk_add_title_blacklist(self.TITLE, ["111", "222"])
        added = database_manager.bulk_add_title_blacklist(
            self.TITLE, ["111", "222", "333"]
        )
        # 111/222 已存在，仅 333 新增
        assert added == 1
        assert database_manager.get_title_blacklist(self.TITLE) == {"111", "222", "333"}

    def test_title_key_normalization(self):
        database_manager.clear_title_blacklist(self.TITLE)
        database_manager.bulk_add_title_blacklist("Test Anime", ["1"])
        # 大小写 / 首尾空白差异应命中同一键
        assert database_manager.get_title_blacklist(" test anime ") == {"1"}

    def test_empty(self):
        assert database_manager.get_title_blacklist("不存在的标题xyz") == set()

    def test_remove_and_clear(self):
        database_manager.clear_title_blacklist(self.TITLE)
        database_manager.bulk_add_title_blacklist(self.TITLE, ["1", "2"])
        assert database_manager.remove_title_blacklist(self.TITLE, "1") is True
        assert database_manager.get_title_blacklist(self.TITLE) == {"2"}
        database_manager.clear_title_blacklist(self.TITLE)
        assert database_manager.get_title_blacklist(self.TITLE) == set()
