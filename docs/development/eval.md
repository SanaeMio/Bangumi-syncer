---
title: 🧪 LLM 匹配 Eval 方案
order: 7
---

# 🧪 LLM 匹配 Eval 方案

面向 LLM 匹配增强的**两层评测体系**：L1 离线回放（零成本，进 CI，防回归）+
L2 真实模型 eval（本地/手动，量质量）。本文档给出设计、数据格式、运行手册与
CI 策略。

## 为什么是两层

| | L1 离线回放 | L2 真实模型 eval |
| --- | --- | --- |
| 何时跑 | 每个 PR（进 CI） | 本地 / 手动 / 定期 |
| 成本 | **0**（不发任何 API） | 按量计费（详见「成本」） |
| 数据 | cassette（真实执行的录制） | 黄金集 + 真实 LLM + 异源 judge |
| 验证什么 | prompt 构建 / 消息序列 / 解析 / 状态流转**没被改坏** | agent 真的变好了还是变坏了 |
| 回答 | "没回归" | "变好还是变坏" |

核心机制（对齐 VCR 模式）：**首次真实执行录制 cassette，之后全部回放**；
请求变化 → 指纹不匹配 → L1 失败（提示重录）；**CI 环境物理上禁止录制**。

## 目录结构

```
eval/
├── golden/public_v1.jsonl   # 黄金集（公开案例，人工标注期望）
├── fixtures/<case_id>.json  # cassette：口令录制（含请求指纹与期望终局）
├── lib.py                   # harness：临时 DB、回放/录制注入器、指纹、cassette IO
├── run_eval.py              # CLI：record / replay / live（CI 禁 record 机制守卫）
├── judge.py                 # LLM-as-judge（经本地 opencode CLI 调异源模型）
└── report.py                # markdown 报告（指标 + delta + judge 问题清单）
```

## 数据格式

### 黄金集（`golden/*.jsonl`，每行一条）

```json
{"id": "m001", "source": "issue#182", "scenario": "S11_同名不同年份",
 "input": {"title": "无职转生：到了异世界就拿出真本事", "ori_title": "", "season": 3,
           "episode": 1, "release_date": "2026-07-04", "media_type": "episode"},
 "expect": {"subject_id": 501963, "acceptable_ids": [501963], "expect_stop": "submit_suggestion"},
 "tags": ["cjk", "season-shift"], "note": "期望 501963"}
```

- `scenario`：输入形态分类，取值对齐 `scripts/gen_golden_cases.py` 的 `SCENARIOS`
  （见下节），用于报告聚合与扩集查漏补缺；`tags` 保留自定义细分标注。
- `expect_stop`：`submit_suggestion`（应给出建议）或 `no_suggestion`（负样本）。
  > 用例规格词汇，与 `agent_runs.status` 无关：状态机已把「无建议」收敛为 `succeeded`，
  > 差异由 `stop_reason`（如 `give_up`）承载。
- 案例来源建议：GitHub issues 中的匹配错误报告（输入 + 正确条目号），标注后用
  Bangumi API 校验 `subject_id` 存在性与标题吻合。

### cassette（`fixtures/<case_id>.json`）

```json
{"schema_version": 1, "case_id": "m001",
 "meta": {"model": "deepseek-chat", "provider": "anthropic_compat", "thinking_level": "medium"},
 "rounds": [{"request_fingerprint": "sha256:...",
             "response": {"stop_reason": "...", "content": "...", "tool_calls": [], "usage": {}},
             "tool_results": [{"tool_use_id": "...", "tool_name": "...", "input": {},
                                "content": "...", "is_error": false}]}],
 "expected_outcome": {"run_status": "succeeded", "stop_reason": "submit_suggestion",
                       "candidate_subject_id": "501963", "notifications": 1}}
```

- **请求指纹** = `sha256(模型 + tools schema + tool_choice + 规范化消息序列)`；
  回放逐轮校验，任何 prompt/消息序列/工具协议变化都会失败（硬锁）。
