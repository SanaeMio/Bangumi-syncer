"""
日志分组解析测试
"""

from app.utils.log_grouping import group_log_lines


def _line(run_id: str, level: str, message: str) -> str:
    return f"[2026/07/16 09:00:01.123] [{level}] [run:{run_id}] {message}"


class TestGroupLogLines:
    def test_group_by_run_id_interleaved(self):
        lines = [
            _line("sync_a", "INFO", "同步开始: 番剧A S01E01 (plex)"),
            _line("sync_b", "INFO", "同步开始: 番剧B S01E02 (emby)"),
            _line("sync_a", "INFO", "bgm: 番剧A 已标记为看过"),
            _line("sync_b", "INFO", "bgm: 番剧B 已标记为看过"),
            _line("sync_a", "INFO", "同步结束: status=success"),
            _line("sync_b", "INFO", "同步结束: status=success"),
        ]
        result = group_log_lines(lines)
        assert len(result["groups"]) == 2
        run_ids = {g["run_id"] for g in result["groups"]}
        assert run_ids == {"sync_a", "sync_b"}
        for g in result["groups"]:
            assert g["status"] == "success"
            assert g["line_count"] == 3

    def test_login_orphan_not_grouped(self):
        lines = [
            _line("sync_a", "INFO", "同步开始: 番剧A S01E01 (plex)"),
            "[2026/07/16 09:00:02.000] [INFO] 用户 admin 登录成功，IP: 127.0.0.1",
            _line("sync_a", "INFO", "同步结束: status=success"),
        ]
        result = group_log_lines(lines)
        assert len(result["groups"]) == 1
        assert result["groups"][0]["run_id"] == "sync_a"
        assert len(result["orphans"]) == 1
        assert "登录成功" in result["orphans"][0]

    def test_emoji_preserved_in_group(self):
        lines = [
            _line("sync_1", "INFO", "同步开始: 番剧A S01E01 (plex)"),
            _line("sync_1", "INFO", "📚 [Bangumi] GET /v0/subjects/1 成功"),
        ]
        result = group_log_lines(lines)
        assert "📚" in result["groups"][0]["lines"][1]

    def test_heuristic_ambiguous_on_overlap(self):
        lines = [
            "[2026/07/16 09:00:01.000] [INFO] 接收到同步请求：番剧A",
            "[2026/07/16 09:00:02.000] [INFO] 接收到同步请求：番剧B",
            "[2026/07/16 09:00:03.000] [INFO] bgm: 番剧A 已标记为看过",
            "[2026/07/16 09:00:04.000] [INFO] bgm: 番剧B 已标记为看过",
        ]
        result = group_log_lines(lines)
        assert len(result["groups"]) >= 1
        assert any(g.get("ambiguous") for g in result["groups"])

    def test_level_counts(self):
        lines = [
            _line("sync_1", "INFO", "同步开始: 番剧A S01E01 (plex)"),
            _line("sync_1", "DEBUG", "detail"),
            _line("sync_1", "INFO", "bgm: 番剧A 已标记为看过"),
            _line("sync_1", "ERROR", "fail"),
        ]
        result = group_log_lines(lines)
        counts = result["groups"][0]["level_counts"]
        assert counts.get("DEBUG") == 1
        assert counts.get("INFO") == 2
        assert counts.get("ERROR") == 1

    def test_truncated_flag(self):
        lines = [_line("sync_1", "INFO", "同步开始: 番剧A S01E01 (plex)")]
        result = group_log_lines(lines, truncated_run_ids={"sync_1"})
        assert result["groups"][0]["truncated"] is True

    def test_title_from_sync_start_line(self):
        lines = [
            _line(
                "retry_1",
                "INFO",
                "同步开始: 关于同组的染谷同学是性感女优这件事。 S01E01 (retry-plex)",
            ),
            _line("retry_1", "ERROR", "bgm: 未查询到番剧信息，跳过"),
            _line("retry_1", "INFO", "同步结束: status=error"),
        ]
        group = group_log_lines(lines)["groups"][0]
        assert "染谷" in group["title"]
        assert group["season"] == 1
        assert group["episode"] == 1
        assert group["source"] == "retry-plex"

    def test_duration_ms(self):
        lines = [
            "[2026/07/16 09:50:37.165] [INFO] [run:retry_1] 同步开始: 番剧A S01E01 (plex)",
            "[2026/07/16 09:50:39.135] [INFO] [run:retry_1] 同步结束: status=error",
        ]
        group = group_log_lines(lines)["groups"][0]
        assert group["duration_ms"] == 1970

    def test_unrecognized_group_goes_to_orphans(self):
        lines = [_line("sync_test_1", "INFO", "hello sync")]
        result = group_log_lines(lines)
        assert result["groups"] == []
        assert len(result["orphans"]) == 1
