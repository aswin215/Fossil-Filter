# Inbox Predator - evaluation summary

Built with Claude Code; **Intern runs inside the product**: every Drex call goes through Intern's `intern.decisions` client (Level A), and the Intern agent row is an Intern session with System 2 = DeepInfra Qwen and System 1 = Drex (Level B).

Mode: **MOCK CLIENTS (numbers are placeholders, not evidence)** · data: `sample` · Drex model: `mock-drex` · Claude model: `mock-claude` · DeepInfra model: `mock-qwen` · profile: `student_jobhunter` · labeled emails: 94 · escalation threshold: 0.7

> Drex price is a PLACEHOLDER ($0.2/M input tokens). Set `DREX_PRICE_PER_MTOK_INPUT` in .env from the Nace pricing page before quoting cost multiples.

## Headline: Claude-only vs DeepInfra Qwen vs Drex vs Hybrid vs Intern agent (email triage)

| System | Category acc | Category macro-F1 | Urgent recall | Action acc | p50 s | p95 s | emails/s | $ / 1k emails | Escalated |
|---|---|---|---|---|---|---|---|---|---|
| Claude-only | 0.840 | 0.838 | 0.643 | 0.617 | 2.65 | 3.26 | 0.39 | 3.5062 | - |
| DeepInfra Qwen | 0.872 | 0.869 | 0.714 | 0.553 | 1.39 | 1.95 | 0.71 | 0.0818 | - |
| Drex (Level A) | 0.819 | 0.818 | 0.643 | 0.511 | 0.20 | 0.25 | 5.25 | 0.1314 | - |
| Hybrid | 0.840 | 0.838 | 0.643 | 0.617 | 2.41 | 3.42 | 0.51 | 2.5142 | 0.68 |
| Intern agent (Level B) | 0.872 | 0.869 | 0.714 | 0.553 | 1.08 | 1.08 | 0.93 | 0.0677 | - |

| Comparison vs Claude-only | Speedup (p50, x) | Cost multiple (Claude / X) | Category acc delta |
|---|---|---|---|
| DeepInfra Qwen | 1.9x | 42.9x | +0.032 |
| Drex (Level A) | 13.3x | 26.7x | -0.021 |
| Hybrid | 1.1x | 1.4x | +0.000 |
| Intern agent (Level B) | 2.5x | 51.8x | +0.032 |

Intern agent latency and cost are the whole session (wall clock, System 2 + System 1) divided by emails handled, so p50 = p95.

## Intern inside the product

**Level A: Drex through `intern.decisions.DecisionClient`.** 473 asks answered (68 from Intern's cache, 0 failed attempts), backend `mock:mock-drex`.

**Level B: Intern agent session** (SIMULATED in mock mode): System 2 `mock-qwen (simulated)`, System 1 `mock-drex (simulated)`. 94/94 emails labeled (100% coverage), 15 turns, 14 tool calls, stop reason `completed`.

| System 1 asks | by purpose | p50 ms | nudges | guard blocks | compactions | System 2 tokens in/out | System 2 $ | System 1 $ | wall s |
|---|---|---|---|---|---|---|---|---|---|
| 21 | {'supervise': 14, 'guard': 7} | 556 | 1 | 0 | 0 | 48606/3760 | 0.0064 | 0.0000 | 101.2 |


![latency and cost](latency_cost.png)

## Hybrid threshold sweep

| threshold | category acc | action acc | urgent recall | $ / 1k | escalated | p50 s |
|---|---|---|---|---|---|---|
| 0.30 | 0.798 | 0.553 | 0.571 | 0.5766 | 0.13 | 0.21 |
| 0.35 | 0.830 | 0.564 | 0.571 | 0.8370 | 0.20 | 0.21 |
| 0.40 | 0.851 | 0.606 | 0.571 | 1.2107 | 0.31 | 0.22 |
| 0.45 | 0.851 | 0.617 | 0.571 | 1.5119 | 0.39 | 0.23 |
| 0.50 | 0.830 | 0.606 | 0.571 | 1.7019 | 0.45 | 0.24 |
| 0.55 | 0.840 | 0.628 | 0.643 | 1.9305 | 0.51 | 1.75 |
| 0.60 | 0.840 | 0.617 | 0.643 | 2.0393 | 0.54 | 1.96 |
| 0.65 | 0.840 | 0.617 | 0.643 | 2.4035 | 0.65 | 2.34 |
| 0.70 | 0.840 | 0.617 | 0.643 | 2.5142 | 0.68 | 2.41 |
| 0.75 | 0.840 | 0.617 | 0.643 | 2.6419 | 0.71 | 2.45 |
| 0.80 | 0.840 | 0.617 | 0.643 | 2.7154 | 0.73 | 2.51 |
| 0.85 | 0.840 | 0.617 | 0.643 | 2.9354 | 0.80 | 2.67 |
| 0.90 | 0.840 | 0.617 | 0.643 | 3.1534 | 0.86 | 2.72 |
| 0.95 | 0.840 | 0.617 | 0.643 | 3.4182 | 0.94 | 2.81 |

![threshold sweep](threshold_sweep.png)

## Job ranking (LinkedIn alerts -> NDI extract -> score)

NDI: 9 alerts -> 41 jobs, p50 0.90s per alert, est. cost $0.1080.

| System | P@5 | P@10 | NDCG@10 | p50 s / job | gold-matched jobs |
|---|---|---|---|---|---|
| Claude-only | 0.60 | 0.60 | 0.748 | 2.49 | 39/39 |
| Drex (Level A) | 0.40 | 0.50 | 0.622 | 0.18 | 39/39 |
| Hybrid | 0.60 | 0.70 | 0.748 | - | 39/39 |

Cost per 1k jobs scored: Drex $0.0854, Claude $2.7342. Hybrid escalated 66%.

## Sensitive-data guardrail benchmark

60 synthetic sensitive + 60 benign emails (includes adversarial cases).

| Configuration | Recall (sensitive) | False-block rate | Missed by class |
|---|---|---|---|
| layer1_only | 0.700 | 0.100 | {'medical': 12, 'tax_or_government_id': 6} |
| drex_only | 1.000 | 0.100 | - |
| claude_only | 1.000 | 0.100 | - |
| layer1+drex | 1.000 | 0.100 | - |
| layer1+claude | 1.000 | 0.100 | - |

Semantic check latency p50/p95: Drex 0.16/0.25s vs Claude 2.64/3.30s. Cost per 1k checks: Drex $0.0317 vs Claude $2.0642.

## Personalization test (same emails, 3 profiles, Drex)

95 emails. labels.csv reflects the student_jobhunter persona; lower agreement for other personas is expected.

| Profile | Category agreement with labels | Share urgent/important | Job alerts in top 10 |
|---|---|---|---|
| student_jobhunter | 0.819 | 0.42 | 0 |
| founder | 0.840 | 0.41 | 0 |
| parent | 0.830 | 0.40 | 0 |

| Pair | Spearman of priority ranks | Top-10 overlap |
|---|---|---|
| student_jobhunter vs founder | 0.873 | 8/10 |
| student_jobhunter vs parent | 0.906 | 7/10 |
| founder vs parent | 0.860 | 7/10 |

## Notes

- Latency is per-call wall clock including network; throughput is single-stream (1 / mean latency).
- Claude baseline uses structured JSON output with low effort (current Claude models do not accept `temperature`).
- Costs come from API-reported cost when the API returns one, else token counts x prices in `config.py`.
- DeepInfra Qwen is scored with JSON mode at temperature 0 and validated against the same enums.
