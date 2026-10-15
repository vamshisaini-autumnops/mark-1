import json
from pathlib import Path

import pandas as pd

def build_table_dataframe(raw_data):
    """Map the largest detected table into strings without changing cell text."""
    tables = raw_data["analyzeResult"]["tables"]
    if not tables:
        raise ValueError("Azure did not detect a table in this invoice.")
    table = max(tables, key=lambda item: item["rowCount"] * item["columnCount"])
    df = pd.DataFrame("", index=range(table["rowCount"]), columns=range(table["columnCount"]), dtype=str)
    for cell in table["cells"]:
        df.iat[cell["rowIndex"], cell["columnIndex"]] = cell["content"]
    return df


def main():
    with (Path(__file__).parent / "output/invoice-1791938597297.raw.json").open(encoding="utf-8") as source:
        raw_data = json.load(source)
    print(json.dumps(raw_data, indent=2))
    df = build_table_dataframe(raw_data)
    print(df.to_string(index=False, header=False))
    df.to_csv(Path(__file__).parent / "output/invoice-1791938597297.table.csv", index=False, header=False)


if __name__ == "__main__":
    main()
