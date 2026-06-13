from __future__ import annotations

import html
import json
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from config import get_settings
from pipeline.pdf import extract_pdf_text, validate_pdf_source
from pipeline.import_payload import build_report_payload, extract_items, get_summary_model, get_summary_text
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


class ReportImportRequest(BaseModel):
    reports: list[dict[str, Any]] | None = None
    items: list[dict[str, Any]] | None = None
    skip_existing: bool = True
    attach_missing_summaries: bool = False


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


def evaluate_record_safely(record: dict[str, Any], store: SupabaseStore | None = None) -> dict[str, Any]:
    store = store or SupabaseStore()
    try:
        return evaluate_record(record, store)
    except Exception as exc:
        report = record.get("report") or {}
        summary = record.get("summary") or {}
        reason = f"Unexpected daily evaluation error: {type(exc).__name__}: {exc}"
        try:
            saved = store.insert_eval_run(
                report_id=report["id"],
                summary_id=summary["id"],
                skeleton_json={},
                judge_json={"verdict": "ERROR", "rationale": reason},
                verdict="ERROR",
                blocks=[],
                flags=[],
                bullet_evals=[],
            )
            return {
                "eval_run": saved,
                "result": {"verdict": "ERROR", "blocks": [], "flags": [], "judge_json": {"verdict": "ERROR", "rationale": reason}},
            }
        except Exception:
            return {
                "result": {
                    "verdict": "ERROR",
                    "blocks": [],
                    "flags": [],
                    "judge_json": {"verdict": "ERROR", "rationale": reason, "persisted": False},
                }
            }


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


@app.get("/pipeline/status")
def pipeline_status() -> dict[str, Any]:
    store = SupabaseStore()
    enabled = store.get_state("pipeline_enabled", True)
    last_run = store.get_state("last_daily_run")
    return {"pipeline_enabled": enabled is not False, "last_daily_run": last_run}