- cassette 是测试夹具：与 golden 一起走代码评审；提交前做敏感信息扫描
  （不得含 token/key 等模式）。

## 场景分类（对齐 scripts/golden_*）

### 两套 golden 的关系（注意区分）

| | `eval/golden/`（本文档） | `scripts/golden_data/`（上游脚本） |
| --- | --- | --- |
| 被测对象 | **规则层失手之后**的 LLM 兜底 | **规则匹配管线本身** |
| 数据来源 | 真实 issue/PR 误配案例（人工标注） | bangumi-data / archive **采样生成**（seed 固定） |
| 判定 | 命中率 vs 人工 oracle | 与基线快照比对（行为变没变）+ oracle 命中率（护栏 ≥ 0.90） |
| 运行 | L1 回放进 CI；L2 手动 | 纯手动（`scripts/golden_check.py`，改动管线前后各跑） |

两套体系测同一条业务链路的**前后两段**，数据互不重叠、互为补充。本黄金集的
`scenario` 字段**单向对齐** `scripts/gen_golden_cases.py` 的 `SCENARIOS` 字典
（不改动上游文件），以保证两类数据可用同一套术语聚合解读。

### 场景清单（SCENARIOS）

| 场景 | 定义（媒体库输入形态） |
| --- | --- |
| S1_原名精确 | JP 原名 + 首播日期，期望命中自身 |
| S2_中文名精确 | 中文名 + 首播日期，期望命中自身 |
| S3_季后缀 | 原名 +「 第二季」且 season=2，考验季后缀剥离 |
| S4_无日期 | 原名但无首播日期（年份消歧失效） |
| S5_剧场版前缀剥离 | 「劇場版 X」条目，用剥离前缀后的 X 查询 |
| S6_三次元 | 三次元条目（仅 scripts L2 使用） |
| S7_短标题碰撞 | 短标题（≤ 5 字）易被长标题包含，期望命中自身 |
| S8_模糊typo | 原名随机替换 1 字符，考验模糊兜底 |
| S9_全角半角 | 原名 ASCII 转全角，考验归一化 |
| S10_同名多版本 | 同一原名多版本，用原名 + **最早**年份查询 |
| S11_同名不同年份 | 同名多版本中取**非最早**版本 + 其年份，考验日期消歧 |
| S12_日期漂移 | 首播日期 +400 天（超扫描门槛），考验扫描兜底 |
| S13_无匹配负例 | 不存在的标题，期望不命中（防阈值放宽误配） |

### 现有案例映射（21 条）

| id | scenario | 备注 |
| --- | --- | --- |
| m001~m005、m007、m008、m010 | `S11_同名不同年份` | 季偏移 / 同名多版本消歧（首批案例集中于此） |
| m006 | `S5_剧场版前缀剥离` | 剧场版匹配失败 |
| m009 | `S7_短标题碰撞` | 通用词被长标题包含（Friends） |
| m011~m013 | `S13_无匹配负例` | 特典命名 / 非番剧内容 / 多部作品歧义，期望放弃 |
| m014、m015 | `S8_模糊typo` | 错字「狐独摇滚」；繁简混「无限列车編」（m015 为上游误配型能力样本） |
| m016、m017 | `S9_全角半角` | 全角化英文原名（Fate/Zero、Steins;Gate） |
| m018~m021 | `S14_罗马音或英文名` | Oshi no Ko / Cyberpunk: Edgerunners / Made in Abyss / Kaguya-sama |

**S12（日期漂移）说明**：实测当前上游对日期漂移（+400 天）已能兜底（标题索引 + 日期择优），
不构成 LLM 兜底输入，故本黄金集暂不纳入该场景。

### 语义差异与扩展规则

- scripts 的 S 分类描述"规则管线**期望能处理**的输入形态"；本黄金集的 S 分类描述
  "**LLM 兜底要修正**的失败模式"——对齐的是**题目类型（输入形态）**的词汇，
  判定标准各自保留。
