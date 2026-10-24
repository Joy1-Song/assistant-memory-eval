# 第一轮回答重新评审（judge-v2）

回答不变，只换评审：v2 能看到回答时注入了哪些记忆，并说明了旧信息被什么替换。

| 配置 | judge-v1 通过 | judge-v2 通过 | v1/v2 判定一致 |
|---|---:|---:|---:|
| 不用记忆 | 17/39 | 15/39 | 33/39 |
| 全量注入（只追加） | 16/39 | 23/39 | 32/39 |
| 时间感知 + 路由 | 30/39 | 33/39 | 32/39 |

## judge-v2 分维度

- 不用记忆：{'accuracy': '2/7', 'application': '1/11', 'forget_privacy': '4/4', 'freshness': '2/15', 'relevance': '10/26'}
- 全量注入（只追加）：{'accuracy': '4/7', 'application': '8/11', 'forget_privacy': '1/4', 'freshness': '4/15', 'relevance': '17/26'}
- 时间感知 + 路由：{'accuracy': '7/7', 'application': '7/11', 'forget_privacy': '4/4', 'freshness': '11/15', 'relevance': '22/26'}

仍需人工抽检：human_review_v2.csv 的最后两列。
