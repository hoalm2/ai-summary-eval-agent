from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from supabase import Client, create_client

from config import Settings, get_settings, require_env


class SupabaseStore:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.client: Client = create_client(
            require_env("SUPABASE_URL", self.settings.supabase_url),
            require_env("SUPABASE_SERVICE_ROLE_KEY", self.settings.supabase_service_role_key),
        )

    def healthcheck(self) -> bool:
        self.client.table("agent_state").select("key").limit(1).execute()
        return True

    def get_state(self, key: str, default: Any = None) -> Any:
        response = self.client.table("agent_state").select("value").eq("key", key).limit(1).execute()
        if not response.data:
            return default
        return response.data[0].get("value", default)

    def set_state(self, key: str, value: Any) -> None:
        payload = {"key": key, "value": value, "updated_at": datetime.now(timezone.utc).isoformat()}
        self.client.table("agent_state").upsert(payload, on_conflict="key").execute()

    def fetch_summaries(self, *, offset: int = 0, limit: int = 5) -> list[dict[str, Any]]:
        summaries = (
            self.client.table("summaries")
            .select("id, report_id, summary_text, summary_model, created_at")
            .order("created_at")
            .range(offset, offset + limit - 1)
            .execute()
            .data
            or []
        )
        return self._attach_reports(summaries)

    def fetch_demo_summaries(self, *, limit: int = 2) -> list[dict[str, Any]]:
        summaries = (
            self.client.table("summaries")
            .select("id, report_id, summary_text, summary_model, created_at")
            .order("created_at")
            .limit(limit)
            .execute()
            .data
            or []
        )
        return self._attach_reports(summaries)

    def fetch_unevaluated_reports(self, *, limit: int = 5) -> list[dict[str, Any]]:
        eval_runs = self.client.table("eval_runs").select("report_id").execute().data or []
        evaluated_report_ids = {item["report_id"] for item in eval_runs if item.get("report_id")}
        reports = (
            self.client.table("reports")
            .select("id, ticker, report_date, source_pdf_url, pdf_storage_path, report_text, status, created_at")
            .order("created_at")
            .limit(200)
            .execute()
            .data
            or []
        )
        return [
            report
            for report in reports
            if report.get("id") not in evaluated_report_ids and (report.get("report_text") or report.get("source_pdf_url"))
        ][:limit]

    def _attach_reports(self, summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        report_ids = [item["report_id"] for item in summaries if item.get("report_id")]
        if not report_ids:
            return []
        reports = (
            self.client.table("reports")
            .select("id, ticker, report_date, source_pdf_url, pdf_storage_path, report_text, status, created_at")
            .in_("id", report_ids)
            .execute()
            .data
            or []
        )
        report_by_id = {item["id"]: item for item in reports}
        records: list[dict[str, Any]] = []
        for summary in summaries:
            report = report_by_id.get(summary.get("report_id"))
            if not report:
                continue
            records.append({"summary": summary, "report": report})
        return records

    def update_report_text(self, report_id: str, report_text: str) -> None:
        self.client.table("reports").update({"report_text": report_text, "status": "ready"}).eq("id", report_id).execute()

    def find_existing_report(self, *, ticker: str | None, report_date: str | None, source_pdf_url: str | None) -> dict[str, Any] | None:
        query = self.client.table("reports").select("id, ticker, report_date, source_pdf_url, status")
        if source_pdf_url:
            response = query.eq("source_pdf_url", source_pdf_url).limit(1).execute()
            return response.data[0] if response.data else None
        if ticker and report_date:
            response = query.eq("ticker", ticker).eq("report_date", report_date).limit(1).execute()
            return response.data[0] if response.data else None
        return None

    def insert_report(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.client.table("reports").insert(payload).execute()
        return response.data[0]

    def insert_summary(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.client.table("summaries").insert(payload).execute()
        return response.data[0]

    def insert_eval_run(
        self,
        *,
        report_id: str,
        summary_id: str,
        skeleton_json: dict[str, Any],
        judge_json: dict[str, Any],
        verdict: str,
        blocks: list[dict[str, Any]],
        flags: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = {
            "report_id": report_id,
            "summary_id": summary_id,
            "skeleton_json": skeleton_json,
            "judge_json": judge_json,
            "verdict": verdict,
            "blocks": blocks,
            "flags": flags,
        }
        response = self.client.table("eval_runs").insert(payload).execute()
        return response.data[0]

    def fetch_eval_runs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        runs = (
            self.client.table("eval_runs")
            .select("id, report_id, summary_id, skeleton_json, judge_json, verdict, blocks, flags, created_at")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
            .data
            or []
        )
        if not runs:
            return []
        report_ids = [run["report_id"] for run in runs if run.get("report_id")]
        reports = (
            self.client.table("reports")
            .select("id, ticker, report_date, source_pdf_url, status")
            .in_("id", report_ids)
            .execute()
            .data
            or []
        )
        report_by_id = {item["id"]: item for item in reports}
        safe_runs: list[dict[str, Any]] = []
        for run in runs:
            safe_runs.append({**run, "report": report_by_id.get(run.get("report_id"), {})})
        return safe_runs

    def aggregate(self) -> dict[str, Any]:
        runs = self.fetch_eval_runs(limit=1000)
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
                if category in {"A_factual", "B_unsupported"}:
                    hallucination_count += 1
                if category.startswith("buy_price"):
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
