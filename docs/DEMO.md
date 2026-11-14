# 5-minute demo script

**Before you go on stage:** run `python scripts/run_all.py --real`, then `streamlit run app.py`. In the sidebar,
pick `student_jobhunter` with mock clients **off**, press **Run triage on inbox** once so the cache is warm,
and open the Results tab in a second browser tab.

---

**0:00 to 0:30 — The problem.**
"Every inbox triage agent today sends every email to a frontier LLM, which is slow and costs money on every
message. Most of that work isn't writing. It's deciding: is this urgent, does it matter to *me*, what should I do with it.
We replaced that decision with Drex, a decision model that returns calibrated probabilities in one forward pass."

**0:30 to 1:15 — Personalization (sidebar plus the Check now tab).**
- Show the profile text in the sidebar expander, built from the onboarding questions.
- In Check now, point to the urgent items at the top: an interview confirmation due tonight and an assessment
  expiring in 24 hours. Each one has a category chip, Drex's confidence bar, the suggested action, and a Claude
  summary that appears only on the top items.
- Switch the profile to `parent` and run again: job emails drop out of the top list. "Same inbox, different person, different priorities."

**1:15 to 2:00 — Jobs tab (NDI plus Drex).**
- "LinkedIn alerts bundle 4-6 jobs into one email. NDI splits them and extracts the fields. Drex scores fit and
  flags dealbreakers."
- Open the NDI grounding expander on one job card to show where the extracted value came from.
- Point at a dealbreaker warning, for example "security clearance required" or "no sponsorship".

**2:00 to 2:40 — Safety tab.**
"Before anything leaves the machine: local rules with a Luhn check, then a Drex semantic check on redacted text,
and any error means the email is dropped. N scanned, M withheld. We never show the content, and the audit log
records rule names only."

**2:40 to 4:15 — Results tab. This is the moment.**
- The headline table: Claude-only vs Drex vs Hybrid, with speedup ×, cost ×, and the accuracy delta. **Say the
  real numbers, including where Drex trails.**
- The threshold-sweep chart: "Hybrid lets you pick a point on the curve. At threshold T we escalate X% to Claude and
  recover Y points of accuracy at Z% of Claude's cost."
- **Live box:** paste an email and press Score with both. Drex answers in ~N ms with a full probability distribution;
  Claude takes ~M ms and returns a single label.
- Then paste "Your verification code is 839201". The guardrail blocks it, and it is never sent to any model.

**4:15 to 5:00 — Honesty and close.**
"These benchmarks run on a small, single-annotator label set. Drex is weaker at knowing when *not* to act, so the
agent only labels and ranks. It never deletes or replies. The decision layer moved from a frontier LLM to Drex:
[speedup]× faster and [cost]× cheaper, at [delta] accuracy."
