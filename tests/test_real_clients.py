"""Offline contract tests for the REAL Drex/NDI clients using httpx.MockTransport.

Responses mirror the shapes documented at console.nace.ai/docs (2026-11-14). No network, no keys.
Run:  python -m pytest -q tests   (or: python tests/test_real_clients.py)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

config.NACE_API_KEY = config.NDI_API_KEY = "nace_sk_test"
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


def test_drex_triage_and_jobs():
    calls = []
    c = DrexClient()
    c._http = httpx.Client(base_url="https://console.nace.ai", transport=httpx.MockTransport(drex_handler(calls)),
                           headers={"Authorization": "Bearer nace_sk_test"})
    r = c.evaluate("PROFILE:\nx\n\nEMAIL:\ny", TRIAGE_QUESTIONS)
    assert len(calls) == 2, "should retry once after 429"
    row = result_from_drex("e1", r)
    assert row["category"] == "urgent_action_needed" and abs(row["relevance"] - 1.5) < 1e-9
    assert r.input_tokens == 812 and r.model == "drex-max-v1.1"
    j = c.evaluate("PROFILE:\nx\n\nJOB:\ny", JOB_QUESTIONS)
    assert abs(j.answers["has_dealbreaker"].p_yes - 0.2) < 1e-9


def test_drex_422_is_clear_error():
    def h(req):
        return httpx.Response(422, json={"error": {"type": "invalid_request_error", "message": "bad",
                                                   "issues": [{"path": "model", "message": "unknown model"}]},
                                         "request_id": "req_" + "b" * 32})
    c = DrexClient()
    c._http = httpx.Client(base_url="https://x", transport=httpx.MockTransport(h))
    try:
        c.evaluate("s", TRIAGE_QUESTIONS)
    except RuntimeError as e:
        assert "422" in str(e) and "unknown model" in str(e)
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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
