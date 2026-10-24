"""Live experiment with qwen-plus + text-embedding-v4 (DashScope). Needs --approved-live.

Steps (each result is saved; re-running resumes and never re-sends a recorded request):
  extract  LLM memory manager processes every session -> ops; applied to a temporal and an append-only store
  embed    embeddings for memory texts and probe questions (cached)
  route    LLM router picks relevant memories (on the LLM-built store and on the oracle store)
  answer   3 configs per probe: none (no memory) / naive (append-only + inject all) / best (temporal + router)
  judge    LLM judge with the probe's rubric; export a CSV for human spot checks

Budget ceilings: 480 generation requests, 400 embedding texts per output folder. Round 2 uses about 430 + 230.
Round 2 (extract-v2 + judge-v2 + "global memories always on" strategy):
  python run_live.py --approved-live --out outputs/live_r2
Usage:
  python run_live.py --approved-live --limit-scenarios 2   # smoke test (about 30 requests)
  python run_live.py --approved-live                       # full run
  python run_live.py --report                              # rebuild report only, no requests
"""

import argparse
import csv
import hashlib
import json
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path

from memlab import llm
from memlab.metrics import aggregate, by_dimension, map_items, score_probe
from memlab.recall import embedding, inject_all, lexical
from memlab.store import MemoryStore
from run_offline import oracle_store

HERE = Path(__file__).resolve().parent
BENCH = HERE / "data/memory_benchmark.json"
OUT = HERE / "outputs/live"
EMBED_MODEL = "text-embedding-v4"
ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1"
GEN_LIMIT, EMB_LIMIT = 480, 400
EMB_THRESHOLD = 0.35  # fixed before the run; scores are saved so it can be re-analysed


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


class Ledger:
    def __init__(self, path):
        self.path = path
        self.data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"requests": []}

    def reserve(self, key, kind, texts=0):
        rows = self.data["requests"]
        if any(r["key"] == key for r in rows):
            raise RuntimeError(f"ledger already has {key}; not replaying")
        if kind == "gen" and sum(r["kind"] == "gen" for r in rows) >= GEN_LIMIT:
            raise RuntimeError("generation budget exhausted")
        if kind == "emb" and sum(r["texts"] for r in rows) + texts > EMB_LIMIT:
            raise RuntimeError("embedding budget exhausted")
        rows.append({"key": key, "kind": kind, "texts": texts, "status": "started",
                     "at": datetime.now(timezone.utc).isoformat()})
        save(self.path, self.data)

    def finish(self, key, **values):
        next(r for r in self.data["requests"] if r["key"] == key).update(values)
        save(self.path, self.data)


class Metered:
    """Wraps the provider: every chat request is reserved in the ledger under the current key."""

    def __init__(self, provider, ledger):
        self.provider, self.ledger, self.key = provider, ledger, None
        self.chat = type("C", (), {})()
        self.chat.completions = self

    def create(self, **payload):
        self.ledger.reserve(self.key, "gen")
        start = time.perf_counter()
        try:
            response = self.provider.chat.completions.create(**payload)
            usage = getattr(response, "usage", None)
            self.ledger.finish(self.key, status="ok", ms=round((time.perf_counter() - start) * 1000),
                               prompt_tokens=getattr(usage, "prompt_tokens", None),
                               completion_tokens=getattr(usage, "completion_tokens", None))
            return response
        except Exception as error:
            self.ledger.finish(self.key, status="failed", error=type(error).__name__)
            raise


# ---------------------------------------------------------------- stores built from LLM ops
def _valid_day(value):
    try:
        return date.fromisoformat(value).isoformat() if value else None
    except ValueError:
        return None


