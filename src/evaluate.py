"""Evaluation: Claude-only vs DeepInfra Qwen vs Drex (via Intern's client) vs Hybrid vs the Intern agent.
Writes results/summary.md, summary.json and charts.

Predictions are cached in results/cache/ (keyed by system, profile, mode/model) so re-runs with real
APIs don't re-pay. Delete the cache directory to force fresh calls.
"""
from __future__ import annotations

import csv
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from sklearn.metrics import accuracy_score, f1_score, recall_score

import config
from src import guardrail
from src.baseline import score_job_claude, triage_email_claude
from src.hybrid import combine_offline
from src.jobs import extract_jobs, norm_key, score_job_drex
from src.loader import load_emails
from src.profile import load_profile
from src.triage import CATEGORIES, triage_email

ALL_SYSTEMS = ["claude", "deepinfra", "drex", "hybrid", "intern_agent"]
JOB_SYSTEMS = ["claude", "drex", "hybrid"]
LABEL = {"claude": "Claude-only", "deepinfra": "DeepInfra Qwen", "drex": "Drex (Level A)", "hybrid": "Hybrid",
         "intern_agent": "Intern agent (Level B)"}
SHORT = {"claude": "Claude", "deepinfra": "Qwen", "drex": "Drex", "hybrid": "Hybrid", "intern_agent": "Intern"}
# Fixed categorical slot per system (palette slots 1-5); bars are also labeled on the axis, never color alone.
COLORS = {"claude": "#2a78d6", "drex": "#eb6834", "hybrid": "#1baf7a", "deepinfra": "#eda100",
          "intern_agent": "#e87ba4"}
INK, INK2, MUTED, GRID, BASE, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"
THRESHOLDS = [round(x, 2) for x in np.arange(0.30, 0.96, 0.05)]


# ------------------------------------------------------------------ caching + parallel map
def _tag(clients, system: str) -> str:
    if clients.mock:
        return "mock"
    m = {"drex": clients.drex, "deepinfra": clients.deepinfra}.get(system, clients.claude)
    return m.model.replace("/", "_").replace(":", "_")


def cached(name: str, items: list, key, fn, workers: int) -> dict:
    """Run fn over items, caching each result in results/cache/<name>.jsonl by key(item)."""
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = config.CACHE_DIR / f"{name}.jsonl"
    done: dict = {}
    if path.exists():
        for line in path.read_text().splitlines():
            row = json.loads(line)
            done[row["_key"]] = row
    todo = [x for x in items if key(x) not in done]
    if todo:
        def run(x):
            try:
                return {**fn(x), "_key": key(x)}
            except Exception as ex:
                print(f"  [{name}] {key(x)} failed: {type(ex).__name__}: {str(ex)[:160]}")
                return None
        with ThreadPoolExecutor(max(1, workers)) as ex, open(path, "a") as f:
            for row in ex.map(run, todo):
                if row is not None:
                    done[row["_key"]] = row
                    f.write(json.dumps(row, default=str) + "\n")
    return {key(x): done[key(x)] for x in items if key(x) in done}


def read_labels() -> dict[str, dict]:
    if not config.LABELS_PATH.exists():
        return {}
    with open(config.LABELS_PATH) as f:
        return {r["id"]: r for r in csv.DictReader(f)}


def read_job_gold() -> dict[str, int]:
    if not config.JOB_GOLD_PATH.exists():
        return {}
    lvl = {"low": 0, "medium": 1, "high": 2}
    out = {}
    with open(config.JOB_GOLD_PATH) as f:
        for r in csv.DictReader(f):
            v = r["my_rank_or_fit"].strip().lower()
            out[norm_key(r["company"], r["job_title"])] = lvl[v] if v in lvl else int(float(v))
    return out


