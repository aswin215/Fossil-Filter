# Inbox Predator

A personalized email triage agent for the Drexathon Apex Predator track. It replaces a frontier-LLM task
(classifying and ranking every incoming email for one specific user) with **Drex**, Nace AI's decision
model, and measures whether the replacement is faster, cheaper or more accurate.

| Layer | Component | Job |
|---|---|---|
| Perception | **NDI** (Nace Document Intelligence) | Splits LinkedIn job-alert emails into individual jobs, extracts fields, and grounds each value back to the source |
| Decision | **Drex** | One forward pass per email: `category` (choice), `relevance` (score) and `action` (choice); per job: `fit` (score), `is_target_role` and `has_dealbreaker` (yes/no) |
| Baseline and fallback | **Claude** | The Claude-only baseline to beat; re-judges low-confidence Drex items (hybrid); writes 2-sentence summaries for the top-K emails only |

```
mbox ─► loader ─► guardrail (local rules ─► Drex semantic check ─► fail closed) ─► Drex triage ─► hybrid escalation ─► dashboard
                                                   └► LinkedIn alerts ─► NDI extract ─► Drex job scoring ─► ranked jobs
```

## Setup

```bash
cd inbox-predator
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # USE_MOCKS=true works with no keys at all
python scripts/run_all.py       # sample data -> eval -> results/summary.md
streamlit run app.py
```

### Using real APIs
1. Fill `.env`: `NACE_API_KEY` (Drex and NDI share one `nace_sk_...` key), `ANTHROPIC_API_KEY`, `CLAUDE_MODEL`
   (for example `claude-opus-5-5`, `claude-sonnet-5-5` or `claude-haiku-5-5`), and `DREX_PRICE_PER_MTOK_INPUT`
   from the Nace pricing page.
2. Set `USE_MOCKS=false`, or pass `--real`. A missing key fails fast with a message naming it.
3. `python scripts/run_all.py --real`. Predictions are cached in `results/cache/`, so re-runs don't pay twice.
   Delete that folder to force fresh calls.

### Using your own inbox
1. Export Gmail with Google Takeout and put the `.mbox` in `data/raw/` (it is gitignored). Delete
   `data/raw/sample.mbox`.
2. `python -m src.loader`, which writes `data/emails.jsonl` (also gitignored).
3. `python scripts/label_emails.py` labels category and action, and `python scripts/label_emails.py --jobs` labels job fit.
   **The shipped `labels.csv` and `job_gold.csv` belong to the synthetic sample. Replace them with your own labels.**
4. Create your profile with the onboarding form in the dashboard sidebar or `python -m src.profile --name me`.

Mail access is read-only: nothing sends, deletes or modifies mail. `load_emails()` is the single source
interface, so a live Gmail/IMAP reader can replace the mbox later.

## Results

> **These numbers come from MOCK clients on the synthetic sample.** The mocks are deterministic keyword
> heuristics with simulated latency, so the plumbing, UI and evaluation can be built before keys exist.
> **They say nothing about Drex or Claude.** Run `python scripts/run_all.py --real` and paste the
> regenerated table from `results/summary.md` here before judging.

| System | Category acc | Category macro-F1 | Urgent recall | Action acc | p50 s | p95 s | emails/s | $ / 1k emails | Escalated |
|---|---|---|---|---|---|---|---|---|---|
| Claude-only | 0.840 | 0.838 | 0.643 | 0.617 | 2.65 | 3.26 | 0.39 | 3.5062 | - |
| Drex | 0.819 | 0.818 | 0.643 | 0.511 | 0.20 | 0.25 | 5.25 | 0.1314 | - |
| Hybrid | 0.840 | 0.838 | 0.643 | 0.617 | 2.41 | 3.42 | 0.51 | 2.5142 | 0.68 |

`results/summary.md` also contains:
- the speedup multiple, the cost multiple and the accuracy delta
- the hybrid threshold sweep (accuracy, cost and share escalated across 14 thresholds)
- job ranking P@5, P@10 and NDCG@10 against `job_gold.csv`
- the guardrail benchmark (recall and false-block rate for each layer combination, plus Drex vs Claude latency and cost for the same check)
- the 3-persona personalization test

## What is measured