def build_llm_stores(scenario, extracted, until=None):
    """Re-apply saved extractor ops to a temporal and an append-only store (deterministic, no requests)."""
    temporal, append = MemoryStore("temporal"), MemoryStore("append_only")
    anomalies, slot_of, n = [], {}, 0
    for i, session in enumerate(scenario["sessions"]):
        if until and session["at"] >= until:
            break
        record = extracted.get(f"{scenario['id']}#{i}")
        if not record or "ops" not in record:
            continue
        for raw in record["ops"]:
            op = dict(raw)
            kind = op.get("op")
            if kind in {"update", "forget"}:
                target = op.get("target_id")
                if target not in slot_of:
                    anomalies.append(f"{scenario['id']}#{i}: {kind} unknown target {target}")
                    if kind == "forget":
                        continue
                    kind = "add"
                else:
                    op["slot"] = slot_of[target]
            if kind == "add":
                n += 1
                op["slot"] = f"x{n}"
            if kind not in {"add", "update", "forget"}:
                anomalies.append(f"{scenario['id']}#{i}: unknown op {kind}")
                continue
            if kind != "forget" and not op.get("value"):
                anomalies.append(f"{scenario['id']}#{i}: empty value")
                continue
            for field in ("valid_from", "expires_at"):
                if op.get(field) and not _valid_day(op[field]):
                    anomalies.append(f"{scenario['id']}#{i}: bad {field} {op[field]}")
                    op[field] = None
            op["op"] = kind
            op.setdefault("label", op.get("label") or "记忆")
            t_item = temporal.apply(op, session["at"], source=f"{scenario['id']}#{i}")
            append.apply(op, session["at"], source=f"{scenario['id']}#{i}")
            if t_item:
                slot_of[t_item.id] = t_item.slot
    return temporal, append, anomalies


def score_extraction(scenario, temporal):
    rows = []
    for i, session in enumerate(scenario["sessions"]):
        created = [m for m in temporal.items if m.source == f"{scenario['id']}#{i}"]
        mapping = map_items(created, scenario, session["at"], inclusive=True)
        gold_pairs = {(op["slot"], op["value"]): op for op in session["gold"] if op["op"] in {"add", "update"}}
        hits, time_ok, time_total = 0, 0, 0
        for pair, op in gold_pairs.items():
            matched = [m for m in created if pair in mapping[m.id]]
            hits += bool(matched)
            for field in ("valid_from", "expires_at"):
                if op.get(field):
                    time_total += 1  # ±1 day: "玩一周""这一个月" have two defensible end dates
                    time_ok += any(getattr(m, field) and abs((date.fromisoformat(getattr(m, field)) -
                                                              date.fromisoformat(op[field])).days) <= 1 for m in matched)
        forgets = [op["slot"] for op in session["gold"] if op["op"] == "forget"]
        earlier = [m for m in temporal.items if m.created_at < session["at"]]
        emap = map_items(earlier, scenario, session["at"])
        forget_ok = sum(all(m.deleted_at for m in earlier if any(p[0] == slot for p in emap[m.id])) for slot in forgets)
        spurious = [m.text for m in created if not (mapping[m.id] & set(gold_pairs))]
        rows.append({"session": f"{scenario['id']}#{i}", "gold_writes": len(gold_pairs), "gold_hit": hits,
                     "time_fields": time_total, "time_ok": time_ok, "forgets": len(forgets), "forget_ok": forget_ok,
                     "spurious": spurious})
    banned = [x for s in scenario["sessions"] for x in s["must_not_store"]]
    leaks = [m.text for m in temporal.items for x in banned if x in m.text]
    return rows, leaks


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--approved-live", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--limit-scenarios", type=int)
    ap.add_argument("--steps", default="extract,embed,route,answer,judge")
    ap.add_argument("--out", default="outputs/live", help="output folder relative to the repository root")
    args = ap.parse_args()
    global OUT
    OUT = HERE / args.out
    bench = json.loads(BENCH.read_text(encoding="utf-8"))
    scenarios = bench["scenarios"][: args.limit_scenarios]
    OUT.mkdir(parents=True, exist_ok=True)
    state_path = OUT / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {
        "benchmark_sha256": sha(BENCH), "extract": {}, "embeddings": {}, "route": {}, "answers": {}, "judges": {}}
    if state["benchmark_sha256"] != sha(BENCH):
        if not args.report:
            raise SystemExit("Benchmark changed since this run started; use a new output folder.")
        print("note: benchmark file changed since this run (scoring fixes); rebuilding report with current scoring")

    if not args.report:
        if not args.approved_live:
            raise SystemExit("Live run needs --approved-live.")
        run_steps(state, state_path, scenarios, set(args.steps.split(",")))
    report = build_report(state, scenarios)
    save(OUT / "report.json", report)
    (OUT / "report.md").write_text(report_md(report), encoding="utf-8")
    print(report_md(report))


