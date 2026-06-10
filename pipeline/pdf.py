from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import fitz
import httpx

from config import Settings, get_settings


def validate_pdf_source(source: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        host = parsed.hostname.lower() if parsed.hostname else ""
        if host not in settings.allowed_pdf_hosts:
            raise ValueError(f"PDF host is not allowed: {host}")
        return

    path = Path(source).resolve()
    data_dir = Path("data").resolve()
    if data_dir not in path.parents and path != data_dir:
        raise ValueError("Local PDF path must be inside data/")


def extract_pdf_text_from_bytes(pdf_bytes: bytes) -> str:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
        return "\n".join(page.get_text("text") for page in document).strip()


def extract_pdf_text(source: str, settings: Settings | None = None) -> str:
    validate_pdf_source(source, settings)
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        with httpx.Client(timeout=60) as client:
            response = client.get(source)
            response.raise_for_status()
        return extract_pdf_text_from_bytes(response.content)

    return extract_pdf_text_from_bytes(Path(source).read_bytes())

