# 记忆实验（联网）

qwen-plus, temperature 0, single run, self-authored scenarios; judge is an LLM and must be spot-checked.
版本：{'extract': 'extract-v2', 'judge': 'judge-v2'}

## 抽取（LLM 记忆管理器）

- 应写入的记忆命中：41/42
- 时间字段（生效日/到期日）正确：4/4
- 删除请求执行：1/1
- 多写的条目（重复或不该记）：1
- 敏感/一次性信息入库：0 []
- 解析异常：0，调用失败：0

## 召回（上下文层面）

| 记忆来源+召回 | 需要的全部召回 | 坏记忆泄漏 | 无关问题被注入 | 平均注入 |
|---|---:|---:|---:|---:|
| llm_append+inject_all | 26/27 | 15/15 | 9/11 | 1.74 |
| llm_temporal+inject_all | 26/27 | 1/15 | 6/11 | 1.33 |
| llm_temporal+lexical | 4/27 | 0/15 | 0/11 | 0.13 |
| llm_temporal+embedding | 14/27 | 0/15 | 1/11 | 0.51 |
| llm_temporal+router | 24/27 | 1/15 | 0/11 | 0.74 |
| llm_temporal+router_global | 26/27 | 1/15 | 6/11 | 1.31 |
| oracle_temporal+embedding | 14/27 | 0/15 | 1/11 | 0.56 |
| oracle_temporal+router | 23/26 | 0/15 | 0/11 | 0.79 |

## 回答（LLM 评审，需人工抽检 human_review.csv）

| 配置 | 通过 | 分维度 |
|---|---:|---|
| 不用记忆 | 17/39 | {'accuracy': '2/7', 'application': '1/11', 'forget_privacy': '4/4', 'freshness': '3/15', 'relevance': '12/26'} |
| 全量注入（只追加） | 21/39 | {'accuracy': '4/7', 'application': '7/11', 'forget_privacy': '1/4', 'freshness': '3/15', 'relevance': '16/26'} |
| 时间感知 + 路由 | 36/39 | {'accuracy': '7/7', 'application': '9/11', 'forget_privacy': '4/4', 'freshness': '12/15', 'relevance': '25/26'} |
| 时间感知 + 全局常驻 + 路由 | 32/39 | {'accuracy': '6/7', 'application': '9/11', 'forget_privacy': '3/4', 'freshness': '11/15', 'relevance': '21/26'} |