def read_key():
    """Read the key from the environment only; nothing is loaded from files."""
    key = os.getenv("DASHSCOPE_API_KEY")
    if not key:
        raise SystemExit("No DASHSCOPE_API_KEY in the environment; no request sent.")
    return key


def provider():
    from openai import OpenAI
    key = read_key()
    return OpenAI(api_key=key, base_url=ENDPOINT, max_retries=0, timeout=30)


def run_steps(state, state_path, scenarios, steps, client_factory=provider):
    state.setdefault("versions", {"extract": llm.EXTRACT_VERSION, "judge": llm.JUDGE_VERSION})
    ledger = Ledger(OUT / "ledger.json")
    raw = client_factory()
    client = Metered(raw, ledger)

    def attempt(bucket, key, fn):
        if key in state[bucket]:
            return
        client.key = f"{bucket}:{key}"
        try:
            state[bucket][key] = fn()
        except RuntimeError as error:
            if "ledger" in str(error) or "budget" in str(error):
                save(state_path, state)
                raise SystemExit(str(error))
            state[bucket][key] = {"error": f"{type(error).__name__}: {error}"[:300]}
        except Exception as error:
            state[bucket][key] = {"error": f"{type(error).__name__}: {error}"[:300]}
        save(state_path, state)

    if "extract" in steps:
        for sc in scenarios:
            for i, session in enumerate(sc["sessions"]):
                temporal, _, _ = build_llm_stores(sc, state["extract"], until=session["at"])
                attempt("extract", f"{sc['id']}#{i}", lambda: llm.extract(
                    client, session["user"], session["at"], temporal.current()).model_dump(mode="json"))
                print("extract", sc["id"], i, flush=True)

    def probe_sets(sc, probe):
        temporal, append, _ = build_llm_stores(sc, state["extract"], until=probe["at"])
        oracle = oracle_store(sc, "temporal", probe["at"])
        return temporal.active(probe["at"]), append.active(probe["at"]), oracle.active(probe["at"])

    if "embed" in steps:
        texts = []
        for sc in scenarios:
            for probe in sc["probes"]:
                llm_active, _, oracle_active = probe_sets(sc, probe)
                texts += [probe["question"]] + [m.text for m in llm_active + oracle_active]
        missing = [t for t in dict.fromkeys(texts) if t not in state["embeddings"]]
        for start in range(0, len(missing), 10):
            batch = missing[start:start + 10]
            key = "emb:" + hashlib.sha256(json.dumps(batch, ensure_ascii=False).encode()).hexdigest()[:16]
            ledger.reserve(key, "emb", texts=len(batch))
            try:
                response = raw.embeddings.create(model=EMBED_MODEL, input=batch, dimensions=1024, encoding_format="float")
                for text, row in zip(batch, sorted(response.data, key=lambda r: r.index)):
                    state["embeddings"][text] = row.embedding
                ledger.finish(key, status="ok")
            except Exception as error:
                ledger.finish(key, status="failed", error=type(error).__name__)
                raise
            save(state_path, state)

    for sc in scenarios:
        for probe in sc["probes"]:
            llm_active, append_active, oracle_active = probe_sets(sc, probe)
            pid = probe["id"]
            if "route" in steps:
                for name, items in (("llm", llm_active), ("oracle", oracle_active)):
                    if items:
                        attempt("route", f"{name}:{pid}",
                                lambda: llm.route(client, probe["question"], probe["at"], items).model_dump())
                    else:
                        state["route"].setdefault(f"{name}:{pid}", {"ids": []})
            if "answer" in steps:
                routed = state["route"].get(f"llm:{pid}", {})
                best = [m for m in llm_active if m.id in set(routed.get("ids", []))] if "ids" in routed else None
                configs = {"none": [], "naive": inject_all(probe["question"], append_active)}
                if best is not None:
                    configs["best"] = best
                    always = [m for m in llm_active if m.scope == "global"]
                    configs["best_global"] = always + [m for m in best if m not in always]
                for cfg, items in configs.items():
                    attempt("answers", f"{cfg}:{pid}", lambda: {
                        "text": llm.answer(client, probe["question"], probe["at"], items),
                        "memories": [m.text for m in items]})
            if "judge" in steps:
                need, avoid = judge_values(sc, probe)
                for cfg in ANSWER_CONFIGS:
                    reply = state["answers"].get(f"{cfg}:{pid}", {})
                    if "text" in reply:
                        attempt("judges", f"{cfg}:{pid}", lambda: llm.judge(
                            client, probe, reply["text"], need, avoid, reply["memories"]).model_dump())
            print("probe", pid, flush=True)