| Metric | How |
|---|---|
| Triage accuracy | Accuracy, macro-F1 and urgent recall for category; accuracy for action, against `labels.csv` |
| Job ranking | P@5 and P@10 ("relevant" means gold fit = high) and NDCG@10 (gains 0/1/2), deduplicated by company + title |
| Latency | Per-call wall clock including network, p50 and p95; throughput is single-stream |
| Cost | Token counts × prices in `config.py`. Drex bills input tokens only (per the docs). NDI is priced per 1k pages |
| Hybrid | Escalate when the category or action top-class probability is below the threshold, or the relevance distribution is near-uniform. Swept offline from cached Drex and Claude predictions |
| Personalization | The same emails under 3 profiles: agreement with labels, Spearman correlation of priority ranks, top-10 overlap |
| Guardrail | 60 synthetic sensitive and 60 benign emails, including adversarial ones (an OTP buried in a newsletter, a statement from a non-bank domain, a benign "statement of accomplishment", a benign meeting-room code) |

## Sensitive-data guardrail

This runs before anything leaves the machine. **Layer 1** is local: regex for OTPs, SSNs, account and routing numbers, passwords and reset links, and tax forms; 13-19 digit card numbers validated with a Luhn check; plus sender-domain, subject-word and attachment-name lists in `config.py`. Any hit drops the email. **Layer 2** asks Drex one `choice` question on *redacted* text (digits, URLs and emails masked) and drops the email if P(any sensitive class) > `SENSITIVE_THRESHOLD`. **Layer 3** drops the email on any exception. The audit log (`results/audit_log.jsonl`, gitignored) stores only ids, layers and rule names.

## Where the Nace docs differed from the build prompt (the docs were followed)

- **Boolean question type** is `"noul"` (answer field `noul` = P(yes)), not "boolean".
- **Question format**: `questions` is an object keyed by id. `choice` criteria are a `{label: description}` map. `score` criteria are an ascending array, and `score` is the expected level index.
- **Models**: `drex-max` and `drex-v1.5` are retired and forward to successors. The default here is the pinned `drex-max-v1.1`; `drex-v2.0` is the text alternative. `drex-latest` (and an omitted `model`) currently resolves to **drex-max-v1.1**, contrary to the prompt's note.
- **Base URL and key**: `https://console.nace.ai`. Drex and NDI share the same `Bearer nace_sk_...` key.
- **NDI** is asynchronous (202 + job polling, or `?wait_seconds=`) and has **no inline-text source**, so the alert HTML is uploaded through an upload grant first. A single `extract` call with an array-of-jobs schema both splits the alert and extracts the fields, with citations for grounding. Unverified: the exact file-id field name in the upload response (the client accepts several shapes).
- **Claude**: current models reject `temperature`, so "temperature 0" became structured JSON output (`output_config.format`) at `effort: low`.
- **Drex per-token price** did not render in the docs. It is a placeholder in `.env` / `config.py`, and the report flags it until you set it.

## Honest limitations

- **Mock numbers are not evidence.** Everything above comes from mocks until you run with real keys.
- **Vendor-reported benchmarks**: Drex claims (speed, calibration) come from Nace. This project measures them only on this small set.
- **Small, single-annotator label set** (~100 emails, ~40 jobs). Confidence intervals are wide, so read differences of a few points as noise.
- **The sample labels are synthetic** (generator intent, not human judgement), and they reflect only the `student_jobhunter` persona. Per-persona "agreement" for the other profiles is expected to be lower.
- **In mock mode the personalization test is weak by construction**: mock category scoring ignores the profile, and only relevance uses it. Real Drex receives the profile in its state.
- **Drex is weaker at knowing when *not* to act.** So the agent only labels and ranks. It never auto-deletes, auto-archives or replies.
- **The guardrail has known false blocks**: Layer 1 blocks the benign "statement of accomplishment" because of the subject word "statement". This is deliberate fail-closed behavior, and it is reported in the benchmark.

## Layout

```
config.py            env, mock flag, thresholds, prices, guardrail lists
src/loader.py        mbox -> emails.jsonl (MIME, charsets, HTML->text, attachments)
src/guardrail.py     3-layer fail-closed guardrail, redaction, audit log
src/clients/         drex_client.py, ndi_client.py, claude_client.py (real + mock), mock_engine.py
src/profile.py       onboarding questions -> profile text (Claude)
src/triage.py        Drex triage (one pass, 3 questions), priority
src/jobs.py          NDI extract + Drex job scoring + dedup ranking
src/hybrid.py        escalation rule, offline sweep combiner, summaries
src/baseline.py      Claude-only triage and job scoring (same schema)
src/evaluate.py      metrics, latency, cost, sweep, personas, guardrail bench, charts, summary.md
src/pipeline.py      end-to-end inbox run used by the dashboard
app.py               Streamlit dashboard (5 tabs + live Drex-vs-Claude box)
scripts/             run_all.py, label_emails.py, make_sample_data.py
tests/               offline contract tests for the real Drex/NDI clients
docs/DEMO.md         5-minute demo script
```
