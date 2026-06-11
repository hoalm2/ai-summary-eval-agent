from __future__ import annotations

import html
import json
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from config import get_settings
from pipeline.pdf import extract_pdf_text, validate_pdf_source
from pipeline.persist import SupabaseStore
from pipeline.stage1_skeleton import extract_skeleton
from pipeline.stage1b_align import align_bullets
from pipeline.stage2_summary import generate_summary
from pipeline.stage3_judge import judge_summary


app = FastAPI(title="AI Summary Eval Agent", version="0.1.0")


class ReportTextError(Exception):
    def __init__(self, reason: str, status: str = "extract_failed") -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


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
    settings = get_settings()
    report_text = report.get("report_text") or ""
    if not report_text and report.get("source_pdf_url"):
        try:
            validate_pdf_source(report["source_pdf_url"])
            report_text = extract_pdf_text(report["source_pdf_url"])
        except Exception as exc:
            store.update_report_status(report["id"], "extract_failed")
            raise ReportTextError(f"PDF extraction failed: {exc}") from exc
        store.update_report_text(report["id"], report_text)
    if len(report_text.strip()) < settings.report_text_min_chars:
        status = "extract_too_short" if report.get("source_pdf_url") else "report_text_too_short"
        store.update_report_status(report["id"], status)
        raise ReportTextError(
            f"Report text too short for reliable evaluation: {len(report_text.strip())} chars",
            status=status,
        )
    return report_text


def persist_error_eval(
    *,
    store: SupabaseStore,
    report: dict[str, Any],
    summary_text: str,
    summary_model: str,
    reason: str,
) -> dict[str, Any]:
    summary = store.insert_summary(
        {
            "report_id": report["id"],
            "summary_text": summary_text,
            "summary_model": summary_model,
        }
    )
    judge_json = {"verdict": "ERROR", "rationale": reason}
    saved = store.insert_eval_run(
        report_id=report["id"],
        summary_id=summary["id"],
        skeleton_json={},
        judge_json=judge_json,
        verdict="ERROR",
        blocks=[],
        flags=[],
        bullet_evals=[],
    )
    return {
        "summary": summary,
        "eval_run": saved,
        "result": {"verdict": "ERROR", "blocks": [], "flags": [], "judge_json": judge_json},
    }


def evaluate_record(record: dict[str, Any], store: SupabaseStore | None = None) -> dict[str, Any]:
    store = store or SupabaseStore()
    report = record["report"]
    summary = record["summary"]
    try:
        report_text = ensure_report_text(report, store)
    except ReportTextError as exc:
        saved = store.insert_eval_run(
            report_id=report["id"],
            summary_id=summary["id"],
            skeleton_json={},
            judge_json={"verdict": "ERROR", "rationale": exc.reason},
            verdict="ERROR",
            blocks=[],
            flags=[],
            bullet_evals=[],
        )
        return {
            "eval_run": saved,
            "result": {"verdict": "ERROR", "blocks": [], "flags": [], "judge_json": {"verdict": "ERROR", "rationale": exc.reason}},
        }
    if not report_text:
        result = {
            "verdict": "ERROR",
            "blocks": [],
            "flags": [],
            "judge_json": {"verdict": "ERROR", "rationale": "Missing report_text and no usable source_pdf_url."},
        }
        skeleton: dict[str, Any] = {}
        bullet_evals: list[dict[str, Any]] = []
    else:
        skeleton = extract_skeleton(
            report_text,
            ticker=report.get("ticker"),
            report_date=report.get("report_date"),
        )
        bullet_evals = align_bullets(report_text, summary["summary_text"])
        result = judge_summary(
            report_text=report_text,
            summary_text=summary["summary_text"],
            skeleton_json=skeleton,
            bullet_evals=bullet_evals,
        )
    saved = store.insert_eval_run(
        report_id=report["id"],
        summary_id=summary["id"],
        skeleton_json=skeleton,
        judge_json=result.get("judge_json", result),
        verdict=result["verdict"],
        blocks=result.get("blocks", []),
        flags=result.get("flags", []),
        bullet_evals=bullet_evals,
    )
    return {"eval_run": saved, "result": result}


