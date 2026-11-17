"""集号抽取纯函数（供各源适配层共用）。

集号在项目里有**两种强度**的需求，刻意拆成两个入口，不要合并：

- :func:`extract_episode_number` —— 只认结构化集号标记
  （``SxxExx`` / ``EPxx`` / ``第N集`` / ``#N`` / ``[N]``）。
  用于「标题 / 条目名」这类本身可能带数字的文本：``86 不存在的战区``、
  ``機動戦士ガンダム0083`` 里的数字是作品编号，绝不能被当成集号。
- :func:`extract_episode_from_filename` —— 结构化标记全落空后，按文件名取
  首个 1-3 位数字（先剥括号、文件大小、分辨率、合集范围、季号标记）。
  激进兜底，**只允许在确认输入是文件路径/文件名时调用**。

这个「两种强度」的边界就是历史漂移的来源：feiniu 曾用一条
``(?:第|E|ep)\\s*0*(\\d+)`` 正则同时冒充两者（无词边界、无上界、会把
``86`` 当集号），fongmi 另写一套六级优先级，两套对同一文件名可能给出不同
集号。现在统一到本模块，调用方只选「用哪个强度」，不再各写正则。

季号解析**不在这里**：标题季号见 :mod:`app.utils.season_title`。本模块只保留
文件名里的季号标记（:func:`detect_filename_season`），它比标题解析窄得多，
这是刻意的 —— ``05 1080p.mp4`` 这种文件名会让标题解析器的「结尾数字=季号」
规则误判成第 4 季。
"""

from __future__ import annotations

import re

from .season_suffix import (
    FILENAME_SEASON_CN_RE,
    FILENAME_SEASON_EN_RE,
    SCOPE_FILENAME,
    strip_season_markers,
)
from .text_constants import CN_NUM

# 集号上界。历史教训：feiniu 侧没有上界，``第1234集`` 会被当成集号，靠下游
# ``get_target_season_episode_id`` 的 max_episode 才兜住，比源头晚了一层。
MAX_EPISODE = 999

# ===== 结构化集号标记（强度 1）=====
SEASON_EPISODE_RE = re.compile(r"[Ss](\d{1,2})\s*\.?[Ee](\d{1,3})", re.IGNORECASE)
_EP_RE = re.compile(r"\b[Ee][Pp]?[\s.\-_]?\s*0*(\d{1,3})\b", re.IGNORECASE)
_CN_EP_RE = re.compile(r"第\s*0*(\d{1,3})\s*[集话話章回]")
_HASH_EP_RE = re.compile(r"#\s*0*(\d{1,3})\b")
_BRACKET_EP_RE = re.compile(r"[\[【(]\s*0*(\d{1,3})\s*[\]】)]")

# 顺序即优先级：EP/Exx > 第N集 > #N > [N]
_EPISODE_PATTERNS = (_EP_RE, _CN_EP_RE, _HASH_EP_RE, _BRACKET_EP_RE)

# ===== 文件名清理（强度 2 兜底用）=====
_BRACKET_RE = re.compile(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}|【[^】]*】|《[^》]*》")
_SIZE_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:KB|MB|GB|TB)", re.IGNORECASE)
_RESOLUTION_RE = re.compile(
    r"\b(?:4K|2160P|1080P|720P|480P|360P|UHD|FHD|HD|HDR)\b", re.IGNORECASE
)
_RANGE_RE = re.compile(r"\b\d{1,4}\s*-\s*\d{1,4}\b")
_FIRST_NUM_RE = re.compile(r"\d{1,3}")

# ===== 文件名季号标记 =====
# 模式单源在 ``app.utils.season_suffix``（filename profile），与其余三套季剥离
# 模式并排维护；本模块只用它的捕获组读季号、用 strip_season_markers 做清理。
# 语义比 season_title 的标题解析**窄得多**，不要替换成 extract_explicit_season：
# 这里的输入是文件名，结尾数字多为分辨率/集号（``05 1080p.mp4``），标题解析器
# 的「结尾数字=季号」规则会误判。故只认带「季/期」或 Season 字样的标记，
# 且只接受 N>1（S01/无标记一律视为第一季，见 fongmi 方案 B）。


