"""Fossil Filter dashboard.  Run:  streamlit run app.py"""
from __future__ import annotations

import html
import json
import time

import pandas as pd
import streamlit as st

import config
from src import guardrail
from src.agent import EVENTS_OUT, load_agent_result, run_agent
from src.baseline import triage_email_claude
from src.clients import get_clients
from src.pipeline import inbox_cache_path, run_inbox
from src.profile import QUESTIONS, build_profile, list_profiles, load_profile, save_profile
from src.triage import triage_email

st.set_page_config(page_title="Fossil Filter", layout="wide")

CHIP = {"urgent_action_needed": ("#e34948", "URGENT"), "important": ("#2a78d6", "IMPORTANT"),
        "informational": ("#898781", "INFO"), "job_alert": ("#1baf7a", "JOB ALERT"),
        "promotion_or_spam": ("#6250d6", "PROMO")}
st.markdown("""<style>
.chip{display:inline-block;padding:2px 8px;border-radius:999px;color:#fff;font-size:11px;font-weight:600;
letter-spacing:.04em;margin-right:6px}
.muted{color:#898781;font-size:12px}
.card{border:1px solid rgba(128,128,128,.25);border-radius:10px;padding:12px 14px;margin-bottom:10px}
</style>""", unsafe_allow_html=True)


def chip(cat: str) -> str:
    c, t = CHIP.get(cat, ("#898781", cat))
    return f'<span class="chip" style="background:{c}">{t}</span>'


@st.cache_resource
def clients_for(mock: bool):
    return get_clients(mock)


# ------------------------------------------------------------------ sidebar
st.sidebar.title("Fossil Filter")
profiles = list_profiles()
profile = st.sidebar.selectbox("Profile", profiles, index=profiles.index("student_jobhunter")
                               if "student_jobhunter" in profiles else 0)
use_mocks = st.sidebar.toggle("Use mock clients", value=config.USE_MOCKS,
                              help="Off = real Drex / NDI / Claude APIs (keys from .env)")
esc_t = st.sidebar.slider("Escalate to Claude below confidence", 0.30, 0.95, config.ESCALATE_THRESHOLD, 0.05)
sens_t = st.sidebar.slider("Sensitive threshold (Drex layer)", 0.05, 0.50, config.SENSITIVE_THRESHOLD, 0.05)
semantic = st.sidebar.checkbox("Drex semantic guardrail layer", value=True)
summaries = st.sidebar.checkbox("Claude summaries for top emails", value=True)

try:
    C = clients_for(use_mocks)
except RuntimeError as e:
    st.error(str(e))
    st.stop()

run_clicked = st.sidebar.button("Run triage on inbox", type="primary", width="stretch")
cache_file = inbox_cache_path(profile, use_mocks)
state_key = f"inbox::{profile}::{use_mocks}"
if run_clicked:
    with st.spinner("Guardrail -> Drex triage -> hybrid -> jobs ..."):
        t0 = time.perf_counter()
        st.session_state[state_key] = run_inbox(profile, C, threshold=esc_t, sensitive_threshold=sens_t,
                                                use_semantic_guard=semantic, summaries=summaries)
        st.sidebar.success(f"done in {time.perf_counter() - t0:.1f}s")
elif state_key not in st.session_state and cache_file.exists():
    st.session_state[state_key] = json.loads(cache_file.read_text())
R = st.session_state.get(state_key)

with st.sidebar.expander("New profile (onboarding)"):
    with st.form("onboard"):
        pname = st.text_input("Profile name", placeholder="my_profile")
        answers = {k: st.text_area(q, height=68) for k, q in QUESTIONS}
        if st.form_submit_button("Build profile"):
            try:
                text = build_profile(answers, C.claude)
                save_profile(pname, answers, text)
                st.success(f"saved profiles/{pname}.json - reload to select it")
                st.text(text)
            except Exception as ex:
                st.error(f"{type(ex).__name__}: {ex}")
    st.caption(load_profile(profile)["profile_text"])

mode = "MOCK" if use_mocks else "REAL"
st.markdown(f"### Fossil Filter <span class='muted'>· {profile} · {mode} clients</span>", unsafe_allow_html=True)
st.caption("Built with Claude Code · **Intern runs inside the product**: Drex via `intern.decisions` (Level A) and "
           "an Intern agent session, System 2 DeepInfra Qwen + System 1 Drex (Level B).")
