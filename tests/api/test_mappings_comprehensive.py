"""
映射 API 测试
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import deps, mappings


@pytest.fixture
def app_with_auth():
    """创建带有认证的测试应用"""
    app = FastAPI()
    app.include_router(mappings.router)

    async def mock_get_current_user(request=None, credentials=None):
        return {"username": "testuser", "id": 1}

    app.dependency_overrides[deps.get_current_user_flexible] = mock_get_current_user

    yield app

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_custom_mappings(app_with_auth):
    """测试获取自定义映射"""
    with patch("app.api.mappings.mapping_service") as mock_service:
        mock_service.get_all_mappings.return_value = {"test": "value"}

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.get("/api/mappings")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"


@pytest.mark.asyncio
async def test_get_custom_mappings_exception(app_with_auth):
    """测试获取自定义映射异常"""
    with patch("app.api.mappings.mapping_service") as mock_service:
        mock_service.get_all_mappings.side_effect = Exception("Error")

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.get("/api/mappings")

            assert response.status_code == 500


@pytest.mark.asyncio
async def test_update_custom_mappings(app_with_auth):
    """测试更新自定义映射"""
    with patch("app.api.mappings.mapping_service"):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/mappings", json={"mappings": {"test": "386809"}}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"


@pytest.mark.asyncio
async def test_update_custom_mappings_exception(app_with_auth):
    """测试更新自定义映射异常"""
    with patch("app.api.mappings.mapping_service") as mock_service:
        mock_service.update_custom_mappings.side_effect = Exception("Error")

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/mappings", json={"mappings": {"test": "386809"}}
            )

            assert response.status_code == 500


@pytest.mark.asyncio
async def test_delete_custom_mapping(app_with_auth):
    """测试删除自定义映射"""
    with patch("app.api.mappings.mapping_service") as mock_service:
        mock_service.get_all_mappings.return_value = {"test": "value"}

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.delete("/api/mappings/test")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"


@pytest.mark.asyncio
async def test_delete_custom_mapping_not_found(app_with_auth):
    """测试删除不存在的映射"""
    with patch("app.api.mappings.mapping_service") as mock_service:
        mock_service.get_all_mappings.return_value = {}

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.delete("/api/mappings/nonexistent")

            assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_custom_mapping_exception(app_with_auth):
    """测试删除映射异常"""
    with patch("app.api.mappings.mapping_service") as mock_service:
        mock_service.get_all_mappings.return_value = {"test": "value"}
        mock_service.delete_single_mapping.side_effect = Exception("Error")

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.delete("/api/mappings/test")

            assert response.status_code == 500


@pytest.mark.asyncio
async def test_update_custom_mappings_write_failure_returns_500(app_with_auth):
    """写入失败必须返回 500

    原实现丢弃 update_custom_mappings 的返回值、无条件回 success，前端会显示
    「保存成功」而改动并未落盘。
    """
    with patch("app.api.mappings.mapping_service") as mock_service:
        mock_service.update_custom_mappings.return_value = False

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/mappings", json={"mappings": {"test": "386809"}}
            )

        assert response.status_code == 500
        assert "写入失败" in response.json()["detail"]


@pytest.mark.asyncio
async def test_update_custom_mappings_rejects_bad_types(app_with_auth):
    """mappings/rules 类型非法时应返回 400，而不是写坏配置文件"""
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        bad_mappings = await client.post("/api/mappings", json={"mappings": []})
        assert bad_mappings.status_code == 400

        bad_rules = await client.post(
            "/api/mappings", json={"mappings": {}, "rules": "oops"}
        )
        assert bad_rules.status_code == 400


@pytest.mark.asyncio
async def test_update_rejects_invalid_subject_id(app_with_auth):
    """写入前必须校验 subject_id，拒绝非动画/三次元条目

    原实现只在「确认待确认候选」路径校验，映射页手动写入不校验 —— 用户可
    写入书籍/音乐条目的 ID，直到真正同步时才失败，错误信息离病因很远。
    """
    with (
        patch("app.api.mappings.mapping_service") as mock_service,
        patch(
            "app.utils.bangumi_api.subject_validation.validate_subject_id",
            return_value=(False, "条目类型为 1，仅支持动画/三次元"),
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/mappings", json={"mappings": {"某番": "12345"}}
            )

        assert response.status_code == 400
        assert "校验失败" in response.json()["detail"]
        # 校验失败时绝不能落盘
        mock_service.update_custom_mappings.assert_not_called()


@pytest.mark.asyncio
async def test_update_validates_segment_subject_ids(app_with_auth):
    """segments 内的 subject_id 同样要校验（否则是绕过校验的后门）"""
    seen: list[str] = []

    def _validate(sid):
        seen.append(str(sid))
        return (True, "")

    with (
        patch("app.api.mappings.mapping_service"),
        patch(
            "app.utils.bangumi_api.subject_validation.validate_subject_id",
            side_effect=_validate,
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/mappings",
                json={
                    "mappings": {
                        "JOJO": {
                            "subject_id": "43558",
                            "segments": [
                                {"season": 6, "from": 3, "subject_id": "639938"}
                            ],
                        }
                    }
                },
            )

        assert response.status_code == 200
        assert "43558" in seen
        assert "639938" in seen


@pytest.mark.asyncio
async def test_update_validates_rule_subject_ids(app_with_auth):
    """正则规则里的 subject_id 同样要校验（与 mappings 一条不漏）

    原实现只收集 mappings（含 segments），rules 整个被漏掉：用户在「添加正则
    规则」表单里填一本轻小说/音乐条目的 ID 会被原样接受，直到同步时才报错。
    """
    seen: list[str] = []

    def _validate(sid):
        seen.append(str(sid))
        return (True, "")

    with (
        patch("app.api.mappings.mapping_service"),
        patch(
            "app.utils.bangumi_api.subject_validation.validate_subject_id",
            side_effect=_validate,
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/mappings",
                json={
                    "mappings": {},
                    "rules": [
                        {"pattern": "^某番.*", "subject_id": "111"},
                        {"pattern": "^另一番$", "subject_id": "222", "season": 2},
                    ],
                },
            )

        assert response.status_code == 200
        assert "111" in seen
        assert "222" in seen


@pytest.mark.asyncio
async def test_update_rejects_invalid_rule_subject_id(app_with_auth):
    """规则里的非法 ID 也要挡住写入"""
    with (
        patch("app.api.mappings.mapping_service") as mock_service,
        patch(
            "app.utils.bangumi_api.subject_validation.validate_subject_id",
            return_value=(False, "条目类型为 1，仅支持动画/三次元"),
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/mappings",
                json={
                    "mappings": {},
                    "rules": [{"pattern": "^某番$", "subject_id": "999"}],
                },
            )

        assert response.status_code == 400
        assert "999" in response.json()["detail"]
        mock_service.update_custom_mappings.assert_not_called()


@pytest.mark.asyncio
async def test_only_submitted_ids_are_validated(app_with_auth):
    """只校验本次提交里出现的 ID，不回读整份配置

    否则配置文件里只要有一个历史遗留的无效 ID，之后连「删掉那一条」「清空
    全部」都会被 400 挡住，用户只能去手改文件才能恢复。
    """
    seen: list[str] = []

    def _validate(sid):
        seen.append(str(sid))
        # 模拟「旧配置里那个 ID 已失效」
        if str(sid) == "LEGACY_BAD":
            return (False, "条目不存在或 API 返回空")
        return (True, "")

    with (
        patch("app.api.mappings.mapping_service"),
        patch(
            "app.utils.bangumi_api.subject_validation.validate_subject_id",
            side_effect=_validate,
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            # 提交的内容里没有那个坏 ID —— 应放行
            response = await client.post(
                "/api/mappings", json={"mappings": {"好番": "12345"}}
            )

        assert response.status_code == 200, response.text
        assert "LEGACY_BAD" not in seen
        assert "12345" in seen


@pytest.mark.asyncio
async def test_unchanged_ids_are_not_revalidated(app_with_auth):
    """未改动的条目不再重复校验（否则每次保存都为全量 ID 打一轮网络）

    前端保存时总是把整份 mappings + rules 发上来，若对全量校验，配置里每个
    ID 都会在**每次**保存时被重新 GET 一遍 —— archive 未命中时每个 ID 都会
    新建 API 客户端，映射一多就是一次扇出。
    """
    seen: list[str] = []

    def _validate(sid):
        seen.append(str(sid))
        return (True, "")

    existing = {
        "老番": "111",
        "另一番": {"subject_id": "222", "season": 2},
    }
    existing_rules = [{"pattern": "^规则$", "subject_id": "333"}]

    with (
        patch("app.api.mappings.mapping_service") as mock_service,
        patch(
            "app.utils.bangumi_api.subject_validation.validate_subject_id",
            side_effect=_validate,
        ),
    ):
        mock_service.get_all_mappings.return_value = existing
        mock_service.get_all_rules.return_value = existing_rules

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            # 提交 = 磁盘现状 + 一条新增（999）
            response = await client.post(
                "/api/mappings",
                json={
                    "mappings": {**existing, "新番": "999"},
                    "rules": existing_rules,
                },
            )

        assert response.status_code == 200, response.text
        # 只有新增那条被校验
        assert seen == ["999"], f"未改动的 ID 被重复校验了: {seen}"


@pytest.mark.asyncio
async def test_changed_id_is_still_validated(app_with_auth):
    """改动的 ID 仍要校验 —— 这正是校验存在的意义（挡住改坏的情况）"""
    seen: list[str] = []

    def _validate(sid):
        seen.append(str(sid))
        return (False, "条目类型为 1，仅支持动画/三次元")

    with (
        patch("app.api.mappings.mapping_service") as mock_service,
        patch(
            "app.utils.bangumi_api.subject_validation.validate_subject_id",
            side_effect=_validate,
        ),
    ):
        mock_service.get_all_mappings.return_value = {"番": "111"}
        mock_service.get_all_rules.return_value = []

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/mappings", json={"mappings": {"番": "999"}}
            )

        assert response.status_code == 400
        assert seen == ["999"]
        mock_service.update_custom_mappings.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_bad_id_does_not_block_unrelated_edit(app_with_auth):
    """配置里存在历史无效 ID 时，改别的地方不应被挡住

    这是「只校验变化」最主要的动机：坏 ID 不该绑架无关编辑。此前每次保存都
    校验全量，用户必须先删掉那条坏映射（而删除本身也要过校验）才能动别处。
    """
    seen: list[str] = []

    def _validate(sid):
        seen.append(str(sid))
        if str(sid) == "111":  # 磁盘上那条坏的，用户没动它
            return (False, "条目不存在或 API 返回空")
        return (True, "")

    existing = {"坏番": "111"}

    with (
        patch("app.api.mappings.mapping_service") as mock_service,
        patch(
            "app.utils.bangumi_api.subject_validation.validate_subject_id",
            side_effect=_validate,
        ),
    ):
        mock_service.get_all_mappings.return_value = existing
        mock_service.get_all_rules.return_value = []
        mock_service.update_custom_mappings.return_value = True

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/mappings",
                json={"mappings": {**existing, "好番": "222"}},
            )

        assert response.status_code == 200, response.text
        assert seen == ["222"], f"坏 ID 不应被重新校验: {seen}"
        mock_service.update_custom_mappings.assert_called_once()


@pytest.mark.asyncio
async def test_removing_bad_id_is_allowed(app_with_auth):
    """删掉坏 ID 本身也必须能成功（删除的条目不在提交里，自然不校验）"""
    seen: list[str] = []

    def _validate(sid):
        seen.append(str(sid))
        return (False, "条目不存在或 API 返回空")

    with (
        patch("app.api.mappings.mapping_service") as mock_service,
        patch(
            "app.utils.bangumi_api.subject_validation.validate_subject_id",
            side_effect=_validate,
        ),
    ):
        mock_service.get_all_mappings.return_value = {"坏番": "111", "好番": "222"}
        mock_service.get_all_rules.return_value = []
        mock_service.update_custom_mappings.return_value = True

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            # 用户删掉了坏番，且没动好番
            response = await client.post(
                "/api/mappings", json={"mappings": {"好番": "222"}}
            )

        assert response.status_code == 200, response.text
        assert seen == [], f"未改动的 ID 不应被校验: {seen}"


@pytest.mark.asyncio
async def test_clearing_all_bad_ids_is_allowed(app_with_auth):
    """清空全部配置也必须能成功（提交为空 → 无可校验项）

    此前「清空」也要过全量校验，于是坏 ID 会把用户锁死在配置里。
    """
    with (
        patch("app.api.mappings.mapping_service") as mock_service,
        patch(
            "app.utils.bangumi_api.subject_validation.validate_subject_id",
            return_value=(False, "条目不存在或 API 返回空"),
        ) as mock_validate,
    ):
        mock_service.get_all_mappings.return_value = {"坏番": "111"}
        mock_service.get_all_rules.return_value = []
        mock_service.update_custom_mappings.return_value = True

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/mappings", json={"mappings": {}, "rules": []}
            )

        assert response.status_code == 200, response.text
        mock_validate.assert_not_called()


@pytest.mark.asyncio
async def test_editing_rule_validates_only_that_rule(app_with_auth):
    """编辑一条规则只校验那条规则的新 ID，其余规则不重验"""
    seen: list[str] = []

    def _validate(sid):
        seen.append(str(sid))
        return (True, "")

    existing_rules = [
        {"pattern": "^甲$", "subject_id": "111"},
        {"pattern": "^乙$", "subject_id": "222"},
    ]
    # 用户把第二条规则的 ID 改成 333
    new_rules = [
        {"pattern": "^甲$", "subject_id": "111"},
        {"pattern": "^乙$", "subject_id": "333"},
    ]

    with (
        patch("app.api.mappings.mapping_service") as mock_service,
        patch(
            "app.utils.bangumi_api.subject_validation.validate_subject_id",
            side_effect=_validate,
        ),
    ):
        mock_service.get_all_mappings.return_value = {}
        mock_service.get_all_rules.return_value = existing_rules

        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/mappings", json={"mappings": {}, "rules": new_rules}
            )

        assert response.status_code == 200, response.text
        assert seen == ["333"], f"只应校验改动的那条规则: {seen}"


@pytest.mark.asyncio
async def test_validation_does_not_block_event_loop(app_with_auth):
    """校验必须在工作线程里跑：validate_subject_id 是阻塞式网络调用

    直接在 async 处理函数里循环调用会卡住事件循环（100 条映射 = 100 次串行
    阻塞 HTTP）。这里断言它被切到线程执行，而不是在主线程内联调用。
    """
    import threading

    main_thread = threading.current_thread().ident
    called_from: list[int | None] = []

    def _validate(sid):
        called_from.append(threading.current_thread().ident)
        return (True, "")

    with (
        patch("app.api.mappings.mapping_service"),
        patch(
            "app.utils.bangumi_api.subject_validation.validate_subject_id",
            side_effect=_validate,
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/mappings", json={"mappings": {"番": "12345"}}
            )

        assert response.status_code == 200
        assert called_from, "校验函数没有被调用"
        assert all(tid != main_thread for tid in called_from), (
            "校验在主线程（事件循环）里执行，会阻塞其它请求"
        )


@pytest.mark.asyncio
async def test_update_tolerates_validation_downgrade(app_with_auth):
    """校验因无账号/网络异常降级放行时，写入仍应成功

    校验不应成为阻塞用户编辑的环节（降级放行是既有约定）。
    """
    with (
        patch("app.api.mappings.mapping_service") as mock_service,
        patch(
            "app.utils.bangumi_api.subject_validation.validate_subject_id",
            return_value=(True, ""),
        ),
    ):
        mock_service.update_custom_mappings.return_value = True
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/mappings", json={"mappings": {"某番": "12345"}}
            )

        assert response.status_code == 200


# --------------------------------------------------------------------------
# 章节列表端点（供映射弹窗自动推算偏移量）
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_subject_episodes(app_with_auth):
    """返回正片章节的 sort/name/airdate"""
    from unittest.mock import MagicMock

    api = MagicMock()
    api.get_episodes.return_value = {
        "data": [
            {"sort": 1, "name": "Ep1", "name_cn": "第一集", "airdate": "2024-01-01"},
            {"sort": 2, "name": "Ep2", "name_cn": "", "airdate": "2024-01-08"},
            {"name": "no sort field"},  # 无 sort 应被跳过
        ],
        "total": 3,
    }

    with patch(
        "app.utils.bangumi_api.factory.build_bangumi_api_from_primary_config",
        return_value=api,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.get("/api/mappings/subject/639938/episodes")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["total"] == 2
    assert data["episodes"][0]["sort"] == 1
    assert data["episodes"][1]["airdate"] == "2024-01-08"
    api.close.assert_called_once()


@pytest.mark.asyncio
async def test_get_subject_episodes_invalid_id(app_with_auth):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_auth), base_url="http://test"
    ) as client:
        response = await client.get("/api/mappings/subject/0/episodes")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_get_subject_episodes_no_account(app_with_auth):
    """无可用账号时给出可操作的 400，而不是 500"""
    with patch(
        "app.utils.bangumi_api.factory.build_bangumi_api_from_primary_config",
        return_value=None,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app_with_auth), base_url="http://test"
        ) as client:
            response = await client.get("/api/mappings/subject/639938/episodes")

    assert response.status_code == 400
    assert "账号" in response.json()["detail"]
