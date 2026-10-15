import json
from pathlib import Path

import pandas as pd

with (Path(__file__).parent / "output/invoice-1791938597297.raw.json").open(encoding="utf-8") as source:
    raw_data = json.load(source)

print(json.dumps(raw_data, indent=2))

table = raw_data["analyzeResult"]["tables"][2]
df = pd.DataFrame("", index=range(table["rowCount"]), columns=range(table["columnCount"]))
for cell in table["cells"]:
    df.iat[cell["rowIndex"], cell["columnIndex"]] = cell["content"]

print(df.to_string(index=False, header=False))

df.to_csv(Path(__file__).parent / "output/invoice-1791938597297.table.csv", index=False, header=False)