- scripts 没有的输入形态：**新增编号**并在本表登记，保持单向对齐；目前新增：

  | 场景 | 定义 |
  | --- | --- |
  | `S14_罗马音或英文名` | 媒体库推送罗马音/英文原名（无中文/日文标题） |

  若未来被上游采纳再反向合并。

## 运行手册

```bash
# 录制（本地，需要 key；CI 环境被硬拒绝）
EVAL_LLM_API_KEY=... uv run python eval/run_eval.py --mode record --all

# 回放（离线、零成本；CI 用）
uv run python eval/run_eval.py --mode replay --all

# 真实模型 eval + judge（本地/手动）
EVAL_LLM_API_KEY=... uv run python eval/run_eval.py --mode live --all --judge
```

环境变量：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `EVAL_LLM_API_KEY` | （必填） | record / live 的 LLM key（**不得入库**） |
| `EVAL_LLM_PROVIDER` | `anthropic_compat` | 或 `openai_compat` |
| `EVAL_LLM_BASE` | `https://api.deepseek.com/anthropic/v1` | OpenAI 兼容填 `https://api.deepseek.com` |
| `EVAL_LLM_MODEL` | `deepseek-chat` | 思考模型（如 `deepseek-v4-pro`）建议配大 max_tokens |
| `EVAL_LLM_MAX_TOKENS` | `2000` | **思考模型需显著调大**（如 16000），否则响应可能被截断 |
| `EVAL_LLM_TEMPERATURE` | `0.0` | 评测稳定性 |
| `EVAL_JUDGE` / `--judge-model` | `opencode-go/qwen3.7-plus` | judge 走本地 opencode CLI（异源） |

## CI 策略（零成本接入）

- **L1 进 CI**：将回放封装为 pytest 用例（`tests/eval/`），由现有 `ci-tests.yml`
  的 `pytest tests/` 收集——不需要新 workflow、不需要 secrets，fork PR 同样可跑。
- **机制守卫**：`--mode record` 在 `CI=true` 下直接报错退出——"CI 绝不录音"由代码
  保证，而非依赖纪律；回放路径完全不触网（LLM 响应/工具结果全部来自 cassette）。
- **L2 不进 PR 门禁**：真实模型调用成本与结果非确定性不适合作为合并条件；
  由贡献者/维护者本地或 fork 运行，报告贴 PR 作为质量证据。

## judge 可信度

- **异源判分**：judge 模型与生成模型不同源（生成 DeepSeek / judge Qwen）；
- 结构化输出（`{"verdict","score","issues"}`）+ 解析失败重试，失败计 error；
- 主指标用人工标注的 ground truth（命中率），judge 只补开放质量维度——judge 有偏差
  也不动摇主结论；
- 建议定期人工抽检校准（记录 judge 与人工一致率）。

## 跑分记录

### 首轮（2026-09-19，3 条案例）

| 模型 / 路径 | top1 | judge |
| --- | --- | --- |
| deepseek-chat · anthropic_compat | 1/3 | 5 / 2 / 2 |
| deepseek-chat · openai_compat | 1/3 | 5 / 1 / 1 |
| deepseek-v4-pro · anthropic_compat | **3/3** | 5 / 5 / 5 |
| deepseek-v4-pro · openai_compat | 2/3 | 5 / 5 / 1 |

首轮即发现并修复 2 个真实缺陷（已在 commit `57baaab` / `445cb00` 修复，含回归测试）：

1. **anthropic_compat 多工具并行 400**：loop 逐条追加 tool_result 违反 Anthropic
   "同一条消息紧跟"约束 → provider 归一化合并；
2. **thinking 模型兼容**：thinking 块需随 tool_use 回传；thinking 模式拒绝强制
   `tool_choice` → loop 保留响应块 + client/provider 三层降级（`auto`）。

遗留问题（恢复路径 thinking 回传、无建议场景的 tokens 口径、预算调优等）
见项目内部归档（`remain/eval_round1_findings.md`）。

### 扩集后（2026-09-20，10 条案例，flash · medium=5 轮）

