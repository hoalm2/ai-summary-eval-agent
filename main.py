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


app = FastAPI(title="AI Summary Judge", version="0.1.0")


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


@app.get("/")
def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/dashboard")


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
def dashboard(source: str = "real", date: str = "") -> str:
    store = SupabaseStore()
    all_runs = store.fetch_eval_runs(limit=200)
    runs = all_runs if source == "all" else [run for run in all_runs if (run.get("summary") or {}).get("summary_model") == "precreated"]
    if date:
        runs = [r for r in runs if str(r.get("created_at", "")).startswith(date)]
    aggregate = aggregate_runs(runs)
    return render_dashboard(aggregate, runs, data_source=source, date_filter=date)


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
    verdict_counts: dict[str, int] = {}
    failure_breakdown: dict[str, int] = {}
    hallucination_count = 0
    buy_violation_count = 0
    total_bullets = 0
    flagged_bullet_keys: set[str] = set()
    for run in runs:
        verdict = str(run.get("verdict") or "ERROR")
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        bullet_evals = run.get("bullet_evals") or []
        if bullet_evals:
            total_bullets += len(bullet_evals)
        else:
            summary_text = str((run.get("summary") or {}).get("summary_text") or "")
            total_bullets += len(_parse_bullets_from_text(summary_text))
        all_issues = (run.get("blocks") or []) + (run.get("flags") or [])
        for issue in all_issues:
            category = issue.get("category", "unknown")
            failure_breakdown[category] = failure_breakdown.get(category, 0) + 1
            if str(category).startswith("buy_price"):
                buy_violation_count += 1
            bi = issue.get("bullet_index")
            if bi is not None:
                flagged_bullet_keys.add(f"{id(run)}:{bi}")
        if any(
            str(i.get("category", "")).startswith(("A_", "B_"))
            and str(i.get("category", "")) != "B_tone_escalation"
            for i in all_issues
        ):
            hallucination_count += 1
    pass_count = verdict_counts.get("PASS", 0)
    fail_count = verdict_counts.get("FAIL", 0)
    flag_count = verdict_counts.get("PASS-WITH-FLAG", 0)
    error_count = verdict_counts.get("ERROR", 0)
    flagged_bullets = len(flagged_bullet_keys)
    return {
        "total_evaluated": total,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "flag_count": flag_count,
        "error_count": error_count,
        "pass_rate": pass_count / total if total else 0,
        "fail_flag_rate": (fail_count + flag_count) / total if total else 0,
        "hallucination_count": hallucination_count,
        "hallucination_rate": hallucination_count / total if total else 0,
        "buy_violation_count": buy_violation_count,
        "total_bullets": total_bullets,
        "flagged_bullets": flagged_bullets,
        "bullet_fail_rate": flagged_bullets / total_bullets if total_bullets else 0,
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
                "flag": 0,
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
            bucket["flag"] += 1
        elif verdict == "ERROR":
            bucket["error"] += 1
        all_issues = (run.get("blocks") or []) + (run.get("flags") or [])
        if any(
            str(i.get("category", "")).startswith(("A_", "B_"))
            and str(i.get("category", "")) != "B_tone_escalation"
            for i in all_issues
        ):
            bucket["hallucination"] += 1
        if any(str(i.get("category", "")).startswith("buy_price") for i in all_issues):
            bucket["buy_violation"] += 1
    return [trends[key] for key in sorted(trends.keys(), reverse=True)]




def _parse_bullets_from_text(text: str) -> list[str]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    result = []
    for ln in lines:
        cleaned = ln.lstrip("-•·*▸►◆●○■").strip()
        if cleaned:
            result.append(cleaned)
    return result


def run_to_js_dict(run: dict[str, Any]) -> dict[str, Any]:
    report = run.get("report") or {}
    summary = run.get("summary") or {}
    blocks = run.get("blocks") or []
    flags = run.get("flags") or []
    bullet_evals = run.get("bullet_evals") or []

    bullets: list[str] = [str(be.get("bullet_text", "")) for be in bullet_evals if be.get("bullet_text")]
    if not bullets:
        bullets = _parse_bullets_from_text(str(summary.get("summary_text", "")))

    def convert_issue(iss: dict[str, Any], severity: str) -> dict[str, Any]:
        cat = str(iss.get("category", ""))
        src = str(iss.get("source", ""))
        bi = iss.get("bullet_index")
        return {
            "group": issue_group(cat),
            "category": cat,
            "severity": severity,
            "source": "deterministic" if "deterministic" in src else "llm_judge",
            "summary_quote": iss.get("summary_quote") or None,
            "report_evidence": iss.get("report_evidence") or None,
            "explanation": iss.get("explanation") or None,
            "bullet_index": (int(bi) + 1) if bi is not None else 0,
        }

    verdict = str(run.get("verdict") or "ERROR")
    created = str(run.get("created_at") or "")[:19]
    return {
        "id": run.get("id", 0),
        "ticker": str(report.get("ticker") or ""),
        "report_date": str(report.get("report_date") or ""),
        "created_at": created,
        "verdict": verdict,
        "bullets": bullets,
        "blocks": [convert_issue(i, "BLOCK") for i in blocks],
        "flags": [convert_issue(i, "FLAG") for i in flags],
    }




def render_dashboard(aggregate: dict[str, Any], runs: list[dict[str, Any]], *, data_source: str = "real", date_filter: str = "") -> str:
    total = aggregate["total_evaluated"]
    fail_count = aggregate.get("fail_count", 0)
    flag_count = aggregate.get("flag_count", 0)
    fail_flag_rate = aggregate.get("fail_flag_rate", (fail_count + flag_count) / total if total else 0)
    buy_n = aggregate.get("buy_violation_count", 0)
    total_bullets = aggregate.get("total_bullets", 0)
    flagged_bullets = aggregate.get("flagged_bullets", 0)
    bullet_fail_rate = aggregate.get("bullet_fail_rate", flagged_bullets / total_bullets if total_bullets else 0)

    group_counts: dict[str, int] = {}
    for category, count in aggregate.get("failure_breakdown", {}).items():
        g = issue_group(str(category))
        group_counts[g] = group_counts.get(g, 0) + int(count)

    run_count = len(runs)
    latest_at = max((str(r.get("created_at") or "")[:19] for r in runs), default="")
    batch_date = latest_at[:10] if latest_at else ""
    batch_display = latest_at.replace("T", " ") if latest_at else "—"

    js_runs = json.dumps([run_to_js_dict(r) for r in runs], ensure_ascii=False)

    trends_data = build_daily_trends(runs)
    js_trends = json.dumps(
        [
            {
                "date": t["date"], "total": t["total"], "pass": t["pass"],
                "fail": t["fail"], "flag": t["flag"], "error": t.get("error", 0),
                "hall": t.get("hallucination", 0), "buy": t.get("buy_violation", 0),
            }
            for t in sorted(trends_data, key=lambda x: x["date"])
        ],
        ensure_ascii=False,
    )

    agg_js_obj = {
        "total": total,
        "pass": aggregate.get("pass_count", 0),
        "fail": fail_count,
        "flag": flag_count,
        "failFlagRate": fail_flag_rate,
        "hallN": aggregate.get("hallucination_count", 0),
        "buyN": buy_n,
        "totalBullets": total_bullets,
        "flaggedBullets": flagged_bullets,
        "bulletFailRate": bullet_fail_rate,
        "groupCounts": group_counts,
    }
    js_agg = json.dumps(agg_js_obj, ensure_ascii=False)

    data_block = (
        "const BATCH_DATE = " + json.dumps(batch_date) + ";\n"
        "const RUNS = " + js_runs + ";\n"
        "const TRENDS = " + js_trends + ";\n"
        "const AGG = " + js_agg + ";\n"
    )

    header_meta = (
        '<span class="hdr-meta">Latest batch: <strong>'
        + html.escape(batch_display)
        + "</strong> &nbsp;·&nbsp; "
        + str(run_count)
        + " runs</span>"
    )

    css = """<style>
:root {
  --p: #0b514b; --p-light: #e8f3f1; --p-mid: #cddbd8;
  --bg: #eef3f2; --surface: #fff; --border: #dfe8e5;
  --text: #16312f; --sub: #5b716e; --dim: #8da8a5;
  --red: #c0392b; --red-bg: #ffe4e1; --red-text: #9d1c14;
  --green: #12612f; --green-bg: #dff7e8;
  --amber: #8a5c00; --amber-bg: #fff3cf;
  --gray-bg: #eceff3; --gray-text: #344054;
}
*, *::before, *::after { box-sizing: border-box; }
body {
  font-family: "Helvetica Neue", Helvetica, Arial, system-ui, sans-serif;
  margin: 0; background: var(--bg); color: var(--text);
  font-size: 14px; line-height: 1.5; -webkit-font-smoothing: antialiased;
}
button { font-family: inherit; cursor: pointer; }
code { font-family: "SF Mono", Consolas, "Courier New", monospace; }

/* ─── HEADER ─────────────────────────────────────────── */
.hdr {
  background: var(--surface); border-bottom: 1px solid var(--border);
  padding: 0 32px; position: sticky; top: 0; z-index: 200;
  box-shadow: 0 1px 12px rgba(11,81,75,.08);
}
.hdr-top {
  display: flex; align-items: baseline; gap: 10px;
  padding: 14px 0 10px;
}
.logo { margin: 0; font-size: 16px; font-weight: 800; color: var(--p); letter-spacing: -.02em; }
.hdr-meta { margin-left: auto; font-size: 12px; color: var(--dim); }
.hdr-meta strong { color: var(--sub); }

/* ─── METRIC CARDS ───────────────────────────────────── */
.metrics {
  display: grid; grid-template-columns: repeat(4,1fr); gap: 10px;
  padding: 6px 0 14px;
}
.mc {
  display: flex; flex-direction: column;
  border: 1.5px solid var(--border); border-radius: 12px;
  padding: 14px 16px; background: var(--surface);
  cursor: pointer; transition: background .14s, transform .12s, box-shadow .14s, border-color .14s;
  position: relative; z-index: 1;
}
.mc:hover {
  background: var(--p-light); border-color: rgba(11,81,75,.35);
  transform: translateY(-2px); box-shadow: 0 6px 20px rgba(11,81,75,.12);
  z-index: 60;
}
.mc.active-card { border-color: var(--p); box-shadow: 0 0 0 2.5px rgba(11,81,75,.14); }
.mc-label {
  font-size: 10px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .05em; color: var(--dim); display: flex; align-items: center;
  justify-content: space-between; width: 100%; margin-bottom: 5px; line-height: 1.4;
}
.mc-value { font-size: 30px; font-weight: 800; line-height: 1; margin-bottom: 7px; }
.mc-desc { font-size: 11px; color: var(--sub); line-height: 1.45; margin: 0 0 8px; }
.mc-desc em { font-style: normal; font-weight: 700; color: var(--text); }
.mc-pill {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 11px; font-weight: 700; padding: 3px 9px; border-radius: 999px;
}
/* ─── TOOLTIP ─────────────────────────────────────────── */
.tip { position: relative; display: inline-flex; }
.tip-box {
  position: absolute; bottom: calc(100% + 8px); left: 50%;
  transform: translateX(-50%); width: 230px;
  background: #1a3533; color: #d6ecea; font-size: 12px; font-weight: 400;
  padding: 9px 12px; border-radius: 9px; line-height: 1.45; text-transform: none;
  pointer-events: none; opacity: 0; transition: opacity .15s; z-index: 999;
  box-shadow: 0 6px 20px rgba(0,0,0,.22); white-space: normal;
}
.tip-box::after {
  content: ''; position: absolute; top: 100%; left: 50%; transform: translateX(-50%);
  border: 5px solid transparent; border-top-color: #1a3533;
}
.tip:hover .tip-box { opacity: 1; }
.tip { z-index: 1; }
.tip:hover { z-index: 1200; }
.tip-down .tip-box { bottom: auto; top: calc(100% + 8px); }
.tip-down .tip-box::after { top: auto; bottom: 100%; border-top-color: transparent; border-bottom-color: #1a3533; }
.ic {
  width: 15px; height: 15px; border-radius: 50%; background: #e0ecea;
  color: var(--sub); font-size: 9px; font-weight: 900; font-style: normal;
  display: inline-flex; align-items: center; justify-content: center;
}

/* ─── TABS ────────────────────────────────────────────── */
.tabs {
  display: flex; margin: 4px -32px 0; padding: 0 32px;
  border-top: 1px solid var(--border);
}
.tab {
  padding: 10px 20px; background: none; border: none;
  border-bottom: 2.5px solid transparent; margin-bottom: -1px;
  font-size: 13px; font-weight: 600; color: var(--dim); transition: color .12s;
}
.tab:hover { color: var(--p); }
.tab.on { color: var(--p); border-bottom-color: var(--p); }

/* ─── CONTENT ─────────────────────────────────────────── */
.content { max-width: 1420px; margin: 0 auto; padding: 24px 32px 80px; }
.panel { display: none; }
.panel.on { display: block; }
.panel-desc { font-size: 13px; color: var(--sub); margin: 0 0 18px; line-height: 1.55; }
.card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 14px; padding: 20px 24px;
  box-shadow: 0 2px 10px rgba(11,81,75,.04);
}
h3.card-title { margin: 0 0 18px; font-size: 15px; font-weight: 700; }

/* ─── BADGES ──────────────────────────────────────────── */
.badge {
  display: inline-flex; align-items: center; justify-content: center;
  padding: 3px 10px; border-radius: 999px; font-size: 11px; font-weight: 700;
}
.b-pass { background: var(--green-bg); color: var(--green); }
.b-fail { background: var(--red-bg); color: var(--red-text); }
.b-flag { background: var(--amber-bg); color: var(--amber); }
.b-error { background: var(--gray-bg); color: var(--gray-text); }
.b-A { background: #fce8e6; color: #9d1c14; }
.b-B { background: #fef0e3; color: #8a3f00; }
.b-BUY { background: #fef0e3; color: #8a3f00; }
.b-C { background: var(--p-light); color: var(--p); }
.b-FMT { background: #f0f0f2; color: #555; }

/* ─── OVERVIEW ────────────────────────────────────────── */
.ov-grid { display: grid; grid-template-columns: 1fr 340px; gap: 16px; }

.fp-item { padding: 14px 0; border-bottom: 1px solid var(--border); }
.fp-item:last-child { border-bottom: none; }
.fp-item.systemic { background: #fffaf4; margin: 0 -24px; padding: 14px 24px;
  border-left: 3px solid #e0a800; padding-left: 21px; }

.fp-top { display: flex; align-items: center; gap: 14px; margin-bottom: 8px; }
.fp-pct { font-size: 28px; font-weight: 800; min-width: 72px; color: var(--text); }
.fp-share { display: flex; flex-direction: column; gap: 3px; flex: 1; }
.fp-share-lbl { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; color: var(--dim); }
.fp-track { flex: none; width: 100%; height: 8px; background: #edf2f1; border-radius: 4px; overflow: hidden; }
.fp-fill { height: 100%; border-radius: 4px; }
.fp-n { font-size: 12px; color: var(--dim); white-space: nowrap; }

.fp-type { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.fp-name { font-size: 14px; font-weight: 600; }
.sys-tag {
  background: var(--amber-bg); color: var(--amber);
  font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 999px;
  display: inline-flex; align-items: center; gap: 3px;
}
.fp-fix {
  display: flex; align-items: flex-start; gap: 7px;
  font-size: 12px; color: var(--sub);
  background: none; border: 1px solid var(--p-mid); border-radius: 8px;
  padding: 9px 13px; margin-top: 8px;
}
.fp-fix-lbl { font-weight: 700; color: var(--p); flex-shrink: 0; }
.fp-fix code { background: var(--p-light); padding: 1px 5px; border-radius: 4px; font-size: 11px; }
.vd-btn {
  display: inline-flex; align-items: center; gap: 7px;
  margin-top: 10px; padding: 5px 16px 5px 13px;
  border: 1px solid var(--p-mid); border-radius: 7px;
  background: var(--surface); font-size: 12px; font-weight: 700; color: var(--p);
  transition: background .12s, border-color .12s;
}
.vd-btn:hover { background: var(--p-light); border-color: var(--p); }

/* Batch summary */
.bs-row { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.bs-lbl { min-width: 60px; }
.bs-track { flex: 1; height: 9px; background: #edf2f1; border-radius: 999px; overflow: hidden; }
.bs-fill { height: 100%; border-radius: 999px; }
.bs-n { font-size: 13px; font-weight: 700; min-width: 24px; text-align: right; }

/* ─── DETAILED REPORT ──────────────────────────────────── */
.filters {
  display: flex; gap: 10px; align-items: flex-end; margin-bottom: 16px; flex-wrap: wrap;
}
.fg { display: flex; flex-direction: column; gap: 4px; }
.fl { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; color: var(--dim); }
select, input[type=text], input[type=date] {
  padding: 8px 11px; border: 1.5px solid var(--border); border-radius: 8px;
  font-size: 13px; background: var(--surface); color: var(--text);
  font-family: inherit; outline: none; transition: border-color .12s;
}
select:focus, input:focus { border-color: var(--p); }
.filter-active-bar {
  display: none; align-items: center; gap: 8px;
  background: var(--p-light); border: 1px solid var(--p-mid);
  border-radius: 8px; padding: 7px 12px; font-size: 12px;
  color: var(--p); font-weight: 600; margin-bottom: 12px;
}
.filter-active-bar.show { display: flex; }
.clear-btn {
  margin-left: auto; background: none; border: 1px solid var(--p-mid);
  border-radius: 6px; padding: 3px 9px; font-size: 11px; font-weight: 700;
  color: var(--p); transition: background .12s;
}
.clear-btn:hover { background: var(--p-light); }

.split { display: grid; grid-template-columns: 290px 1fr; gap: 16px; align-items: start; }
.run-list-wrap { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
.rl-head {
  background: var(--p); color: white;
  display: grid; grid-template-columns: 90px 64px 1fr; gap: 12px;
  padding: 9px 14px; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em;
}
.rl-row {
  display: grid; grid-template-columns: 90px 64px 1fr; gap: 12px;
  padding: 10px 14px; border-bottom: 1px solid #edf2f1;
  cursor: pointer; align-items: center; transition: background .1s;
}
.rl-row:hover { background: var(--p-light); }
.rl-row.sel { background: var(--p-light); border-left: 2.5px solid var(--p); padding-left: 11.5px; }
.rl-row:last-child { border-bottom: none; }
.rl-sym { font-size: 13px; font-weight: 700; }
.rl-date { font-size: 12px; color: var(--dim); }
.no-runs { padding: 28px 14px; text-align: center; font-size: 13px; color: var(--dim); }

/* ─── RUN DETAIL ──────────────────────────────────────── */
.detail-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 14px; padding: 24px 28px; min-height: 340px;
}
.d-placeholder { color: var(--dim); text-align: center; padding: 80px 20px; font-size: 14px; }

.d-hdr {
  display: flex; align-items: flex-start; gap: 12px;
  padding-bottom: 18px; border-bottom: 1px solid var(--border); margin-bottom: 20px;
}
.d-sym { font-size: 28px; font-weight: 800; line-height: 1; }
.d-date { font-size: 13px; color: var(--dim); margin-top: 5px; }
.d-time { font-size: 11px; color: #b5c9c7; margin-left: auto; margin-top: 3px; }

.d-iss-row {
  display: flex; align-items: center; gap: 8px;
  font-size: 13px; color: var(--sub); margin-bottom: 16px;
}
.dot { color: var(--p-mid); }

.d-sec {
  display: flex; align-items: center; gap: 7px;
  font-size: 13px; font-weight: 700; margin: 22px 0 10px;
}
hr.d-rule { border: none; border-top: 1px solid var(--border); margin: 18px 0; }

.summary-box {
  background: #f8fcfb; border: 1px solid #e3eeec; border-radius: 10px;
  padding: 14px 18px; font-size: 13px; line-height: 1.75; color: #2a4442;
}
.summary-box ul { margin: 0; padding-left: 18px; }
.summary-box li + li { margin-top: 7px; }

/* ─── ISSUE CARDS ─────────────────────────────────────── */
.iss-list { display: flex; flex-direction: column; gap: 10px; }
.iss-card {
  border: 1px solid var(--border); border-radius: 10px;
  padding: 14px 16px; background: #fdfffe;
}
.iss-card.is-block { border-left: 3px solid var(--red); }
.iss-card.is-flag  { border-left: 3px solid #e0a800; }
.iss-hdr { display: flex; align-items: center; gap: 7px; margin-bottom: 10px; flex-wrap: wrap; }
.iss-src {
  margin-left: auto; font-size: 10px; color: var(--dim); font-weight: 600;
  background: var(--bg); padding: 2px 8px; border-radius: 4px; white-space: nowrap;
}

.iss-section-label {
  font-size: 10px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .05em; color: var(--dim); margin-bottom: 4px;
}
.iss-quote {
  font-size: 13px; background: #fff4f3; border-radius: 7px;
  padding: 8px 12px; color: #7b1c18; font-style: italic;
  border-left: 2.5px solid #f5b8b5; margin-bottom: 8px; line-height: 1.55;
}
.iss-evidence {
  font-size: 12.5px; color: var(--sub); background: #f8fcfb;
  border-radius: 7px; padding: 8px 12px; border-left: 2.5px solid var(--p-mid);
  margin-bottom: 8px; line-height: 1.55;
}
.iss-evidence::before { content: "→ "; color: var(--p); font-weight: 700; }
.iss-explanation { font-size: 12px; color: #4a6660; line-height: 1.6; }

.iss-type {
  display: flex; align-items: center; gap: 8px;
  font-size: 13px; font-weight: 700; margin: 6px 0 12px;
}
.iss-type::before {
  content: ''; width: 8px; height: 8px; border-radius: 2px;
  background: currentColor; flex-shrink: 0;
}

.iss-bullet-tag {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em;
  background: var(--gray-bg); color: var(--gray-text);
  padding: 2px 8px; border-radius: 999px;
}
.iss-why {
  display: inline-flex; align-items: center; gap: 6px;
  margin-top: 2px; padding: 6px 12px;
  border: 1px solid var(--border); border-radius: 7px;
  background: var(--surface); font-size: 12px; font-weight: 700; color: var(--p);
  transition: background .12s, border-color .12s;
}
.iss-why:hover { background: var(--p-light); border-color: var(--p-mid); }
.iss-why .chev { transition: transform .18s; font-size: 10px; }
.iss-why[aria-expanded="true"] .chev { transform: rotate(90deg); }
.iss-detail { display: none; margin-top: 8px; }
.iss-detail.open { display: block; }

.json-toggle {
  display: inline-flex; align-items: center; gap: 5px;
  margin-top: 14px; padding: 5px 0; background: none; border: none;
  font-size: 12px; font-weight: 700; color: var(--p);
}
.json-toggle:hover { text-decoration: underline; }
.json-pre {
  margin-top: 8px; background: var(--bg); border: 1px solid var(--border);
  border-radius: 8px; padding: 12px;
  font-size: 11px; white-space: pre-wrap; max-height: 260px; overflow-y: auto;
  color: #2a4442; line-height: 1.6; display: none;
}

/* ─── TREND ───────────────────────────────────────────── */
.trend-svg-wrap { overflow-x: auto; margin-bottom: 4px; }
.t-table { width: 100%; border-collapse: collapse; }
.t-table th {
  background: var(--p); color: white; padding: 10px 14px;
  text-align: left; font-size: 11px; font-weight: 700;
  text-transform: uppercase; letter-spacing: .04em;
}
.t-table td { padding: 10px 14px; border-bottom: 1px solid #edf2f1; font-size: 13px; vertical-align: middle; }
.t-table tr:last-child td { border-bottom: none; }
.t-table tr.warn-row { background: #fff9f8; }
.t-minibar { display: flex; height: 7px; border-radius: 4px; overflow: hidden; min-width: 80px; gap: 1px; }
.t-minibar > div { cursor: help; }
.dist-legend {
  display: flex; flex-wrap: wrap; gap: 6px 16px; align-items: center;
  margin: 0 0 14px; font-size: 12px; color: var(--sub);
}
.dist-legend .lg { display: inline-flex; align-items: center; gap: 6px; }
.dist-legend .sw { width: 12px; height: 12px; border-radius: 3px; }
.dist-legend .lg-hint { color: var(--dim); font-size: 11px; }

@media (max-width: 1000px) {
  .metrics { grid-template-columns: 1fr; }
  .ov-grid, .split { grid-template-columns: 1fr; }
}
</style>"""

    html_body = """
<header class="hdr">
  <div class="hdr-top">
    <h1 class="logo">AI Summary Judge</h1>
    HEADER_META_PLACEHOLDER
  </div>
  <div class="metrics" id="metricsRow"></div>
  <nav class="tabs" id="tabNav">
    <button class="tab on" data-tab="overview">Overview</button>
    <button class="tab" data-tab="detail">Detailed report</button>
    <button class="tab" data-tab="trend">Trend</button>
  </nav>
</header>

<div class="content">

  <!-- ── OVERVIEW ─────────────────────────────── -->
  <div class="panel on" id="panel-overview">
    <p class="panel-desc">Overview distribution của các type fail/flag. Click <em>View detail</em> để xem chi tiết các summary bị fail/flag.</p>
    <div class="ov-grid">
      <div class="card">
        <h3 class="card-title">Failure patterns</h3>
        <div id="fpList"></div>
      </div>
      <div class="card">
        <h3 class="card-title" id="bsTitle">Latest batch summary</h3>
        <div id="bsSummary"></div>
      </div>
    </div>
  </div>

  <!-- ── DETAILED REPORT ───────────────────────── -->
  <div class="panel" id="panel-detail">
    <p class="panel-desc">Chi tiết issue của từng summary đã được eval bởi AI, bao gồm type fail/flag và lý giải tại sao fail/flag.</p>
    <div class="filters">
      <div class="fg">
        <span class="fl">Eval status</span>
        <select id="fVerdict">
          <option value="">All</option>
          <option value="PASS">PASS</option>
          <option value="FAIL">FAIL</option>
          <option value="PASS-WITH-FLAG">FLAG</option>
        </select>
      </div>
      <div class="fg">
        <span class="fl">Symbol</span>
        <input type="text" id="fSymbol" placeholder="e.g. GMD" style="width:120px">
      </div>
      <div class="fg">
        <span class="fl">Eval date</span>
        <input type="date" id="fDate">
      </div>
      <div class="fg">
        <span class="fl">Category</span>
        <select id="fCategory">
          <option value="">All categories</option>
          <option value="hallucination">Hallucination (A + B)</option>
          <option value="A">Type A — Factual / Logic</option>
          <option value="B">Type B — Unsupported / Fabricated</option>
          <option value="BUY">Buy price violations</option>
          <option value="C">Type C — Disclaimer omission</option>
          <option value="FMT">Format / Render</option>
        </select>
      </div>
      <button onclick="clearFilters()" style="align-self:flex-end;padding:8px 14px;border:1.5px solid var(--border);border-radius:8px;background:var(--surface);font-size:13px;font-weight:600;color:var(--sub)">↺ Clear</button>
    </div>
    <div class="filter-active-bar" id="filterBar">
      <span id="filterBarText"></span>
      <button class="clear-btn" onclick="clearFilters()">✕ Clear</button>
    </div>
    <div class="split">
      <div class="run-list-wrap">
        <div class="rl-head">
          <span>Status</span><span>Symbol</span><span>Eval date</span>
        </div>
        <div id="runListBody"></div>
      </div>
      <div class="detail-card" id="detailPane">
        <div class="d-placeholder">← Select a run to view details</div>
      </div>
    </div>
  </div>

  <!-- ── TREND ──────────────────────────────────── -->
  <div class="panel" id="panel-trend">
    <p class="panel-desc">Monitor trend của pass rate theo daily evaluation.</p>
    <div class="card" style="margin-bottom:16px">
      <h3 class="card-title">Pass rate trend <span style="font-weight:400;font-size:12px;color:var(--sub)">(7 days · 85% threshold)</span></h3>
      <div class="trend-svg-wrap" id="trendChart"></div>
    </div>
    <div class="card">
      <table class="t-table" id="trendTable"></table>
    </div>
  </div>

</div>"""

    js_logic = """
/* ═══════════════════ SVG RING ═══════════════════ */
function ring(pct, color, sz, sw) {
  sz = sz || 48; sw = sw || 5;
  var r = (sz-sw*2)/2, cx=sz/2, cy=sz/2;
  var c = 2*Math.PI*r, f = Math.min(pct,1)*c;
  return '<svg width="'+sz+'" height="'+sz+'" viewBox="0 0 '+sz+' '+sz+'" aria-hidden="true">'
    +'<circle cx="'+cx+'" cy="'+cy+'" r="'+r+'" fill="none" stroke="#e0ebe9" stroke-width="'+sw+'"/>'
    +'<circle cx="'+cx+'" cy="'+cy+'" r="'+r+'" fill="none" stroke="'+color+'" stroke-width="'+sw+'"'
    +' stroke-dasharray="'+f+' '+c+'" stroke-linecap="round"'
    +' transform="rotate(-90 '+cx+' '+cy+')"/>'
    +'</svg>';
}

/* ═══════════════════ METRIC CARDS ═══════════════════ */
var TIPS = {
  failflag:'Đo theo đơn vị summary: mỗi summary được tính 1 lần. % summary có ít nhất 1 issue (FAIL hoặc FLAG). Mục tiêu ≤ 15% (tức pass rate ≥ 85%).',
  hall:'% summary có ít nhất 1 hallucination issue (Type A hoặc Type B) trong batch. Đây là chỉ số chất lượng factual — mục tiêu ≤ 20%.',
  buy:'Số issue vi phạm quy tắc buy price (đề xuất vùng giá mua, upside không kèm ngày tham chiếu…) trong batch. Đây là rủi ro an toàn sản phẩm — mục tiêu = 0 tuyệt đối.',
  bullet:'Đo theo đơn vị bullet point: % các câu (bullet) trong summary bị đánh dấu FAIL/FLAG, tính trên tổng số bullet của tất cả summary trong batch. Mục tiêu ≤ 10%.'
};

function renderMetrics(agg) {
  var hallRate = agg.total ? agg.hallN / agg.total : 0;
  var cards = [
    { label:'% Summary Fail/Flag', val:Math.round(agg.failFlagRate*100)+'%',
      desc:'% <em>summary</em> có ≥ 1 issue — đo theo từng summary, không phải bullet.',
      target:'≤ 15%', ok:agg.failFlagRate<=0.15,
      ringPct:agg.failFlagRate, tip:TIPS.failflag, fn:'onMC0' },
    { label:'Hallucination rate', val:Math.round(hallRate*100)+'%',
      desc:'% <em>summary</em> có issue Type A hoặc Type B — '+agg.hallN+'/'+agg.total+' summaries trong batch.',
      target:'≤ 20%', ok:hallRate<=0.20,
      ringPct:hallRate, tip:TIPS.hall, fn:'onMC1' },
    { label:'Buy violations', val:''+agg.buyN,
      desc:'Số <em>issue vi phạm quy tắc buy price</em> trong batch — rủi ro an toàn sản phẩm.',
      target:'= 0', ok:agg.buyN===0,
      ringPct:Math.min(agg.buyN/4,1), tip:TIPS.buy, fn:'onMC2' },
    { label:'% Fail/Flag bullet / summary', val:Math.round(agg.bulletFailRate*100)+'%',
      desc:'% <em>bullet point</em> bị đánh dấu — '+agg.flaggedBullets+'/'+agg.totalBullets+' bullet trên toàn batch.',
      target:'≤ 10%', ok:agg.bulletFailRate<=0.10,
      ringPct:Math.min(agg.bulletFailRate,1), tip:TIPS.bullet, fn:'onMC3' }
  ];
  document.getElementById('metricsRow').innerHTML = cards.map(function(c,i) {
    var col = c.ok ? '#12612f' : '#c0392b';
    var pillTxt = c.ok ? '✓ Trong ngưỡng (mục tiêu '+c.target+')' : '✗ Vượt ngưỡng (mục tiêu '+c.target+')';
    return '<div class="mc" id="mc'+i+'" onclick="'+c.fn+'()">'
      +'<div class="mc-label"><span>'+c.label+'</span>'
      +'<span class="tip tip-down"><i class="ic">i</i><span class="tip-box">'+c.tip+'</span></span>'
      +'</div>'
      +'<div class="mc-value" style="color:'+col+'">'+c.val+'</div>'
      +'<p class="mc-desc">'+c.desc+'</p>'
      +'<span class="mc-pill" style="background:'+(c.ok?'var(--green-bg)':'var(--red-bg)')+';color:'+col+'">'
      +pillTxt+'</span>'
      +'</div>';
  }).join('');
}

function setActiveCard(idx) {
  [0,1,2,3].forEach(function(i) {
    var el = document.getElementById('mc'+i);
    if (el) el.classList.toggle('active-card', i===idx);
  });
}

function onMC0() { setActiveCard(0); switchTab('overview'); }
function onMC1() { setActiveCard(1); filterAndDetail({cat:'hallucination'}); }
function onMC2() { setActiveCard(2); filterAndDetail({cat:'BUY'}); }
function onMC3() { setActiveCard(3); filterAndDetail({cat:'hallucination'}); }

/* ═══════════════════ OVERVIEW ═══════════════════ */
var G_LABEL = { A:'Type A — factual/logic hallucination', B:'Type B — unsupported/fabricated claim', BUY:'BUY — buy price violation', C:'Type C — disclaimer omission', FMT:'Format inconsistency' };
var G_FIX   = { A:'Review <code>prompts/judge.md</code> — tighten factual accuracy check for numbers and dates (Stage 3b)', B:"Review <code>prompts/judge.md</code> — add 'only cite text present in report' constraint (Stage 3b)", BUY:'Review <code>prompts/judge.md</code> — strengthen buy price prohibition rule (Stage 3b)', C:'Review <code>prompts/stage1_skeleton.md</code> — ensure disclaimers are extracted in skeleton (Stage 1)', FMT:'Review <code>prompts/stage2_summary.md</code> — tighten bullet format constraints (Stage 2)' };
var G_COLOR  = { A:'#c0392b', B:'#e67e22', BUY:'#e67e22', C:'#0b514b', FMT:'#8da8a5' };
var G_TO_CAT = { A:'A', B:'B', BUY:'BUY', C:'C', FMT:'FMT' };
var CAT_NAME = {
  A_factual:            'Type A — Sai lệch dữ kiện / số liệu',
  A_logic_temporal:     'Type A — Bóp méo logic / mốc thời gian',
  B_tone_escalation:    'Type B — Thổi phồng mức độ (tone escalation)',
  B_unsupported:        'Type B — Tuyên bố không có căn cứ trong report',
  C_disclaimer_omission:'Type C — Bỏ sót disclaimer / cảnh báo rủi ro',
  buy_price_absolute:   'Buy price — Đề xuất vùng giá mua cụ thể',
  buy_price_upside:     'Buy price — Upside % thiếu ngày tham chiếu'
};

function renderOverview(agg) {
  var gc = agg.groupCounts;
  var total = Object.keys(gc).reduce(function(s,k){ return s+gc[k]; }, 0);
  var sorted = Object.keys(gc).sort(function(a,b){ return gc[b]-gc[a]; });

  var fpHtml = sorted.map(function(g) {
    var n = gc[g];
    var pct = Math.round(n/total*100);
    var systemic = pct > 30;
    var col = G_COLOR[g] || '#8da8a5';
    var cat = G_TO_CAT[g] || '';
    var fix = G_FIX[g] || 'Review pipeline configuration';
    return '<div class="fp-item '+(systemic?'systemic':'')+'">'+
      '<div class="fp-top">'+
      '<div class="fp-pct">'+pct+'%</div>'+
      '<div class="fp-share">'+
      '<span class="fp-share-lbl">Chiếm '+pct+'% tổng số issue trong batch</span>'+
      '<div class="fp-track"><div class="fp-fill" style="width:'+pct+'%;background:'+col+'"></div></div>'+
      '</div>'+
      '<div class="fp-n">'+n+' issue'+(n>1?'s':'')+'</div>'+
      '</div>'+
      '<div class="fp-type">'+
      '<span class="badge b-'+g+'">'+g+'</span>'+
      '<span class="fp-name">'+(G_LABEL[g]||g)+'</span>'+
      (systemic?'<span class="sys-tag">⚠ Lỗi hệ thống (&gt;30% tổng issue)</span>':'')+
      '</div>'+
      '<div class="fp-fix"><span class="fp-fix-lbl">Suggested solution:</span><span>'+fix+'</span></div>'+
      '<button class="vd-btn" onclick="filterAndDetail({cat:\\''+cat+'\\'})">View detail <span>→</span></button>'+
      '</div>';
  }).join('');

  document.getElementById('fpList').innerHTML = fpHtml || '<p style="color:var(--dim)">No failures this batch.</p>';

  var t = agg.total, pass = agg.pass, fail = agg.fail, flag = agg.flag;
  var bsRows = [
    { lbl:'PASS',  n:pass, col:'#12612f', cls:'b-pass' },
    { lbl:'FAIL',  n:fail, col:'#c0392b', cls:'b-fail' },
    { lbl:'FLAG',  n:flag, col:'#e0a800', cls:'b-flag' }
  ];
  var bsDate = BATCH_DATE ? new Date(BATCH_DATE+'T00:00:00').toLocaleDateString('vi-VN',{day:'2-digit',month:'2-digit',year:'numeric'}) : '';
  var bsTitleEl = document.getElementById('bsTitle');
  if (bsTitleEl) bsTitleEl.innerHTML = 'Latest batch summary <span style="font-weight:400;font-size:12px;color:var(--sub)">· '+bsDate+'</span>';
  document.getElementById('bsSummary').innerHTML =
    '<p style="font-size:13px;margin:0 0 18px;color:var(--sub)">Total runs: <strong style="font-size:18px;color:var(--text)">'+t+'</strong></p>'+
    bsRows.map(function(r){ return '<div class="bs-row"><span class="badge '+r.cls+' bs-lbl">'+r.lbl+'</span><div class="bs-track"><div class="bs-fill" style="width:'+(t?r.n/t*100:0)+'%;background:'+r.col+'"></div></div><span class="bs-n">'+r.n+'</span></div>'; }).join('')+
    '<p style="font-size:12px;color:var(--dim);margin:14px 0 0;line-height:1.55">Click a failure pattern to drill into the Detailed report with filters pre-applied.</p>';
}

/* ═══════════════════ RUN LIST ═══════════════════ */
var filtered = RUNS.slice().sort(function(a,b){ return (b.created_at||'').localeCompare(a.created_at||''); });
var selId = null;

function matchCat(run, cat) {
  if (!cat) return true;
  var issues = (run.blocks||[]).concat(run.flags||[]);
  if (cat==='hallucination') return issues.some(function(i){ return i.group==='A'||i.group==='B'; });
  return issues.some(function(i){ return i.group===cat; });
}

function applyFilters() {
  var v = document.getElementById('fVerdict').value;
  var s = document.getElementById('fSymbol').value.trim().toUpperCase();
  var d = document.getElementById('fDate').value;
  var c = document.getElementById('fCategory').value;

  filtered = RUNS.filter(function(r) {
    return (!v || r.verdict===v) &&
           (!s || r.ticker.includes(s)) &&
           (!d || (r.created_at||'').slice(0,10)===d) &&
           matchCat(r,c);
  }).sort(function(a,b){ return (b.created_at||'').localeCompare(a.created_at||''); });

  var parts = [];
  if (v) parts.push('Eval status: '+v);
  if (s) parts.push('Symbol: '+s);
  if (d) parts.push('Date: '+d);
  if (c) {
    var sel = document.getElementById('fCategory');
    parts.push('Category: '+sel.options[sel.selectedIndex].text);
  }
  var bar = document.getElementById('filterBar');
  bar.classList.toggle('show', parts.length > 0);
  document.getElementById('filterBarText').textContent = 'Filtered — ' + parts.join(' · ');

  if (selId && !filtered.find(function(r){ return r.id===selId; })) { selId = null; }
  renderList();
  if (!selId && filtered.length) { selectRun(filtered[0].id); }
  else if (!filtered.length) {
    document.getElementById('detailPane').innerHTML = '<div class="d-placeholder">No runs match filters</div>';
  }
}

function clearFilters() {
  ['fVerdict','fSymbol','fDate','fCategory'].forEach(function(id) {
    document.getElementById(id).value = '';
  });
  applyFilters();
}

function renderList() {
  var body = document.getElementById('runListBody');
  if (!filtered.length) {
    body.innerHTML = '<div class="no-runs">No runs match filters</div>';
    return;
  }
  body.innerHTML = filtered.map(function(r) {
    var vc = r.verdict==='PASS'?'b-pass':r.verdict==='FAIL'?'b-fail':r.verdict==='PASS-WITH-FLAG'?'b-flag':'b-error';
    var vl = r.verdict==='PASS-WITH-FLAG'?'FLAG':r.verdict;
    var dt = r.created_at ? r.created_at.slice(0,10).replace(/^(\d{4})-(\d{2})-(\d{2})$/,'$3/$2') : '—';
    return '<div class="rl-row '+(selId===r.id?'sel':'')+'" onclick="selectRun(\\''+r.id+'\\')">'
      +'<span class="badge '+vc+'">'+vl+'</span>'
      +'<span class="rl-sym">'+r.ticker+'</span>'
      +'<span class="rl-date">'+dt+'</span>'
      +'</div>';
  }).join('');
}

function selectRun(id) {
  selId = id;
  renderList();
  var run = RUNS.find(function(r){ return r.id===id; });
  if (run) renderDetail(run);
}

/* ═══════════════════ RUN DETAIL ═══════════════════ */
function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function bulletIndexFor(run, iss) {
  if (iss && typeof iss.bullet_index === 'number' && iss.bullet_index > 0) return iss.bullet_index;
  var quote = iss && iss.summary_quote;
  if (!quote) return 0;
  var q = String(quote).trim();
  var bs = run.bullets || [];
  for (var i = 0; i < bs.length; i++) { if (bs[i].includes(q)) return i + 1; }
  var head = q.slice(0, 12);
  for (var i = 0; i < bs.length; i++) { if (bs[i].includes(head)) return i + 1; }
  return 0;
}

function issueCard(iss, idx, run) {
  var gc = iss.severity==='BLOCK'?'is-block':'is-flag';
  var sc = iss.severity==='BLOCK'?'b-fail':'b-flag';
  var srcLbl = iss.source==='deterministic'?'Deterministic check':'LLM Judge';
  var bi = run ? bulletIndexFor(run, iss) : 0;
  var bulletTag = bi
    ? '<span class="iss-bullet-tag">Bullet #'+bi+'</span>'
    : '<span class="iss-bullet-tag">Toàn summary</span>';
  var typeName = CAT_NAME[iss.category] || iss.category;
  var typeCol = (G_COLOR[iss.group]) || '#16312f';
  return '<div class="iss-card '+gc+'">'
    +'<div class="iss-hdr">'
    +bulletTag
    +'<span class="badge '+sc+'" style="font-size:10px">'+(iss.severity==='BLOCK'?'🔴 BLOCK':'🟡 FLAG')+'</span>'
    +'<span class="iss-src">'+srcLbl+'</span>'
    +'</div>'
    +'<div class="iss-type" style="color:'+typeCol+'">'+typeName+'</div>'
    +(iss.explanation?'<div class="iss-section-label">Giải thích</div><div class="iss-explanation">'+esc(iss.explanation)+'</div>':'')
    +(iss.summary_quote?'<div class="iss-section-label" style="margin-top:12px">Summary quote'+(bi?' · bullet #'+bi:'')+'</div><div class="iss-quote">"'+esc(iss.summary_quote)+'"</div>':'')
    +(iss.report_evidence?'<button class="iss-why" aria-expanded="false" onclick="toggleWhy(this)"><span class="chev">▶</span> Xem bằng chứng trong report</button><div class="iss-detail"><div class="iss-section-label" style="margin-top:4px">Report evidence</div><div class="iss-evidence">'+esc(iss.report_evidence)+'</div></div>':'')
    +'</div>';
}

function toggleWhy(btn) {
  var open = btn.getAttribute('aria-expanded') === 'true';
  btn.setAttribute('aria-expanded', String(!open));
  var detail = btn.nextElementSibling;
  detail.classList.toggle('open', !open);
  btn.lastChild.textContent = open ? ' Xem bằng chứng trong report' : ' Ẩn bằng chứng';
}

function renderDetail(run) {
  var blocks = run.blocks||[], flags = run.flags||[];
  var allIss = blocks.concat(flags);
  var vc = run.verdict==='PASS'?'b-pass':run.verdict==='FAIL'?'b-fail':run.verdict==='PASS-WITH-FLAG'?'b-flag':'b-error';
  var vl = run.verdict==='PASS-WITH-FLAG'?'PASS-WITH-FLAG':run.verdict;
  var evalDt = run.created_at ? run.created_at.slice(0,10).replace(/^(\d{4})-(\d{2})-(\d{2})$/,'$3/$2/$1') : '—';
  var rawJson = esc(JSON.stringify(allIss, null, 2));

  document.getElementById('detailPane').innerHTML =
    '<div class="d-hdr">'
    +'<div><div class="d-sym">'+run.ticker+'</div><div class="d-date">Eval date: '+evalDt+'</div></div>'
    +'<div style="display:flex;gap:8px;align-items:center;padding-top:4px"><span class="badge '+vc+'">'+vl+'</span></div>'
    +'<div class="d-time">'+(run.created_at||'').replace('T',' ')+'</div>'
    +'</div>'
    +'<div class="d-iss-row">'
    +'<span><strong>'+allIss.length+'</strong> issue'+(allIss.length!==1?'s':'')+'</span>'
    +'<span class="dot">·</span>'
    +'<span class="badge b-fail" style="font-size:11px">'+blocks.length+' block</span>'
    +'<span class="badge b-flag" style="font-size:11px">'+flags.length+' flag</span>'
    +'</div>'
    +'<hr class="d-rule">'
    +'<div class="d-sec">Summary text <span class="tip"><i class="ic">i</i><span class="tip-box">Nội dung AI summary được đánh giá. Đây là output từ mô hình AI, không phải báo cáo gốc.</span></span></div>'
    +'<div class="summary-box"><ul>'+(run.bullets||[]).map(function(b){ return '<li>'+esc(b)+'</li>'; }).join('')+'</ul></div>'
    +'<div class="d-sec" style="margin-top:24px">Issues <span style="font-size:12px;font-weight:400;color:var(--sub)">('+allIss.length+')</span>'
    +'<span class="tip"><i class="ic">i</i><span class="tip-box">BLOCK = summary phải bị reject, không được publish. FLAG = cần human review thêm, có thể publish cho user.</span></span></div>'
    +'<div class="iss-list">'
    +(allIss.length ? allIss.map(function(iss,i){ return issueCard(iss,i,run); }).join('') : '<p style="color:var(--dim);font-size:13px;margin:0">Không có issue nào.</p>')
    +'</div>'
    +'<button class="json-toggle" onclick="toggleJson(this)"><span>{ }</span> View raw JSON</button>'
    +'<pre class="json-pre">'+rawJson+'</pre>';
}

function toggleJson(btn) {
  var pre = btn.nextElementSibling;
  var vis = pre.style.display==='block';
  pre.style.display = vis ? 'none' : 'block';
  btn.querySelector('span').textContent = vis ? '{ }' : '{ · }';
  btn.childNodes[1].textContent = vis ? ' View raw JSON' : ' Hide raw JSON';
}

/* ═══════════════════ TREND ═══════════════════ */
function renderTrendChart() {
  var W=700, H=130, PL=44, PR=24, PT=16, PB=28;
  var cw=W-PL-PR, ch=H-PT-PB, n=TRENDS.length;
  var rates = TRENDS.map(function(t){ return t.total?t.pass/t.total:0; });
  function xi(i){ return n<=1 ? PL+cw/2 : PL + (i/(n-1))*cw; }
  function yr(r){ return PT + (1-r)*ch; }
  var ty = yr(0.85);
  var pts = rates.map(function(r,i){ return xi(i).toFixed(1)+','+yr(r).toFixed(1); }).join(' ');
  var area = n<=1
    ? (xi(0).toFixed(1)+','+(PT+ch)+' '+xi(0).toFixed(1)+','+yr(rates[0]).toFixed(1)+' '+xi(0).toFixed(1)+','+(PT+ch))
    : (PL+','+(PT+ch)+' '+pts+' '+(PL+cw).toFixed(1)+','+(PT+ch));
  var dots = rates.map(function(r,i){
    return '<circle cx="'+xi(i).toFixed(1)+'" cy="'+yr(r).toFixed(1)+'" r="5" fill="'+(r>=.85?'#12612f':'#c0392b')+'" stroke="white" stroke-width="2.5"/>';
  }).join('');
  var dlabels = TRENDS.map(function(t,i){
    var d = t.date.replace(/^\d{4}-(\d{2})-(\d{2})$/,'$2/$1');
    return '<text x="'+xi(i).toFixed(1)+'" y="'+(H-4)+'" text-anchor="middle" font-size="11" fill="#8da8a5">'+d+'</text>';
  }).join('');
  var ylabels = [0,.25,.5,.75,.85,1].map(function(v){
    return '<text x="'+(PL-6)+'" y="'+(yr(v).toFixed(1)*1+4)+'" text-anchor="end" font-size="10" fill="#8da8a5">'+Math.round(v*100)+'%</text>';
  }).join('');

  document.getElementById('trendChart').innerHTML =
    '<svg width="100%" viewBox="0 0 '+W+' '+H+'" style="max-width:'+W+'px;display:block;min-width:480px">'
    +'<polygon points="'+area+'" fill="rgba(11,81,75,.05)"/>'
    +'<line x1="'+PL+'" y1="'+ty.toFixed(1)+'" x2="'+(W-PR)+'" y2="'+ty.toFixed(1)+'" stroke="#e0a800" stroke-width="1.5" stroke-dasharray="6,3"/>'
    +'<text x="'+(W-PR+4)+'" y="'+(ty+4).toFixed(1)+'" font-size="10" fill="#9a5000" font-weight="700">85%</text>'
    +(n>1?'<polyline points="'+pts+'" fill="none" stroke="#0b514b" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>':'')
    +dots+dlabels+ylabels
    +'</svg>';
}

function renderTrendTable() {
  var thead = '<thead><tr><th>Date</th><th>Total</th><th>Pass rate</th><th>PASS</th><th>FAIL</th><th>FLAG</th></tr></thead>';
  var rows = TRENDS.slice().reverse().map(function(t) {
    var pr = t.total ? t.pass/t.total : 0;
    var ok = pr >= .85;
    var warn = !ok ? '<span style="background:var(--amber-bg);color:var(--amber);font-size:10px;padding:1px 6px;border-radius:4px;font-weight:700;margin-left:5px">⚠</span>' : '';
    return '<tr class="'+(ok?'':'warn-row')+'">'
      +'<td><strong>'+t.date+'</strong>'+warn+'</td>'
      +'<td>'+t.total+'</td>'
      +'<td style="font-weight:700;color:'+(ok?'#12612f':'#c0392b')+'">'+Math.round(pr*100)+'%</td>'
      +'<td>'+t.pass+'</td><td>'+t.fail+'</td><td>'+t.flag+'</td>'
      +'</tr>';
  }).join('');
  document.getElementById('trendTable').innerHTML = thead + '<tbody>'+rows+'</tbody>';
}

/* ═══════════════════ TABS & NAV ═══════════════════ */
function switchTab(name) {
  document.querySelectorAll('.tab').forEach(function(b){ b.classList.toggle('on', b.dataset.tab===name); });
  document.querySelectorAll('.panel').forEach(function(p){ p.classList.toggle('on', p.id==='panel-'+name); });
}

function filterAndDetail(opts) {
  document.getElementById('fVerdict').value  = opts.verdict || '';
  document.getElementById('fCategory').value = opts.cat || '';
  document.getElementById('fSymbol').value   = '';
  document.getElementById('fDate').value     = '';
  applyFilters();
  switchTab('detail');
}

/* ═══════════════════ INIT ═══════════════════ */
(function init() {
  renderMetrics(AGG);
  renderOverview(AGG);
  renderList();
  if (filtered.length) { selectRun(filtered[0].id); }
  if (TRENDS.length) { renderTrendChart(); renderTrendTable(); }

  document.getElementById('tabNav').addEventListener('click', function(e) {
    var btn = e.target.closest('.tab');
    if (btn) { switchTab(btn.dataset.tab); setActiveCard(-1); }
  });

  document.getElementById('fVerdict').addEventListener('change', applyFilters);
  document.getElementById('fCategory').addEventListener('change', applyFilters);
  document.getElementById('fSymbol').addEventListener('input', applyFilters);
  document.getElementById('fDate').addEventListener('change', applyFilters);
})();"""

    head = (
        "<!doctype html>\n<html lang=\"vi\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
        "<title>AI Summary Judge Dashboard</title>\n"
        + css + "\n</head>\n<body>\n"
    )

    body = html_body.replace("HEADER_META_PLACEHOLDER", header_meta)

    return (
        head
        + body
        + "\n<script>\n"
        + data_block
        + "\n"
        + js_logic
        + "\n</script>\n</body>\n</html>"
    )
