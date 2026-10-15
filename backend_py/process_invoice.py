"""Send an invoice to Azure Document Intelligence and save its full response.
Dependencies: requests, python-dotenv
"""

import base64
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

from dotenv import load_dotenv
import requests


BASE_DIR = Path(__file__).resolve().parent
API_VERSION = "2024-11-30"
MODEL_ID = "prebuilt-invoice"
REQUEST_TIMEOUT_SECONDS = 60
POLL_INTERVAL_SECONDS = 2
MAX_WAIT_SECONDS = 300


def check_response(response: requests.Response, expected_status: int) -> None:
    """Include Azure's error message when an HTTP request fails."""
    if response.status_code == expected_status:
        return
    message = ""
    try:
        body = response.json()
        message = (body.get("error") or {}).get("message", "")
    except (ValueError, AttributeError):
        pass
    raise RuntimeError(
        f"Azure returned HTTP {response.status_code}."
        + (f" {message}" if message else "")
    )


def wait_for_result(session: requests.Session, operation_url: str, headers: dict) -> dict:
    """Poll the operation URL until Azure succeeds, fails, or times out."""
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Timed out waiting for Azure to finish processing.")

        response = session.get(
            operation_url,
            headers=headers,
            timeout=min(REQUEST_TIMEOUT_SECONDS, remaining),
            allow_redirects=False,
        )
        check_response(response, 200)
        body = response.json()
        status = body.get("status")

        if status == "succeeded":
            return body  # Preserve the full operation response, not just analyzeResult.
        if status == "failed":
            message = (body.get("error") or {}).get("message", "Invoice analysis did not succeed.")
            raise RuntimeError(message)
        if status not in ("notStarted", "running"):
            raise RuntimeError(f"Unexpected Azure analysis status: {status!r}.")

        time.sleep(min(POLL_INTERVAL_SECONDS, max(0, deadline - time.monotonic())))


def analyze_invoice(file_contents: bytes) -> dict:
    """Analyze uploaded bytes and return Azure's full operation response."""
    # Resolve configuration and default file paths relative to this script,
    # so it works even when launched from the Mark1 root.
    load_dotenv(BASE_DIR / ".env.local", override=False)
    endpoint = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "").strip().rstrip("/")
    key = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", "").strip()
    if not endpoint or not key:
        raise RuntimeError("Azure endpoint or key is missing from .env.local or the environment.")

    endpoint_parts = urlsplit(endpoint)
    if endpoint_parts.scheme != "https" or not endpoint_parts.netloc:
        raise RuntimeError("The Azure endpoint must be a valid HTTPS resource endpoint.")

    base64_source = base64.b64encode(file_contents).decode("ascii")
    analyze_url = f"{endpoint}/documentintelligence/documentModels/{MODEL_ID}:analyze"
    headers = {"Ocp-Apim-Subscription-Key": key}

    with requests.Session() as session:
        print("Sending invoice to Azure...")
        initial_response = session.post(
            analyze_url,
            params={"api-version": API_VERSION, "_overload": "analyzeDocument"},
            headers=headers,
            json={"base64Source": base64_source},
            timeout=REQUEST_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        check_response(initial_response, 202)
        operation_url = initial_response.headers.get("Operation-Location")
        if not operation_url:
            raise RuntimeError("Azure did not return an Operation-Location header.")

        # Send the subscription key only to the same HTTPS resource endpoint.
        operation_parts = urlsplit(operation_url)
        if (operation_parts.scheme, operation_parts.netloc) != (endpoint_parts.scheme, endpoint_parts.netloc):
            raise RuntimeError("Azure returned an unexpected operation endpoint.")

        print("Waiting for Azure to finish processing...")
        raw_result = wait_for_result(session, operation_url, headers)

    return raw_result


def process_invoice(invoice_path: Path | None = None) -> Path:
    """Analyze a local invoice and save Azure's full response beside this script."""
    invoice_path = Path(invoice_path) if invoice_path is not None else BASE_DIR / "invoices" / "IMG_3807.jpeg"
    raw_result = analyze_invoice(invoice_path.read_bytes())
    output_dir = BASE_DIR / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"invoice-{time.time_ns() // 1_000_000}.raw.json"
    serialized = json.dumps(raw_result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    # Never overwrite an existing extraction.
    with output_path.open("x", encoding="utf-8") as output:
        output.write(serialized)

    print(f"Raw JSON saved to: {output_path}")
    return output_path


def main() -> int:
    try:
        process_invoice()
        return 0
    except (OSError, RuntimeError, ValueError, requests.RequestException) as error:
        print(f"Processing failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