# ------------------------------------------------------------------ metrics
def triage_metrics(rows: dict[str, dict], labels: dict[str, dict]) -> dict:
    ids = [i for i in rows if i in labels]
    if not ids:
        return {}
    yc = [labels[i]["category"] for i in ids]
    pc = [rows[i]["category"] for i in ids]
    ya = [labels[i]["action"] for i in ids]
    pa = [rows[i]["action"] for i in ids]
    return {
        "n": len(ids),
        "category_acc": accuracy_score(yc, pc),
        "category_macro_f1": f1_score(yc, pc, average="macro", labels=CATEGORIES, zero_division=0),
        "urgent_recall": recall_score(yc, pc, labels=["urgent_action_needed"], average="macro", zero_division=0),
        "action_acc": accuracy_score(ya, pa),
    }


def latency_stats(rows: dict[str, dict]) -> dict:
    lat = np.array([r["latency_s"] for r in rows.values()]) if rows else np.array([0.0])
    return {"p50_s": float(np.percentile(lat, 50)), "p95_s": float(np.percentile(lat, 95)),
            "throughput_per_s": float(len(lat) / lat.sum()) if lat.sum() else 0.0}


def drex_cost(input_tokens: float, reported: float = 0.0) -> float:
    """API-reported cost (Intern passes usage.cost through) when present, else tokens x config price."""
    return reported or input_tokens * config.DREX_PRICE_PER_MTOK_INPUT / 1e6


def claude_cost(inp: float, out: float, model: str | None) -> float:
    p = config.claude_price(model)
    return (inp * p["input"] + out * p["output"]) / 1e6


def deepinfra_cost(inp: float, out: float, reported: float = 0.0) -> float:
    p = config.DEEPINFRA_PRICE_PER_MTOK
    return reported or (inp * p["input"] + out * p["output"]) / 1e6


def row_cost(r: dict, system: str) -> float:
    if system == "intern_agent":
        return r.get("cost_usd", 0.0)
    if system == "deepinfra":
        return deepinfra_cost(r["input_tokens"], r["output_tokens"], r.get("cost_usd", 0.0))
    if system == "drex" or (system == "hybrid" and not r.get("escalated")):
        return drex_cost(r["input_tokens"], r.get("drex_cost_usd", 0.0))
    c = claude_cost(r["input_tokens"], r["output_tokens"], r.get("model"))
    return c + (drex_cost(r.get("drex_input_tokens", 0)) if system == "hybrid" else 0.0)


def cost_per_1k(rows: dict[str, dict], system: str) -> float:
    return 1000 * float(np.mean([row_cost(r, system) for r in rows.values()])) if rows else 0.0


def ranking_metrics(scored: list[dict], gold: dict[str, int]) -> dict:
    best: dict[str, float] = {}
    for j in scored:
        k = norm_key(j["company"], j["title"])
        best[k] = max(best.get(k, -1), j["rank_score"])
    ranked = sorted(best, key=best.get, reverse=True)
    rel = [gold.get(k, 0) for k in ranked]

    def p_at(k):
        return sum(1 for r in rel[:k] if r == 2) / k

    def dcg(rs):
        return sum((2 ** r - 1) / math.log2(i + 2) for i, r in enumerate(rs))

    ideal = sorted(gold.values(), reverse=True)
    return {"n_jobs": len(ranked), "matched_gold": sum(k in gold for k in ranked), "p@5": p_at(5), "p@10": p_at(10),
            "ndcg@10": dcg(rel[:10]) / dcg(ideal[:10]) if dcg(ideal[:10]) else 0.0}


# ------------------------------------------------------------------ experiments
def run_triage_systems(emails, labels, profile_name, clients, workers) -> dict:
    prof = load_profile(profile_name)["profile_text"]
    labeled = [e for e in emails if e["id"] in labels]
    key = lambda e: e["id"]  # noqa: E731
    drex = cached(f"triage_drex_{profile_name}_{_tag(clients, 'drex')}", labeled, key,
                  lambda e: triage_email(prof, e, clients.drex), workers)
    claude = cached(f"triage_claude_{profile_name}_{_tag(clients, 'claude')}", labeled, key,
                    lambda e: triage_email_claude(prof, e, clients.claude), workers)
    out = {"drex": drex, "claude": claude}
    if clients.deepinfra is not None:
        out["deepinfra"] = cached(f"triage_deepinfra_{profile_name}_{_tag(clients, 'deepinfra')}", labeled, key,
                                  lambda e: triage_email_claude(prof, e, clients.deepinfra), workers)
    else:
        print("  [eval] DEEPINFRA_API_KEY not set: skipping the DeepInfra Qwen baseline")
    return out


