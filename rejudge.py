"""Re-judge round-1 answers with judge-v2 (the judge now sees which memories were in context).

Round-1 answers are NOT regenerated; only the evaluator changes, the same way for every config.
Writes outputs/live/rejudge_v2.json, rejudge_v2.md and human_review_v2.csv (v1 and v2 verdicts side by side).
Usage:  python rejudge.py --approved-live      (about 117 requests, ceiling 130)
        python rejudge.py --report
"""

import argparse
import csv
import json
from pathlib import Path

import run_live
from memlab import llm

HERE = Path(__file__).resolve().parent
ROUND1 = HERE / "outputs/live"
CONFIGS = ("none", "naive", "best")
LIMIT = 130


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--approved-live", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    bench = json.loads((HERE / "data/memory_benchmark.json").read_text(encoding="utf-8"))
    state = json.loads((ROUND1 / "state.json").read_text(encoding="utf-8"))
    out_path = ROUND1 / "rejudge_v2.json"
    out = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {"judge": llm.JUDGE_VERSION, "judges": {}}

    if not args.report:
        if not args.approved_live:
            raise SystemExit("Needs --approved-live.")
        run_live.OUT = ROUND1
        ledger = run_live.Ledger(ROUND1 / "ledger_rejudge.json")
        ledger_limit_check = lambda: sum(1 for r in ledger.data["requests"]) < LIMIT  # noqa: E731
        client = run_live.Metered(run_live.provider(), ledger)
        for sc in bench["scenarios"]:
            for probe in sc["probes"]:
                need, avoid = run_live.judge_values(sc, probe)
                for cfg in CONFIGS:
                    key = f"{cfg}:{probe['id']}"
                    answer = state["answers"].get(key, {})
                    if "text" not in answer or key in out["judges"]:
                        continue
                    if not ledger_limit_check():
                        raise SystemExit("rejudge budget exhausted")
                    client.key = f"rejudge:{key}"
                    try:
                        out["judges"][key] = llm.judge(client, probe, answer["text"], need, avoid,
                                                       answer["memories"]).model_dump()
                    except RuntimeError as error:
                        raise SystemExit(str(error))
                    except Exception as error:
                        out["judges"][key] = {"error": f"{type(error).__name__}: {error}"[:300]}
                    run_live.save(out_path, out)
                print("rejudged", probe["id"], flush=True)

    lines = ["# 第一轮回答重新评审（judge-v2）", "",
             "回答不变，只换评审：v2 能看到回答时注入了哪些记忆，并说明了旧信息被什么替换。", "",
             "| 配置 | judge-v1 通过 | judge-v2 通过 | v1/v2 判定一致 |", "|---|---:|---:|---:|"]
    names = {"none": "不用记忆", "naive": "全量注入（只追加）", "best": "时间感知 + 路由"}
    rows = []
    for cfg in CONFIGS:
        v1 = v2 = agree = n = 0
        for sc in bench["scenarios"]:
            for probe in sc["probes"]:
                key = f"{cfg}:{probe['id']}"
                j1, j2 = state["judges"].get(key), out["judges"].get(key)
                if not j1 or not j2 or "error" in j1 or "error" in j2:
                    continue
                p1, p2 = llm.JudgeResult.model_validate(j1).passed, llm.JudgeResult.model_validate(j2).passed
                n += 1
                v1 += p1
                v2 += p2
                agree += p1 == p2
                a = state["answers"][key]
                rows.append([probe["id"], cfg, probe["question"], " | ".join(a["memories"]), a["text"], p1, p2,
                             j2.get("reason", ""), "", ""])
        lines.append(f"| {names[cfg]} | {v1}/{n} | {v2}/{n} | {agree}/{n} |")
    by_dim = {}
    for cfg in CONFIGS:
        dims = {}
        for sc in bench["scenarios"]:
            for probe in sc["probes"]:
                j2 = out["judges"].get(f"{cfg}:{probe['id']}")
                if j2 and "error" not in j2:
                    ok = llm.JudgeResult.model_validate(j2).passed
                    for d in probe["dimensions"]:
                        a, b = dims.get(d, (0, 0))
                        dims[d] = (a + ok, b + 1)
        by_dim[cfg] = {d: f"{a}/{b}" for d, (a, b) in sorted(dims.items())}
    lines += ["", "## judge-v2 分维度", ""] + [f"- {names[c]}：{by_dim[c]}" for c in CONFIGS]
    lines += ["", "仍需人工抽检：human_review_v2.csv 的最后两列。"]
    (ROUND1 / "rejudge_v2.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with (ROUND1 / "human_review_v2.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["probe", "config", "question", "memories_in_context", "answer", "judge_v1_pass", "judge_v2_pass",
                    "judge_v2_reason", "human_pass(填)", "human_note(填)"])
        w.writerows(rows)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