@app.post("/pipeline/enable")
def pipeline_enable(x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_demo_token(x_demo_token)
    SupabaseStore().set_state("pipeline_enabled", True)
    return {"pipeline_enabled": True}


@app.post("/pipeline/disable")
def pipeline_disable(x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_demo_token(x_demo_token)
    SupabaseStore().set_state("pipeline_enabled", False)
    return {"pipeline_enabled": False}


@app.post("/run-daily")
def run_daily(x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_demo_token(x_demo_token)
    settings = get_settings()
    store = SupabaseStore()
    if store.get_state("pipeline_enabled", True) is False:
        return {"processed": 0, "status": "disabled", "message": "pipeline is disabled — POST /pipeline/enable to re-enable"}
    records = store.fetch_unevaluated_summaries(limit=settings.daily_batch_size, summary_model="precreated")
    if not records:
        return {"processed": 0, "message": "no unevaluated summaries"}
    outputs = [evaluate_record_safely(record, store) for record in records]
    store.set_state(
        "last_daily_run",
        {
            "processed": len(outputs),
            "mode": "mock" if settings.mock_llm_mode else "greennode",
            "summary_source": "precreated",
        },
    )
    return {"processed": len(outputs), "outputs": outputs}


@app.post("/reports/import")
def import_reports(payload: ReportImportRequest, x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_demo_token(x_demo_token)
    store = SupabaseStore()
    inserted: list[dict[str, Any]] = []
    inserted_summaries: list[dict[str, Any]] = []
    summaries_missing = 0
    skipped: list[dict[str, Any]] = []
    items = extract_items(payload.model_dump(exclude_none=True))
    if not items:
        raise HTTPException(status_code=400, detail="Request needs reports[] or items[]")
    for item in items:
        report_payload = build_report_payload(item)
        source_pdf_url = report_payload["source_pdf_url"]
        report_text = report_payload["report_text"]
        if source_pdf_url:
            validate_pdf_source(source_pdf_url)
        if not report_text and not source_pdf_url:
            raise HTTPException(status_code=400, detail="Each report needs report_text or source_pdf_url")
        if report_text and len(report_text.strip()) < get_settings().report_text_min_chars:
            raise HTTPException(status_code=400, detail="report_text is too short for reliable evaluation")
        existing = store.find_existing_report(
            ticker=report_payload["ticker"],
            report_date=report_payload["report_date"],
            source_pdf_url=source_pdf_url,
        )
        if existing and payload.skip_existing:
            summary_text = get_summary_text(item)
            if payload.attach_missing_summaries and summary_text and not store.find_summary_for_report(existing["id"]):
                inserted_summaries.append(
                    store.insert_summary(
                        {
                            "report_id": existing["id"],
                            "summary_text": summary_text,
                            "summary_model": get_summary_model(item),
                        }
                    )
                )
            elif payload.attach_missing_summaries and not summary_text:
                summaries_missing += 1
            skipped.append(existing)
            continue
        report = store.insert_report(report_payload)
        inserted.append(report)
        summary_text = get_summary_text(item)
        if summary_text:
            inserted_summaries.append(
                store.insert_summary(
                    {
                        "report_id": report["id"],
                        "summary_text": summary_text,
                        "summary_model": get_summary_model(item),
                    }
                )
            )
        else:
            summaries_missing += 1
    return {
        "inserted": len(inserted),
        "summaries_inserted": len(inserted_summaries),
        "summaries_missing": summaries_missing,
        "skipped": len(skipped),
        "reports": inserted,
        "summaries": inserted_summaries,
        "skipped_reports": skipped,
    }


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(source: str = "real") -> str:
    store = SupabaseStore()
    all_runs = store.fetch_eval_runs(limit=100)
    runs = all_runs if source == "all" else [run for run in all_runs if (run.get("summary") or {}).get("summary_model") == "precreated"]
    aggregate = aggregate_runs(runs)
    return render_dashboard(aggregate, runs, data_source=source)


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

SUGGESTED_FIXES: dict[str, str] = {
    "A": "Review <code>prompts/judge.md</code> — tighten factual accuracy check for numbers and dates (Stage 3b)",
    "B": "Review <code>prompts/judge.md</code> — add explicit 'only cite text present in report' constraint (Stage 3b)",
    "BUY": "Review <code>prompts/judge.md</code> — strengthen buy price prohibition rule (Stage 3b)",
    "C": "Review <code>prompts/stage1_skeleton.md</code> — ensure disclaimers are extracted in skeleton (Stage 1)",
    "FMT": "Review <code>prompts/stage2_summary.md</code> — tighten bullet format constraints (Stage 2)",
    "RENDER": "Check summary rendering pipeline for encoding or whitespace issues",
    "ERROR": "Check pipeline logs — likely PDF extraction or judge output parsing failure",
    "OTHER": "Review issue category and update eval checklist taxonomy",
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


def aggregate_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(runs)
    verdict_counts = {"PASS": 0, "FAIL": 0, "PASS-WITH-FLAG": 0, "ERROR": 0}
    failure_breakdown: dict[str, int] = {}
    hallucination_count = 0
    buy_violation_count = 0
    for run in runs:
        verdict = run.get("verdict", "ERROR")
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        for issue in (run.get("blocks") or []) + (run.get("flags") or []):
            category = issue.get("category", "unknown")
            failure_breakdown[category] = failure_breakdown.get(category, 0) + 1
            if str(category).startswith(("A_", "B_")):
                hallucination_count += 1
            if str(category).startswith("buy_price"):
                buy_violation_count += 1
    return {
        "total_evaluated": total,
        "pass_count": verdict_counts.get("PASS", 0),
        "fail_count": verdict_counts.get("FAIL", 0),
        "pass_with_flag_count": verdict_counts.get("PASS-WITH-FLAG", 0),
        "error_count": verdict_counts.get("ERROR", 0),
        "pass_rate": verdict_counts.get("PASS", 0) / total if total else 0,
        "hallucination_count": hallucination_count,
        "hallucination_rate": hallucination_count / total if total else 0,
        "buy_violation_count": buy_violation_count,
        "failure_breakdown": failure_breakdown,
    }


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


def render_sparkline(trends: list[dict[str, Any]], width: int = 80, height: int = 24) -> str:
    recent = sorted(trends, key=lambda x: x["date"])[-7:]
    if len(recent) < 2:
        return ""
    pts = []
    for i, t in enumerate(recent):
        rate = t["pass"] / t["total"] if t["total"] else 0
        x = int(i * (width - 1) / max(len(recent) - 1, 1))
        y = int((1 - rate) * (height - 2)) + 1
        pts.append(f"{x},{y}")
    threshold_y = int((1 - 0.85) * (height - 2)) + 1
    pts_str = " ".join(pts)
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'style="display:block;margin-top:4px" aria-hidden="true">'
        f'<line x1="0" y1="{threshold_y}" x2="{width}" y2="{threshold_y}" '
        f'stroke="#e8b400" stroke-width="1" stroke-dasharray="3,2" opacity="0.8"/>'
        f'<polyline points="{pts_str}" fill="none" stroke="currentColor" stroke-width="1.5" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'</svg>'
    )


def render_run_detail(run: dict[str, Any], idx: int) -> str:
    report = run.get("report") or {}
    summary = run.get("summary") or {}
    issues = (run.get("blocks") or []) + (run.get("flags") or [])
    ticker = html.escape(str(report.get("ticker", "")))
    report_date = html.escape(str(report.get("report_date", "")))
    verdict = str(run.get("verdict", ""))
    verdict_esc = html.escape(verdict)
    summary_text = html.escape(str(summary.get("summary_text", "")))
    summary_model = html.escape(str(summary.get("summary_model", "")))
    created_at = html.escape(str(run.get("created_at", ""))[:19])
    issue_html = "".join(
        f"<div class='issue-row'>"
        f"<span class='issue-group'>{html.escape(issue_group(str(i.get('category', ''))))}</span> "
        f"<code>{html.escape(str(i.get('category', '')))}</code>: "
        f"{html.escape(str(i.get('summary_quote', '')))} — {html.escape(str(i.get('explanation', '')))}"
        f"</div>"
        for i in issues
    ) or "<em style='color:#aaa;font-size:12px'>No issues</em>"
    bullet_evals_data = run.get("bullet_evals") or []
    bullet_html = render_bullet_breakdown(bullet_evals_data, run.get("blocks") or [], run.get("flags") or [])
    issues_json = html.escape(json.dumps(issues, ensure_ascii=False, indent=2))
    skeleton_json = html.escape(json.dumps(run.get("skeleton_json") or {}, ensure_ascii=False, indent=2))
    judge_json = html.escape(json.dumps(run.get("judge_json") or {}, ensure_ascii=False, indent=2))
    bullet_json = html.escape(json.dumps(bullet_evals_data, ensure_ascii=False, indent=2))
    return (
        f"<div class='detail-pane' id='detail-{idx}'>"
        f"<div class='detail-header'>"
        f"<span class='detail-ticker'>{ticker or '—'}</span>"
        f"<span class='badge {verdict.lower()}'>{verdict_esc}</span>"
        f"<span class='detail-date'>{report_date}</span>"
        f"<span class='detail-time'>{created_at}</span>"
        f"</div>"
        f"<div class='detail-model'>Source: <code>{summary_model or 'unknown'}</code></div>"
        f"<h4>Issues</h4>{issue_html}"
        f"{bullet_html}"
        f"<h4>Summary text</h4>"
        f"<div class='summary'>{summary_text or '<em>No summary saved</em>'}</div>"
        f"<details><summary>Inspect JSON</summary>"
        f"<h4>Issues</h4><pre>{issues_json}</pre>"
        f"<h4>Bullet evals</h4><pre>{bullet_json}</pre>"
        f"<h4>Skeleton</h4><pre>{skeleton_json}</pre>"
        f"<h4>Judge</h4><pre>{judge_json}</pre>"
        f"</details></div>"
    )


def render_demo_example(run: dict[str, Any] | None, label: str) -> str:
    if not run:
        return (
            f"<div class='card'><h3>{html.escape(label)}</h3>"
            f"<p class='muted'>No {html.escape(label.lower())} available in current batch.</p></div>"
        )
    report = run.get("report") or {}
    summary = run.get("summary") or {}
    issues = (run.get("blocks") or []) + (run.get("flags") or [])
    ticker = html.escape(str(report.get("ticker", "")))
    report_date = html.escape(str(report.get("report_date", "")))
    verdict = str(run.get("verdict", ""))
    verdict_esc = html.escape(verdict)
    summary_text = html.escape(str(summary.get("summary_text", "")))
    issue_html = "".join(
        f"<div class='issue-row'>"
        f"<span class='issue-group'>{html.escape(issue_group(str(i.get('category', ''))))}</span> "
        f"<code>{html.escape(str(i.get('category', '')))}</code>: "
        f"{html.escape(str(i.get('summary_quote', '')))} — {html.escape(str(i.get('explanation', '')))}"
        f"</div>"
        for i in issues
    ) or "<em class='muted'>No issues</em>"
    bullet_evals_data = run.get("bullet_evals") or []
    bullet_html = render_bullet_breakdown(bullet_evals_data, run.get("blocks") or [], run.get("flags") or [])
    skeleton_json = html.escape(json.dumps(run.get("skeleton_json") or {}, ensure_ascii=False, indent=2))
    judge_json = html.escape(json.dumps(run.get("judge_json") or {}, ensure_ascii=False, indent=2))
    bullet_json = html.escape(json.dumps(bullet_evals_data, ensure_ascii=False, indent=2))
    return (
        f"<div class='card'>"
        f"<div class='detail-header'>"
        f"<span class='detail-ticker'>{ticker}</span>"
        f"<span class='badge {verdict.lower()}'>{verdict_esc}</span>"
        f"<span class='detail-date'>{report_date}</span>"
        f"</div>"
        f"<h4>Issues</h4>{issue_html}"
        f"{bullet_html}"
        f"<h4>Summary text</h4><div class='summary'>{summary_text or '<em>No summary saved</em>'}</div>"
        f"<details open><summary>Skeleton JSON (Stage 1)</summary><pre>{skeleton_json}</pre></details>"
        f"<details open><summary>Bullet citations (Stage 1b)</summary><pre>{bullet_json}</pre></details>"
        f"<details open><summary>Judge rationale (Stage 3b)</summary><pre>{judge_json}</pre></details>"
        f"</div>"
    )


def render_dashboard(aggregate: dict[str, Any], runs: list[dict[str, Any]], *, data_source: str = "real") -> str:
    # Zone 0: safety banner
    has_safety = aggregate["hallucination_count"] > 0 or aggregate["buy_violation_count"] > 0
    banner_html = (
        "<div class='banner banner-red'>⚠ Safety violations detected in this batch — do not publish until reviewed</div>"
        if has_safety else
        "<div class='banner banner-green'>✓ No safety violations this batch</div>"
    )

    # Zone 0: metrics
    total = aggregate["total_evaluated"]
    error_count = aggregate.get("error_count", 0)
    pass_rate = aggregate.get("pass_rate", 0)
    hallucination_rate = aggregate.get("hallucination_rate", 0)
    buy_violations = aggregate["buy_violation_count"]
    creation_success_rate = (total - error_count) / total if total else 0
    pass_ok = pass_rate >= 0.85
    hall_ok = hallucination_rate <= 0.02
    buy_ok = buy_violations == 0
    creation_ok = creation_success_rate >= 0.98

    trends = build_daily_trends(runs)
    sparkline = render_sparkline(trends)

    def metric_card(label: str, value: str, target_label: str, ok: bool, extra: str = "") -> str:
        color = "#12612f" if ok else "#9d1c14"
        bg = "#dff7e8" if ok else "#ffe1df"
        return (
            f"<div class='metric-card' style='border-top:3px solid {color}'>"
            f"<div class='metric-label'>{label}</div>"
            f"<div class='metric-value' style='color:{color}'>{value}</div>"
            f"<div class='metric-target' style='background:{bg};color:{color}'>{target_label}</div>"
            f"{extra}</div>"
        )

    pass_card = metric_card("Pass rate", format_rate(pass_rate), "target ≥ 85%", pass_ok, extra=sparkline)
    hall_card = metric_card("Hallucination rate", format_rate(hallucination_rate), "target ≤ 2%", hall_ok)
    buy_card = metric_card("Buy violations", str(buy_violations), "target = 0", buy_ok)
    creation_card = metric_card("Creation success", format_rate(creation_success_rate), "target ≥ 98%", creation_ok)

    # Zone 1: failure patterns
    group_counts: dict[str, int] = {}
    for category, count in aggregate.get("failure_breakdown", {}).items():
        g = issue_group(str(category))
        group_counts[g] = group_counts.get(g, 0) + int(count)
    total_issues = sum(group_counts.values())
    failure_pattern_rows = ""
    for rank, (group, count) in enumerate(sorted(group_counts.items(), key=lambda x: -x[1]), 1):
        pct = count / total_issues * 100 if total_issues else 0
        is_systemic = pct > 30
        fix = SUGGESTED_FIXES.get(group, SUGGESTED_FIXES["OTHER"])
        systemic_badge = "<span class='systemic-badge'>⚠ systemic &gt;30%</span>" if is_systemic else ""
        extra_class = " pattern-systemic" if is_systemic else ""
        failure_pattern_rows += (
            f"<div class='pattern-row{extra_class}'>"
            f"<div class='pattern-rank'>{rank}</div>"
            f"<div class='pattern-body'>"
            f"<div class='pattern-name'>"
            f"<span class='issue-group'>{html.escape(group)}</span> "
            f"<strong>{html.escape(ISSUE_GROUP_LABELS.get(group, group))}</strong>"
            f"<span class='pattern-count'>{count} ({pct:.0f}%)</span>"
            f"{systemic_badge}</div>"
            f"<div class='pattern-fix'>→ {fix}</div>"
            f"</div></div>"
        )
    if not failure_pattern_rows:
        failure_pattern_rows = "<p class='muted'>No failures this batch.</p>"

    # Zone 3: trend rows
    trend_rows = []
    for trend in sorted(trends, key=lambda x: x["date"], reverse=True):
        pr = trend["pass"] / trend["total"] if trend["total"] else 0
        hr = trend["hallucination"] / trend["total"] if trend["total"] else 0
        below = pr < 0.85
        row_style = " style='background:#fff8f7'" if below else ""
        warn = " <span class='badge-warn'>⚠</span>" if below else ""
        trend_rows.append(
            f"<tr{row_style}>"
            f"<td>{html.escape(str(trend['date']))}{warn}</td>"
            f"<td>{trend['total']}</td>"
            f"<td>{format_rate(pr)}</td>"
            f"<td>{trend['pass']}</td>"
            f"<td>{trend['fail']}</td>"
            f"<td>{trend['pass_with_flag']}</td>"
            f"<td>{trend['error']}</td>"
            f"<td>{trend['hallucination']} ({format_rate(hr)})</td>"
            f"<td>{trend['buy_violation']}</td>"
            "</tr>"
        )

    # Zone 2: run list + detail panes
    run_list_rows = []
    detail_divs = []
    for i, run in enumerate(runs):
        report = run.get("report") or {}
        verdict = str(run.get("verdict", ""))
        ticker = html.escape(str(report.get("ticker", "")))
        report_date = html.escape(str(report.get("report_date", "")))
        run_list_rows.append(
            f"<tr class='run-row' data-idx='{i}' data-verdict='{html.escape(verdict)}' "
            f"data-ticker='{ticker.lower()}' data-date='{report_date.lower()}'>"
            f"<td><span class='badge {verdict.lower()}'>{html.escape(verdict)}</span></td>"
            f"<td>{ticker}</td>"
            f"<td>{report_date}</td>"
            f"</tr>"
        )
        detail_divs.append(render_run_detail(run, i))

    # Zone 4: demo examples
    demo_pass = next((r for r in runs if r.get("verdict") == "PASS"), None)
    demo_fail = next((r for r in runs if r.get("verdict") == "FAIL"), None)
    demo_pass_html = render_demo_example(demo_pass, "PASS example")
    demo_fail_html = render_demo_example(demo_fail, "FAIL example")

    data_source_label = html.escape(
        "Real precreated summaries only" if data_source != "all" else "All eval runs, including demo/mock history"
    )
    run_count = len(runs)

    return f"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>AI Summary Eval Dashboard</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{ font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; color: #16312f; background: #f7faf9; }}
    a {{ color: #0b514b; }}
    .zone0 {{ background: white; border-bottom: 1px solid #dfe8e5; padding: 14px 28px 0; position: sticky; top: 0; z-index: 100; box-shadow: 0 2px 12px rgba(22,49,47,.08); }}
    .zone0-inner {{ max-width: 1400px; margin: 0 auto; }}
    .zone0-title {{ display: flex; align-items: baseline; gap: 16px; margin-bottom: 10px; }}
    .zone0-title h1 {{ margin: 0; font-size: 18px; }}
    .zone0-meta {{ font-size: 12px; color: #5b716e; }}
    .banner {{ padding: 7px 14px; border-radius: 8px; font-weight: 700; font-size: 13px; margin-bottom: 10px; }}
    .banner-red {{ background: #ffe1df; color: #9d1c14; border-left: 4px solid #9d1c14; }}
    .banner-green {{ background: #dff7e8; color: #12612f; border-left: 4px solid #12612f; }}
    .metrics-row {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 10px; }}
    .metric-card {{ background: #f7faf9; border: 1px solid #dfe8e5; border-radius: 10px; padding: 10px 14px; }}
    .metric-label {{ font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; color: #5b716e; margin-bottom: 3px; }}
    .metric-value {{ font-size: 24px; font-weight: 750; line-height: 1; }}
    .metric-target {{ display: inline-block; margin-top: 4px; font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 999px; }}
    .tab-nav {{ display: flex; gap: 2px; margin-top: 6px; }}
    .tab-btn {{ padding: 7px 16px; background: none; border: none; border-bottom: 3px solid transparent; cursor: pointer; font-size: 13px; font-weight: 600; color: #5b716e; border-radius: 6px 6px 0 0; transition: color .15s; }}
    .tab-btn:hover {{ color: #0b514b; background: #f0f5f4; }}
    .tab-btn.active {{ color: #0b514b; border-bottom-color: #0b514b; }}
    .tab-panel {{ display: none; max-width: 1400px; margin: 24px auto; padding: 0 28px 40px; }}
    .tab-panel.active {{ display: block; }}
    .card {{ background: white; border: 1px solid #dfe8e5; border-radius: 16px; padding: 20px; box-shadow: 0 4px 16px rgba(22,49,47,.05); margin-bottom: 16px; }}
    h2 {{ margin: 0 0 14px; font-size: 16px; }}
    h3 {{ margin: 0 0 12px; font-size: 15px; }}
    h4 {{ margin: 12px 0 6px; font-size: 13px; color: #344054; }}
    .split-overview {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    .pattern-row {{ display: flex; gap: 12px; padding: 10px 0; border-bottom: 1px solid #edf2f1; align-items: flex-start; }}
    .pattern-row:last-child {{ border-bottom: none; }}
    .pattern-systemic {{ background: #fff8f0; border-radius: 8px; padding: 10px; margin: 2px -10px; }}
    .pattern-rank {{ width: 20px; font-size: 16px; font-weight: 700; color: #b0c4c1; flex-shrink: 0; padding-top: 1px; }}
    .pattern-body {{ flex: 1; min-width: 0; }}
    .pattern-name {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 4px; }}
    .pattern-count {{ font-size: 12px; color: #5b716e; }}
    .pattern-fix {{ font-size: 12px; color: #5b716e; }}
    .systemic-badge {{ background: #fff3cd; color: #a05800; font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 999px; }}
    .filters {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: end; margin-bottom: 14px; }}
    .filters label {{ display: grid; gap: 4px; font-size: 12px; font-weight: 700; }}
    input, select {{ padding: 7px 11px; border: 1px solid #cddbd8; border-radius: 8px; font-size: 13px; background: white; }}
    .split-panel {{ display: grid; grid-template-columns: 320px 1fr; gap: 16px; align-items: start; }}
    .run-list-wrap {{ background: white; border: 1px solid #dfe8e5; border-radius: 14px; overflow: hidden; }}
    .run-list-wrap table {{ width: 100%; border-collapse: collapse; }}
    .run-row {{ cursor: pointer; }}
    .run-row:hover {{ background: #f0f5f4; }}
    .run-row.selected {{ background: #e8f3f1; }}
    .run-row td {{ padding: 9px 12px; border-bottom: 1px solid #edf2f1; font-size: 13px; vertical-align: middle; }}
    .run-detail-wrap {{ background: white; border: 1px solid #dfe8e5; border-radius: 14px; padding: 20px; min-height: 280px; }}
    .detail-pane {{ display: none; }}
    .detail-pane.visible {{ display: block; }}
    .detail-placeholder {{ color: #b0c4c1; font-size: 14px; padding: 60px 0; text-align: center; }}
    .detail-header {{ display: flex; align-items: center; gap: 10px; margin-bottom: 8px; flex-wrap: wrap; }}
    .detail-ticker {{ font-size: 20px; font-weight: 700; }}
    .detail-date {{ color: #5b716e; font-size: 13px; }}
    .detail-time {{ color: #aaa; font-size: 11px; margin-left: auto; }}
    .detail-model {{ font-size: 12px; color: #5b716e; margin-bottom: 10px; }}
    .issue-row {{ font-size: 12px; margin-bottom: 6px; line-height: 1.5; }}
    table.trend {{ width: 100%; border-collapse: collapse; }}
    table.trend th, table.trend td {{ padding: 9px 12px; border-bottom: 1px solid #edf2f1; text-align: left; font-size: 13px; }}
    table.trend th {{ background: #0b514b; color: white; font-size: 12px; }}
    .badge-warn {{ background: #fff3cd; color: #a05800; font-size: 10px; padding: 1px 5px; border-radius: 999px; font-weight: 700; }}
    .demo-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
    code {{ background: #eef6f4; padding: 2px 6px; border-radius: 6px; font-size: 12px; }}
    .badge {{ display: inline-block; padding: 3px 9px; border-radius: 999px; font-size: 12px; font-weight: 700; }}
    .pass {{ background: #dff7e8; color: #12612f; }}
    .fail {{ background: #ffe1df; color: #9d1c14; }}
    .pass-with-flag {{ background: #fff3cf; color: #7a5400; }}
    .error {{ background: #eceff3; color: #344054; }}
    .issue-group {{ display: inline-block; min-width: 44px; padding: 2px 7px; border-radius: 999px; background: #e8f3f1; color: #0b514b; font-size: 11px; font-weight: 800; text-align: center; }}
    .summary {{ max-width: 100%; white-space: pre-wrap; font-size: 13px; background: #f6f8f8; border-radius: 10px; padding: 10px 14px; margin: 4px 0; }}
    .muted {{ color: #aaa; font-size: 13px; }}
    details {{ margin-top: 8px; }}
    summary {{ cursor: pointer; color: #0b514b; font-weight: 700; font-size: 13px; }}
    pre {{ white-space: pre-wrap; font-size: 12px; background: #f6f8f8; border: 1px solid #e4ecea; border-radius: 10px; padding: 10px; max-height: 360px; overflow-y: auto; margin: 6px 0; }}
    @media (max-width: 900px) {{
      .metrics-row {{ grid-template-columns: repeat(2, 1fr); }}
      .split-panel, .demo-grid, .split-overview {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>

<header class="zone0">
  <div class="zone0-inner">
    <div class="zone0-title">
      <h1>AI Summary Eval Dashboard</h1>
      <span class="zone0-meta">{data_source_label} · <a href="/dashboard">Real only</a> · <a href="/dashboard?source=all">All history</a></span>
    </div>
    {banner_html}
    <div class="metrics-row">
      {pass_card}
      {hall_card}
      {buy_card}
      {creation_card}
    </div>
    <nav class="tab-nav">
      <button class="tab-btn active" data-tab="overview">Overview</button>
      <button class="tab-btn" data-tab="runs">Runs ({run_count})</button>
      <button class="tab-btn" data-tab="trend">Trend</button>
      <button class="tab-btn" data-tab="demo">Demo</button>
    </nav>
  </div>
</header>

<section class="tab-panel active" id="tab-overview">
  <div class="split-overview">
    <div class="card">
      <h2>Failure patterns</h2>
      {failure_pattern_rows}
    </div>
    <div class="card">
      <h2>Batch summary</h2>
      <p style="margin:0 0 8px"><strong>Total runs:</strong> {aggregate["total_evaluated"]}</p>
      <p style="margin:0 0 8px">
        <span class="badge pass">PASS {aggregate["pass_count"]}</span>&nbsp;
        <span class="badge fail">FAIL {aggregate["fail_count"]}</span>&nbsp;
        <span class="badge pass-with-flag">FLAG {aggregate["pass_with_flag_count"]}</span>&nbsp;
        <span class="badge error">ERROR {aggregate["error_count"]}</span>
      </p>
      <p style="font-size:12px;color:#5b716e;margin:12px 0 0">Full report text is intentionally hidden in all endpoints.</p>
    </div>
  </div>
</section>

<section class="tab-panel" id="tab-runs">
  <div class="filters">
    <label>Verdict
      <select id="verdictFilter">
        <option value="">All</option>
        <option value="PASS">PASS</option>
        <option value="FAIL">FAIL</option>
        <option value="PASS-WITH-FLAG">PASS-WITH-FLAG</option>
        <option value="ERROR">ERROR</option>
      </select>
    </label>
    <label>Ticker <input id="tickerFilter" placeholder="e.g. VTP"></label>
    <label>Report date <input id="dateFilter" placeholder="e.g. 2026-06"></label>
  </div>
  <div class="split-panel">
    <div class="run-list-wrap">
      <table>
        <thead><tr style="background:#0b514b;color:white">
          <th style="padding:8px 12px;font-size:12px">Verdict</th>
          <th style="padding:8px 12px;font-size:12px">Ticker</th>
          <th style="padding:8px 12px;font-size:12px">Date</th>
        </tr></thead>
        <tbody id="runsBody">{"".join(run_list_rows) or "<tr><td colspan='3' style='padding:20px;color:#aaa;text-align:center'>No runs</td></tr>"}</tbody>
      </table>
    </div>
    <div class="run-detail-wrap" id="runDetailWrap">
      <div class="detail-placeholder" id="detailPlaceholder">← Select a run to inspect</div>
      {"".join(detail_divs)}
    </div>
  </div>
</section>

<section class="tab-panel" id="tab-trend">
  <div class="card">
    <h2>Pass rate trend <span style="font-size:12px;font-weight:400;color:#5b716e">(85% threshold)</span></h2>
    <table class="trend">
      <thead><tr>
        <th>Date</th><th>Total</th><th>Pass rate</th><th>PASS</th><th>FAIL</th><th>FLAG</th><th>ERROR</th><th>Hallucination</th><th>Buy</th>
      </tr></thead>
      <tbody>{"".join(trend_rows) or "<tr><td colspan='9' style='color:#aaa;padding:20px'>No trend data yet.</td></tr>"}</tbody>
    </table>
  </div>
</section>

<section class="tab-panel" id="tab-demo">
  <div class="demo-grid">
    {demo_pass_html}
    {demo_fail_html}
  </div>
</section>

<script>
  const tabBtns = document.querySelectorAll('.tab-btn');
  const tabPanels = document.querySelectorAll('.tab-panel');
  tabBtns.forEach(btn => {{
    btn.addEventListener('click', () => {{
      tabBtns.forEach(b => b.classList.remove('active'));
      tabPanels.forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
    }});
  }});
  const verdictFilter = document.getElementById('verdictFilter');
  const tickerFilter = document.getElementById('tickerFilter');
  const dateFilter = document.getElementById('dateFilter');
  const runRows = Array.from(document.querySelectorAll('#runsBody .run-row'));
  function applyFilters() {{
    const v = verdictFilter.value, t = tickerFilter.value.trim().toLowerCase(), d = dateFilter.value.trim().toLowerCase();
    for (const row of runRows) {{
      const ok = (!v || row.dataset.verdict === v) && (!t || row.dataset.ticker.includes(t)) && (!d || row.dataset.date.includes(d));
      row.style.display = ok ? '' : 'none';
    }}
  }}
  verdictFilter.addEventListener('change', applyFilters);
  tickerFilter.addEventListener('input', applyFilters);
  dateFilter.addEventListener('input', applyFilters);
  const placeholder = document.getElementById('detailPlaceholder');
  let activeRow = null, activePane = null;
  runRows.forEach(row => {{
    row.addEventListener('click', () => {{
      if (activeRow) activeRow.classList.remove('selected');
      if (activePane) activePane.classList.remove('visible');
      row.classList.add('selected');
      activeRow = row;
      const pane = document.getElementById('detail-' + row.dataset.idx);
      if (pane) {{ pane.classList.add('visible'); activePane = pane; placeholder.style.display = 'none'; }}
    }});
  }});
</script>
</body>
</html>"""