def threshold_sweep(drex, claude, labels) -> list[dict]:
    out = []
    for t in THRESHOLDS:
        h = combine_offline(drex, claude, t)
        m = triage_metrics(h, labels)
        out.append({"threshold": t, "category_acc": m.get("category_acc", 0), "action_acc": m.get("action_acc", 0),
                    "urgent_recall": m.get("urgent_recall", 0), "cost_per_1k": cost_per_1k(h, "hybrid"),
                    "escalated_frac": float(np.mean([r["escalated"] for r in h.values()])),
                    "p50_s": latency_stats(h)["p50_s"]})
    return out


def run_jobs_eval(emails, profile_name, clients, workers) -> dict:
    prof = load_profile(profile_name)["profile_text"]
    alerts = [e for e in emails if e.get("is_linkedin_alert")]
    ex = cached(f"ndi_extract_{_tag(clients, 'drex')}", alerts, lambda e: e["id"],
                lambda e: dict(zip(("jobs", "meta"), extract_jobs(e, clients.ndi))), workers)
    jobs = [j for v in ex.values() for j in v["jobs"]]
    jkey = lambda j: f"{j['email_id']}|{norm_key(j['company'], j['title'])}"  # noqa: E731
    drex = cached(f"jobs_drex_{profile_name}_{_tag(clients, 'drex')}", jobs, jkey,
                  lambda j: score_job_drex(prof, j, clients.drex), workers)
    claude = cached(f"jobs_claude_{profile_name}_{_tag(clients, 'claude')}", jobs, jkey,
                    lambda j: score_job_claude(prof, j, clients.claude), workers)
    pages = sum(v["meta"]["pages"] for v in ex.values())
    return {"jobs": jobs, "drex": drex, "claude": claude, "jkey": jkey, "ndi_pages": pages,
            "ndi_latency": [v["meta"]["latency_s"] for v in ex.values()], "n_alerts": len(alerts)}


def jobs_metrics(J: dict, gold: dict, threshold: float, claude_model: str | None = None) -> dict:
    out = {}
    for sys in JOB_SYSTEMS:
        scored = []
        for j in J["jobs"]:
            k = J["jkey"](j)
            if k not in J["drex"]:
                continue
            if sys == "hybrid":
                d = J["drex"][k]
                s = J["claude"].get(k, d) if d["confidence"] < threshold else d
            else:
                s = J[sys].get(k)
            if s:
                scored.append({"company": j["company"], "title": j["title"], "rank_score": s["rank_score"]})
        out[sys] = ranking_metrics(scored, gold)
    d, c = J["drex"].values(), J["claude"].values()
    out["drex"].update(latency_stats(J["drex"]))
    out["claude"].update(latency_stats(J["claude"]))
    out["hybrid"]["escalated_frac"] = float(np.mean([x["confidence"] < threshold for x in d])) if d else 0
    out["cost_per_1k_jobs"] = {"drex": 1000 * float(np.mean([drex_cost(x["input_tokens"], x.get("drex_cost_usd", 0))
                                                             for x in d])) if d else 0,
                               "claude": 1000 * float(np.mean([claude_cost(x["input_tokens"], x["output_tokens"],
                                                                           claude_model)
                                                               for x in c])) if c else 0}
    out["ndi"] = {"alerts": J["n_alerts"], "jobs_extracted": len(J["jobs"]),
                  "p50_s": float(np.median(J["ndi_latency"])) if J["ndi_latency"] else 0,
                  "cost_usd": J["ndi_pages"] * config.NDI_EXTRACT_PER_1K_PAGES / 1000}
    return out


