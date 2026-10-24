"""Offline tests: store policies, recall, gold mapping, LLM-op replay and the live pipeline with a fake provider."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as Obj

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import run_live  # noqa: E402
from memlab.metrics import map_items, score_probe  # noqa: E402
from memlab.recall import lexical  # noqa: E402
from memlab.store import MemoryStore  # noqa: E402

BENCH = json.loads((ROOT / "data/memory_benchmark.json").read_text(encoding="utf-8"))


def scenario(sid):
    return next(s for s in BENCH["scenarios"] if s["id"] == sid)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.ops = [({"op": "add", "slot": "city", "label": "工作城市", "value": "杭州"}, "2026-09-01T10:00"),
                    ({"op": "update", "slot": "city", "label": "工作城市", "value": "上海", "valid_from": "2026-10-01"},
                     "2026-09-20T10:00")]

    def values(self, policy, at):
        store = MemoryStore(policy)
        for op, t in self.ops:
            store.apply(op, t)
        return [m.value for m in store.active(at)]

    def test_future_update_waits_until_valid_from(self):
        self.assertEqual(self.values("temporal", "2026-09-25T10:00"), ["杭州"])
        self.assertEqual(self.values("temporal", "2026-10-01T08:00"), ["上海"])
        self.assertEqual(self.values("overwrite", "2026-09-25T10:00"), ["上海"])  # switches too early
        self.assertEqual(self.values("append_only", "2026-10-05T10:00"), ["杭州", "上海"])  # keeps both

    def test_expiry_and_forget(self):
        store = MemoryStore("temporal")
        store.apply({"op": "add", "slot": "trip", "value": "北京出差", "expires_at": "2026-09-11"}, "2026-09-07T08:00")
        store.apply({"op": "add", "slot": "bday", "value": "3月14日"}, "2026-09-02T08:00")
        store.apply({"op": "forget", "slot": "bday"}, "2026-09-10T08:00")
        self.assertEqual([m.value for m in store.active("2026-09-11T20:00")], ["北京出差"])  # last day still valid
        self.assertEqual(store.active("2026-09-12T08:00"), [])
        naive = MemoryStore("append_only")
        naive.apply({"op": "add", "slot": "bday", "value": "3月14日"}, "2026-09-02T08:00")
        naive.apply({"op": "forget", "slot": "bday"}, "2026-09-10T08:00")
        self.assertEqual(len(naive.active("2026-09-12T08:00")), 1)  # naive baseline cannot delete


class RecallAndMetricsTest(unittest.TestCase):
    def test_lexical_needs_surface_overlap(self):
        store = MemoryStore("temporal")
        store.apply({"op": "add", "slot": "r", "label": "阅读偏好", "value": "科幻小说"}, "2026-09-01T10:00")
        self.assertEqual(lexical("推荐几本书", store.active("2026-09-02T10:00")), [])
        self.assertEqual(len(lexical("推荐科幻小说", store.active("2026-09-02T10:00"))), 1)

    def test_free_text_maps_to_latest_gold_value(self):
        sc = scenario("M08")  # IELTS progress Part 1 -> Part 1 and 2
        store = MemoryStore("temporal")
        new = store.apply({"op": "add", "slot": "x1", "label": "进度", "value": "已练完 Part 1 和 Part 2"},
                          "2026-09-15T20:00")
        old = store.apply({"op": "add", "slot": "x2", "label": "进度", "value": "已练完 Part 1"}, "2026-09-08T20:00")
        mapping = map_items([new, old], sc, "2026-09-16T19:00")
        self.assertIn(("progress", "已练完口语Part 1和Part 2"), mapping[new.id])
        self.assertNotIn(("progress", "已练完口语Part 1"), mapping[new.id])
        self.assertEqual(mapping[old.id], {("progress", "已练完口语Part 1")})
        row = score_probe(sc["probes"][0], [new, old], mapping)
        self.assertTrue(row["leak"])


class ReplayTest(unittest.TestCase):
    def test_llm_ops_replay_with_targets_and_bad_dates(self):
        sc = scenario("M01")
        extracted = {
            "M01#0": {"ops": [{"op": "add", "label": "工作城市", "value": "杭州"},
                              {"op": "add", "label": "居住", "value": "杭州滨江区"}]},
            "M01#1": {"ops": [{"op": "update", "target_id": "m1", "label": "工作城市", "value": "上海",
                               "valid_from": "2026-10-01"},
                              {"op": "update", "target_id": "m99", "value": "幽灵"},
                              {"op": "add", "value": "x", "expires_at": "下周"}]},
        }
        temporal, append, anomalies = run_live.build_llm_stores(sc, extracted)
        self.assertEqual(sorted(m.value for m in temporal.active("2026-09-25T10:00")), ["x", "幽灵", "杭州", "杭州滨江区"])
        self.assertIn("上海", [m.value for m in temporal.active("2026-10-08T08:00")])
        self.assertEqual(len(anomalies), 2)  # unknown target + bad date
        rows, leaks = run_live.score_extraction(sc, temporal)
        self.assertEqual(rows[1]["gold_hit"], 1)
        self.assertEqual(rows[1]["time_ok"], 1)
        self.assertEqual(leaks, [])


class JudgeValuesTest(unittest.TestCase):
    def test_outdated_values_say_what_replaced_them(self):
        sc = scenario("M17")
        need, avoid = run_live.judge_values(sc, sc["probes"][0])
        self.assertEqual(need, ["科幻小说、历史类"])
        self.assertIn("已更新为：科幻小说、历史类", avoid[0])
        sc = scenario("M06")
        _, avoid = run_live.judge_values(sc, sc["probes"][1])
        self.assertTrue(any("心理医生" in x and "敏感" in x for x in avoid))


class FakeProvider:
    """Deterministic stand-in for DashScope: plausible JSON for each prompt type."""

    def __init__(self):
        self.calls = 0
        self.chat = Obj(completions=self)
        self.embeddings = Obj(create=self.embed)

    def create(self, **p):
        self.calls += 1
        system, user = p["messages"][0]["content"], p["messages"][-1]["content"]
        if "长期记忆管理器" in system:
            said = user.split("用户说：")[-1]
            content = {"ops": [{"op": "add", "label": "信息", "value": said[:30],
                                "scope": "global" if "工作" in said else "topical"}]}
        elif "选出回答" in system:
            ids = [line.split("]")[0].split("[")[1] for line in user.splitlines() if line.startswith("- [")]
            content = {"ids": ids[:1]}
        elif "评审" in system:
            content = {"uses_needed": "pass", "avoids_bad": "pass", "no_irrelevant_personal": "pass", "reason": "ok"}
        else:
            return Obj(choices=[Obj(finish_reason="stop", message=Obj(content="好的，这是回答。"))], usage=None)
        return Obj(choices=[Obj(finish_reason="stop", message=Obj(content=json.dumps(content, ensure_ascii=False)))],
                   usage=Obj(prompt_tokens=50, completion_tokens=10))

    def embed(self, model, input, **_):
        return Obj(data=[Obj(index=i, embedding=[float(len(t) % 7 + 1), 1.0, float(i)]) for i, t in enumerate(input)])


class PipelineTest(unittest.TestCase):
    def test_end_to_end_with_fake_provider_and_resume(self):
        tmp = Path(tempfile.mkdtemp())
        old = run_live.OUT
        run_live.OUT = tmp
        try:
            scenarios = BENCH["scenarios"][:2]
            state = {"benchmark_sha256": "x", "extract": {}, "embeddings": {}, "route": {}, "answers": {}, "judges": {}}
            fake = FakeProvider()
            steps = {"extract", "embed", "route", "answer", "judge"}
            run_live.run_steps(state, tmp / "state.json", scenarios, steps, client_factory=lambda: fake)
            first = fake.calls
            self.assertGreater(first, 10)
            run_live.run_steps(state, tmp / "state.json", scenarios, steps, client_factory=lambda: fake)
            self.assertEqual(fake.calls, first)  # resume sends nothing new
            report = run_live.build_report(state, scenarios)
            self.assertIn("best", report["answers"])
            self.assertIn("best_global", report["answers"])
            globals_ = [k for k, v in state["answers"].items() if k.startswith("best_global:") and v.get("memories")]
            self.assertTrue(globals_)  # global memories are injected even when the router picks nothing
            self.assertTrue((tmp / "human_review.csv").exists())
            ledger = json.loads((tmp / "ledger.json").read_text(encoding="utf-8"))
            self.assertEqual(len({r["key"] for r in ledger["requests"]}), len(ledger["requests"]))
        finally:
            run_live.OUT = old
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
