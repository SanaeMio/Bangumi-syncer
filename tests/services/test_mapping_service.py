"""
MappingService tests - Simplified version
"""

import json
from unittest.mock import MagicMock, patch


class TestMappingServiceSimple:
    """Test MappingService with simplified tests"""

    def test_mapping_service_init(self):
        """Test mapping service initialization"""
        from app.services.mapping_service import MappingService

        service = MappingService()
        assert hasattr(service, "_cached_mappings")
        assert service._cached_mappings == {}

    def test_update_custom_mappings(self, temp_dir):
        """Test updating custom mappings"""
        mapping_file = temp_dir / "bangumi_mapping.json"
        mapping_data = {"mappings": {"动画1": "123456"}}
        mapping_file.write_text(json.dumps(mapping_data), encoding="utf-8")

        with patch("app.services.mapping_service.os.path.exists", return_value=True):
            from app.services.mapping_service import MappingService

            service = MappingService()
            service._mapping_file_path = str(mapping_file)

            with (
                patch("builtins.open", MagicMock()) as mock_open,
                patch("app.services.mapping_service.json.dump") as mock_dump,
            ):
                mock_file = MagicMock()
                mock_open.return_value.__enter__.return_value = mock_file

                service.update_custom_mappings({"动画1": "123456", "动画2": "789012"})
                mock_dump.assert_called_once()

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
