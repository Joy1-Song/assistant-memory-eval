"""Context-level scoring: did the right memories (and only those) reach the prompt?

Items are mapped to gold (slot, value) pairs. Oracle items carry their slot; items written by an LLM
extractor are mapped by keywords (see map_items). Answer quality is judged separately (live only).
"""

from collections import defaultdict

from memlab.store import MemoryItem


def gold_history(scenario: dict, before: str, inclusive: bool = False) -> list[dict]:
    """Gold add/update ops that happened before (or at, if inclusive) a time, oldest first."""
    return [op for s in scenario["sessions"] if (s["at"] <= before if inclusive else s["at"] < before)
            for op in s["gold"] if op["op"] in {"add", "update"}]


def map_items(items: list[MemoryItem], scenario: dict, at: str, inclusive: bool = False) -> dict[str, set[tuple]]:
    """item id -> set of gold (slot, value) it represents.

    Oracle items: their own (slot, value). Free-text items: per slot, the LATEST gold op whose match
    keywords occur in the item text (so "Part 1 和 Part 2" counts as the newer value, not the old one).
    """
    history = gold_history(scenario, at, inclusive)
    out = {}
    for m in items:
        if m.extra.get("oracle"):
            out[m.id] = {(m.slot, m.value)}
            continue
        per_slot = {}
        for op in history:
            if any(k.lower() in m.text.lower() for k in op["match"]):
                per_slot[op["slot"]] = (op["slot"], op["value"])  # later ops overwrite earlier ones
        out[m.id] = set(per_slot.values())
    return out


def score_probe(probe: dict, injected: list[MemoryItem], mapping: dict) -> dict:
    present = set().union(*(mapping.get(m.id, set()) for m in injected)) if injected else set()
    need = {tuple(x) for x in probe["need"]}
    avoid = {tuple(x) for x in probe["avoid"]}
    useful = {m.id for m in injected if mapping.get(m.id, set()) & need}
    return {
        "need_total": len(need), "need_found": len(need & present), "all_need": need <= present,
        "leak": bool(avoid & present), "injected": len(injected), "noise": len(injected) - len(useful),
        "no_memory": probe["no_memory"],
    }


def aggregate(rows: list[dict]) -> dict:
    with_need = [r for r in rows if r["need_total"]]
    with_avoid = [r for r in rows if r["avoid_total"]]
    no_mem = [r for r in rows if r["no_memory"]]
    return {
        "probes": len(rows),
        "all_need_recalled": f"{sum(r['all_need'] for r in with_need)}/{len(with_need)}",
        "bad_memory_leaked": f"{sum(r['leak'] for r in with_avoid)}/{len(with_avoid)}",
        "noise_on_no_memory_probes": f"{sum(r['injected'] > 0 for r in no_mem)}/{len(no_mem)}",
        "avg_injected": round(sum(r["injected"] for r in rows) / len(rows), 2) if rows else None,
        "avg_noise": round(sum(r["noise"] for r in rows) / len(rows), 2) if rows else None,
    }


def by_dimension(rows: list[dict]) -> dict:
    groups = defaultdict(list)
    for r in rows:
        for d in r["dimensions"]:
            groups[d].append(r)
    return {d: aggregate(g) for d, g in sorted(groups.items())}
