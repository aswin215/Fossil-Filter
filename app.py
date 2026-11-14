"""Inbox Predator dashboard.  Run:  streamlit run app.py"""
from __future__ import annotations

import html
import json
import time

import pandas as pd
import streamlit as st

import config
from src import guardrail
from src.baseline import triage_email_claude
from src.clients import get_clients
from src.pipeline import inbox_cache_path, run_inbox
from src.profile import QUESTIONS, build_profile, list_profiles, load_profile, save_profile
from src.triage import triage_email

st.set_page_config(page_title="Inbox Predator", layout="wide")

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
st.sidebar.title("Inbox Predator")
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
st.markdown(f"### Inbox Predator <span class='muted'>· {profile} · {mode} clients</span>", unsafe_allow_html=True)
tabs = st.tabs(["Check now", "Jobs", "Everything else", "Safety", "Results"])

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
        df = pd.DataFrame([{"system": {"claude": "Claude-only", "drex": "Drex", "hybrid": "Hybrid"}[s],
                            "category acc": tri[s].get("category_acc"), "macro-F1": tri[s].get("category_macro_f1"),
                            "urgent recall": tri[s].get("urgent_recall"), "action acc": tri[s].get("action_acc"),
                            "p50 s": tri[s]["p50_s"], "p95 s": tri[s]["p95_s"], "$ / 1k emails": tri[s]["cost_per_1k"]}
                           for s in ("claude", "drex", "hybrid")])
        st.dataframe(df.style.format(precision=3), hide_index=True, width="stretch")
        c, d = tri["claude"], tri["drex"]
        m1, m2, m3 = st.columns(3)
        m1.metric("Drex speedup (p50)", f"{c['p50_s'] / d['p50_s']:.1f}x")
        m2.metric("Drex cost multiple", f"{c['cost_per_1k'] / d['cost_per_1k']:.1f}x cheaper")
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
