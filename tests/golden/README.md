# 匹配管线黄金用例集（golden cases）

给匹配管线的后续改造（契约层 P2 / 裁决层 P3）提供**可回归的闸门**。
改造前后各跑一次，逐条比对，任何行为变化都必须被人工 review 后才能更新基线。

## 两层

| 层 | 文件 | 覆盖 | 数据依赖 | CI |
|---|---|---|---|---|
| L1 | `bangumi_data_cases.json` | bangumi-data 层的 `find_bangumi_id` | 无（fixture 自带） | **必跑** |
| L2 | `matching_cases.json` | 完整管线（custom_mapping → archive → bangumi-data → api_search） | Bangumi Archive 归档库 | 默认 skip |

L1 自带精简后的 bangumi-data 子集（目标条目 + 每个目标的 top-10 易混淆干扰项），
不联网、不读 707MB 的归档库，因此 CI 一定跑得起来。

L2 依赖约 300MB 的 Archive 归档库（不在仓库中），默认跳过；本地有数据时显式启用：

```bash
GOLDEN_L2=1 uv run pytest tests/golden -m golden -k l2
```

## 场景

| 场景 | 含义 |
|---|---|
| S1 原名精确 / S2 中文名精确 / S4 无日期 | 基础形态 |
| S3 季后缀 / S5 剧场版前缀剥离 | 标题变体 |
| S7 短标题碰撞 / S8 模糊typo / S9 全角半角 | 归一化与模糊兜底 |
| S10 同名多版本 / S11 同名不同年份 | 日期消歧（S11 区分度最高） |
| S12 日期漂移 | 首播日期 +400 天，跨过 180 天索引门槛走扫描兜底 |
| S13 无匹配负例 | 不存在的标题，防阈值放宽导致凭空匹配 |

## 基线的语义

**基线是「生成时的实际行为快照」，不是「应当正确」的断言。**

因此测试失败只说明"行为变了"，不说明"变坏了"——需要结合报告里的
oracle 命中率判断方向。命中率在断言输出中给出（如 `88.8% → 86.2%`）。

每条用例都带 `oracle`（构造用例时的真实答案），正例命中率即为质量参考：

- L1：326/330 = 98.8%（bangumi-data 单层相当准）
- L2：213/240 = 88.8%（完整管线更差 —— 差异来自 archive 短路的盲信）

候选列表（top-5 + 分数）也参与比对。只比对最终 subject_id 时，打分与排序的
改动往往仍命中同一条目，闸门会漏掉；加上候选后，实测把部分匹配阈值 0.4→0.1
能触发 322/360 条告警。

## 重新生成基线

确认行为变化是有意为之后：

```bash
uv run python scripts/gen_golden_cases.py --mode l1     # 离线集
uv run python scripts/gen_golden_cases.py --mode l2     # 全量管线（需 archive 数据）
```

采样是**确定性**的（固定种子 + 按 bangumi id 排序的候选池），同样的输入会
产出逐字节一致的结果——这一点已验证，否则 A/B 数字没有可比性。

## 已知局限

- L1 只覆盖 bangumi-data 一层，跑不到 archive / api_search 策略。
  **裁决层（P3）改动「哪个策略赢」，只有 L2 拦得住**，合入前必须在有
  archive 数据的机器上跑一次 L2。
- L1 的 fixture 是全量的约 42%（3676/8829）。若某次改动命中了子集外的
  条目，L1 看不出来。
