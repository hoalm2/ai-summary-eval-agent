from __future__ import annotations

import html
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from config import get_settings
from pipeline.pdf import extract_pdf_text, validate_pdf_source
from pipeline.persist import SupabaseStore
from pipeline.stage1_skeleton import extract_skeleton
from pipeline.stage2_summary import generate_summary
from pipeline.stage3_judge import judge_summary


app = FastAPI(title="AI Summary Eval Agent", version="0.1.0")


class RunOneRequest(BaseModel):
    report_id: str | None = None
    summary_id: str | None = None
    report_text: str | None = None
    summary_text: str
    ticker: str | None = None
    report_date: str | None = None
    pdf_path_or_url: str | None = None


class ReportImportItem(BaseModel):
    ticker: str | None = None
    report_date: str | None = None
    source_pdf_url: str | None = None
    pdf_storage_path: str | None = None
    report_text: str | None = None
    status: str = "ready"


class ReportImportRequest(BaseModel):
    reports: list[ReportImportItem]
    skip_existing: bool = True


def require_demo_token(x_demo_token: str | None) -> None:
    settings = get_settings()
    if not settings.demo_token or x_demo_token != settings.demo_token:
        raise HTTPException(status_code=401, detail="Missing or invalid X-Demo-Token")


def ensure_report_text(report: dict[str, Any], store: SupabaseStore) -> str:
    report_text = report.get("report_text") or ""
    if not report_text and report.get("source_pdf_url"):
        validate_pdf_source(report["source_pdf_url"])
        report_text = extract_pdf_text(report["source_pdf_url"])
        store.update_report_text(report["id"], report_text)
    return report_text


def evaluate_record(record: dict[str, Any], store: SupabaseStore | None = None) -> dict[str, Any]:
    store = store or SupabaseStore()
    report = record["report"]
    summary = record["summary"]
    report_text = ensure_report_text(report, store)
    if not report_text:
        result = {
            "verdict": "ERROR",
            "blocks": [],
            "flags": [],
            "judge_json": {"verdict": "ERROR", "rationale": "Missing report_text and no usable source_pdf_url."},
        }
        skeleton = {}
    else:
        skeleton = extract_skeleton(
            report_text,
            ticker=report.get("ticker"),
            report_date=report.get("report_date"),
        )
        result = judge_summary(
            report_text=report_text,
            summary_text=summary["summary_text"],
            skeleton_json=skeleton,
        )
    saved = store.insert_eval_run(
        report_id=report["id"],
        summary_id=summary["id"],
        skeleton_json=skeleton,
        judge_json=result.get("judge_json", result),
        verdict=result["verdict"],
        blocks=result.get("blocks", []),
        flags=result.get("flags", []),
    )
    return {"eval_run": saved, "result": result}


def generate_and_evaluate_report(report: dict[str, Any], store: SupabaseStore | None = None) -> dict[str, Any]:
    store = store or SupabaseStore()
    report_text = ensure_report_text(report, store)
    if not report_text:
        summary = store.insert_summary(
            {
                "report_id": report["id"],
                "summary_text": "",
                "summary_model": "missing_report_text",
            }
        )
        result = {
            "verdict": "ERROR",
            "blocks": [],
            "flags": [],
            "judge_json": {"verdict": "ERROR", "rationale": "Missing report_text and no usable source_pdf_url."},
        }
        saved = store.insert_eval_run(
            report_id=report["id"],
            summary_id=summary["id"],
            skeleton_json={},
            judge_json=result["judge_json"],
            verdict="ERROR",
            blocks=[],
            flags=[],
        )
        return {"summary": summary, "eval_run": saved, "result": result}

    summary_text = generate_summary(report_text, ticker=report.get("ticker"))
    summary = store.insert_summary(
        {
            "report_id": report["id"],
            "summary_text": summary_text,
            "summary_model": get_settings().model_summary if not get_settings().mock_llm_mode else "mock_llm",
        }
    )
    return evaluate_record({"report": report, "summary": summary}, store)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/results")
def results() -> list[dict[str, Any]]:
    return SupabaseStore().fetch_eval_runs()


