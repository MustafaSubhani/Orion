# DATA_CARD — Orion heterogeneous enterprise lake

**Pin:** keep source files under `data/` unchanged. Queryable gold lives in `data/frozen/` after `python -m retrieval.freeze`.

| Table / collection | Entity | Modality | Source (unchanged) | Notes |
|---|---|---|---|---|
| `hr` parquet | MNC | table | `data/hr/HR_Data_MNC_Data Science Lovers.csv` | Stratified sample (~20k) of 2M rows. Columns: Employee_ID, Full_Name, Department, Job_Title, Hire_Date, Location, Performance_Rating, Experience_Years, Status, Work_Mode, Salary_INR |
| `hr_data` chroma | MNC | table + policy | MNC CSV + `HR Anaytics.xlsx` + Business Conduct PDF | Retrieval only; do not treat as the SQL gold table |
| `applfin` | AAPL | table | `data/finance/ApplFin.csv` | Quarterly Apple financials including NetSales_Total |
| `fred_*` | FRED | macro | `fred_cpi.csv`, `fred_fedfunds.csv`, `fred_umcsent.csv` | DATE + series value |
| `finance_data` chroma | MSFT / AAPL / FRED | filing, table, macro | SEC EDGAR + Apple CSVs + FRED + products | Chunk metadata includes `entity` |
| `calendar` | SYNTH_CAL | calendar | `apple_synthetic_calendar_events.json` | Deduped by event_id; sampled |
| `products` parquet | AAPL | table | `apple_products_dataset_100k.csv` | Stratified sample (~12k). Catalog SKUs; not MNC sales CRM |
| `scheduler_data` chroma | SYNTH_CAL | calendar | JSON + ICS | Retrieval only |

**Legal joins:** AAPL+FRED, MSFT+FRED, SYNTH_CAL+AAPL (calendar context). **Illegal:** MNC payroll × MSFT 10-K (tasks must abstain).

**Splits:** tasks are split, not rows. `bench/tasks/SPLIT_LOCK.json` hashes the compiled JSONL. Never tune on `test`.

**PII:** limited role cannot read salary columns through `sql_spec`. Output redaction still runs on the final answer.

**Embeddings:** `all-MiniLM-L6-v2` (384-d). Rebuild chroma after changing the model.

**Live KB (product, not gold):** user chat attachments land in `kb/` (`kb_*` tables + chunk jsonl, optional chroma `kb_live`). They are entity `KB` and cannot join MNC/AAPL/MSFT/FRED. Do not compile oracles from `kb/`.
