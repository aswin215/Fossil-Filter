"""NDI (Nace Document Intelligence) client (real + mock).

Real API, per console.nace.ai/docs/perception (read 2026-11-14):
  * No inline-text source exists, so the alert email is uploaded as an .html file:
      POST /v1/documents/upload-grants {"path", "ttl_seconds"} -> {workspace_id, upload_url, token, ...}
      POST <upload_url> multipart (file + metadata text part), header X-Upload-Token
  * POST /v1/documents/extract?wait_seconds=60 with source {"type":"workspace_file",...}, a JSON-Schema
    subset "schema" (array of jobs -> one call both splits the alert into jobs and extracts fields),
    citations enabled -> 200 with result, or 202 then poll GET /v1/documents/jobs/{job_id}.
  * result.data holds values; result.fields[] carries path/status/confidence/citations (grounding).
UNVERIFIED: the exact field name of the file id in the upload response (we accept several shapes).
"""
from __future__ import annotations

import html as htmllib
import json
import re
import time
from pathlib import Path

import httpx

import config
from src.clients.base import NDIJob, NDIResult

JOB_FIELDS = ["title", "company", "location", "seniority", "employment_type", "tech_stack",
              "sponsorship_or_clearance_notes", "url"]

JOBS_SCHEMA = {
    "type": "object",
    "properties": {
        "jobs": {
            "type": "array",
            "description": "Every individual job posting listed in this job-alert email, in order.",
            "maxItems": 50,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string", "description": "Job title"},
                    "company": {"type": "string"},
                    "location": {"type": "string", "description": "City/state, or Remote"},
                    "seniority": {"type": "string", "description": "e.g. Internship, Entry level, Mid-Senior"},
                    "employment_type": {"type": "string", "description": "e.g. Full-time, Internship, Contract"},
                    "tech_stack": {"type": "array", "items": {"type": "string"}},
                    "sponsorship_or_clearance_notes": {
                        "type": "string",
                        "description": "Any visa sponsorship, citizenship or security clearance requirement"},
                    "url": {"type": "string", "description": "Link to the posting"},
                },
                "required": ["title", "company"],
            },
        }
    },
    "required": ["jobs"],
}
EXTRACT_INSTRUCTIONS = ("This is a LinkedIn job alert email. Split it into individual job postings and extract "
                        "each one. Ignore footer, settings and promotional links.")


