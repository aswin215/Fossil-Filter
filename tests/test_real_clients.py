"""Offline contract tests for the REAL Drex (via Intern's DecisionsApiBackend) and NDI clients, using
httpx.MockTransport. Responses mirror the shapes documented at console.nace.ai/docs and Intern's
src/intern/decisions.py (2026-11-14). No network, no keys.
Run:  python -m pytest -q tests   (or: python tests/test_real_clients.py)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

config.NACE_API_KEY = config.NDI_API_KEY = "nace_sk_test"
from intern.decisions import DecisionsApiBackend, System1Unavailable  # noqa: E402

from src.clients.drex_client import DrexClient  # noqa: E402
from src.clients.ndi_client import NDIClient  # noqa: E402
from src.jobs import JOB_QUESTIONS  # noqa: E402
from src.triage import TRIAGE_QUESTIONS, result_from_drex  # noqa: E402


def drex_handler(calls):
    def h(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if len(calls) == 1:  # first call rate-limited -> client must retry
            return httpx.Response(429, headers={"retry-after-ms": "10"},
                                  json={"error": {"type": "rate_limit_error", "message": "slow down"},
                                        "request_id": "req_" + "0" * 32})
        body = json.loads(req.content)
        assert req.headers["authorization"] == "Bearer nace_sk_test"
        assert req.url.path == "/v1/systemone"
        answers = {}
        for qid, q in body["questions"].items():
            assert q["type"] in ("choice", "score", "noul")
            if q["type"] == "choice":
                labs = list(q["criteria"])
                p = {k: 0.1 / (len(labs) - 1) for k in labs[1:]}
                p[labs[0]] = 0.9
                answers[qid] = {"type": "choice", "choice": labs[0], "confidence": 0.88, "probabilities": p}
            elif q["type"] == "score":
                n = len(q["criteria"])
                probs = {str(i): 1 / n for i in range(n)}
                answers[qid] = {"type": "score", "score": (n - 1) / 2, "legend": q["criteria"],
                                "probabilities": probs, "confidence": 0.5}
            else:
                answers[qid] = {"type": "noul", "noul": 0.2, "confidence": 0.8}
        return httpx.Response(200, json={"model": "drex-max-v1.1", "answers": answers,
                                         "usage": {"input_tokens": 812, "output_tokens": 0},
                                         "evaluation_time_ms": 41.5, "request_id": "req_" + "a" * 32})
    return h


def _client(handler, retry_window_s=30.0):
    backend = DecisionsApiBackend("https://console.nace.ai/v1", SecretStr("nace_sk_test"), ["drex-latest"],
                                  transport=httpx.MockTransport(handler))
    return DrexClient(model="drex-latest", backend=backend, retry_window_s=retry_window_s)


def test_drex_triage_and_jobs():
    calls = []
    c = _client(drex_handler(calls))
    r = c.evaluate("PROFILE:\nx\n\nEMAIL:\ny", TRIAGE_QUESTIONS)
    assert len(calls) == 2, "Intern's backend should retry once after 429"
    row = result_from_drex("e1", r)
    assert row["category"] == "urgent_action_needed" and abs(row["relevance"] - 1.5) < 1e-9
    assert r.input_tokens == 812 and not r.cached
    j = c.evaluate("PROFILE:\nx\n\nJOB:\ny", JOB_QUESTIONS)
    assert abs(j.answers["has_dealbreaker"].p_yes - 0.2) < 1e-9
    assert c.evaluate("PROFILE:\nx\n\nEMAIL:\ny", TRIAGE_QUESTIONS).cached, "Intern's cache answers repeats"
    assert c.usage()["asks"] == 2


def test_drex_422_is_clear_error():
    def h(req):
        return httpx.Response(422, json={"error": {"type": "invalid_request_error", "message": "bad",
                                                   "issues": [{"path": "model", "message": "unknown model"}]},
                                         "request_id": "req_" + "b" * 32})
    c = _client(h, retry_window_s=0.5)
    try:
        c.evaluate("s", TRIAGE_QUESTIONS)
    except System1Unavailable as e:  # Intern stops instead of guessing; the guardrail drops the email
        assert "422" in str(e)
    else:
        raise AssertionError("expected error")


def test_ndi_extract_flow():
    seen = []

    def h(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path))
        if req.url.path == "/v1/documents/upload-grants":
            return httpx.Response(200, json={"workspace_id": "ws_1", "upload_url": "https://up.example/u",
                                             "token": "tok", "expires_at": "x", "max_bytes": 10**8})
        if req.url.host == "up.example":
            assert req.headers["x-upload-token"] == "tok"
            return httpx.Response(200, json={"file_id": "file_9", "workspace_id": "ws_1"})
        if req.url.path == "/v1/documents/extract":
            body = json.loads(req.content)
            assert body["source"] == {"type": "workspace_file", "workspace_id": "ws_1", "file_id": "file_9"}
            assert body["schema"]["properties"]["jobs"]["type"] == "array"
            return httpx.Response(202, json={"job_id": "job_1", "status": "queued"})
        if req.url.path == "/v1/documents/jobs/job_1":
            return httpx.Response(200, json={
                "job_id": "job_1", "status": "succeeded", "result": {
                    "result_type": "extract",
                    "data": {"jobs": [{"title": "ML Intern", "company": "Acme", "location": "Remote",
                                       "seniority": None, "employment_type": None, "tech_stack": ["Python"],
                                       "sponsorship_or_clearance_notes": None, "url": None}]},
                    "fields": [{"path": "/jobs/0", "value": {}, "status": "found", "confidence": 0.93, "citations": []},
                               {"path": "/jobs/0/title", "value": "ML Intern", "status": "found", "confidence": None,
                                "citations": [{"location": {"kind": "page_region", "page": 1, "polygons": []},
                                               "source_text": "ML Intern"}]}]}})
        return httpx.Response(404, json={"error": {"type": "not_found_error", "code": "not_found", "message": "?"}})

    import src.clients.ndi_client as nd
    transport = httpx.MockTransport(h)
    c = NDIClient()
    c._http = httpx.Client(base_url="https://console.nace.ai", transport=transport)
    orig_post = nd.httpx.post
    nd.httpx.post = lambda url, **kw: httpx.Client(transport=transport).post(url, **kw)
    orig_sleep = nd.time.sleep
    nd.time.sleep = lambda s: None
    try:
        r = c.extract_jobs({"id": "e1", "body_text": "ML Intern at Acme", "body_html": "<p>ML Intern</p>"})
    finally:
        nd.httpx.post, nd.time.sleep = orig_post, orig_sleep
    assert len(r.jobs) == 1 and r.jobs[0].fields["company"] == "Acme"
    assert r.jobs[0].grounding["title"]["source_text"] == "ML Intern" and r.jobs[0].confidence == 0.93


def test_deepinfra_judge_validates_json():
    import src.clients.deepinfra_client as di
    from src.baseline import TRIAGE_SCHEMA

    config.DEEPINFRA_API_KEY = "di_test"

    def h(req):
        body = json.loads(req.content)
        assert req.url.path == "/v1/openai/chat/completions" and body["temperature"] == 0
        assert body["response_format"] == {"type": "json_object"}
        content = '<think>hmm</think>```json\n{"category": "Important", "relevance": "high", "action": "reply_today"}\n```'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
                                         "usage": {"prompt_tokens": 500, "completion_tokens": 20,
                                                   "estimated_cost": 0.00007}})
    c = di.DeepInfraClient("Qwen/Qwen3.8-27B")
    c._http = httpx.Client(base_url="https://api.deepinfra.com/v1/openai", transport=httpx.MockTransport(h))
    r = c.judge("sys", "user", TRIAGE_SCHEMA)
    assert r.data == {"category": "important", "relevance": "high", "action": "reply_today"}
    assert r.input_tokens == 500 and r.cost_usd == 0.00007


def test_agent_events_never_keep_content():
    from src.agent import _sanitize

    assert _sanitize({"type": "text_delta", "turn": 1, "text": "Your code is 123456"}) is None
    end = _sanitize({"type": "tool_call_end", "turn": 2, "name": "read", "call_id": "c", "status": "ok",
                     "content": "Subject: bank statement", "duration_ms": 5})
    assert "content" not in end and end["name"] == "read"
    start = _sanitize({"type": "tool_call_start", "turn": 2, "name": "bash", "call_id": "c",
                       "arguments": {"command": "cat emails.jsonl"}})
    assert "arguments" not in start
    assert "final_text" not in _sanitize({"type": "run_end", "reason": "completed", "turns": 3, "usage": {},
                                          "final_text": "DONE: email from bank", "duration_ms": 1})


def test_intern_subprocess_event_parsing(tmp_path):
    """The real Level B path, with a stand-in `intern` that prints Intern-shaped JSON events."""
    import stat

    import src.agent as ag

    fake = tmp_path / "intern"
    events = [
        {"type": "run_start", "session_id": "s1", "model": "deepinfra/Qwen/Qwen3.8-27B", "tools": ["read"], "cwd": "x"},
        {"type": "turn_start", "turn": 1},
        {"type": "tool_call_start", "turn": 1, "name": "read", "call_id": "c1", "arguments": {"path": "emails.jsonl"}},
        {"type": "tool_call_end", "turn": 1, "name": "read", "call_id": "c1", "status": "ok", "content": "SECRET",
         "duration_ms": 3},
        {"type": "decision", "purpose": "supervise", "backend": "decisions-api:drex-latest", "questions": 2, "ok": True,
         "cached": False, "cost_usd": 0.0001, "input_tokens": 900, "duration_ms": 410, "turn": 1,
         "answers": {"stuck": {"noul": 0.1}}},
        {"type": "nudge", "turn": 1, "reason": "stuck", "text": "You re-read the same file"},
        {"type": "tool_guard", "turn": 1, "name": "bash", "call_id": "c2", "layer": "system1", "rule": "network",
         "reason": "curl to an unknown host", "risk": 0.91, "asked": 0.05},
        {"type": "text_delta", "turn": 1, "text": "SECRET"},
        {"type": "run_end", "run_id": "r", "reason": "completed", "turns": 1, "final_text": "DONE SECRET",
         "usage": {"input_tokens": 5000, "output_tokens": 300, "cost_usd": 0.002},
         "system1": {"asks": 1, "cost_usd": 0.0001}, "duration_ms": 1200},
    ]
    lines = "\n".join(json.dumps(e) for e in events)
    fake.write_text(f"#!/bin/sh\ncat <<'EOF'\n{lines}\nEOF\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    config.INTERN_BIN, config.DEEPINFRA_API_KEY = str(fake), "di_test"
    got, wall = ag._run_intern(tmp_path, 3)
    assert "SECRET" not in json.dumps(got) and len(got) == 8
    s = ag.summarize_events(got, wall, 3, simulated=False)
    assert s["nudges"] == 1 and s["guard_blocks"] == 1 and s["system1_asks"] == 1
    assert abs(s["total_cost_usd"] - 0.0021) < 1e-9 and s["reason"] == "completed"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
