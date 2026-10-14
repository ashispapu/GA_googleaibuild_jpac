# Past work orders for the AI's search (ML-6)

**One file: `work_orders.json`** is a JSON array of 200 `WorkOrder` objects (`plantguard_contracts/schemas/maintenance.py`). Only contract fields are used, so it validates with `TypeAdapter(list[WorkOrder])`. It is made by `python -m data_gen.generate --only corpus`.

- **Period:** 2 Jan 2024 → 13 Oct 2026 (the alert). Sorted by `created_at`, all `status: done`.
- **Mix:** 100 corrective, 82 preventive, 18 predictive.
- **Matches the training data:** every breakdown in `data/train.parquet` has its corrective work order, and `data/failure_events.csv` says which one.
  - The repair start and duration are the same as in the sensor data.
  - `findings` describe the warning signs seen before the stop (e.g. "Vibration rose ~120% over 5 days; kurtosis peaked at 5.4. Found outer race spalling…"). This is the text the search should match.
- **EQ-3012 has exactly the two work orders in `fixtures/story/history_EQ-3012.json`**, copied unchanged:
  - **WO-2024-0871:** the Nov 2024 rear bearing failure (the "same failure before" evidence)
  - **WO-2026-0091:** the June 2026 service that "hours since service" counts from
- **Numbers** count every work order in the plant that year, so there are gaps. 2026 stops at **WO-2026-0152**: the demo's approval creates **WO-2026-0153** (C9).
- **Technicians:**
  - Plant A uses TECH-104 / 112 / 120 from `fixtures/story/technicians.json`.
  - Plant B has no technicians in the fixtures yet, so its work orders leave `technician_id` empty.
- **Part numbers:** SKF-6205 / SKF-7008 / BLT-HTD-8M match the EQ-3012 BOM fixture. Other parts (e.g. SKF-6308, CPL-42) appear only here.