def generate_and_evaluate_report(report: dict[str, Any], store: SupabaseStore | None = None) -> dict[str, Any]:
    store = store or SupabaseStore()
    try:
        report_text = ensure_report_text(report, store)
    except ReportTextError as exc:
        return persist_error_eval(
            store=store,
            report=report,
            summary_text="",
            summary_model=exc.status,
            reason=exc.reason,
        )

    summary_text = generate_summary(report_text, ticker=report.get("ticker"))
    summary = store.insert_summary(
        {
            "report_id": report["id"],
            "summary_text": summary_text,
            "summary_model": get_settings().model_summary if not get_settings().mock_llm_mode else "mock_llm",
        }
    )
    return evaluate_record({"report": report, "summary": summary}, store)


def generate_and_evaluate_report_safely(report: dict[str, Any], store: SupabaseStore | None = None) -> dict[str, Any]:
    store = store or SupabaseStore()
    try:
        return generate_and_evaluate_report(report, store)
    except Exception as exc:
        reason = f"Unexpected daily evaluation error: {type(exc).__name__}: {exc}"
        try:
            return persist_error_eval(
                store=store,
                report=report,
                summary_text="",
                summary_model="unexpected_error",
                reason=reason,
            )
        except Exception:
            return {
                "result": {
                    "verdict": "ERROR",
                    "blocks": [],
                    "flags": [],
                    "judge_json": {"verdict": "ERROR", "rationale": reason, "persisted": False},
                }
            }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/status")
