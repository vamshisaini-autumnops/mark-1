import io
import json
import unittest
from unittest.mock import patch

import pandas as pd
from fastapi import HTTPException

from extract_invoice_table import build_table_dataframe
from main import process_uploaded_invoice


def azure_result():
    return {
        "status": "succeeded",
        "analyzeResult": {"tables": [
            {"rowCount": 1, "columnCount": 1, "cells": [
                {"rowIndex": 0, "columnIndex": 0, "content": "Invoice"},
            ]},
            {"rowCount": 3, "columnCount": 2, "cells": [
                {"rowIndex": 0, "columnIndex": 0, "content": "Item ID"},
                {"rowIndex": 0, "columnIndex": 1, "content": "Description"},
                {"rowIndex": 1, "columnIndex": 0, "content": "00123"},
                {"rowIndex": 1, "columnIndex": 1, "content": 'Text, "quoted"\n:selected:'},
                {"rowIndex": 2, "columnIndex": 0, "content": "NA"},
            ]},
        ]},
    }


class InvoicePipelineTests(unittest.TestCase):
    def test_csv_round_trip_preserves_raw_strings_and_headers(self):
        frame = build_table_dataframe(azure_result())
        restored = pd.read_csv(
            io.StringIO(frame.to_csv(index=False, header=False)),
            header=None, dtype=str, keep_default_na=False,
        )
        self.assertEqual(restored.values.tolist(), [
            ["Item ID", "Description"],
            ["00123", 'Text, "quoted"\n:selected:'],
            ["NA", ""],
        ])

    @patch("main.requests.Session")
    @patch("main.uuid4", return_value="test-invoice")
    def test_original_then_azure_then_json_and_csv(self, _uuid, _session):
        events = []
        saved = {}

        def save(_session, path, contents, _content_type):
            events.append(path)
            saved[path] = contents

        def analyze(contents):
            self.assertEqual(contents, b"invoice bytes")
            events.append("azure")
            return azure_result()

        with patch("main.save_file", side_effect=save), patch("main.analyze_invoice", side_effect=analyze):
            result = process_uploaded_invoice(b"invoice bytes", "Invoice.JPEG", "image/jpeg")

        self.assertEqual(events, [
            "test-invoice/original.jpeg", "azure", "test-invoice/raw.json", "test-invoice/table.csv",
        ])
        self.assertEqual(saved[result["files"]["original"]], b"invoice bytes")
        self.assertEqual(json.loads(saved[result["files"]["raw_json"]]), azure_result())
        self.assertEqual(len(result["files"]), 3)

    @patch("main.requests.Session")
    @patch("main.save_file")
    @patch("main.analyze_invoice", side_effect=TimeoutError("Azure timeout"))
    def test_azure_failure_reports_original_saved(self, _analyze, save, _session):
        with self.assertRaises(HTTPException) as caught:
            process_uploaded_invoice(b"invoice", "original.pdf", "application/pdf")
        self.assertEqual(save.call_count, 1)
        self.assertEqual(set(caught.exception.detail["files"]), {"original"})
        self.assertIn("extracting", caught.exception.detail["message"])

    @patch("main.requests.Session")
    @patch("main.save_file")
    @patch("main.analyze_invoice", return_value={"analyzeResult": {"tables": []}})
    def test_no_table_preserves_original_and_raw_json(self, _analyze, save, _session):
        with self.assertRaises(HTTPException) as caught:
            process_uploaded_invoice(b"invoice", "original.pdf", "application/pdf")
        self.assertEqual(save.call_count, 2)
        self.assertEqual(set(caught.exception.detail["files"]), {"original", "raw_json"})
        self.assertIn("mapping", caught.exception.detail["message"])


if __name__ == "__main__":
    unittest.main()