ANSWER_CONFIGS = ("none", "naive", "best", "best_global")


def judge_values(sc, probe):
    """Needed values, and outdated/forbidden values annotated with what replaced them (judge-v2)."""
    banned = [x for s in sc["sessions"] if s["at"] < probe["at"] for x in s["must_not_store"]]
    current = {slot: value for slot, value in probe["need"]}
    outdated = []
    for slot, value in probe["avoid"]:
        if slot in current:
            outdated.append(f"{value}（已更新为：{current[slot]}）")
        else:
            outdated.append(f"{value}（已过期或已被用户删除，不能再用）")
    return [v for _, v in probe["need"]], outdated + [f"{x}（敏感或一次性信息，不应出现）" for x in banned]


def build_report(state, scenarios):
    extraction, leaks, anomalies = [], [], []
    for sc in scenarios:
        temporal, _, bad = build_llm_stores(sc, state["extract"])
        rows, leak = score_extraction(sc, temporal)
        extraction += rows
        leaks += leak
        anomalies += bad
    ex = {
        "sessions": len(extraction),
        "gold_writes_hit": f"{sum(r['gold_hit'] for r in extraction)}/{sum(r['gold_writes'] for r in extraction)}",
        "time_fields_ok": f"{sum(r['time_ok'] for r in extraction)}/{sum(r['time_fields'] for r in extraction)}",
        "forgets_ok": f"{sum(r['forget_ok'] for r in extraction)}/{sum(r['forgets'] for r in extraction)}",
        "spurious_items": sum(len(r["spurious"]) for r in extraction),
        "must_not_store_leaks": leaks, "anomalies": anomalies,
        "extract_errors": [k for k, v in state["extract"].items() if "error" in v],
    }
    contexts = {}
    for name in ("llm_append+inject_all", "llm_temporal+inject_all", "llm_temporal+lexical", "llm_temporal+embedding",
                 "llm_temporal+router", "llm_temporal+router_global", "oracle_temporal+embedding",
                 "oracle_temporal+router"):
        rows = []
        for sc in scenarios:
            for probe in sc["probes"]:
                temporal, append, _ = build_llm_stores(sc, state["extract"], until=probe["at"])
                source = {"llm_append": append, "llm_temporal": temporal}.get(name.split("+")[0])
                items = source.active(probe["at"]) if source else oracle_store(sc, "temporal", probe["at"]).active(probe["at"])
                how = name.split("+")[1]
                if how == "inject_all":
                    chosen = items
                elif how == "lexical":
                    chosen = lexical(probe["question"], items)
                elif how == "embedding":
                    if probe["question"] not in state["embeddings"] or any(m.text not in state["embeddings"] for m in items):
                        continue
                    chosen = embedding(probe["question"], items, state["embeddings"], threshold=EMB_THRESHOLD)
                else:
                    routed = state["route"].get(f"{name.split('_')[0]}:{probe['id']}")
                    if not routed or "ids" not in routed:
                        continue
                    chosen = [m for m in items if m.id in set(routed["ids"])]
                    if how == "router_global":
                        chosen = [m for m in items if m.scope == "global"] + [m for m in chosen if m.scope != "global"]
                row = score_probe(probe, chosen, map_items(chosen, sc, probe["at"]))
                row.update(id=probe["id"], dimensions=probe["dimensions"], avoid_total=len(probe["avoid"]))
                rows.append(row)
        if rows:
            contexts[name] = {"overall": aggregate(rows), "by_dimension": by_dimension(rows)}
    answers = {}
    for cfg in ANSWER_CONFIGS:
        rows = []
        for sc in scenarios:
            for probe in sc["probes"]:
                j = state["judges"].get(f"{cfg}:{probe['id']}")
                if j and "error" not in j:
                    rows.append((probe, llm.JudgeResult.model_validate(j).passed))
        if rows:
            dims = {}
            for probe, ok in rows:
                for d in probe["dimensions"]:
                    a, n = dims.get(d, (0, 0))
                    dims[d] = (a + ok, n + 1)
            answers[cfg] = {"passed": f"{sum(ok for _, ok in rows)}/{len(rows)}",
                            "by_dimension": {d: f"{a}/{n}" for d, (a, n) in sorted(dims.items())}}
    export_review(state, scenarios)
    return {"extraction": ex, "contexts": contexts, "answers": answers,
            "versions": state.get("versions", {"extract": "extract-v1", "judge": "judge-v1"}),
            "scope": "qwen-plus, temperature 0, single run, self-authored scenarios; judge is an LLM and must be spot-checked."}


