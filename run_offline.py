"""Offline experiment: oracle extraction (gold ops) x 3 update policies x 2 recall strategies.

Isolates the memory *policy* and *recall* from extraction quality. No model, no API key.
Usage:  python run_offline.py      -> outputs/offline_report.md and offline_report.json
"""

import json
from pathlib import Path

from memlab.metrics import aggregate, by_dimension, map_items, score_probe
from memlab.recall import STRATEGIES
from memlab.store import POLICIES, MemoryStore

HERE = Path(__file__).resolve().parent
BENCH = HERE / "data/memory_benchmark.json"
OUT = HERE / "outputs"


def oracle_store(scenario: dict, policy: str, until: str) -> MemoryStore:
    store = MemoryStore(policy)
    for session in scenario["sessions"]:
        if session["at"] >= until:
            break
        for op in session["gold"]:
            item = store.apply(op, session["at"], source=session["at"])
            if item:
                item.extra["oracle"] = True
    return store


def run(bench: dict) -> dict:
    results = {}
    for policy in POLICIES:
        for name, recall in STRATEGIES.items():
            rows = []
            for sc in bench["scenarios"]:
                for probe in sc["probes"]:
                    store = oracle_store(sc, policy, probe["at"])
                    active = store.active(probe["at"])
                    injected = recall(probe["question"], active)
                    row = score_probe(probe, injected, map_items(injected, sc, probe["at"]))
                    row.update(id=probe["id"], dimensions=probe["dimensions"], avoid_total=len(probe["avoid"]),
                               injected_text=[m.text for m in injected])
                    rows.append(row)
            results[f"{policy}+{name}"] = {"overall": aggregate(rows), "by_dimension": by_dimension(rows), "rows": rows}
    return results


def markdown(results: dict) -> str:
    lines = ["# 记忆策略离线对比（oracle 抽取）", "",
             "抽取用标注答案代替模型，只比较“怎么更新”和“召回哪些”。上下文层面指标，不含回答质量。", "",
             "| 更新策略 + 召回 | 需要的记忆全部召回 | 过期/错误/已删记忆进入上下文 | 无关问题被注入记忆 | 平均注入条数 | 平均无关条数 |",
             "|---|---:|---:|---:|---:|---:|"]
    for key, r in results.items():
        o = r["overall"]
        lines.append(f"| {key} | {o['all_need_recalled']} | {o['bad_memory_leaked']} | {o['noise_on_no_memory_probes']} | "
                     f"{o['avg_injected']} | {o['avg_noise']} |")
    lines += ["", "## 按维度（需要的全部召回 / 坏记忆泄漏 / 无关注入）", ""]
    dims = sorted({d for r in results.values() for d in r["by_dimension"]})
    lines += ["| 策略 | " + " | ".join(dims) + " |", "|---|" + "---|" * len(dims)]
    for key, r in results.items():
        cells = []
        for d in dims:
            x = r["by_dimension"].get(d)
            cells.append(f"{x['all_need_recalled']} / {x['bad_memory_leaked']} / {x['noise_on_no_memory_probes']}" if x else "-")
        lines.append(f"| {key} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main():
    bench = json.loads(BENCH.read_text(encoding="utf-8"))
    results = run(bench)
    OUT.mkdir(exist_ok=True)
    (OUT / "offline_report.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "offline_report.md").write_text(markdown(results), encoding="utf-8")
    print(markdown(results))


if __name__ == "__main__":
    main()
