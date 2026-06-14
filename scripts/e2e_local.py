#!/usr/bin/env python3
"""
E2E local test: pick 1 unevaluated report → run pipeline → start server → open dashboard.

Usage:
    python scripts/e2e_local.py           # uses first unevaluated precreated summary
    python scripts/e2e_local.py GMD       # pick a specific ticker
    python scripts/e2e_local.py --mock    # dry-run with MOCK_LLM_MODE=true (fast, no tokens)

Flow:
  1. Fetch 1 unevaluated precreated summary from Supabase
  2. Run evaluate_record_safely() in-process — real LLM unless --mock
  3. Start uvicorn on port 8080
  4. Open http://localhost:8080/dashboard in browser
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Parse CLI args before dotenv so --mock overrides the file
args = sys.argv[1:]
mock_mode = "--mock" in args
ticker_filter = next((a for a in args if not a.startswith("--")), None)

if mock_mode:
    os.environ["MOCK_LLM_MODE"] = "true"
    print("Mock mode: MOCK_LLM_MODE=true (no LLM tokens used)")

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import httpx
from pipeline.persist import SupabaseStore

PORT = 8080
BASE_URL = f"http://localhost:{PORT}"


def pick_record(store: SupabaseStore) -> dict | None:
    """Return first unevaluated precreated summary, optionally filtered by ticker."""
    records = store.fetch_unevaluated_summaries(limit=20, summary_model="precreated")
    if not records:
        return None
    if ticker_filter:
        match = [r for r in records if (r.get("report") or {}).get("ticker", "").upper() == ticker_filter.upper()]
        if not match:
            available = [r.get("report", {}).get("ticker", "?") for r in records]
            print(f"Ticker '{ticker_filter}' not found in unevaluated precreated summaries.")
            print(f"Available: {available}")
            return None
        return match[0]
    return records[0]


def wait_for_server(timeout: int = 30) -> bool:
    for _ in range(timeout):
        try:
            r = httpx.get(f"{BASE_URL}/health", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def main() -> None:
    print("=== E2E Local Test ===\n")

    # ── Step 1: Pick record ───────────────────────────────────────────────────
    print("[1/4] Fetching unevaluated precreated summary from Supabase...")
    store = SupabaseStore()
    record = pick_record(store)
    if record is None:
        print("No unevaluated precreated summaries found — all records already evaluated.")
        print("Run the server and open /dashboard to see results:")
        print(f"  uvicorn main:app --port {PORT}")
        sys.exit(0)

    ticker = (record.get("report") or {}).get("ticker", "?")
    report_date = (record.get("report") or {}).get("report_date", "?")
    summary_id = (record.get("summary") or {}).get("id", "?")
    print(f"  Ticker      : {ticker}")
    print(f"  Report date : {report_date}")
    print(f"  Summary id  : {summary_id}")

    # ── Step 2: Run pipeline ──────────────────────────────────────────────────
    from main import evaluate_record_safely
    print(f"\n[2/4] Running pipeline on {ticker}...")
    if not mock_mode:
        print("  Stage 1  — skeleton extraction (Gemini, may take 30-90s)")
        print("  Stage 1b — citation alignment  (Gemini, may take 30-90s)")
        print("  Stage 3b — LLM judge           (GPT-5 Mini)")
        print("  Stage 3a — deterministic factcheck\n")

    t0 = time.time()
    outcome = evaluate_record_safely(record, store)
    elapsed = time.time() - t0

    result = outcome.get("result", {})
    verdict = result.get("verdict", "?")
    blocks = result.get("blocks", [])
    flags = result.get("flags", [])
    eval_run = outcome.get("eval_run", {})

    print(f"\n  ── Pipeline result ({elapsed:.0f}s) ──────────────────")
    print(f"  Verdict    : {verdict}")
    print(f"  Blocks     : {len(blocks)}")
    print(f"  Flags      : {len(flags)}")
    print(f"  Eval run id: {eval_run.get('id', '(not persisted)')}")
    for b in blocks:
        print(f"    BLOCK [{b.get('category')}] {b.get('summary_quote', '')[:70]}")
    for f in flags:
        print(f"    FLAG  [{f.get('category')}] {f.get('summary_quote', '')[:70]}")

    rationale = (result.get("judge_json") or result).get("rationale", "")
    if rationale:
        print(f"\n  Judge rationale:\n  {rationale[:300]}")

    # ── Step 3: Start server ──────────────────────────────────────────────────
    print(f"\n[3/4] Starting uvicorn on port {PORT}...")
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", str(PORT)],
        cwd=str(ROOT),
    )

    try:
        if not wait_for_server(30):
            print("ERROR: server did not respond within 30s — check for port conflicts")
            server.terminate()
            sys.exit(1)
        print("  Server ready.")

        # ── Step 4: Open dashboard ────────────────────────────────────────────
        url = f"{BASE_URL}/dashboard"
        print(f"\n[4/4] Opening dashboard: {url}")
        webbrowser.open(url)
        print("\nPress Ctrl+C to stop the server.")
        server.wait()

    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.terminate()
        server.wait()
        print("Done.")


if __name__ == "__main__":
    main()
