"""Parse Azure prebuilt-invoice JSON for the MLCC / Delivered By layout.

Python 3.10+; standard library only (no pip packages required).
This is a layout-specific parser, not a universal invoice parser.
Review candidates before database insertion.

Command line:
    python3 parse_mlcc_invoice.py input.raw.json [output.parsed.json]

Import:
    from parse_mlcc_invoice import parse_invoice
    parsed = parse_invoice(raw, {"rawFile": "input.raw.json"})

The output schema and review rules match the supplied JavaScript parser.
The garbled issuer-name dash in the source is corrected to an em dash.
Monetary calculations use integer cents, never floating-point arithmetic.
This module parses an existing Azure response; it does not call Azure, upload
invoices, expose a web API, or insert records into a database.
"""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

VERSION = "1.1.0"
MAX_SAFE_INTEGER = 2**53 - 1  # Preserve the JavaScript quantity limit.


def clean(text: Any) -> str:
    text = "" if text is None else str(text)
    return re.sub(r"\s+", " ", re.sub(r":(?:un)?selected:", "", text)).strip()


def cell_text(cell: dict | None) -> str:
    return clean((cell or {}).get("content"))


def numeric_lines(text: Any) -> list[str]:
    text = re.sub(r":(?:un)?selected:", "", "" if text is None else str(text))
    return [line.strip() for line in text.split("\n")
            if re.fullmatch(r"[0-9]+", line.strip())]


def money(text: Any) -> str | None:
    # Reject unexpected formats instead of silently interpreting them.
    match = re.fullmatch(r"\$?((?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+))\.([0-9]{2})", clean(text))
    return f"{match[1].replace(',', '')}.{match[2]}" if match else None


def cents(text: str | None) -> int | None:
    return int(text.replace(".", "")) if text is not None else None


def decimal(value: int) -> str:
    return f"{value // 100}.{value % 100:02d}"


def quantity(text: Any) -> int | None:
    value = clean(text)
    if not re.fullmatch(r"[0-9]+", value):
        return None
    number = int(value)
    return number if number <= MAX_SAFE_INTEGER else None