@app.post("/run-one")
def run_one(payload: RunOneRequest, x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_demo_token(x_demo_token)
    if payload.pdf_path_or_url:
        validate_pdf_source(payload.pdf_path_or_url)
    report_text = payload.report_text or ""
    if not report_text and payload.pdf_path_or_url:
        report_text = extract_pdf_text(payload.pdf_path_or_url)
    if not report_text:
        raise HTTPException(status_code=400, detail="report_text or allowed pdf_path_or_url is required")
    skeleton = extract_skeleton(report_text, ticker=payload.ticker, report_date=payload.report_date)
    result = judge_summary(report_text=report_text, summary_text=payload.summary_text, skeleton_json=skeleton)
    if payload.report_id and payload.summary_id:
        SupabaseStore().insert_eval_run(
            report_id=payload.report_id,
            summary_id=payload.summary_id,
            skeleton_json=skeleton,
            judge_json=result.get("judge_json", result),
            verdict=result["verdict"],
            blocks=result.get("blocks", []),
            flags=result.get("flags", []),
        )
    return {"skeleton": skeleton, **result}


@app.post("/run-demo")
def run_demo(x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_demo_token(x_demo_token)
    store = SupabaseStore()
    records = store.fetch_demo_summaries(limit=get_settings().demo_batch_size)
    outputs = [evaluate_record(record, store) for record in records]
    return {"processed": len(outputs), "outputs": outputs}


@app.post("/run-daily")
def run_daily(x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_demo_token(x_demo_token)
    settings = get_settings()
    store = SupabaseStore()
    reports = store.fetch_unevaluated_reports(limit=settings.daily_batch_size)
    if not reports:
        return {"processed": 0, "message": "no unevaluated reports"}
    outputs = [generate_and_evaluate_report(report, store) for report in reports]
    store.set_state("last_daily_run", {"processed": len(outputs), "mode": "mock" if settings.mock_llm_mode else "greennode"})
    return {"processed": len(outputs), "outputs": outputs}


@app.post("/reports/import")
def import_reports(payload: ReportImportRequest, x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_demo_token(x_demo_token)
    store = SupabaseStore()
    inserted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in payload.reports:
        if item.source_pdf_url:
            validate_pdf_source(item.source_pdf_url)
        if not item.report_text and not item.source_pdf_url:
            raise HTTPException(status_code=400, detail="Each report needs report_text or source_pdf_url")
        existing = store.find_existing_report(
            ticker=item.ticker,
            report_date=item.report_date,
            source_pdf_url=item.source_pdf_url,
        )
        if existing and payload.skip_existing:
            skipped.append(existing)
            continue
        inserted.append(
            store.insert_report(
                {
                    "ticker": item.ticker,
                    "report_date": item.report_date,
                    "source_pdf_url": item.source_pdf_url,
                    "pdf_storage_path": item.pdf_storage_path,
                    "report_text": item.report_text,
                    "status": item.status,
                }
            )
        )
    return {"inserted": len(inserted), "skipped": len(skipped), "reports": inserted, "skipped_reports": skipped}


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    store = SupabaseStore()
    aggregate = store.aggregate()
    runs = store.fetch_eval_runs(limit=100)
    return render_dashboard(aggregate, runs)


def render_dashboard(aggregate: dict[str, Any], runs: list[dict[str, Any]]) -> str:
    breakdown_items = "".join(
        f"<li><code>{html.escape(category)}</code>: {count}</li>"
        for category, count in sorted(aggregate.get("failure_breakdown", {}).items())
    )
    rows = []
    for run in runs:
        report = run.get("report") or {}
        issues = (run.get("blocks") or []) + (run.get("flags") or [])
        issue_text = "<br>".join(
            html.escape(f"{issue.get('category')}: {issue.get('summary_quote', '')} — {issue.get('explanation', '')}")
            for issue in issues
        ) or "No issues"
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(run.get('created_at', '')))}</td>"
            f"<td>{html.escape(str(report.get('ticker', '')))}</td>"
            f"<td>{html.escape(str(report.get('report_date', '')))}</td>"
            f"<td><span class='badge {html.escape(str(run.get('verdict', '')).lower())}'>{html.escape(str(run.get('verdict', '')))}</span></td>"
            f"<td>{issue_text}</td>"
            "</tr>"
        )
    return f"""
<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Summary Eval Dashboard</title>
  <style>
    body {{ font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #16312f; background: #f7faf9; }}
    h1 {{ margin-bottom: 4px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin: 24px 0; }}
    .card {{ background: white; border: 1px solid #dfe8e5; border-radius: 16px; padding: 18px; box-shadow: 0 8px 24px rgba(22,49,47,.06); }}
    .metric {{ font-size: 32px; font-weight: 750; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 16px; overflow: hidden; }}
    th, td {{ padding: 12px 14px; border-bottom: 1px solid #edf2f1; text-align: left; vertical-align: top; }}
    th {{ background: #0b514b; color: white; }}
    code {{ background: #eef6f4; padding: 2px 6px; border-radius: 6px; }}
    .badge {{ display: inline-block; padding: 4px 10px; border-radius: 999px; font-weight: 700; }}
    .pass {{ background: #dff7e8; color: #12612f; }}
    .fail {{ background: #ffe1df; color: #9d1c14; }}
    .pass-with-flag {{ background: #fff3cf; color: #7a5400; }}
    .error {{ background: #eceff3; color: #344054; }}
  </style>
</head>
<body>
  <h1>AI Summary Eval Dashboard</h1>
  <p>Supabase-backed daily evaluation history. Full report text is intentionally hidden.</p>
  <section class="grid">
    <div class="card"><div>Total</div><div class="metric">{aggregate["total_evaluated"]}</div></div>
    <div class="card"><div>PASS</div><div class="metric">{aggregate["pass_count"]}</div></div>
    <div class="card"><div>FAIL</div><div class="metric">{aggregate["fail_count"]}</div></div>
    <div class="card"><div>PASS-WITH-FLAG</div><div class="metric">{aggregate["pass_with_flag_count"]}</div></div>
    <div class="card"><div>Hallucination</div><div class="metric">{aggregate["hallucination_count"]}</div></div>
    <div class="card"><div>Buy violation</div><div class="metric">{aggregate["buy_violation_count"]}</div></div>
  </section>
  <section class="card">
    <h2>Failure breakdown</h2>
    <ul>{breakdown_items or "<li>No issues yet</li>"}</ul>
  </section>
  <h2>Latest eval runs</h2>
  <table>
    <thead><tr><th>Time</th><th>Ticker</th><th>Report date</th><th>Verdict</th><th>Issues</th></tr></thead>
    <tbody>{"".join(rows) or "<tr><td colspan='5'>No eval runs yet.</td></tr>"}</tbody>
  </table>
</body>
</html>
"""