tabs = st.tabs(["Check now", "Jobs", "Everything else", "Safety", "Results", "Intern agent"])

if R is None:
    for t in tabs[:4]:
        t.info("Press **Run triage on inbox** in the sidebar.")
else:
    emails = R["emails"]
    # ---------------------------------------------------------------- tab 1
    with tabs[0]:
        top = [r for r in emails if r["category"] in ("urgent_action_needed", "important")]
        st.caption(f"{len(top)} emails need you · sorted by priority · {R['escalated_frac']:.0%} escalated to Claude")
        for r in top:
            with st.container(border=True):
                c1, c2 = st.columns([4, 1.3])
                who = "Claude (escalated)" if r.get("escalated") else "Drex"
                c1.markdown(f"{chip(r['category'])} **{html.escape(r['subject'])}**<br>"
                            f"<span class='muted'>{html.escape(r['from'])} · decided by {who}</span>",
                            unsafe_allow_html=True)
                if r.get("summary"):
                    c1.write(r["summary"])
                c2.markdown(f"**{r['action'].replace('_', ' ')}**")
                conf = r["drex"]["confidence"]
                c2.progress(min(1.0, conf), text=f"Drex confidence {conf:.0%}")
                c2.caption(f"priority {r['priority']:.2f}")
    # ---------------------------------------------------------------- tab 2
    with tabs[1]:
        jobs = R["jobs"]
        st.caption(f"Top {len(jobs)} jobs across all LinkedIn alerts, deduplicated by company + title")
        cols = st.columns(2)
        for i, j in enumerate(jobs):
            s = j["score"]
            with cols[i % 2].container(border=True):
                st.markdown(f"**{html.escape(j.get('title') or '?')}** · {html.escape(j.get('company') or '')}")
                st.caption(f"{j.get('location') or ''} · {j.get('seniority') or ''} · {j.get('employment_type') or ''}"
                           f" · {', '.join(j.get('tech_stack') or [])}")
                st.progress(min(1.0, s["rank_score"]), text=f"fit score {s['rank_score']:.2f}"
                            + (" (Claude)" if s.get("escalated") else ""))
                if s["p_dealbreaker"] > 0.5:
                    st.warning(f"Possible dealbreaker: {j.get('sponsorship_or_clearance_notes') or 'see posting'}",
                               icon="⚠️")
                if j.get("url"):
                    st.markdown(f"[Open posting]({j['url']})")
                if j.get("grounding"):
                    with st.expander("NDI grounding"):
                        for f, g in j["grounding"].items():
                            st.caption(f"**{f}** ← “{g.get('source_text')}” @ {json.dumps(g.get('location'))}")
    # ---------------------------------------------------------------- tab 3
    with tabs[2]:
        rest = [r for r in emails if r["category"] not in ("urgent_action_needed", "important")]
        groups = {}
        for r in rest:
            groups.setdefault(r["category"], []).append(r)
        cols = st.columns(max(1, len(groups)))
        for c, (cat, rows) in zip(cols, sorted(groups.items())):
            c.metric(CHIP.get(cat, ("", cat))[1].title(), len(rows))
        for cat, rows in sorted(groups.items()):
            with st.expander(f"{CHIP.get(cat, ('', cat))[1].title()} · {len(rows)}"):
                st.dataframe(pd.DataFrame([{"subject": r["subject"], "from": r["from"],
                                            "suggested": r["action"]} for r in rows]),
                             hide_index=True, width="stretch")
    # ---------------------------------------------------------------- tab 4
    with tabs[3]:
        g = R["guard"]
        a, b, c = st.columns(3)
        a.metric("Scanned", g["scanned"])
        b.metric("Withheld as sensitive", g["withheld"])
        c.metric("Sent to models", g["scanned"] - g["withheld"])
        st.caption(f"{g['scanned']} scanned, {g['withheld']} withheld as sensitive. Withheld emails never leave "
                   "this machine for triage; only ids and rule names are logged. No content is shown here.")
        if g["rule_counts"]:
            st.bar_chart(pd.Series(g["rule_counts"], name="emails").sort_values(ascending=False), horizontal=True)
        st.json(g["layer_counts"])

