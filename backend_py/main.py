from asyncio import to_thread
import json
import logging
import os
from pathlib import Path
from uuid import uuid4

import requests
from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException, UploadFile

from extract_invoice_table import build_table_dataframe
from process_invoice import analyze_invoice

settings = dotenv_values(Path(__file__).parent / ".env.local")
supabase_url = os.getenv("SUPABASE_URL") or settings["SUPABASE_URL"]
supabase_secret_key = os.getenv("SUPABASE_SECRET_KEY") or settings["SUPABASE_SECRET_KEY"]

app = FastAPI()


def save_file(session, storage_path, contents, content_type):
    response = session.post(
        f"{supabase_url.rstrip('/')}/storage/v1/object/invoices/{storage_path}",
        headers={"Content-Type": content_type, "x-upsert": "false"},
        data=contents, timeout=60,
    )
    response.raise_for_status()


def process_uploaded_invoice(file_contents, filename, content_type):
    invoice_id = str(uuid4())
    original_path = f"{invoice_id}/original{Path(filename or '').suffix.lower()}"
    saved_files = {}
    stage = "saving the original invoice"
    try:
        with requests.Session() as session:
            session.headers["apikey"] = supabase_secret_key
            if supabase_secret_key.startswith("eyJ"):
                session.headers["Authorization"] = f"Bearer {supabase_secret_key}"

            save_file(session, original_path, file_contents, content_type or "application/octet-stream")
            saved_files["original"] = original_path

            stage = "extracting the invoice with Azure"
            raw_data = analyze_invoice(file_contents)

            stage = "saving the Azure JSON"
            raw_path = f"{invoice_id}/raw.json"
            raw_json = json.dumps(raw_data, indent=2, ensure_ascii=False).encode("utf-8")
            save_file(session, raw_path, raw_json, "application/json")
            saved_files["raw_json"] = raw_path

            stage = "mapping the invoice table"
            df = build_table_dataframe(raw_data)
            csv_contents = df.to_csv(index=False, header=False).encode("utf-8")

            stage = "saving the CSV"
            csv_path = f"{invoice_id}/table.csv"
            save_file(session, csv_path, csv_contents, "text/csv; charset=utf-8")
            saved_files["csv"] = csv_path
    except (requests.RequestException, RuntimeError, OSError, ValueError, KeyError, IndexError, TypeError) as error:
        logging.exception("Invoice %s failed while %s", invoice_id, stage)
        raise HTTPException(
            status_code=502,
            detail={
                "message": f"Processing failed while {stage}. Previously saved files remain in the invoice folder.",
                "invoice_id": invoice_id,
                "files": saved_files,
            },
        ) from error

    return {
        "filename": filename,
        "invoice_id": invoice_id,
        "storage_path": original_path,
        "files": saved_files,
    }


@app.post("/api/invoices")
async def upload_invoice(invoice: UploadFile):
    try:
        file_contents = await invoice.read()
        return await to_thread(process_uploaded_invoice, file_contents, invoice.filename, invoice.content_type)
    finally:
        await invoice.close()