def personalization(emails, labels, clients, workers, profiles=("student_jobhunter", "founder", "parent")) -> dict:
    kept, _ = guardrail.filter_emails(emails, None, use_semantic=False, audit=False)
    sample = kept[:100]
    pri: dict[str, dict] = {}
    res = {}
    for p in profiles:
        prof = load_profile(p)["profile_text"]
        rows = cached(f"triage_drex_{p}_{_tag(clients, 'drex')}", sample, lambda e: e["id"],
                      lambda e, prof=prof: triage_email(prof, e, clients.drex), workers)
        pri[p] = {i: r["priority"] for i, r in rows.items()}
        top10 = sorted(rows, key=lambda i: rows[i]["priority"], reverse=True)[:10]
        cats = [r["category"] for r in rows.values()]
        res[p] = {"agreement_with_labels": triage_metrics(rows, labels).get("category_acc", 0),
                  "top10": top10,
                  "top10_job_alerts": sum(rows[i]["category"] == "job_alert" for i in top10),
                  "share_urgent_or_important": float(np.mean([c in ("urgent_action_needed", "important")
                                                              for c in cats]))}
    pairs = {}
    names = list(profiles)
    for a in range(len(names)):
        for b in range(a + 1, len(names)):
            pa, pb = pri[names[a]], pri[names[b]]
            ids = [i for i in pa if i in pb]
            ra = np.argsort(np.argsort([-pa[i] for i in ids]))
            rb = np.argsort(np.argsort([-pb[i] for i in ids]))
            rho = float(np.corrcoef(ra, rb)[0, 1]) if len(ids) > 2 else 0.0
            ta, tb = set(res[names[a]]["top10"]), set(res[names[b]]["top10"])
            pairs[f"{names[a]} vs {names[b]}"] = {"spearman_priority": rho, "top10_overlap": len(ta & tb)}
    return {"n_emails": len(sample), "per_profile": res, "pairs": pairs,
            "note": "labels.csv reflects the student_jobhunter persona; lower agreement for other personas is expected"}


def guardrail_bench(clients, workers) -> dict:
    rows = [json.loads(line) for line in config.SENSITIVE_BENCH_PATH.read_text().splitlines()]
    key = lambda r: r["id"]  # noqa: E731
    l1 = {r["id"]: bool(guardrail.layer1_rules(r)) for r in rows}
    dx = cached(f"guard_drex_{_tag(clients, 'drex')}", rows, key,
                lambda r: dict(zip(("drop", "cls", "meta"), guardrail.layer2_drex(r, clients.drex,
                                                                                   config.SENSITIVE_THRESHOLD))),
                workers)
    cl = cached(f"guard_claude_{_tag(clients, 'claude')}", rows, key,
                lambda r: dict(zip(("drop", "cls", "meta"), guardrail.layer2_claude(r, clients.claude))), workers)

    def score(pred: dict[str, bool]) -> dict:
        sens = [r for r in rows if r["sensitive"]]
        ben = [r for r in rows if not r["sensitive"]]
        missed = [r["class"] for r in sens if not pred.get(r["id"], True)]
        return {"recall": sum(pred.get(r["id"], True) for r in sens) / len(sens),
                "false_block_rate": sum(pred.get(r["id"], True) for r in ben) / len(ben),
                "missed_by_class": {c: missed.count(c) for c in set(missed)}}

    out = {
        "n_sensitive": sum(r["sensitive"] for r in rows), "n_benign": sum(not r["sensitive"] for r in rows),
        "layer1_only": score(l1),
        "drex_only": score({i: v["drop"] for i, v in dx.items()}),
        "claude_only": score({i: v["drop"] for i, v in cl.items()}),
        "layer1+drex": score({i: l1[i] or dx[i]["drop"] for i in dx}),
        "layer1+claude": score({i: l1[i] or cl[i]["drop"] for i in cl}),
    }
    dlat = np.array([v["meta"]["latency_s"] for v in dx.values()])
    clat = np.array([v["meta"]["latency_s"] for v in cl.values()])
    out["check_latency"] = {"drex_p50_s": float(np.median(dlat)), "drex_p95_s": float(np.percentile(dlat, 95)),
                            "claude_p50_s": float(np.median(clat)), "claude_p95_s": float(np.percentile(clat, 95))}
    cmodel = None if clients.mock else clients.claude.model
    out["check_cost_per_1k"] = {
        "drex": 1000 * float(np.mean([drex_cost(v["meta"]["input_tokens"], v["meta"].get("drex_cost_usd", 0))
                                      for v in dx.values()])),
        "claude": 1000 * float(np.mean([claude_cost(v["meta"]["input_tokens"], v["meta"]["output_tokens"], cmodel)
                                        for v in cl.values()]))}
    return out