def status() -> dict[str, Any]:
    settings = get_settings()
    response: dict[str, Any] = {
        "status": "ok",
        "mock_llm_mode": settings.mock_llm_mode,
        "greennode_configured": bool(settings.greennode_api_key),
        "supabase_configured": bool(settings.supabase_url and settings.supabase_service_role_key),
        "daily_batch_size": settings.daily_batch_size,
        "demo_batch_size": settings.demo_batch_size,
        "report_text_min_chars": settings.report_text_min_chars,
    }
    if not response["supabase_configured"]:
        return {**response, "supabase_ok": False}
    try:
        store = SupabaseStore()
        reports_count = store.client.table("reports").select("id", count="exact").limit(1).execute().count
        summaries_count = store.client.table("summaries").select("id", count="exact").limit(1).execute().count
        eval_runs_count = store.client.table("eval_runs").select("id", count="exact").limit(1).execute().count
        response.update(
            {
                "supabase_ok": True,
                "reports_count": reports_count,
                "summaries_count": summaries_count,
                "eval_runs_count": eval_runs_count,
            }
        )
    except Exception as exc:
        response.update({"supabase_ok": False, "supabase_error": str(exc)})
    return response


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
    if len(report_text.strip()) < get_settings().report_text_min_chars:
        raise HTTPException(status_code=400, detail="report_text is too short for reliable evaluation")
    skeleton = extract_skeleton(report_text, ticker=payload.ticker, report_date=payload.report_date)
    bullet_evals = align_bullets(report_text, payload.summary_text)
    result = judge_summary(report_text=report_text, summary_text=payload.summary_text, skeleton_json=skeleton, bullet_evals=bullet_evals)
    if payload.report_id and payload.summary_id:
        SupabaseStore().insert_eval_run(
            report_id=payload.report_id,
            summary_id=payload.summary_id,
            skeleton_json=skeleton,
            judge_json=result.get("judge_json", result),
            verdict=result["verdict"],
            blocks=result.get("blocks", []),
            flags=result.get("flags", []),
            bullet_evals=bullet_evals,
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
    outputs = [generate_and_evaluate_report_safely(report, store) for report in reports]
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
        if item.report_text and len(item.report_text.strip()) < get_settings().report_text_min_chars:
            raise HTTPException(status_code=400, detail="report_text is too short for reliable evaluation")
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
                    "status": item.status if item.report_text else "pending",
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


ISSUE_GROUP_LABELS = {
    "A": "Type A — factual/logic hallucination",
    "B": "Type B — unsupported/fabricated claim",
    "BUY": "BUY — buy price violation",
    "C": "Type C — disclaimer omission",
    "FMT": "FMT — format inconsistency",
    "RENDER": "RENDER — render quality",
    "ERROR": "ERROR — operational issue",
    "OTHER": "OTHER",
}


def issue_group(category: str) -> str:
    if category.startswith("A_"):
        return "A"
    if category.startswith("B_"):
        return "B"
    if category.startswith("buy_price"):
        return "BUY"
    if category == "C_disclaimer_omission":
        return "C"
    if category == "format":
        return "FMT"
    if category == "render":
        return "RENDER"
    if category == "ERROR":
        return "ERROR"
    return "OTHER"


def format_rate(value: Any) -> str:
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return "0%"


def build_daily_trends(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trends: dict[str, dict[str, Any]] = {}
    for run in runs:
        report = run.get("report") or {}
        date_key = str(run.get("created_at") or report.get("report_date") or "unknown")[:10]
        bucket = trends.setdefault(
            date_key,
            {
                "date": date_key,
                "total": 0,
                "pass": 0,
                "fail": 0,
                "pass_with_flag": 0,
                "error": 0,
                "hallucination": 0,
                "buy_violation": 0,
            },
        )
        bucket["total"] += 1
        verdict = run.get("verdict")
        if verdict == "PASS":
            bucket["pass"] += 1
        elif verdict == "FAIL":
            bucket["fail"] += 1
        elif verdict == "PASS-WITH-FLAG":
            bucket["pass_with_flag"] += 1
        elif verdict == "ERROR":
            bucket["error"] += 1
        for issue in (run.get("blocks") or []) + (run.get("flags") or []):
            category = str(issue.get("category", ""))
            if category.startswith(("A_", "B_")):
                bucket["hallucination"] += 1
            if category.startswith("buy_price"):
                bucket["buy_violation"] += 1
    return [trends[key] for key in sorted(trends.keys(), reverse=True)]


def render_bullet_breakdown(
    bullet_evals: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
    flags: list[dict[str, Any]],
) -> str:
    if not bullet_evals:
        return ""
    issues_by_bullet: dict[int, list[dict[str, Any]]] = {}
    for issue in blocks + flags:
        idx = issue.get("bullet_index")
        if idx is not None:
            issues_by_bullet.setdefault(int(idx), []).append(issue)
    rows = []
    for be in bullet_evals:
        idx = be.get("bullet_index", "?")
        bullet_text = html.escape(str(be.get("bullet_text", "")))
        citations = "".join(
            f"<blockquote style='margin:2px 0 4px 8px;padding:2px 8px;border-left:3px solid #cddbd8;font-size:11px;color:#555'>{html.escape(str(c))}</blockquote>"
            for c in (be.get("report_citations") or [])
        ) or "<span style='color:#aaa;font-size:11px'>—</span>"
        bullet_issues = issues_by_bullet.get(int(idx) if isinstance(idx, (int, float)) else -1, [])
        issue_cells = "".join(
            f"<div style='font-size:11px;margin-bottom:3px'>"
            f"<span class='issue-group'>{html.escape(issue_group(str(issue.get('category',''))))}</span> "
            f"<code>{html.escape(str(issue.get('category','')))}</code>: "
            f"{html.escape(str(issue.get('explanation','')))}</div>"
            for issue in bullet_issues
        ) or "<span style='color:#aaa;font-size:11px'>—</span>"
        rows.append(
            f"<tr style='vertical-align:top;border-bottom:1px solid #edf2f1'>"
            f"<td style='padding:5px 8px;font-weight:700;width:24px;color:#0b514b'>{idx}</td>"
            f"<td style='padding:5px 8px;max-width:300px;font-size:12px'>{bullet_text}</td>"
            f"<td style='padding:5px 8px;max-width:260px'>{citations}</td>"
            f"<td style='padding:5px 8px;max-width:220px'>{issue_cells}</td>"
            "</tr>"
        )
    n = len(bullet_evals)
    return (
        f"<details style='margin-top:6px'>"
        f"<summary>Bullet breakdown ({n} bullets)</summary>"
        f"<table style='width:100%;margin-top:6px;border-collapse:collapse;font-size:12px'>"
        f"<thead><tr style='background:#f0f5f4'>"
        f"<th style='padding:4px 8px;text-align:left'>#</th>"
        f"<th style='padding:4px 8px;text-align:left'>Bullet</th>"
        f"<th style='padding:4px 8px;text-align:left'>Report citations</th>"
        f"<th style='padding:4px 8px;text-align:left'>Issues</th>"
        f"</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        f"</table></details>"
    )


def render_dashboard(aggregate: dict[str, Any], runs: list[dict[str, Any]]) -> str:
    group_counts: dict[str, int] = {}
    for category, count in aggregate.get("failure_breakdown", {}).items():
        group = issue_group(str(category))
        group_counts[group] = group_counts.get(group, 0) + int(count)
    group_items = "".join(
        f"<li><strong>{html.escape(ISSUE_GROUP_LABELS.get(group, group))}</strong>: {count}</li>"
        for group, count in sorted(group_counts.items())
    )
    breakdown_items = "".join(
        f"<li><span class='issue-group'>{html.escape(issue_group(str(category)))}</span> <code>{html.escape(category)}</code>: {count}</li>"
        for category, count in sorted(aggregate.get("failure_breakdown", {}).items())
    )
    trend_rows = []
    for trend in build_daily_trends(runs):
        pass_rate = trend["pass"] / trend["total"] if trend["total"] else 0
        hallucination_rate = trend["hallucination"] / trend["total"] if trend["total"] else 0
        trend_rows.append(
            "<tr>"
            f"<td>{html.escape(str(trend['date']))}</td>"
            f"<td>{trend['total']}</td>"
            f"<td>{format_rate(pass_rate)}</td>"
            f"<td>{trend['pass']}</td>"
            f"<td>{trend['fail']}</td>"
            f"<td>{trend['pass_with_flag']}</td>"
            f"<td>{trend['error']}</td>"
            f"<td>{trend['hallucination']} ({format_rate(hallucination_rate)})</td>"
            f"<td>{trend['buy_violation']}</td>"
            "</tr>"
        )
    rows = []
    for run in runs:
        report = run.get("report") or {}
        summary = run.get("summary") or {}
        issues = (run.get("blocks") or []) + (run.get("flags") or [])
        issue_text = "<br>".join(
            (
                f"<span class='issue-group'>{html.escape(issue_group(str(issue.get('category', ''))))}</span> "
                f"<code>{html.escape(str(issue.get('category', '')))}</code>: "
                f"{html.escape(str(issue.get('summary_quote', '')))} — {html.escape(str(issue.get('explanation', '')))}"
            )
            for issue in issues
        ) or "No issues"
        issues_detail = html.escape(json.dumps(issues, ensure_ascii=False, indent=2))
        skeleton_detail = html.escape(json.dumps(run.get("skeleton_json") or {}, ensure_ascii=False, indent=2))
        judge_detail = html.escape(json.dumps(run.get("judge_json") or {}, ensure_ascii=False, indent=2))
        bullet_evals_data = run.get("bullet_evals") or []
        bullet_evals_detail = html.escape(json.dumps(bullet_evals_data, ensure_ascii=False, indent=2))
        bullet_breakdown_html = render_bullet_breakdown(bullet_evals_data, run.get("blocks") or [], run.get("flags") or [])
        summary_text = html.escape(str(summary.get("summary_text", "")))
        ticker = html.escape(str(report.get("ticker", "")))
        report_date = html.escape(str(report.get("report_date", "")))
        verdict = html.escape(str(run.get("verdict", "")))
        rows.append(
            f"<tr data-verdict='{verdict}' data-ticker='{ticker.lower()}' data-date='{report_date.lower()}'>"
            f"<td>{html.escape(str(run.get('created_at', '')))}</td>"
            f"<td>{ticker}</td>"
            f"<td>{report_date}</td>"
            f"<td><span class='badge {verdict.lower()}'>{verdict}</span></td>"
            f"<td><div class='summary'>{summary_text or '<em>No summary saved</em>'}</div></td>"
            f"<td>{issue_text}{bullet_breakdown_html}<details><summary>Inspect JSON</summary><h4>Issues</h4><pre>{issues_detail}</pre><h4>Bullet evals</h4><pre>{bullet_evals_detail}</pre><h4>Skeleton</h4><pre>{skeleton_detail}</pre><h4>Judge</h4><pre>{judge_detail}</pre></details></td>"
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
    .filters {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: end; margin: 20px 0; }}
    .filters label {{ display: grid; gap: 6px; font-weight: 700; }}
    input, select {{ padding: 10px 12px; border: 1px solid #cddbd8; border-radius: 10px; min-width: 180px; }}
    .metric {{ font-size: 32px; font-weight: 750; }}
    .submetric {{ color: #5b716e; font-size: 14px; margin-top: 4px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 16px; overflow: hidden; }}
    th, td {{ padding: 12px 14px; border-bottom: 1px solid #edf2f1; text-align: left; vertical-align: top; }}
    th {{ background: #0b514b; color: white; }}
    code {{ background: #eef6f4; padding: 2px 6px; border-radius: 6px; }}
    .badge {{ display: inline-block; padding: 4px 10px; border-radius: 999px; font-weight: 700; }}
    .pass {{ background: #dff7e8; color: #12612f; }}
    .fail {{ background: #ffe1df; color: #9d1c14; }}
    .pass-with-flag {{ background: #fff3cf; color: #7a5400; }}
    .error {{ background: #eceff3; color: #344054; }}
    .summary {{ max-width: 420px; white-space: pre-wrap; }}
    .issue-group {{ display: inline-block; min-width: 52px; padding: 2px 7px; border-radius: 999px; background: #e8f3f1; color: #0b514b; font-size: 12px; font-weight: 800; text-align: center; }}
    .split {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; margin: 20px 0; }}
    details {{ margin-top: 8px; }}
    summary {{ cursor: pointer; color: #0b514b; font-weight: 700; }}
    pre {{ white-space: pre-wrap; max-width: 720px; background: #f6f8f8; border: 1px solid #e4ecea; border-radius: 12px; padding: 12px; }}
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
    <div class="card"><div>Pass rate</div><div class="metric">{format_rate(aggregate.get("pass_rate"))}</div></div>
    <div class="card"><div>Hallucination</div><div class="metric">{aggregate["hallucination_count"]}</div><div class="submetric">{format_rate(aggregate.get("hallucination_rate"))} of evals</div></div>
    <div class="card"><div>Buy violation</div><div class="metric">{aggregate["buy_violation_count"]}</div></div>
  </section>
  <section class="split">
    <div class="card">
      <h2>Judge taxonomy groups</h2>
      <ul>{group_items or "<li>No issues yet</li>"}</ul>
    </div>
    <div class="card">
      <h2>Failure breakdown</h2>
      <ul>{breakdown_items or "<li>No issues yet</li>"}</ul>
    </div>
  </section>
  <section class="card">
    <h2>Daily trend</h2>
    <table>
      <thead><tr><th>Eval date</th><th>Total</th><th>Pass rate</th><th>PASS</th><th>FAIL</th><th>FLAG</th><th>ERROR</th><th>Hallucination</th><th>Buy</th></tr></thead>
      <tbody>{"".join(trend_rows) or "<tr><td colspan='9'>No daily trend yet.</td></tr>"}</tbody>
    </table>
  </section>
  <h2>Latest eval runs</h2>
  <section class="filters">
    <label>Verdict
      <select id="verdictFilter">
        <option value="">All</option>
        <option value="PASS">PASS</option>
        <option value="FAIL">FAIL</option>
        <option value="PASS-WITH-FLAG">PASS-WITH-FLAG</option>
        <option value="ERROR">ERROR</option>
      </select>
    </label>
    <label>Ticker
      <input id="tickerFilter" placeholder="e.g. VTP">
    </label>
    <label>Report date
      <input id="dateFilter" placeholder="e.g. 2026-06">
    </label>
  </section>
  <table>
    <thead><tr><th>Time</th><th>Ticker</th><th>Report date</th><th>Verdict</th><th>Generated summary</th><th>Issues</th></tr></thead>
    <tbody id="runsBody">{"".join(rows) or "<tr><td colspan='6'>No eval runs yet.</td></tr>"}</tbody>
  </table>
  <script>
    const verdictFilter = document.getElementById('verdictFilter');
    const tickerFilter = document.getElementById('tickerFilter');
    const dateFilter = document.getElementById('dateFilter');
    const rows = Array.from(document.querySelectorAll('#runsBody tr[data-verdict]'));
    function applyFilters() {{
      const verdict = verdictFilter.value;
      const ticker = tickerFilter.value.trim().toLowerCase();
      const date = dateFilter.value.trim().toLowerCase();
      for (const row of rows) {{
        const okVerdict = !verdict || row.dataset.verdict === verdict;
        const okTicker = !ticker || row.dataset.ticker.includes(ticker);
        const okDate = !date || row.dataset.date.includes(date);
        row.style.display = okVerdict && okTicker && okDate ? '' : 'none';
      }}
    }}
    verdictFilter.addEventListener('change', applyFilters);
    tickerFilter.addEventListener('input', applyFilters);
    dateFilter.addEventListener('input', applyFilters);
  </script>
</body>
</html>
"""
