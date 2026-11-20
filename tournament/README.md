# Quant Tournament

| Path | What it is |
|---|---|
| `PHASE1_SPEC.md` | Pre-registered rules, gates, splits, friction model |
| `pine/gen1_*.pine` | Gen 1 Pine v6 strategies (assembled from `pine/src/`: `_header` + `_common` + agent body + `_footer`) |
| `state/TOURNAMENT_STATE.json` | Single source of truth: generations, mutation log, execution specs |
| `dashboard/index.html` | Tracker page, published as a Claude artifact; reads its data from the artifact db |

Dashboard: https://claude.ai/artifact/KaAbKZpaJXjBPz3e5epx63 (private until shared from its Share menu)

## TradingView scripts (Gen 1, v1.0)
| Agent | script_id |
|---|---|
| Alpha | `USER;dd41a45061b447b3ab5876ecd07cf2cd` (saved before the margin fix; run with margin inputs in_16/in_17 = 10) |
| Beta | `USER;0b8e71f436c34da7a50b42c675249a03` |
| Gamma | `USER;5e08f45326fb45c4855ad8425e37eee7` |
| Delta | `USER;d0b2414640e746f5abc7e91c7ab01183` |

## Append-only protocol for Gen N+1
1. Mutations are calibrated on IS data only and logged as `mutations/m<gen><nn>` before any run.
2. New results go in a NEW document `generations/gen_<N+1>` and are appended to `generations[]` in `TOURNAMENT_STATE.json`. Never edit or delete an earlier generation.
3. Out-of-sample data is looked at no more than twice per agent (`oosTouches`). An agent that fails in-sample does not get an OOS look.
4. `deploy/current` is the only document that is overwritten. It changes only when an agent or ensemble passes every gate.