def _bounded_episode(raw: str) -> int | None:
    """把正则捕获的集号归一化：0 抬成 1（``EP00`` → 1），超上界视为未识别。"""
    n = int(raw)
    if n > MAX_EPISODE:
        return None
    return max(1, n)


def extract_episode_number(text: str) -> int | None:
    """从标题/条目名抽取结构化集号，识别不到返回 ``None``。

    只认显式标记，**不做纯数字兜底**：``86 不存在的战区`` → ``None``。
    需要文件名级别的激进兜底请用 :func:`extract_episode_from_filename`。
    """
    if not text:
        return None
    m = SEASON_EPISODE_RE.search(text)
    if m:
        return _bounded_episode(m.group(2))
    for pattern in _EPISODE_PATTERNS:
        m = pattern.search(text)
        if m:
            return _bounded_episode(m.group(1))
    return None


def has_episode_marker(text: str) -> bool:
    """是否含任意结构化集号标记（不关心具体集号）。"""
    if not text:
        return False
    if SEASON_EPISODE_RE.search(text):
        return True
    return any(p.search(text) for p in _EPISODE_PATTERNS)


def extract_episode_from_filename(text: str) -> int:
    """文件名兜底：清理后取第一个 1-3 位数字；识别不到返回 ``0``。

    依次剥掉扩展名、方括号内容、文件大小、分辨率、合集范围、季号标记，再取
    首个数字。仅在确认输入是文件名时调用 —— 它对 ``86`` / ``0083`` 这类作品
    编号无免疫力。
    """
    if not text:
        return 0
    filename = text.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    filename = re.sub(r"\.\w+$", "", filename)
    filename = _BRACKET_RE.sub(" ", filename)
    filename = _SIZE_RE.sub(" ", filename)
    filename = _RESOLUTION_RE.sub(" ", filename)
    filename = _RANGE_RE.sub(" ", filename)
    filename = strip_season_markers(filename, SCOPE_FILENAME)
    m = _FIRST_NUM_RE.search(filename)
    if m:
        n = int(m.group())
        if 1 <= n <= MAX_EPISODE:
            return n
    return 0


def detect_filename_season(text: str) -> int:
    """从文件名/路径检测明确季号，仅当 N>1 时返回 N，否则返回 1。

    方案 B：S01、S00、无标记一律返回 1 —— 源站常把长番统一标成 S01，据此
    断言「第一季」会误匹配到某一具体季，交给后续续集链查找更安全。
    """
    if not text:
        return 1
    m = FILENAME_SEASON_CN_RE.search(text)
    if m:
        token = m.group(1)
        n = CN_NUM.get(token) or (int(token) if token.isdigit() else 0)
        if n > 1:
            return n
    m = FILENAME_SEASON_EN_RE.search(text)
    if m:
        n = int(m.group(1))
        if n > 1:
            return n
    return 1


def parse_season_episode(*sources: str) -> tuple[int, int]:
    """从若干候选文本（url、artist …）解析 ``(season, episode)``。

    文本按传入顺序构成优先级：前一个文本解析出结果就不再往后看。集号缺失
    回退为 1；季号按 :func:`detect_filename_season` 的方案 B 语义。

    原先 fongmi 的 ``parse_episode_info(url, artist)`` 就是本函数的二参数特例，
    保留那个名字是为了不惊动调用方与既有测试。
    """
    texts = [s for s in sources if s]

    # 1. SxxExx（同时给出季号与集号，优先级最高）
    for text in texts:
        m = SEASON_EPISODE_RE.search(text)
        if m:
            episode = _bounded_episode(m.group(2))
            if episode is not None:
                season = int(m.group(1))
                return (season if season > 1 else 1, episode)

    # 2. 其余结构化标记（只给集号）
    episode: int | None = None
    for pattern in _EPISODE_PATTERNS:
        for text in texts:
            m = pattern.search(text)
            if m:
                episode = _bounded_episode(m.group(1))
                if episode is not None:
                    break
        if episode is not None:
            break

    # 3. 文件名兜底
    if episode is None:
        for text in texts:
            ep = extract_episode_from_filename(text)
            if ep:
                episode = ep
                break

    if episode is None:
        episode = 1

    # 4. 季号（首个给出 N>1 的文本生效）
    season = 1
    for text in texts:
        s = detect_filename_season(text)
        if s > 1:
            season = s
            break

    return season, episode
