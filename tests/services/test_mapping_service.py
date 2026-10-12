"""
MappingService tests - Simplified version
"""

from unittest.mock import patch


class TestMappingServiceSimple:
    """Test MappingService with simplified tests"""

    def test_mapping_service_init(self):
        """Test mapping service initialization"""
        from app.services.mapping_service import MappingService

        service = MappingService()
        assert hasattr(service, "_cached_mappings")
        assert service._cached_mappings == {}

    def test_update_custom_mappings(self, temp_dir):
        """更新映射应真实落盘并可回读

        原用例把 builtins.open 与 json.dump 都 mock 掉、只断言 dump 被调用
        一次，因此对「写入了什么」完全无感；且新实现会在读取到非法内容时
        **拒绝写入**（防止覆盖用户配置），mock 掉的 open 恰好触发该保护。
        改为真实文件往返断言。
        """
        import json as _json

        from app.services.mapping_service import MappingService

        mapping_file = temp_dir / "bangumi_mapping.json"
        mapping_file.write_text(
            _json.dumps({"mappings": {"动画1": "123456"}}), encoding="utf-8"
        )

        service = MappingService()
        service._mapping_file_path = str(mapping_file)

        # 直接指定路径写入（绕开 _find_existing_path 的候选路径查找）
        ok = service.update_custom_mappings_with_path(
            str(mapping_file), {"动画1": "123456", "动画2": "789012"}
        )
        assert ok is True

        data = _json.loads(mapping_file.read_text(encoding="utf-8"))
        assert data["mappings"] == {"动画1": "123456", "动画2": "789012"}

    def test_delete_custom_mapping_not_found(self):
        """Test deleting non-existent mapping"""
        from app.services.mapping_service import MappingService

        service = MappingService()
        service._cached_mappings = {"动画1": "123456"}

        result = service.delete_custom_mapping("不存在的动画")
        assert result is False

    def test_update_mappings_alias(self):
        """Test update_mappings is alias"""
        from app.services.mapping_service import MappingService

        service = MappingService()

        with patch.object(
            service, "update_custom_mappings", return_value=True
        ) as mock_update:
            service.update_mappings({})
            mock_update.assert_called_once()