# ---------------------------------------------------------------- tab 5
with tabs[4]:
    sp = config.RESULTS_DIR / "summary.json"
    if sp.exists():
        S = json.loads(sp.read_text())
        st.caption(f"From `results/summary.json` · {'MOCK clients - placeholder numbers' if S['mock'] else 'real APIs'}"
                   f" · Drex `{S['drex_model']}` vs Claude `{S['claude_model']}`")
        tri = S["triage"]
        names = {"claude": "Claude-only", "deepinfra": "DeepInfra Qwen", "drex": "Drex (Level A, via Intern)",
                 "hybrid": "Hybrid", "intern_agent": "Intern agent (Level B)"}
        df = pd.DataFrame([{"system": names[s],
                            "category acc": tri[s].get("category_acc"), "macro-F1": tri[s].get("category_macro_f1"),
                            "urgent recall": tri[s].get("urgent_recall"), "action acc": tri[s].get("action_acc"),
                            "p50 s": tri[s]["p50_s"], "p95 s": tri[s]["p95_s"], "$ / 1k emails": tri[s]["cost_per_1k"]}
                           for s in names if s in tri])
        st.dataframe(df.style.format(precision=3), hide_index=True, width="stretch")
        c, d = tri["claude"], tri["drex"]
        m1, m2, m3 = st.columns(3)
        m1.metric("Drex speedup (p50)", f"{c['p50_s'] / d['p50_s']:.1f}x")
        ratio = c["cost_per_1k"] / d["cost_per_1k"] if d["cost_per_1k"] else 0.0
        m2.metric("Drex cost vs Claude", f"{ratio:.1f}x cheaper" if ratio >= 1 else f"{1 / ratio:.1f}x pricier"
                  if ratio else "n/a", help="Drex price is a placeholder until DREX_PRICE_PER_MTOK_INPUT is set"
                  if config.DREX_PRICE_IS_PLACEHOLDER else None)
        m3.metric("Accuracy delta (Drex - Claude)", f"{d.get('category_acc', 0) - c.get('category_acc', 0):+.3f}")
        for ch in S.get("charts", []):
            st.image(str(config.RESULTS_DIR / ch), width="stretch")
        with st.expander("Full report (summary.md)"):
            st.markdown((config.RESULTS_DIR / "summary.md").read_text().replace("![", "<!--").replace(".png)", "-->"),
                        unsafe_allow_html=True)
    else:
        st.info("Run `python scripts/run_all.py` to produce results.")

    st.divider()
    st.subheader("Score this email live: Drex vs Claude")
    with st.form("live"):
        lf = st.text_input("From", "Recruiter <talent@nimbus.example>")
        ls = st.text_input("Subject", "Can you confirm your interview time by tonight?")
        lb = st.text_area("Body", "Hi! We'd love to move you to the onsite for the ML Engineer Intern role. "
                                  "Please confirm Thursday 2pm by tonight so we can hold the slot.", height=110)
        go = st.form_submit_button("Score with both", type="primary")
    if go:
        e = {"id": "live", "from": lf, "subject": ls, "body_text": lb, "attachments": [], "date": ""}
        keep, layer, rules, _ = guardrail.check(e, None, use_semantic=False)
        if not keep:
            st.error(f"Withheld by guardrail ({layer}: {', '.join(rules)}). Not sent to any model.")
        else:
            prof = load_profile(profile)["profile_text"]
            colA, colB = st.columns(2)
            try:
                d = triage_email(prof, e, C.drex)
                colA.metric("Drex latency", f"{d['latency_s'] * 1000:.0f} ms")
                colA.markdown(f"{chip(d['category'])} → **{d['action'].replace('_', ' ')}**", unsafe_allow_html=True)
                colA.bar_chart(pd.Series(d["category_probs"], name="P(category)"), horizontal=True, height=200)
            except Exception as ex:
                colA.error(f"Drex: {type(ex).__name__}: {ex}")
            try:
                c = triage_email_claude(prof, e, C.claude)
                colB.metric("Claude latency", f"{c['latency_s'] * 1000:.0f} ms")
                colB.markdown(f"{chip(c['category'])} → **{c['action'].replace('_', ' ')}**", unsafe_allow_html=True)
                colB.caption("Claude returns one label (no calibrated distribution).")
            except Exception as ex:
                colB.error(f"Claude: {type(ex).__name__}: {ex}")
            if use_mocks:
                st.caption("Mock mode: latencies are simulated.")

