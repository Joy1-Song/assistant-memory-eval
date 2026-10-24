# 记忆实验（联网）

qwen-plus, temperature 0, single run, self-authored scenarios; judge is an LLM and must be spot-checked.
版本：{'extract': 'extract-v1', 'judge': 'judge-v1'}

## 抽取（LLM 记忆管理器）

- 应写入的记忆命中：42/42
- 时间字段（生效日/到期日）正确：4/4
- 删除请求执行：1/1
- 多写的条目（重复或不该记）：1
- 敏感/一次性信息入库：0 []
- 解析异常：0，调用失败：0

## 召回（上下文层面）

| 记忆来源+召回 | 需要的全部召回 | 坏记忆泄漏 | 无关问题被注入 | 平均注入 |
|---|---:|---:|---:|---:|
| llm_append+inject_all | 27/27 | 15/15 | 9/11 | 1.9 |
| llm_temporal+inject_all | 24/27 | 1/15 | 6/11 | 1.21 |
| llm_temporal+lexical | 4/27 | 0/15 | 0/11 | 0.13 |
| llm_temporal+embedding | 14/27 | 1/15 | 1/11 | 0.49 |
| llm_temporal+router | 21/27 | 1/15 | 0/11 | 0.62 |
| llm_temporal+router_global | 21/27 | 1/15 | 0/11 | 0.62 |
| oracle_temporal+embedding | 14/27 | 0/15 | 1/11 | 0.56 |
| oracle_temporal+router | 24/27 | 0/15 | 0/11 | 0.79 |

## 回答（LLM 评审，需人工抽检 human_review.csv）

| 配置 | 通过 | 分维度 |
|---|---:|---|
| 不用记忆 | 17/39 | {'accuracy': '1/7', 'application': '2/11', 'forget_privacy': '4/4', 'freshness': '3/15', 'relevance': '13/26'} |
| 全量注入（只追加） | 16/39 | {'accuracy': '2/7', 'application': '7/11', 'forget_privacy': '1/4', 'freshness': '1/15', 'relevance': '12/26'} |
| 时间感知 + 路由 | 30/39 | {'accuracy': '5/7', 'application': '7/11', 'forget_privacy': '4/4', 'freshness': '9/15', 'relevance': '22/26'} |