def date_from_label(content: str, label: str) -> str | None:
    match = re.search(re.escape(label) + r"\s+(?:[A-Za-z]{3}\s+)?([0-9]{2})/([0-9]{2})/([0-9]{4})", content)
    if not match:
        return None
    month, day, year = map(int, match.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def packaging(text: str, description: str) -> dict:
    regular = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(ml|l)\s*/\s*([0-9]+)(?:\s+(.*))?", text, re.I)
    multipack = re.fullmatch(r"([0-9]+)pk\s*/\s*([0-9]+(?:\.[0-9]+)?)\s*(ml|l)\s*(.*?)\s*/\s*([0-9]+)", text, re.I)
    if regular:
        return {
            "description": description or regular[4] or None,
            "volume_ml": float(regular[1]) * (1000 if regular[2].lower() == "l" else 1),
            "units_per_case": int(regular[3]),
            "bottles_per_unit": 1,
            "selling_unit": "bottle",
        }
    if multipack:
        return {
            "description": description or multipack[4] or None,
            "volume_ml": float(multipack[2]) * (1000 if multipack[3].lower() == "l" else 1),
            "units_per_case": int(multipack[5]),
            "bottles_per_unit": int(multipack[1]),
            "selling_unit": "multipack",
        }
    return {"description": description or None, "volume_ml": None,
            "units_per_case": None, "bottles_per_unit": None, "selling_unit": None}


def center(polygon: list | None, angle: float = 0) -> dict | None:
    if not polygon:
        return None
    n = len(polygon) / 2
    x = sum(value / n for value in polygon[::2])
    y = sum(value / n for value in polygon[1::2])
    theta = -angle * math.pi / 180
    return {"x": x * math.cos(theta) - y * math.sin(theta),
            "y": x * math.sin(theta) + y * math.cos(theta)}


def upc_checksum(value: str | None) -> bool:
    if not re.fullmatch(r"[0-9]{12}", value or ""):
        return False
    return sum(int(digit) * (3 if i % 2 == 0 else 1)
               for i, digit in enumerate(value)) % 10 == 0


def table_layout(table: dict) -> dict | None:
    cells = table.get("cells") or []

    def find(pattern: str) -> dict | None:
        return next((cell for cell in cells if cell["rowIndex"] < 2
                     and re.search(pattern, clean(cell.get("content")), re.I)), None)

    id_header = find(r"Item ID\s+MLCC ID\s+Size\s*/\s*Pack")
    shelf, base, cases = find(r"^Shelf$"), find(r"^Base$"), find(r"^Cases$")
    bottles, price, total = find(r"^Btl"), find(r"^Price$"), find(r"^Total$")
    if any(header is None for header in (id_header, shelf, base, cases, bottles, price, total)):
        return None
    # Item ID + MLCC ID share one column, followed by Size/Pack and Item.
    # Reject a different topology rather than guessing.
    if id_header.get("columnSpan", 1) != 3 or shelf["columnIndex"] != id_header["columnIndex"] + 3:
        return None
    return {
        "ids": id_header["columnIndex"], "size": id_header["columnIndex"] + 1,
        "description": id_header["columnIndex"] + 2,
        "shelf": shelf["columnIndex"], "base": base["columnIndex"],
        "cases": cases["columnIndex"], "bottles": bottles["columnIndex"],
        "price": price["columnIndex"], "total": total["columnIndex"],
        "firstDataRow": max(id_header["rowIndex"] + id_header.get("rowSpan", 1),
                            cases["rowIndex"] + 1, bottles["rowIndex"] + 1),
    }


def source_cell(cell: dict) -> dict:
    return {"column_index": cell["columnIndex"], "row_span": cell.get("rowSpan", 1),
            "column_span": cell.get("columnSpan", 1), "raw_text": cell.get("content") or "",
            "kind": cell.get("kind"), "bounding_regions": cell.get("boundingRegions") or [],
            "spans": cell.get("spans") or []}


def field_candidate(field: dict | None) -> dict | None:
    if not field:
        return None
    typed = next((value for key, value in field.items() if key.startswith("value")), None)
    return {"value": typed, "raw_text": field.get("content"),
            "confidence": field.get("confidence"),
            "bounding_regions": field.get("boundingRegions") or []}


def first_polygon(cell: dict | None) -> list | None:
    regions = (cell or {}).get("boundingRegions") or []
    return regions[0].get("polygon") if regions else None


def parse_invoice(raw: dict, options: dict | None = None) -> dict:
    """Return reviewable invoice data from a completed Azure extraction.

    ``options`` accepts the original ``rawFile`` and ``rawSha256`` keys.
    Raises ValueError for unsuccessful extraction, unsupported layout, missing
    product-page evidence, or an invoice without supported product rows.
    """
    options = options or {}
    if raw.get("status") != "succeeded":
        raise ValueError("Extraction has not succeeded; no parsed invoice was produced.")
    result = raw.get("analyzeResult")
    if not result or result.get("modelId") != "prebuilt-invoice":
        raise ValueError("Expected Azure prebuilt-invoice analyzeResult.")
    content = result.get("content") or ""
    if not re.search("Liquor Control Commission", content, re.I) or not re.search("Delivered By", content, re.I):
        raise ValueError("Unsupported invoice layout. This parser requires an MLCC / Delivered By invoice.")

    issues, items, non_product_rows = [], [], []

    def issue(code: str, message: str, item: dict | None = None, details: dict | None = None) -> str:
        identifier = f"issue-{len(issues) + 1}"
        issues.append({"id": identifier, "code": code, "severity": "review",
                       "line_number": item["line_number"] if item is not None else None,
                       "message": message, **(details or {})})
        if item is not None:
            item["validation"]["issue_ids"].append(identifier)
        return identifier

    documents = result.get("documents") or []
    fields = (documents[0].get("fields") or {}) if documents else {}

    def field_value(name: str, key: str = "valueString") -> Any:
        return (fields.get(name) or {}).get(key)

    def capture(pattern: str) -> str | None:
        match = re.search(pattern, content)
        return match[1] if match else None

    delivered_by = capture(r"Delivered By\s*\n(?:License:[^\n]*\n)?([^\n]+)")
    issuer_candidate = field_value("VendorName")
    invoice = {
        "invoice_number": field_value("InvoiceId"),
        "customer_name": field_value("VendorAddressRecipient"),
        "customer_id": capture(r"Cust ID:\s*([0-9]+)"),
        "license_number": capture(r"License:\s*([0-9]+)"),
        "issuer_name": (f"{clean(issuer_candidate)} — Liquor Control Commission"
                        if issuer_candidate and re.search("Michigan Department", issuer_candidate, re.I) else None),
        "delivered_by": delivered_by.strip() if delivered_by is not None else None,
        "customer_address": field_value("VendorAddress", "valueAddress"),
        "order_date": date_from_label(content, "Ordered On"),
        "scheduled_delivery_date": date_from_label(content, "Sched Delv"),
        "invoice_date": None, "currency": "USD", "invoice_total": None,
        "tax_total": None, "identities_verified": False,
    }
    issue("HEADER_ROLES_REQUIRE_REVIEW", "Customer and issuer roles are provisional template-based mappings; the model assigns some roles incorrectly.",
          details={"fields": ["customer_name", "customer_address", "issuer_name", "delivered_by"]})
    if fields.get("InvoiceDate") is not None:
        issue("INVOICE_DATE_UNVERIFIED", "The model date is retained as evidence. Dates labeled Ordered On and Sched Delv are stored separately; invoice_date remains null.")
    if not invoice["invoice_number"]:
        issue("INVOICE_NUMBER_MISSING", "Invoice number was not extracted.")
    if invoice["invoice_total"] is None:
        issue("INVOICE_TOTAL_MISSING", "No verified full invoice total is available.")

    matched_tables = 0
    for table_index, table in enumerate(result.get("tables") or []):
        layout = table_layout(table)
        if layout is None:
            continue
        matched_tables += 1
        regions = table.get("boundingRegions") or []
        page_number = regions[0].get("pageNumber") if regions else None
        page = next((p for p in (result.get("pages") or []) if p.get("pageNumber") == page_number), None)
        if page is None:
            raise ValueError(f"Missing page evidence for product table {table_index}.")
        rows: dict[int, list[dict]] = {}
        for cell in table["cells"]:
            rows.setdefault(cell["rowIndex"], []).append(cell)
        group_label = None
        table_anchors = []
        for row_index, cells in sorted(rows.items()):
            if row_index < layout["firstDataRow"]:
                continue

            def at(index: int) -> dict | None:
                return next((cell for cell in cells if cell["columnIndex"] == index), None)

            ids = numeric_lines((at(layout["ids"]) or {}).get("content"))
            size_cell, description_cell = at(layout["size"]), at(layout["description"])
            total, price = money(cell_text(at(layout["total"]))), money(cell_text(at(layout["price"])))
            label = next((cell_text(c) for c in cells
                          if re.fullmatch(r"(?:Lockup|Split) Box\s+[0-9]+", cell_text(c), re.I)), None)
            if not ids and total is None and price is None:
                if label:
                    group_label = label
                non_product_rows.append({"page_number": page_number, "table_index": table_index,
                                         "row_index": row_index,
                                         "reason": "box_group_label" if label else "non_product_content",
                                         "cells": [source_cell(cell) for cell in cells]})
                continue
            pack = packaging(cell_text(size_cell), cell_text(description_cell))
            cases = quantity((at(layout["cases"]) or {}).get("content"))
            loose = quantity((at(layout["bottles"]) or {}).get("content"))
            total_units = (cases * pack["units_per_case"] + loose
                           if cases is not None and loose is not None and pack["units_per_case"] is not None else None)
            price_cents, total_cents = cents(price), cents(total)
            inferred = None
            if (price_cents is not None and price_cents > 0 and total_cents is not None
                    and total_cents % price_cents == 0
                    and total_cents // price_cents <= MAX_SAFE_INTEGER):
                inferred = total_cents // price_cents
            item = {
                "line_number": len(items) + 1, "item_id": ids[0] if ids else None,
                "mlcc_id": ids[1] if len(ids) > 1 else None,
                **pack, "raw_size_pack": cell_text(size_cell), "box_group": group_label,
                "shelf_price": money(cell_text(at(layout["shelf"]))),
                "base_price": money(cell_text(at(layout["base"]))),
                "purchase_unit_price": price, "line_total": total,
                "cases_shipped": cases, "loose_units_shipped": loose, "total_units": total_units,
                "physical_bottle_count": (total_units * pack["bottles_per_unit"]
                                          if total_units is not None and pack["bottles_per_unit"] is not None else None),
                "inferred_total_units": inferred, "upc": None, "upc_candidates": [],
                "validation": {"status": "needs_review", "quantity_price_check": "uncheckable", "issue_ids": []},
                "source": {"page_number": page_number, "table_index": table_index,
                           "row_index": row_index, "cells": [source_cell(cell) for cell in cells]},
            }
            # Retain word-level candidates inside each quantity cell. These
            # remain evidence, never automatic replacements for cell values.
            item["quantity_word_candidates"] = {}
            for name, column in (("cases", layout["cases"]), ("loose_units", layout["bottles"])):
                polygon = first_polygon(at(column))
                candidates = []
                if polygon:
                    points = [center(polygon[j:j + 2], page.get("angle") or 0)
                              for j in range(0, len(polygon), 2)]
                    xmin, xmax = min(p["x"] for p in points), max(p["x"] for p in points)
                    ymin, ymax = min(p["y"] for p in points), max(p["y"] for p in points)
                    for word in page.get("words") or []:
                        point = center(word.get("polygon"), page.get("angle") or 0)
                        if point and xmin <= point["x"] <= xmax and ymin <= point["y"] <= ymax:
                            candidates.append({"text": word["content"],
                                               "confidence": word.get("confidence"),
                                               "polygon": word.get("polygon"),
                                               "span": word.get("span")})
                item["quantity_word_candidates"][name] = candidates
            item["raw_cases_text"] = cell_text(at(layout["cases"]))
            item["raw_loose_units_text"] = cell_text(at(layout["bottles"]))
            items.append(item)
            if pack["selling_unit"] == "multipack":
                issue("MULTIPACK_UNIT_REQUIRES_REVIEW",
                      "Verify whether shipped units refer to packs or individual bottles; calculations use packs as sale units.", item)
            if len(ids) != 2:
                issue("PRODUCT_IDENTIFIERS_UNCERTAIN", "Expected separate Item ID and MLCC ID values.", item)
            if not pack["description"] or not pack["volume_ml"] or not pack["units_per_case"]:
                issue("PACKAGING_OR_DESCRIPTION_UNCERTAIN", "Description or packaging could not be completely parsed.", item)
            if price is None or total is None:
                issue("PRICE_OR_AMOUNT_MISSING", "A purchase price or line amount is missing or malformed.", item)
            if cases is None or loose is None:
                issue("QUANTITY_MISSING_OR_AMBIGUOUS", "A shipped quantity is missing or ambiguous. Inferred units are diagnostic only; extracted quantities were not replaced.", item,
                      {"raw_cases": (at(layout["cases"]) or {}).get("content"),
                       "raw_loose_units": (at(layout["bottles"]) or {}).get("content")})
            if total_units is not None and price_cents is not None and total_cents is not None:
                expected_cents = total_units * price_cents
                item["validation"]["quantity_price_check"] = "matched" if expected_cents == total_cents else "conflict"
                if expected_cents != total_cents:
                    issue("QUANTITY_PRICE_CONFLICT", "Extracted shipped units multiplied by purchase price do not equal the printed line total. Review source; discounts or adjustments may also explain a difference.", item,
                          {"extracted_total_units": total_units, "expected_line_total": decimal(expected_cents),
                           "printed_line_total": total, "inferred_total_units": inferred})
            anchor_cell = description_cell if description_cell is not None else size_cell
            anchor = center(first_polygon(anchor_cell), page.get("angle") or 0)
            if anchor:
                table_anchors.append({"item": item, "y": anchor["y"]})

        # Barcodes often appear in the next extracted row. Use deskewed word
        # positions as candidates; do not silently shift cells or assign UPCs.
        barcode_column = [c for c in table["cells"] if c["columnIndex"] == layout["base"] + 1]
        x_range = [x for c in barcode_column for r in (c.get("boundingRegions") or [])
                   for x in (r.get("polygon") or [])[::2]]
        min_x, max_x = (min(x_range), max(x_range)) if x_range else (math.inf, -math.inf)
        words = page.get("words") or []
        table_anchors.sort(key=lambda a: a["y"])
        for i in range(1, len(words) - 2):
            group = words[i - 1:i + 3]
            tokens = [w["content"] for w in group]
            if not all(re.fullmatch(pattern, token) for pattern, token in
                       zip((r"[0-9]", r"[0-9]{5}", r"[0-9]{5}", r"[A-Za-z0-9]"), tokens)):
                continue
            points = [center(w.get("polygon"), page.get("angle") or 0) for w in group]
            if any(p is None for p in points):
                continue
            if max(p["y"] for p in points) - min(p["y"] for p in points) > page["height"] * 0.01:
                continue
            original_center = center(group[1].get("polygon"))
            if original_center["x"] < min_x or original_center["x"] > max_x:
                continue
            y = sum(p["y"] / len(points) for p in points)
            owner = next((a for a in reversed(table_anchors) if a["y"] <= y), None)
            if owner is None or y - owner["y"] > page["height"] * 0.04:
                continue
            value = "".join(tokens)
            owner["item"]["upc_candidates"].append({
                "value": value, "raw_text": " ".join(tokens), "checksum_valid": upc_checksum(value),
                "min_word_confidence": min(w.get("confidence") or 0 for w in group),
                "association": "positional_candidate_unverified", "page_number": page_number,
                "word_spans": [w.get("span") for w in group], "word_polygons": [w.get("polygon") for w in group],
            })

    if not matched_tables or not items:
        raise ValueError("No supported product table with product records was found.")
    for item in items:
        if not item["upc_candidates"]:
            issue("UPC_NOT_FOUND", "No complete UPC candidate was recovered for this product.", item)
        else:
            issue("UPC_ASSOCIATION_UNVERIFIED", "Check candidate UPC against the source image or catalog before assigning upc.", item)
            if any(not candidate["checksum_valid"] for candidate in item["upc_candidates"]):
                issue("UPC_INVALID", "A UPC candidate contains a non-digit or fails its check digit. It was not automatically corrected.", item)
            if len(item["upc_candidates"]) > 1:
                issue("MULTIPLE_UPC_CANDIDATES", "More than one UPC candidate is associated with this product.", item)
    footers = list(re.finditer(r"Page\s+([0-9]+)\s+of\s+([0-9]+)", content, re.I))
    expected_pages = max(int(m[2]) for m in footers) if footers else None
    received_pages = len(result.get("pages") or [])
    if expected_pages is not None and received_pages < expected_pages:
        issue("MISSING_PAGES", f"Only {received_pages} of {expected_pages} expected pages were processed.")
    elif expected_pages is None:
        issue("PAGE_COMPLETENESS_UNVERIFIED", "No page-count footer was found; document completeness has not been established.")
    line_sum = sum(cents(item["line_total"]) or 0 for item in items)
    source = {
        "raw_file": options.get("rawFile"), "raw_sha256": options.get("rawSha256"),
        "parser_name": "mlcc-delivered-by-layout", "parser_version": VERSION,
        "model_id": result["modelId"], "extraction_created_at": raw.get("createdDateTime"),
        "header_field_candidates": {key: field_candidate(field) for key, field in fields.items() if key != "Items"},
        "structured_item_count": len((fields.get("Items") or {}).get("valueArray") or []),
        "non_product_rows": non_product_rows,
    }
    if "apiVersion" in result:
        source["api_version"] = result["apiVersion"]
    return {
        "schema_version": "1.0", "invoice": invoice, "items": items,
        "validation": {
            "status": "needs_review", "ready_for_database": False, "product_count": len(items),
            "pages_received": received_pages, "pages_expected": expected_pages,
            "extracted_line_sum": decimal(line_sum),
            "line_sum_complete": all(item["line_total"] is not None for item in items),
            "line_sum_scope": "processed pages only; not a verified invoice total", "issues": issues,
        },
        "source": source,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="Azure prebuilt-invoice response JSON")
    parser.add_argument("output", nargs="?", type=Path, help="New parsed JSON file (must not already exist)")
    args = parser.parse_args(argv)
    try:
        target = args.output if args.output is not None else Path(re.sub(r"(?:\.raw)?\.json$", "", str(args.input), flags=re.I) + ".parsed.json")
        if args.input.resolve() == target.resolve():
            raise ValueError("Output must be a new file, not the raw input.")
        raw_bytes = args.input.read_bytes()
        parsed = parse_invoice(json.loads(raw_bytes.decode("utf-8")), {
            "rawFile": args.input.name, "rawSha256": hashlib.sha256(raw_bytes).hexdigest(),
        })
        serialized = json.dumps(parsed, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        # Exclusive creation protects raw data and any reviewed output.
        with target.open("x", encoding="utf-8") as output:
            output.write(serialized)
        print(json.dumps({"output": str(target), "products": len(parsed["items"]),
                          "extracted_line_sum": parsed["validation"]["extracted_line_sum"],
                          "status": parsed["validation"]["status"],
                          "review_issues": len(parsed["validation"]["issues"])}, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, OverflowError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