# ------------------------------------------------------------------ charts
def _style(ax, title, ylabel):
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", fontsize=11, color=INK, pad=10)
    ax.set_ylabel(ylabel, color=INK2, fontsize=9)
    ax.tick_params(colors=MUTED, labelsize=8, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(BASE)


def charts(summary: dict, sweep: list[dict], out_dir: Path) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    files = []
    ts = [s["threshold"] for s in sweep]
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.6), facecolor=SURFACE)
    panels = [("category_acc", "Category accuracy", "accuracy"), ("cost_per_1k", "Cost per 1,000 emails", "USD"),
              ("escalated_frac", "Share escalated to Claude", "fraction")]
    for ax, (k, title, yl) in zip(axes, panels):
        _style(ax, title, yl)
        ax.plot(ts, [s[k] for s in sweep], color=COLORS["hybrid"], linewidth=2, marker="o", markersize=4,
                label="Hybrid")
        if k != "escalated_frac":
            for sysname in [x for x in ("claude", "deepinfra", "drex") if x in summary["triage"]]:
                v = summary["triage"][sysname]["category_acc" if k == "category_acc" else "cost_per_1k"]
                ax.axhline(v, color=COLORS[sysname], linewidth=1.5, linestyle="--", label=LABEL[sysname])
        ax.axvline(config.ESCALATE_THRESHOLD, color=MUTED, linewidth=0.8, linestyle=":")
        ax.set_xlabel("escalation threshold", color=INK2, fontsize=9)
    axes[0].legend(frameon=False, fontsize=8, labelcolor=INK2)
    fig.tight_layout()
    p = out_dir / "threshold_sweep.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    files.append(p.name)

    fig, axes = plt.subplots(1, 3, figsize=(14, 3.6), facecolor=SURFACE)
    tri = summary["triage"]
    systems = [s for s in ALL_SYSTEMS if s in tri]
    for ax, (k, title, yl, fmt) in zip(axes, [("p50_s", "Latency p50 per email", "seconds", "{:.2f}s"),
                                              ("category_acc", "Category accuracy", "accuracy", "{:.2f}"),
                                              ("cost_per_1k", "Cost per 1,000 emails", "USD", "${:.3f}")]):
        _style(ax, title, yl)
        vals = [tri[s].get(k, 0) or 0 for s in systems]
        bars = ax.bar([SHORT[s] for s in systems], vals, color=[COLORS[s] for s in systems], width=0.6,
                      edgecolor=SURFACE, linewidth=2)
        for b, v in zip(bars, vals):
            ax.annotate(fmt.format(v), (b.get_x() + b.get_width() / 2, v), ha="center", va="bottom",
                        fontsize=8, color=INK2, xytext=(0, 3), textcoords="offset points")
    fig.tight_layout()
    p = out_dir / "latency_cost.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    files.append(p.name)
    return files


