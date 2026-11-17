"""
季度信息检测 Mixin：把「标题声明了第几季」的判定转发给纯函数。

历史上这里有两套并行实现：一组本地关键词表 + 内联中文数字表，与
``app/utils/season_title.py`` 的结构化解析器各自演进。后果是口径漂移 ——
``笨蛋，测验，召唤兽2`` / ``偶像大师 II`` 这类不含「第N季」字样的续作只有
解析器认得，季过滤器因此把这些候选当第一季剔除（L1 黄金集曾整链失败）。

现在两个方法都只是转发，判定逻辑只有一份（``app.utils.season_title``），
匹配层、单测与跨季选集看到的是同一套行为。保留 Mixin 是因为调用方通过
``service._check_season_info_in_title`` 访问（也有测试替身依赖这个入口）。
"""

from __future__ import annotations

from ...utils.season_title import extract_explicit_season, title_declares_season


class SeasonInfoMixin:
    """季度信息检测（纯逻辑，无外部依赖）。"""

    def _get_explicit_season_from_title(self, title: str) -> int | None:
        """从标题中提取明确声明的季度编号。

        返回值：
        - 明确声明第 N 季时返回 N（>=1）
        - 标题不含季度声明时返回 None（可能是第一季本体，也可能是总集篇等）

        覆盖形式：第 X 季 / 第 X 期（阿拉伯与中文数字）、Xnd/Xrd/Xth season、
        Season X，以及标题结尾的季号与罗马数字（见
        :func:`app.utils.season_title.extract_explicit_season`）。
        """
        return extract_explicit_season(title)

    def _check_season_info_in_title(self, title: str, season: int) -> bool:
        """判断标题是否声称自己就是第 ``season`` 季。

        实现见 :func:`app.utils.season_title.title_declares_season`（关键词、
        裸 ``S{N}``、结构化解析三级判定）。
        """
        return title_declares_season(title, season)
