# Manuals for the AI's search (ML-6)

Made by `python -m data_gen.generate --only corpus` (text in `data_gen/manual_text.py`). All products and makers are fictional.

| doc_id | File | Pages | Machines |
|---|---|---|---|
| **MAN-SPINDLE-200** | CNC Spindle 200 Maintenance Manual | 66 | spindles: EQ-3002, 3006, 3011, **3012**, 4002, 4005 |
| MAN-PRESS-50 | Hydraulic Press HP-50 Maintenance Manual | 8 | presses: EQ-3001, 3005, 3010, 4001 |
| MAN-ROBOT-6 | Assembly Robot AR-6 Maintenance Manual | 6 | robots: EQ-3003, 3007, 3013, 4003 |
| MAN-DRIVE-30 | Conveyor and Test Bench Drives — Maintenance Guide | 7 | conveyors and test benches |
| PS-VIB-01 | Vibration Monitoring and Alignment Standard | 8 | all 20 (where the 4.5 mm/s rule comes from) |

**Pinned to the story fixture** (`fixtures/story/docsearch_bearing.json`):
- **§7.4 "Bearing wear" starts on page 58** and opens with the fixture's sentence word for word ("If vibration RMS rises more than 30% within 24 h together with kurtosis above 4, …").
- **§7.6 "Misalignment" starts on page 61** ("Misalignment shows as high vibration at 2× running speed with normal kurtosis.").
- Page numbers in the footer are the real PDF page numbers, so a parser that counts pages gets 58 and 61.

**`index.json`** lists each manual with its `doc_id`, title, file, the machines it applies to and every section with its page. Use it to put `doc_id` + `section` + `page` on every chunk (`DocChunk`).

**Not here on purpose:** a copy of the press manual with a hidden instruction ("ignore all previous instructions…") for the security demo. It is in `fixtures/security/`, so the normal search never indexes it.