# ---------------------------------------------------------------- tab 6: Intern
with tabs[5]:
    st.subheader("Intern, Nace's agent harness, running inside Fossil Filter")
    st.markdown(
        "**Level A.** Every Drex call in the pipeline (triage, job fit, guardrail) goes through Intern's "
        "`DecisionClient`, which supplies retries, 429 handling, failover, the answer cache and concurrency.  \n"
        "**Level B.** An Intern agent session: System 2 (DeepInfra Qwen) does the triage with Intern's file tools, and "
        "System 1 (Drex) judges every turn: stuck detection, the tool-call guard and compaction. It only sees "
        "emails that passed the guardrail.")
    u = C.drex.usage()
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Level A: Drex asks (this app)", u["asks"])
    a2.metric("Answered from Intern cache", u["cached"])
    a3.metric("Failed attempts", u["errors"])
    a4.metric("Backend", u["backend"].split(":", 1)[-1])

    st.divider()
    lim = st.number_input("Emails for the agent (0 = all guardrail-passed)", 0, 500, 0, 10)
    use_mcp = st.checkbox("Level C: give the agent our MCP tools (list_emails, get_email, score_email = Drex, "
                          "save_label) instead of raw files", value=config.INTERN_USE_MCP)
    if st.button("Run Intern agent session", type="primary"):
        box = st.empty()
        log: list[dict] = []

        def on_event(ev):
            log.append(ev)
            if ev["type"] in ("decision", "nudge", "tool_guard", "tool_call_start", "run_end"):
                box.caption(f"{len(log)} events · last: {ev['type']} {ev.get('purpose') or ev.get('name') or ''}")
        with st.spinner("Intern session running..." if not use_mocks else "Simulating an Intern session (mock)..."):
            try:
                run_agent(profile, C, limit=lim or None, on_event=on_event, use_mcp=use_mcp)
            except Exception as ex:
                st.error(f"{type(ex).__name__}: {ex}")
    A = load_agent_result()
    if A is None:
        st.info("No agent run yet. Press the button, or run `python scripts/run_agent.py`.")
    else:
        s6 = A["summary"]
        if s6["simulated"]:
            st.warning("SIMULATED session (mock mode): Intern needs a reachable System 1 to start, so these events are "
                       "synthetic. Turn mocks off with NACE_API_KEY and DEEPINFRA_API_KEY set to run the real harness.")
        st.caption(f"System 2 `{s6['system2']}` · System 1 `{s6['system1']}` · profile `{s6['profile']}` · "
                   f"stop reason `{s6['reason']}` · tools: {'our MCP server (Level C)' if s6.get('mcp') else 'files'}")
        b1, b2, b3, b4, b5, b6 = st.columns(6)
        b1.metric("Emails labeled", f"{s6['labeled_by_agent']}/{s6['n_emails']}")
        b2.metric("Turns", s6["turns"])
        b3.metric("System 1 decisions", s6["system1_asks"])
        b4.metric("Stuck nudges", s6["nudges"])
        b5.metric("Guard blocks", s6["guard_blocks"])
        b6.metric("Session cost", f"${s6['total_cost_usd']:.4f}")
        st.caption(f"Wall {s6['wall_s']:.1f}s · System 1 p50 {s6['system1_p50_ms']} ms · by purpose "
                   f"{s6['system1_by_purpose']} · System 2 tokens {s6['system2_input_tokens']} in / "
                   f"{s6['system2_output_tokens']} out")
        from src.evaluate import read_labels, triage_metrics

        m = triage_metrics(A["rows"], read_labels())
        if m:
            st.metric("Agent category accuracy vs labels", f"{m['category_acc']:.3f}",
                      help=f"action acc {m['action_acc']:.3f} on {m['n']} labeled emails")
        if EVENTS_OUT.exists():
            evs = [json.loads(x) for x in EVENTS_OUT.read_text().splitlines()]
            rows = []
            for ev in evs:
                if ev["type"] in ("decision", "nudge", "tool_guard", "tool_call_start", "tool_call_end",
                                  "compaction", "run_end"):
                    detail = ev.get("purpose") or ev.get("name") or ev.get("reason") or ""
                    if ev["type"] == "decision" and ev.get("answers"):
                        detail += " " + json.dumps(ev["answers"])[:120]
                    if ev["type"] == "tool_guard":
                        detail = f"{ev.get('name')}: {ev.get('rule')} (risk {ev.get('risk')})"
                    rows.append({"turn": ev.get("turn"), "event": ev["type"], "detail": detail,
                                 "ms": ev.get("duration_ms")})
            st.markdown("**Intern event stream** (sanitized: no email text, tool arguments or outputs)")
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=360)