def export_review(state, scenarios):
    with (OUT / "human_review.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["probe", "config", "question", "memories_in_context", "answer", "judge_pass", "judge_reason",
                    "human_pass(填)", "human_note(填)"])
        for sc in scenarios:
            for probe in sc["probes"]:
                for cfg in ANSWER_CONFIGS:
                    a = state["answers"].get(f"{cfg}:{probe['id']}", {})
                    j = state["judges"].get(f"{cfg}:{probe['id']}", {})
                    if "text" not in a:
                        continue
                    passed = llm.JudgeResult.model_validate(j).passed if j and "error" not in j else ""
                    w.writerow([probe["id"], cfg, probe["question"], " | ".join(a["memories"]), a["text"], passed,
                                j.get("reason", ""), "", ""])


def report_md(r):
    e = r["extraction"]
    lines = ["# 记忆实验（联网）", "", r["scope"], f"版本：{r.get('versions')}", "", "## 抽取（LLM 记忆管理器）", "",
             f"- 应写入的记忆命中：{e['gold_writes_hit']}",
             f"- 时间字段（生效日/到期日）正确：{e['time_fields_ok']}",
             f"- 删除请求执行：{e['forgets_ok']}",
             f"- 多写的条目（重复或不该记）：{e['spurious_items']}",
             f"- 敏感/一次性信息入库：{len(e['must_not_store_leaks'])} {e['must_not_store_leaks']}",
             f"- 解析异常：{len(e['anomalies'])}，调用失败：{len(e['extract_errors'])}", "",
             "## 召回（上下文层面）", "",
             "| 记忆来源+召回 | 需要的全部召回 | 坏记忆泄漏 | 无关问题被注入 | 平均注入 |", "|---|---:|---:|---:|---:|"]
    for k, v in r["contexts"].items():
        o = v["overall"]
        lines.append(f"| {k} | {o['all_need_recalled']} | {o['bad_memory_leaked']} | {o['noise_on_no_memory_probes']} | {o['avg_injected']} |")
    lines += ["", "## 回答（LLM 评审，需人工抽检 human_review.csv）", "", "| 配置 | 通过 | 分维度 |", "|---|---:|---|"]
    names = {"none": "不用记忆", "naive": "全量注入（只追加）", "best": "时间感知 + 路由",
             "best_global": "时间感知 + 全局常驻 + 路由"}
    for k, v in r["answers"].items():
        lines.append(f"| {names[k]} | {v['passed']} | {v['by_dimension']} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