class NDIClient:
    name = "ndi"

    def __init__(self):
        config.require_real_keys("NDI_API_KEY")
        self._http = httpx.Client(base_url=config.NDI_BASE_URL, timeout=90.0,
                                  headers={"Authorization": f"Bearer {config.NDI_API_KEY}"})

    def _err(self, r: httpx.Response) -> RuntimeError:
        try:
            e = r.json().get("error", {})
        except json.JSONDecodeError:
            e = {"message": r.text[:200]}
        return RuntimeError(f"NDI {r.status_code} {e.get('type')}/{e.get('code')}: {e.get('message')}")

    def _upload(self, name: str, content: bytes, content_type: str) -> dict:
        path = f"fossil-filter/{name}"
        g = self._http.post("/v1/documents/upload-grants", json={"path": path, "ttl_seconds": 600})
        if g.status_code >= 300:
            raise self._err(g)
        grant = g.json()
        up = httpx.post(grant["upload_url"], headers={"X-Upload-Token": grant["token"]}, timeout=90.0,
                        files={"file": (name, content, content_type)},
                        data={"metadata": json.dumps({"path": path, "on_conflict": "new_version"})})
        if up.status_code >= 300:
            raise self._err(up)
        info = up.json()
        # Verified live (2026-11-14): the upload answers with a `file_upload` JOB; the file is at
        # result.file.file_id. Poll the job if it has not finished yet.
        deadline = time.time() + 60
        while info.get("job_id") and info.get("status") not in ("succeeded", "failed", "cancelled"):
            if time.time() > deadline:
                raise TimeoutError(f"NDI upload job {info['job_id']} still {info.get('status')}")
            time.sleep(1.0)
            j = self._http.get(f"/v1/documents/jobs/{info['job_id']}")
            if j.status_code != 200:
                raise self._err(j)
            info = j.json()
        if info.get("status") in ("failed", "cancelled"):
            raise RuntimeError(f"NDI upload job {info.get('status')}: {info.get('result_error') or info.get('error')}")
        f = ((info.get("result") or {}).get("file") or info.get("file") or {})
        file_id = f.get("file_id") or info.get("file_id") or f.get("id")
        if not file_id:
            raise RuntimeError(f"NDI upload: could not find file id in response keys {list(info)}")
        return {"type": "workspace_file",
                "workspace_id": f.get("workspace_id") or info.get("workspace_id") or grant["workspace_id"],
                "file_id": file_id}

    def _run(self, tool: str, body: dict) -> dict:
        r = self._http.post(f"/v1/documents/{tool}", params={"wait_seconds": 60}, json=body)
        if r.status_code not in (200, 202):
            raise self._err(r)
        job = r.json()
        deadline = time.time() + 180
        delay = 1.0
        while job.get("status") not in ("succeeded", "failed", "cancelled"):
            if time.time() > deadline:
                raise TimeoutError(f"NDI job {job.get('job_id')} still {job.get('status')}")
            time.sleep(delay)
            delay = min(delay * 2, 10)
            g = self._http.get(f"/v1/documents/jobs/{job['job_id']}")
            if g.status_code != 200:
                raise self._err(g)
            job = g.json()
        if job["status"] != "succeeded":
            raise RuntimeError(f"NDI job {job.get('job_id')} {job['status']}: {job.get('error')}")
        return job

    def extract_jobs(self, email: dict) -> NDIResult:
        t0 = time.perf_counter()
        content = (email.get("body_html") or f"<pre>{htmllib.escape(email['body_text'])}</pre>").encode()
        source = self._upload(f"{re.sub(r'[^A-Za-z0-9_-]', '_', email['id'])}.html", content, "text/html")
        job = self._run("extract", {"source": source, "schema": JOBS_SCHEMA, "instructions": EXTRACT_INSTRUCTIONS,
                                    "citations": {"enabled": True, "include_source_text": True},
                                    "parse_mode": "low"})
        res = job["result"]
        items = (res.get("data") or {}).get("jobs") or []
        by_path: dict[str, dict] = {f["path"]: f for f in res.get("fields", [])}
        jobs = []
        for i, item in enumerate(items):
            grounding = {}
            for fld in JOB_FIELDS:
                f = by_path.get(f"/jobs/{i}/{fld}")
                if f and f.get("citations"):
                    c = f["citations"][0]
                    grounding[fld] = {"location": c.get("location"), "source_text": c.get("source_text")}
            whole = by_path.get(f"/jobs/{i}") or {}
            if not grounding and whole.get("citations"):  # live API cites the whole job record (verified)
                c = whole["citations"][0]
                grounding["job"] = {"location": c.get("location"), "source_text": c.get("source_text")}
            jobs.append(NDIJob({k: item.get(k) for k in JOB_FIELDS}, grounding, whole.get("confidence")))
        # Verified live: usage.lines[*] {unit: "page", quantity}, and cost.total_usd is the billed amount.
        lines = (job.get("usage") or {}).get("lines") or []
        pages = float(max([ln.get("quantity", 0) for ln in lines if ln.get("unit") == "page"] or [1]))
        cost = float(((job.get("cost") or {}).get("total_usd")) or 0.0)
        return NDIResult(jobs, pages, time.perf_counter() - t0, job.get("job_id"), cost_usd=cost)

    def parse_file(self, path: str | Path) -> str:
        """Parse an attachment (PDF job description, resume) into markdown."""
        p = Path(path)
        source = self._upload(p.name, p.read_bytes(), "application/octet-stream")
        job = self._run("parse", {"source": source, "output": {"formats": ["markdown"]}})
        return job["result"]["document"].get("markdown") or ""


class MockNDIClient:
    """Parses the alert HTML locally (job cards) and reports text-span grounding like NDI would."""
    name = "ndi"

    CARD = re.compile(r'<div class="job-card">(.*?)</div>\s*<!--/job-->', re.S)

    @staticmethod
    def _txt(s: str) -> str:
        return htmllib.unescape(re.sub(r"<[^>]+>", " ", s)).strip()

    def extract_jobs(self, email: dict) -> NDIResult:
        t0 = time.perf_counter()
        html = email.get("body_html") or ""
        body = email.get("body_text") or ""
        jobs = []
        for card in self.CARD.findall(html):
            get = lambda cls: (m.group(1) if (m := re.search(rf'class="{cls}"[^>]*>(.*?)</', card, re.S)) else "")
            url_m = re.search(r'href="([^"]+)"', card)
            f = {
                "title": self._txt(get("title")),
                "company": self._txt(get("company")),
                "location": self._txt(get("location")),
                "seniority": self._txt(get("seniority")),
                "employment_type": self._txt(get("type")),
                "tech_stack": [s.strip() for s in self._txt(get("skills")).split(",") if s.strip()],
                "sponsorship_or_clearance_notes": self._txt(get("notes")) or None,
                "url": url_m.group(1) if url_m else None,
            }
            grounding = {}
            for k in ("title", "company", "sponsorship_or_clearance_notes"):
                v = f.get(k)
                if v and (pos := body.find(v)) >= 0:
                    grounding[k] = {"location": {"kind": "text_span", "char_start": pos, "char_end": pos + len(v)},
                                    "source_text": v}
            jobs.append(NDIJob(f, grounding, 0.95))
        return NDIResult(jobs, 1.0, 0.9 + (time.perf_counter() - t0))

    def parse_file(self, path: str | Path) -> str:
        p = Path(path)
        if p.suffix.lower() in (".txt", ".md", ".html"):
            return p.read_text(errors="ignore")
        return ""
