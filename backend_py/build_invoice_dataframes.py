"""Map the MLCC invoice to one product DataFrame and one CSV.

Place beside parse_mlcc_invoice.py in backend_py. Requires pandas.
No Azure calls, quantity inference, or database writes.
"""
import argparse
import json
from pathlib import Path
import re

import pandas as pd
from parse_mlcc_invoice import parse_invoice

BASE_DIR = Path(__file__).resolve().parent
COLUMNS = [
    "invoice_number", "item_id", "mlcc_id", "size_pack", "item",
    "shelf", "base", "upc", "ship_cases", "ship_btls", "price", "total",
]


def invoice_size_pack(text):
    """Remove descriptions Azure merged into the Size/Pack cell."""
    standard = re.fullmatch(
        r"([0-9]+(?:\.[0-9]+)?\s*(?:ml|l)\s*/\s*[0-9]+)(?:\s+.*)?",
        text, re.I,
    )
    if standard:
        return standard[1]
    multipack = re.fullmatch(
        r"([0-9]+\s*pk\s*/\s*[0-9]+(?:\.[0-9]+)?\s*(?:ml|l))"
        r"\s*.*?\s*/\s*([0-9]+)", text, re.I,
    )
    if multipack:
        return f"{multipack[1]}/{multipack[2]}"
    # Unknown formats require inspection; do not put merged text in this column.
    return None


def build_items_df(raw_data):
    parsed = parse_invoice(raw_data)
    records = []
    for product in parsed["items"]:
        # A checksum does not establish product ownership. Keep ambiguous or
        # invalid barcode candidates blank pending source/catalog verification.
        candidates = product["upc_candidates"]
        upc = (candidates[0]["value"] if len(candidates) == 1
               and candidates[0]["checksum_valid"] else None)
        records.append({
            "invoice_number": parsed["invoice"]["invoice_number"],
            "item_id": product["item_id"],
            "mlcc_id": product["mlcc_id"],
            "size_pack": invoice_size_pack(product["raw_size_pack"]),
            "item": product["description"],
            "shelf": product["shelf_price"],
            "base": product["base_price"],
            "upc": upc,
            "ship_cases": product["cases_shipped"],
            "ship_btls": product["loose_units_shipped"],
            "price": product["purchase_unit_price"],
            "total": product["line_total"],
        })
    frame = pd.DataFrame(records, columns=COLUMNS)
    for column in ("ship_cases", "ship_btls"):
        frame[column] = frame[column].astype("Int64")
    for column in ("invoice_number", "item_id", "mlcc_id", "upc"):
        frame[column] = frame[column].astype("string")
    return frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_file", nargs="?", type=Path,
                        default=Path("output/invoice-1791938597297.raw.json"))
    parser.add_argument("--csv", type=Path, help="Optional new CSV output path")
    args = parser.parse_args()
    raw_path = args.raw_file if args.raw_file.is_absolute() else BASE_DIR / args.raw_file
    with raw_path.open(encoding="utf-8") as source:
        items_df = build_items_df(json.load(source))
    stem = raw_path.name.removesuffix(".raw.json").removesuffix(".json")
    csv_path = args.csv or raw_path.parent / f"{stem}.items.csv"
    if not csv_path.is_absolute():
        csv_path = BASE_DIR / csv_path
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    # Protect earlier exports from accidental overwrite.
    with csv_path.open("x", encoding="utf-8-sig", newline="") as output:
        items_df.to_csv(output, index=False)
    print(items_df.to_string(index=False))
    print(f"\n{len(items_df)} product rows. CSV saved to: {csv_path}")
    print("Blank values were unreadable or unavailable. UPCs are positional OCR candidates.")


if __name__ == "__main__":
    main()
