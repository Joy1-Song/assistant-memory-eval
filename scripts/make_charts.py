"""Draw the README charts from saved results (no model requests).

python scripts/make_charts.py  ->  docs/images/*.png
Reads outputs/offline_report.json and outputs/live_r2/report.json.
"""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parents[1]
IMG = ROOT / "docs/images"
IMG.mkdir(parents=True, exist_ok=True)

for path in ("C:/Windows/Fonts/msyh.ttc", "/System/Library/Fonts/PingFang.ttc",
             "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"):
    if Path(path).exists():
        font_manager.fontManager.addfont(path)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=path).get_name()
        break

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
ACCENT, MUTED = "#2a78d6", "#b9b8b1"
plt.rcParams.update({"figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "axes.edgecolor": GRID,
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2, "font.size": 10})


def frac(s):
    a, b = s.split("/")
    return int(a), int(b)


def tidy(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=0)


def hbar(labels, values, total, highlight, title, note, name):
    fig, ax = plt.subplots(figsize=(6.4, 0.55 * len(labels) + 1.3), dpi=200)
    colors = [ACCENT if i == highlight else MUTED for i in range(len(labels))]
    y = range(len(labels))
    ax.barh(y, values, height=0.5, color=colors)
    for i, v in enumerate(values):
        ax.text(v + total * 0.015, i, f"{v}/{total}", va="center", color=INK, fontsize=10)
    ax.set_yticks(list(y), labels, color=INK)
    ax.set_xlim(0, total * 1.12)
    ax.invert_yaxis()
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_visible(False)
    tidy(ax)
    ax.set_title(title, loc="left", color=INK, fontsize=11.5, pad=10)
    fig.text(0.01, 0.01, note, color=INK2, fontsize=8)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(IMG / name)
    plt.close(fig)


offline = json.loads((ROOT / "outputs/offline_report.json").read_text(encoding="utf-8"))
leak = [frac(offline[f"{p}+inject_all"]["overall"]["bad_memory_leaked"]) for p in ("append_only", "overwrite", "temporal")]
hbar(["只追加", "同槽覆盖", "时效化（生效日 / 到期日 / 删除）"], [a for a, _ in leak], leak[0][1], 2,
     "过期、已纠正或已删除的记忆进入上下文的探针数",
     "离线实验：用标注答案代替模型抽取，召回为全量注入。数据：outputs/offline_report.json", "update_strategy.png")

live = json.loads((ROOT / "outputs/live_r2/report.json").read_text(encoding="utf-8"))
ans = live["answers"]
order = [("none", "不用记忆"), ("naive", "只追加 + 全量注入"), ("best_global", "时效化 + 全局常驻 + 路由"), ("best", "时效化 + LLM 路由")]
passed = [frac(ans[k]["passed"]) for k, _ in order]
hbar([label for _, label in order], [a for a, _ in passed], passed[0][1], 3,
     "回答通过的探针数",
     "第二轮联网，qwen-plus；评审为 LLM（judge-v2），人工抽检前不是最终数字。数据：outputs/live_r2/report.json",
     "answer_pass.png")

ctx = live["contexts"]
points = [("llm_temporal+inject_all", "全量注入"), ("llm_temporal+lexical", "字面匹配"),
          ("llm_temporal+embedding", "Embedding top-3"), ("llm_temporal+router_global", "全局常驻 + 路由"),
          ("llm_temporal+router", "LLM 路由")]
groups = {}  # strategies that land on the same point share one label
for key, label in points:
    r = frac(ctx[key]["overall"]["all_need_recalled"])
    n = frac(ctx[key]["overall"]["noise_on_no_memory_probes"])
    groups.setdefault((r, n), []).append((key, label))
fig, ax = plt.subplots(figsize=(6.4, 4.2), dpi=200)
for ((r_a, r_b), (n_a, n_b)), members in groups.items():
    x, y = 100 * r_a / r_b, 100 * n_a / n_b
    chosen = any(k.endswith("+router") for k, _ in members)
    name = " / ".join(label for _, label in members) + ("（两者重合）" if len(members) > 1 else "")
    ax.scatter([x], [y], s=70 if chosen else 50, color=ACCENT if chosen else MUTED,
               edgecolors=SURFACE, linewidths=2, zorder=3)
    dx, ha = (-3, "right") if x > 80 else (3, "left")
    ax.annotate(f"{name}\n召回 {r_a}/{r_b} · 无关注入 {n_a}/{n_b}", (x, y), xytext=(dx, 6), textcoords="offset points",
                ha=ha, va="bottom", fontsize=8.5, color=INK if chosen else INK2)
ax.set_xlim(0, 105)
ax.set_ylim(-5, 75)
ax.set_xlabel("需要的记忆全部召回（%），越高越好")
ax.set_ylabel("无关问题被注入记忆（%），越低越好")
ax.grid(True, color=GRID, linewidth=0.8)
ax.set_axisbelow(True)
tidy(ax)
ax.set_title("召回策略：召回要全，无关问题不该被塞进记忆", loc="left", color=INK, fontsize=11.5, pad=10)
fig.text(0.01, 0.01, "第二轮联网，基于模型写入的时效化记忆库。数据：outputs/live_r2/report.json", color=INK2, fontsize=8)
fig.tight_layout(rect=(0, 0.04, 1, 1))
fig.savefig(IMG / "recall_tradeoff.png")
plt.close(fig)
print("charts ->", IMG)
