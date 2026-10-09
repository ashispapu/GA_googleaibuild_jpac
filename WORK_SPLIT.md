# Work split — 4 people

Each person's tasks, rules and starting steps are in their own folder's README. Sizes are in **points** (relative effort).

| Person | Folder | Track | Points |
|---|---|---|---|
| P1 | `p1-backend/` | Backend core + security + SSO | 34 |
| P2 | `p2-agents/` | Agents + RAG | 29 |
| P3 | `p3-frontend/` | Frontend | 32 |
| P4 | `p4-ml-data/` | ML + data + slides | 20 |

## Flex pool (whoever finishes early takes one; tell the team)
| ID | Task | Pts | Best fit |
|---|---|---|---|
| FX-1 | 3D line view (React Three Fiber) | 4 | P3 |
| FX-2 | Local LLM via Ollama (`LLM_PROVIDER=ollama`) | 2 | P2 |
| FX-3 | Retrain model from feedback labels live | 3 | P4 |
| FX-4 | Poisoned-manual injection demo | 1 | P2 |
| FX-5 | Voice question ("Why is EQ-3012 red?") | 2 | P3 |
| FX-7 | Energy idle-waste detector on other lines | 2 | P4 |

## Rebalancing rules
- Tasks move whole, never half. Update the owner in both READMEs.
- If P4 needs help, ML-5 (simulator) can move to P1, who owns the gateway.
- If P3 is behind, cut FE-7 to the audit badge only. Never cut FE-4 (it's the hero screen).
- If P2 is behind, AG-8 replay is the safety net: ship it first, then make agents real one at a time.
- If P1 is behind, keep `mock_server` running for routers not yet replaced. The frontend won't notice.

## Checkpoints
| When | Checkpoint | Everyone shows |
|---|---|---|
| Start | **M0 Kickoff** | runs `make mock` + `make test`, reads own README |
| ~30% | **M1 Contracts frozen** | own track works end-to-end against mocks; P4 corpus delivered |
| ~60% | **M2 Integration** | mocks swapped for real parts; full story runs once |
| ~80% | **M3 Demo freeze** | bugs only; replay tested with network off |
| End | **Rehearse ×2** | timed 8 scenes + live demo of scenes 2–7 |