- **10/10 全中**（季偏移 8 条 + 剧场版 + 通用词），总 tokens ≈ 17.5 万；
- L1 回放 10/10 一致；`tests/eval` 17 passed；
- **环境快照注意**：cassette 里的工具结果（搜索返回）携带录制时的外部 API 状态
  （如 v0/legacy 搜索通道、数据时间）——同一模型同一输入的跑分**跨时间不可直接对比**，
  L2 数字需标注环境与日期。

### 负样本修复后（2026-09-21，21 条，flash · medium=5 轮）

- 实施「放弃协议（`give_up`）+ 终止提交软护栏（veto 一次）+ 收尾 prompt 强化」后重录：
  **20/21 命中**（此前 18/21）；
- **m011/m012 转为明确放弃**（`succeeded(stop_reason=give_up)`、不落库、不通知）——搜索充分性明显提升
  （m012 尝试 4 组关键词后放弃），且是**直接 `give_up`**（veto 未触发，护栏作为兜底保留）；
- 18 条正向**零误伤**（veto 词表预检 + 实测均未触发）；
- 遗留：m013「剧场版 高达」（未识别歧义型——理由中无不确定表述，需更强的歧义检测，非本机制覆盖）。

### 流式化兼容性验证（2026-09-24，T11；2026-09-26 收口）

LLM 调用底层为**流式唯一形态**：`LLMClient.stream_chat()` 是唯一调用入口，
聚合消费一律由调用方 `collect(stream_chat(...))` 完成（`chat()` 过渡别名已删除）。
对 eval 体系做兼容性核查，**结论：无需任何适配，cassette 不必重录**。

- **回放匹配键不含 HTTP 请求体**：指纹 =
  `sha256(模型 + tools schema + tool_choice + 规范化消息序列)`（见 `eval/lib.py`
  的 `fingerprint()`），与 `stream`/`stream_options` 等传输层字段无关。
- **拦截层在 `stream_fn`（provider 之上）**：replay 用 `FixtureDriver.stream`
  直接顶替 `stream_fn`，测试侧经 `collect(driver.stream(...))` 聚合为
  `ChatResponse`；工具结果由 `ToolRegistry.execute_batch` 的 patch 供给；
  `build_real_stream_fn`（record/live）包装 `LLMClient.stream_chat()`。
- **`stream: true` 注入点在 provider 层**（`providers/anthropic.py`、
  `openai_compat.py`、`openai_responses.py` 组装请求体处），位于指纹计算边界
  之下，因此不会改变指纹，也不会影响 cassette 匹配。
- **端点须支持 SSE**：不接受流式请求的端点会确定性失败（提示更换端点/升级网关），
  不再有非流式兜底；评测使用的服务与网关需支持 SSE。
- **验证**：`uv run pytest tests/eval/ -q` 全绿（golden 回放 + 指纹守卫 +
  敏感信息扫描 + 零网络守卫）。
- **live 链路**：代码路径已就绪（`run_eval.py --mode live --case <id>`，经
  `stream_chat()` 走真实流式端点），但需设置
  `EVAL_LLM_API_KEY`/`DEEPSEEK_API_KEY` 等凭据后本地补跑。

## 演进方向

1. 黄金集扩至 20~30 条：第二批按「场景分类」清单补齐（`S8`/`S9`/`S12`/`S13` 优先），
   并做 dev/holdout 切分（holdout 优先真实来源案例）；
2. ~~L1 进 CI~~ ✅ 已完成（`tests/eval/`：10 条回放 + fixture 安全扫描 + 零网络守卫）；
3. ~~恢复/重放路径 thinking 块支持~~ ✅ 已完成（recorder 存全量 blocks + replay 重建消费）；
4. ~~provider/thinking 兼容与预算调优~~ ✅ 已完成（thinking 回传、tool_choice 降级、
   动态超时、终态守卫；详见 `remain/eval_round1_findings.md`）；
5. judge 人工抽检校准与成本上报（`llm_usage_logs`，`job_name="eval"`）；
6. 维护者可选 opt-in：为 L2 配置 secrets 后按 label/定时触发（默认不启用）。
