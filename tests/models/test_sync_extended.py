"""
Sync 模型测试 - 简化版
"""

from app.models.sync import (
    CustomItem,
    SyncResponse,
)


class TestCustomItem:
    """CustomItem 模型测试"""

    def test_create_custom_item(self):
        """测试创建 CustomItem"""
        item = CustomItem(
            media_type="episode",
            title="测试番剧",
            ori_title="Test Show",
            season=1,
            episode=5,
            release_date="2024-01-01",
            user_name="test_user",
        )
        assert item.media_type == "episode"
        assert item.title == "测试番剧"
        assert item.season == 1
        assert item.episode == 5

    def test_custom_item_optional_fields(self):
        """测试可选字段"""
        item = CustomItem(
            media_type="episode",
            title="测试番剧",
            season=1,
            episode=1,
            release_date="2024-01-01",
            user_name="test_user",
            source="plex",
        )
        assert item.source == "plex"
        assert item.ori_title is None


class TestSyncResponse:
    """SyncResponse 模型测试"""

    def test_create_sync_response(self):
        """测试创建 SyncResponse"""
        response = SyncResponse(
            status="success",
            message="同步成功",
        )
        assert response.status == "success"
        assert response.message == "同步成功"

    def test_sync_response_with_data(self):
        """测试带数据的响应"""
        response = SyncResponse(
            status="success",
            message="同步成功",
            data={"id": 123},
        )
        assert response.data == {"id": 123}