# ------------------------------------------------------------------ report
def _f(x, nd=3):
    return "n/a" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def write_report(S: dict, out_dir: Path) -> Path:
    tri = S["triage"]
    L = []
    mode = "MOCK CLIENTS (numbers are placeholders, not evidence)" if S["mock"] else "REAL APIs"
    L += ["# Inbox Predator - evaluation summary", "",
          "Built with Claude Code; **Intern runs inside the product**: every Drex call goes through Intern's "
          "`intern.decisions` client (Level A), and the Intern agent row is an Intern session with System 2 = "
          "DeepInfra Qwen and System 1 = Drex (Level B).", "",
          f"Mode: **{mode}** · data: `{S.get('data_source')}` · Drex model: `{S['drex_model']}` · Claude model: "
          f"`{S['claude_model']}` · DeepInfra model: `{S.get('deepinfra_model')}` · "
          f"profile: `{S['profile']}` · labeled emails: {tri['claude'].get('n', 0)} · "
          f"escalation threshold: {S['threshold']}", ""]
    if config.DREX_PRICE_IS_PLACEHOLDER:
        L += [f"> Drex price is a PLACEHOLDER (${config.DREX_PRICE_PER_MTOK_INPUT}/M input tokens). Set "
              "`DREX_PRICE_PER_MTOK_INPUT` in .env from the Nace pricing page before quoting cost multiples.", ""]
    systems = [s for s in ALL_SYSTEMS if s in tri]
    L += ["## Headline: Claude-only vs DeepInfra Qwen vs Drex vs Hybrid vs Intern agent (email triage)", "",
          "| System | Category acc | Category macro-F1 | Urgent recall | Action acc | p50 s | p95 s | emails/s | $ / 1k emails | Escalated |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for s in systems:
        m = tri[s]
        L.append(f"| {LABEL[s]} | {_f(m.get('category_acc'))} | {_f(m.get('category_macro_f1'))} | "
                 f"{_f(m.get('urgent_recall'))} | {_f(m.get('action_acc'))} | {_f(m['p50_s'], 2)} | "
                 f"{_f(m['p95_s'], 2)} | {_f(m['throughput_per_s'], 2)} | {_f(m['cost_per_1k'], 4)} | "
                 f"{_f(m.get('escalated_frac', 0.0), 2) if s == 'hybrid' else '-'} |")
    c = tri["claude"]
    L += ["", "| Comparison vs Claude-only | Speedup (p50, x) | Cost multiple (Claude / X) | Category acc delta |",
          "|---|---|---|---|"]
    for s in systems[1:]:
        m = tri[s]
        sp = f"{c['p50_s'] / m['p50_s']:.1f}x" if m["p50_s"] else "n/a"
        cm = f"{c['cost_per_1k'] / m['cost_per_1k']:.1f}x" if m["cost_per_1k"] else "n/a"
        L.append(f"| {LABEL[s]} | {sp} | {cm} | {m.get('category_acc', 0) - c.get('category_acc', 0):+.3f} |")
    A = S.get("agent")
    if A:
        L += ["", "Intern agent latency and cost are the whole session (wall clock, System 2 + System 1) divided "
                  "by emails handled, so p50 = p95.", ""]
        L += ["## Intern inside the product", "",
              f"**Level A: Drex through `intern.decisions.DecisionClient`.** {S['intern_level_a']['asks']} asks "
              f"answered ({S['intern_level_a']['cached']} from Intern's cache, {S['intern_level_a']['errors']} "
              f"failed attempts), backend `{S['intern_level_a']['backend']}`.", "",
              f"**Level B: Intern agent session** ({'SIMULATED in mock mode' if A['simulated'] else 'real run'}): "
              f"System 2 `{A['system2']}`, System 1 `{A['system1']}`. {A['labeled_by_agent']}/{A['n_emails']} emails "
              f"labeled ({A['coverage']:.0%} coverage), {A['turns']} turns, {A['tool_calls']} tool calls, "
              f"stop reason `{A['reason']}`.", "",
              "| System 1 asks | by purpose | p50 ms | nudges | guard blocks | compactions | System 2 tokens in/out | "
              "System 2 $ | System 1 $ | wall s |", "|---|---|---|---|---|---|---|---|---|---|",
              f"| {A['system1_asks']} | {A['system1_by_purpose']} | {A['system1_p50_ms']} | {A['nudges']} | "
              f"{A['guard_blocks']} | {A['compactions']} | {A['system2_input_tokens']}/{A['system2_output_tokens']} | "
              f"{A['system2_cost_usd']:.4f} | {A['system1_cost_usd']:.4f} | {A['wall_s']:.1f} |", ""]
    L += ["", "![latency and cost](latency_cost.png)", "",
          "## Hybrid threshold sweep", "", "| threshold | category acc | action acc | urgent recall | $ / 1k | escalated | p50 s |",
          "|---|---|---|---|---|---|---|"]
    for s in S["sweep"]:
        L.append(f"| {s['threshold']:.2f} | {s['category_acc']:.3f} | {s['action_acc']:.3f} | {s['urgent_recall']:.3f} | "
                 f"{s['cost_per_1k']:.4f} | {s['escalated_frac']:.2f} | {s['p50_s']:.2f} |")
    L += ["", "![threshold sweep](threshold_sweep.png)", ""]
    J = S.get("jobs")
    if J:
        L += ["## Job ranking (LinkedIn alerts -> NDI extract -> score)", "",
              f"NDI: {J['ndi']['alerts']} alerts -> {J['ndi']['jobs_extracted']} jobs, p50 {J['ndi']['p50_s']:.2f}s per "
              f"alert, est. cost ${J['ndi']['cost_usd']:.4f}.", "",
              "| System | P@5 | P@10 | NDCG@10 | p50 s / job | gold-matched jobs |", "|---|---|---|---|---|---|"]
        for s in JOB_SYSTEMS:
            m = J[s]
            L.append(f"| {LABEL[s]} | {m['p@5']:.2f} | {m['p@10']:.2f} | {m['ndcg@10']:.3f} | "
                     f"{_f(m.get('p50_s'), 2) if 'p50_s' in m else '-'} | {m['matched_gold']}/{m['n_jobs']} |")
        L += ["", f"Cost per 1k jobs scored: Drex ${J['cost_per_1k_jobs']['drex']:.4f}, Claude "
                  f"${J['cost_per_1k_jobs']['claude']:.4f}. Hybrid escalated {J['hybrid'].get('escalated_frac', 0):.0%}.", ""]
    G = S.get("guardrail")
    if G:
        L += ["## Sensitive-data guardrail benchmark", "",
              f"{G['n_sensitive']} synthetic sensitive + {G['n_benign']} benign emails (includes adversarial cases).", "",
              "| Configuration | Recall (sensitive) | False-block rate | Missed by class |", "|---|---|---|---|"]
        for k in ("layer1_only", "drex_only", "claude_only", "layer1+drex", "layer1+claude"):
            g = G[k]
            L.append(f"| {k} | {g['recall']:.3f} | {g['false_block_rate']:.3f} | {g['missed_by_class'] or '-'} |")
        cl = G["check_latency"]
        L += ["", f"Semantic check latency p50/p95: Drex {cl['drex_p50_s']:.2f}/{cl['drex_p95_s']:.2f}s vs Claude "
                  f"{cl['claude_p50_s']:.2f}/{cl['claude_p95_s']:.2f}s. Cost per 1k checks: Drex "
                  f"${G['check_cost_per_1k']['drex']:.4f} vs Claude ${G['check_cost_per_1k']['claude']:.4f}.", ""]
    P = S.get("personalization")
    if P:
        L += ["## Personalization test (same emails, 3 profiles, Drex)", "", f"{P['n_emails']} emails. {P['note']}.", "",
              "| Profile | Category agreement with labels | Share urgent/important | Job alerts in top 10 |",
              "|---|---|---|---|"]
        for p, m in P["per_profile"].items():
            L.append(f"| {p} | {m['agreement_with_labels']:.3f} | {m['share_urgent_or_important']:.2f} | "
                     f"{m['top10_job_alerts']} |")
        L += ["", "| Pair | Spearman of priority ranks | Top-10 overlap |", "|---|---|---|"]
        for k, m in P["pairs"].items():
            L.append(f"| {k} | {m['spearman_priority']:.3f} | {m['top10_overlap']}/10 |")
        L.append("")
    L += ["## Notes", "",
          "- Latency is per-call wall clock including network; throughput is single-stream (1 / mean latency).",
          "- Claude baseline uses structured JSON output with low effort (current Claude models do not accept "
          "`temperature`).",
          "- Costs come from API-reported cost when the API returns one, else token counts x prices in `config.py`.",
          "- DeepInfra Qwen is scored with JSON mode at temperature 0 and validated against the same enums.", ""]
    p = out_dir / "summary.md"
    p.write_text("\n".join(L))
    return p


def run(clients, profile="student_jobhunter", workers: int | None = None, do_jobs=True, do_guard=True,
        do_personas=True, do_agent=True) -> dict:
    workers = workers or (8 if clients.mock else 4)
    t0 = time.time()
    emails = load_emails()
    labels = read_labels()
    print(f"[eval] {len(emails)} emails, {len(labels)} labels, mock={clients.mock}")
    R = run_triage_systems(emails, labels, profile, clients, workers)
    th = config.ESCALATE_THRESHOLD
    hyb = combine_offline(R["drex"], R["claude"], th)
    systems_rows = [("claude", R["claude"]), ("deepinfra", R.get("deepinfra")), ("drex", R["drex"]), ("hybrid", hyb)]
    agent = None
    if do_agent:
        from src.agent import run_agent

        try:
            agent = run_agent(profile, clients, emails, use_mcp=config.INTERN_USE_MCP)
            systems_rows.append(("intern_agent", {i: r for i, r in agent["rows"].items() if i in labels}))
        except Exception as ex:  # keep the rest of the eval; report the failure
            print(f"  [eval] Intern agent run failed: {type(ex).__name__}: {str(ex)[:300]}")
    tri = {}
    for s, rows in systems_rows:
        if rows:
            tri[s] = {**triage_metrics(rows, labels), **latency_stats(rows), "cost_per_1k": cost_per_1k(rows, s)}
    tri["hybrid"]["escalated_frac"] = float(np.mean([r["escalated"] for r in hyb.values()])) if hyb else 0.0
    S = {"mock": clients.mock, "profile": profile, "threshold": th, "data_source": config.DATA_SOURCE,
         "drex_model": getattr(clients.drex, "model", "?"), "claude_model": getattr(clients.claude, "model", "?"),
         "deepinfra_model": getattr(clients.deepinfra, "model", None),
         "triage": tri, "sweep": threshold_sweep(R["drex"], R["claude"], labels)}
    if agent:
        S["agent"] = agent["summary"]
    print("[eval] triage done")
    if do_jobs:
        J = run_jobs_eval(emails, profile, clients, workers)
        S["jobs"] = jobs_metrics(J, read_job_gold(), th, getattr(clients.claude, "model", None))
        print("[eval] jobs done")
    if do_guard:
        S["guardrail"] = guardrail_bench(clients, workers)
        print("[eval] guardrail done")
    if do_personas:
        S["personalization"] = personalization(emails, labels, clients, workers)
        print("[eval] personalization done")
    out = config.RESULTS_DIR
    out.mkdir(exist_ok=True)
    S["intern_level_a"] = clients.drex.usage()
    S["charts"] = charts(S, S["sweep"], out)
    (out / "summary.json").write_text(json.dumps(S, indent=2, default=str))
    p = write_report(S, out)
    print(f"[eval] wrote {p.relative_to(config.ROOT)} in {time.time() - t0:.1f}s")
    return S


if __name__ == "__main__":
    import argparse

    from src.clients import get_clients

    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="student_jobhunter")
    ap.add_argument("--real", action="store_true", help="override USE_MOCKS and use real APIs")
    ap.add_argument("--skip", nargs="*", default=[], choices=["jobs", "guard", "personas", "agent"])
    a = ap.parse_args()
    run(get_clients(False if a.real else None), a.profile, do_jobs="jobs" not in a.skip,
        do_guard="guard" not in a.skip, do_personas="personas" not in a.skip, do_agent="agent" not in a.skip)
