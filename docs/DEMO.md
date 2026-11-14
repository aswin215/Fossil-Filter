# 5-minute demo script

**Before you go on stage:**
1. Run `python scripts/run_all.py --real`, then `streamlit run app.py`.
2. In the sidebar, pick `student_jobhunter` with mock clients **off**, and press **Run triage on inbox** once so the
   cache is warm.
3. Open the Results tab in a second browser tab.
4. Make sure `results/agent_triage.json` comes from a real Intern run. Check the Intern agent tab: it must not show
   the "SIMULATED" banner.

Say it once, early: **"Built with Claude Code; Intern runs inside the product."**

---

**0:00 to 0:30 — The problem.**
"Every inbox triage agent today sends every email to a frontier LLM, which is slow and costs money on every
message. Most of that work isn't writing. It's deciding: is this urgent, does it matter to *me*, what should I do with it.
We replaced that decision with Drex, a decision model that returns calibrated probabilities in one forward pass.
It runs through Nace's Intern harness."

**0:30 to 1:05 — Personalization (sidebar plus the Check now tab).**
- Show the profile text, built from the onboarding questions.
- Urgent items are at the top, each with a category chip, Drex's confidence bar and the suggested action.
  Claude summaries appear on the top items only.
- Switch the profile to `parent` and run again: job emails drop out of the top list. "Same inbox, different person, different priorities."
- The measured version: "Swapping the generic persona for my own 74-word profile raised accuracy for every model,
  +7 points for Drex, +8 for Claude and +14 for Qwen, on the same emails and labels. Drex found 13 of my 25
  important emails instead of 3."

**1:05 to 1:40 — Jobs tab (NDI plus Drex).**
- NDI splits each LinkedIn alert into jobs and extracts the fields. Open the NDI grounding expander to show where a
  value came from.
- Drex scores fit and flags dealbreakers. Point at a "no sponsorship" or "clearance" warning.

**1:40 to 2:10 — Safety tab.**
"Before anything leaves the machine, including what the Intern agent sees: local rules with a Luhn check, then a Drex
check on redacted text, and any error means the email is dropped. N scanned, M withheld. We never show the content."

**2:10 to 3:10 — Intern agent tab. Partner tools and Nace's harness.**
- Level A: "Every Drex call in the pipeline goes through Intern's `DecisionClient`, which handles retries, failover,
  caching and concurrency." Show the asks and cache-hit counters.
- Level B: "Here an Intern agent triages the same inbox. DeepInfra's Qwen is System 2 and does the work; Drex is
  System 1 and judges every turn." Walk the event stream: System 1 supervise and guard decisions, a stuck nudge,
  any guard block, and the session cost.

**3:10 to 4:25 — Results tab. This is the moment.**
- The headline table has five rows: Claude-only, DeepInfra Qwen, Drex via Intern, Hybrid and the Intern agent, with
  speedup ×, cost × and the accuracy delta. **Say the real numbers, including where Drex trails.**
- Threshold sweep: "At threshold T we escalate X% to Claude and recover Y points at Z% of Claude's cost."
- **Live box:** paste an email and press Score with both. Drex answers in ~N ms with a full distribution, and
  Claude takes ~M ms. Then paste "Your verification code is 839201": it is blocked and never sent to a model.

**4:25 to 5:00 — Honesty and close.**
"These benchmarks run on a small, single-annotator label set. Drex is weaker at knowing when *not* to act, so the
agent only labels and ranks. It never deletes or replies. Built with Claude Code; Intern runs inside the product.
The decision layer moved from a frontier LLM to Drex: [speedup]× faster and [cost]× cheaper, at [delta] accuracy."
