# Phase 1: Tournament Rule Specification (pre-registered)

Status: DRAFT v1, 2026-11-20. This spec is frozen before any IS/OOS data is looked at.
Every later change is a *mutation*: it goes in the mutation log (§9), it is calibrated on
IS data only, and it counts against the agent's trial budget.

---

## 0. Data and connectivity checks (done 2026-11-20)

| Feed | Instrument / TF | Usable history | Source |
|---|---|---|---|
| TradingView Deep Backtest | MES1! 5m | 2022-01-03 → now (~4.9 y) | `run_pine_backtest` (date_from) |
| TradingView Deep Backtest | MNQ1! 1m | 2023-11-21 → now (~3 y) | `run_pine_backtest` |
| TradingView raw OHLCV | SPX / SPY 5m | 2025-11-17 / 2025-11-10 → now (~1 y) | `get_ohlcv` |
| TradingView raw OHLCV | SPX 1m | 2026-09-08 → now | `get_ohlcv` |
| TradingView raw OHLCV | MES/MNQ 5m; MES 1m | 2026-08-09; 2026-10-25 → now | `get_ohlcv` (raw bars are shallow; Deep Backtest is not) |
| TradingView raw OHLCV | VIX1D 5m | 2025-11-24 → now | `get_ohlcv` |
| Robinhood | SPXW live chain, Greeks, IV, OI, NBBO | live only | `get_option_quotes` |
| Robinhood | Expired SPXW intraday 5m bars | **~1 month only** (2026-11-13 real; 2026-10-16 and older = `interpolated:true` placeholders) | `get_option_historicals` |
| Robinhood | Agent-tradable account | Cash account, **no options level**; the L3 margin account is not agent-accessible; futures not supported via MCP | `get_accounts` |

Consequences (non-negotiable):
1. **Futures (MES/MNQ) are backtestable on real data** with enough history for ≥150 trades and a 70/30 split.
2. **SPX/SPY option P&L cannot be backtested on observed option prices** beyond about 25 sessions.
   Options legs are backtested with a **calibrated pricing model** (§7). Results are labelled `MODELED`
   and carry a lower confidence grade than futures results. The ~25 sessions of real RH option bars are
   used only to measure model error, never to tune rules.
3. **No automated execution path exists today.** MCP cannot trade futures, and the only agent-tradable account
   cannot trade options. "Deployment" means TradingView alerts plus manual execution on the L3 account,
   unless the user changes account permissions.
4. Null benchmark already measured: a frictionless 20/50 EMA cross, long-only, scores **PF 1.05** on MES 5m (2022–2026) and **PF 1.06** on MNQ 1m (2023–2026).
   Any agent must clearly beat a trivial trend rule *after* friction.

Live snapshot used for spec sanity (2026-11-19 close): SPX 7175.83 (−2.0%), VIX 28.44 (+15.8%),
SPXW 2026-11-20 7100P: Δ −0.279, IV 42.0%, bid/ask 23.10/23.30, OI 9,076. The current regime is stressed.

---

## 1. Shared primitives (all agents)

All times are America/New_York. The execution TF is 5m (primary). 1m is used only for MNQ (Alpha, Gamma) and as a robustness re-run.

| Symbol | Definition |
|---|---|
| `ATR` | ATR(14) on the execution TF |
| `dATR` | Daily ATR(14) of **completed** RTH days, computed in-script from tracked session H/L/C (no `request.security`) |
| `VWAP` | Session VWAP anchored at 09:30 (futures: RTH-anchored, ignores Globex volume) |
| `OR` | Opening range = high/low of 09:30–09:45 (three 5m bars). ORH, ORL |
| `PDH/PDL/PDC` | Prior RTH day high/low/close (in-script) |
| `ONH/ONL` | Globex overnight high/low 18:00–09:30 (futures feed; mapped to SPX via basis = MES − SPX at 09:30) |
| `ADX, +DI, −DI` | DMI(14,14) on the execution TF |
| `EMAn` | EMA of close, n ∈ {9, 20, 21, 50, 200} |
| `HTF(tf, x)` | x computed on **confirmed** higher-TF bars, built in-script with `timeframe.change(tf)` + `var` state. No `request.security`, no lookahead. Historical and realtime values are identical by construction |
| `IVR` | 252-day percentile rank of VIX daily close (0DTE option IVR is undefined, so VIX is the proxy) |
| `EMrem` | Expected remaining move = `SPX × (VIX1D/100)/√252 × √(min_to_close/390)` |
| `EVENT` | FOMC / CPI / NFP / PCE days from the TradingView economic calendar (importance = high) |

Signal source per vehicle: SPX and SPY options use **SPY 5m** (real volume for VWAP), with levels scaled to SPX by the live SPX/SPY ratio. MES uses MES. MNQ uses MNQ.

All entries are evaluated on **bar close** (`calc_on_every_tick=false`, `process_orders_on_close=false`), so fills happen at the next bar's open plus slippage.

---

## 2. Risk & Regime Manager (gatekeeper). Applies to every agent.

### 2.1 Session gates
| Window | New directional entries | New SPX credit spreads |
|---|---|---|
| 09:30–09:45 | Blocked (OR forming) | Blocked |
| 09:45–12:00 | Allowed | Allowed |
| 12:00–13:30 | **Blocked (dead zone)** | Allowed only if short strike ≥ 1.0×EMrem from spot **and** ADX < 20 |
| 13:30–14:30 | Allowed | Allowed |
| 14:30–15:30 | Allowed (futures, SPY 2DTE); SPY 0DTE blocked | Allowed only if short strike > 0.5% from spot (gamma-pin guard) |
| 15:30–16:00 | Exit only | Exit / hold-to-settle rule (§2.4) |

**Chop filter (directional only):** block if `ADX < 20` **or** `RTH range so far / dATR < 0.35` after 10:30.
The dead zone and chop filter are *hypotheses*. Phase 3 runs an ablation with each one off, and a filter that does not improve OOS PF is dropped. It is not kept by default.

### 2.2 Event filter
- CPI / NFP / PCE (08:30 release): no new SPX credit spreads before 10:00. Directional entries are allowed.
- FOMC day: no new short-premium positions after 13:00. Flatten all SPX spreads by 13:55. No new directional entries 13:55–14:30.
- Any day with an |overnight gap| > 1.0×dATR: SPX credit spreads require Δshort ≤ 0.10.

### 2.3 Instrument routing (deterministic)
```
if signal_class == LEVEL_DEFENSE (Beta, Delta-continuation):
    if window/event gates pass and liquidity gates pass
       and 1-lot max loss (W − credit)×100 ≤ risk_budget:      -> SPX 0DTE credit spread
    else                                                      -> MES (or MNQ if NQ relative strength aligns)
if signal_class == MOMENTUM/EXPANSION (Alpha, Gamma):
    if IVR < 30 and 09:45 ≤ t ≤ 11:30:                       -> SPY 0DTE long call/put, |Δ| 0.40–0.55
    elif IVR < 30 and (11:30 < t < 14:30 or 1-lot SPX risk > budget): -> SPY 2DTE long option, |Δ| 0.40–0.55
    elif t ≥ 14:00 and trend_day and IVR < 30:               -> SPY 0DTE (entries end 14:30)
    else                                                      -> MNQ if |MNQ%chg| > |MES%chg| in signal direction, else MES
trend_day := ADX ≥ 25 and price has stayed on one side of VWAP since 10:30
```
At VIX 28 (current), IVR is high, so SPY long options are **off** and momentum signals route to futures.

### 2.4 SPX 0DTE credit spread rules
- Short strike |Δ| 0.10–0.20 (puts capped at 0.15 for skew/crash asymmetry). It must also sit beyond the agent's structural level **and** ≥ 1.0×EMrem from spot (calls) / ≥ 1.25×EMrem (puts).
- Width W ∈ {5, 10, 15}. Prefer the widest W whose max loss fits the budget, because fees and slippage are per-spread.
- **Credit floor (amended, see §8.1):** `credit ≥ max(0.10×W, 0.85×|Δshort|×W)` at the mid, after subtracting modeled slippage.
- Profit take: buy back at 50% of credit (variant: 65%).
- Stop (two pre-registered variants, only these two):
  - **S-A (mark):** spread mark ≥ 2.0× initial credit (loss ≈ 1× credit + friction).
  - **S-B (structure):** 5m SPX close beyond the agent's invalidation level, which lies between spot and the short strike. Hard backstop: mark ≥ 3× credit.
- 15:30 rule: close if the short strike is within 15 pts (0.25%) of spot. Hold to settlement only if > 0.6% OTM **and** ≥ 85% of credit captured.
- Liquidity: each leg bid-ask ≤ $0.20, OI ≥ 1,000. Otherwise reject and route to futures.

### 2.5 SPY long options
- |Δ| 0.40–0.55. Leg bid-ask ≤ $0.03. OI ≥ 1,000.
- 0DTE: entries 09:35–11:30 (or 14:00–14:30 on a trend day). Hard exit 15:30. **Time stop:** exit if the underlying has not moved ≥ 0.5R favorably within 4 bars (20 min).
- Stop: underlying invalidation from the agent (not an option-premium %). Size so that option loss at the underlying stop, estimated via Δ+Γ, is ≤ the risk budget.

### 2.6 Account risk
- Risk per trade ≤ 1.0% of equity (spreads: `(W − credit)×100×n`; futures: stop distance × multiplier × n + friction).
- Max concurrent open risk 3%. Max 2 positions per agent.
- Daily: −2% → no new entries; −3% → flatten all. Weekly −5% → halt until the next week. These are portfolio-level, so they are applied in the ensemble simulation, not per agent.

---

## 3. Agent Alpha: Opening-range / VWAP momentum (MES, MNQ; SPY when IVR < 30)

Thesis: on expansion days the first break of the OR with VWAP and DMI alignment continues.
Long and short are deliberately **not mirrored.** Up-moves grind and reward trailing exits. Down-moves are fast and mean-revert, which rewards fixed targets and short time stops.

| | LONG | SHORT |
|---|---|---|
| Trigger | 5m close > ORH, first time today | 5m close < ORL, first time today |
| Confirmation | close > VWAP; ADX ≥ 20 and +DI > −DI; volume > SMA(volume,20) | close < VWAP; ADX ≥ 20 and −DI > +DI; **VIX up on day** (VIX > prior close). No volume requirement (down-breaks often start on thin volume) |
| Window | 09:45–12:00, 13:30–15:00 | same |
| Initial stop | entry − 1.0×ATR | entry + 1.0×ATR |
| Exit | trail: 5m close < EMA9 (activated after +1R) | target **+2.0R** fixed; **time stop:** exit if < +0.5R after 6 bars |
| Max trades | 1 per side per day | 1 per side per day |
| Hard flat | 15:50 | 15:50 |
| Signal class | MOMENTUM | MOMENTUM |

Bias logic: direction = side of the OR broken, valid only if VWAP and DI agree. A break against VWAP is ignored.

---

## 4. Agent Beta: Liquidity sweep / failed-breakout reversal (primary: SPX credit spreads; fallback MES)

Thesis: stops resting beyond obvious levels get swept. When price **fails to accept** beyond the level, the swept side is defended for the rest of the session. That makes defined-risk premium selling on the far side of the sweep the natural vehicle.

Levels L ∈ {PDH, PDL, ONH, ONL, ORH, ORL}. A level is eligible once per day.

| | BEARISH sweep → Bear Call Spread / short MES | BULLISH sweep → Bull Put Spread / long MES |
|---|---|---|
| Sweep bar | high > L_up (≥ 1 tick) and close < L_up | low < L_dn (≥ 1 tick) and close > L_dn |
| Confirmation (next bar) | close < sweep-bar midpoint | close > sweep-bar midpoint |
| Divergence filter | RSI(14) at sweep high < RSI(14) at last confirmed pivot high (pivot 5/5) | **VIX fails to confirm:** VIX intraday high since sweep < VIX high at prior swing low (cross-asset divergence). RSI divergence is an acceptable substitute |
| Extra veto | none | no bull-put if day gap-down > 1.0×dATR **or** price < VWAP − 1.5×ATR (falling-knife veto) |
| Window | 09:45–14:30 | 09:45–14:30 |
| Invalidation (S-B stop) | 5m close > sweep high | 5m close < sweep low |
| SPX short strike | ≥ max(sweep high, spot + 1.0×EMrem), Δ 0.10–0.20 | ≤ min(sweep low, spot − 1.25×EMrem), Δ 0.10–0.15 |
| Futures exits | stop = sweep high + 0.25×ATR; T1 = VWAP (half off); T2 = opposite OR side | stop = sweep low − 0.25×ATR; T1 = VWAP; T2 = opposite OR side |
| Signal class | LEVEL_DEFENSE | LEVEL_DEFENSE |

---

## 5. Agent Gamma: Volatility compression → expansion (MNQ primary; SPY long options when IVR < 30)

Thesis: low-volatility compression precedes directional expansion, and long gamma (futures, long options) captures it.
**Gamma never sells premium.** Selling into an expansion signal contradicts its own thesis.

| | LONG | SHORT |
|---|---|---|
| Setup | Squeeze ON: BB(20,2.0) fully inside KC(20, 1.5×ATR) for ≥ 6 consecutive bars | Same squeeze setup, **or** an *expansion-without-squeeze*: ATR > 1.5×SMA(ATR,20) **and** close < session low of prior bar (crash legs rarely start from a clean squeeze) |
| Trigger | squeeze releases (BB exits KC) and momentum `M` > 0 and rising | release with `M` < 0 and falling, or the expansion trigger above |
| Filter | close > EMA20; ATR > ATR[5] | close < EMA20 |
| Stop | lower KC or 1.5×ATR, whichever is tighter | upper KC or 1.5×ATR, whichever is tighter |
| Exit | `M` histogram declines 2 consecutive bars (after +1R), else trail EMA20 | target **+2.5R**, or `M` turns up 2 bars |
| Window | 09:45–12:00, 13:30–15:30 | same |

`M = linreg(close − avg(avg(highest(high,20), lowest(low,20)), SMA(close,20)), 20, 0)` (standard TTM momentum).
"6 bars" is the one non-standard constant in this agent. It gets a sensitivity sweep over {4, 6, 8}, and the result must be stable across all three or the agent fails.

---

## 6. Agent Delta: Multi-timeframe regime pullback (MES primary; SPX credit spreads in the trend direction)

Thesis: trade only in the direction the 1h/15m structure has already chosen, entering on 5m pullbacks to value.

| | LONG bias | SHORT bias |
|---|---|---|
| 1h regime | HTF(1h): EMA20 > EMA50 and close > EMA20 | HTF(1h): EMA20 < EMA50 and close < EMA20 |
| 15m confirm | HTF(15m): EMA9 > EMA21 | HTF(15m): EMA9 < EMA21 **for ≥ 2 confirmed 15m bars** (short bias must persist; bear rallies are violent) |
| Breadth | NYSE ADD > 0 and ADD > ADD[3] (fallback if the `USI:ADD` feed is unavailable: MNQ and MES both above VWAP) | ADD < 0 and ADD < ADD[3] (fallback: both below VWAP) |
| 5m pullback | low ≤ EMA20 and close > EMA50 | high ≥ EMA20 and close < EMA50 |
| Trigger | next bar closes > prior bar high | next bar closes < prior bar low |
| Stop | pullback swing low − 0.25×ATR | pullback swing high + 0.25×ATR |
| Exit | trail: 5m close < EMA20 | 50% off at +1.5R, rest trail EMA9 |
| SPX vehicle | Bull put: short strike ≤ min(pullback low, HTF(1h) EMA50), Δ ≤ 0.15 | Bear call: short strike ≥ max(pullback high, HTF(1h) EMA50), Δ ≤ 0.20 |
| Signal class | LEVEL_DEFENSE (spread) / MOMENTUM (futures) | same |

---

## 7. Backtest methodology (Quant Auditor)

### 7.1 Pre-registered splits
| Universe | In-sample (calibration) | Out-of-sample (blind) |
|---|---|---|
| MES/MNQ 5m (Deep Backtest) | 2022-01-03 → 2025-06-30 | 2025-07-01 → 2026-11-19 |
| MNQ/MES 1m (Deep Backtest) | 2023-11-21 → 2025-12-31 | 2026-01-01 → 2026-11-19 |
| SPX/SPY options (modeled on 5m SPY/SPX) | 2025-11-17 → 2026-07-31 | 2026-08-01 → 2026-11-19 |
| Real RH option bars (~25 sessions) | not used for tuning | pricing-model error check only |

Inside IS, rules are calibrated with anchored walk-forward (3 folds). **OOS is touched at most twice per agent.** A third touch eliminates the agent.

### 7.2 Gates (all must pass, evaluated on OOS)
| Gate | Threshold |
|---|---|
| Profit factor | ≥ 1.45 |
| PF degradation | OOS PF ≥ 0.75 × IS PF |
| Max drawdown | ≤ 12% at 1% fixed-fractional risk sizing |
| Sample | ≥ 150 trades over full window; **each side ≥ 40 trades with PF ≥ 1.15** (bidirectionality is measured, not asserted) |
| Robustness (added) | 5th percentile of day-block-bootstrap OOS PF ≥ 1.10 |
| Null test (added) | Beats a random-entry null (same exits, same time-of-day and side distribution, 1,000 runs) at p < 0.05 |
| Multiple testing (added) | Number of configurations tried is logged. If > 10, the Deflated Sharpe Ratio must be > 0 at 95% |
| Parameter stability (added) | ±1 step on every lookback (e.g. 14 → {10, 20}) must keep OOS PF ≥ 1.25 |

### 7.3 Friction model (assumptions, to be verified against the broker fee schedule in Phase 2)
| Instrument | Fees | Slippage |
|---|---|---|
| MES / MNQ | $1.00 per contract per side (commission + exchange + NFA) | 1 tick/side on market and stop orders; stress test at 2 ticks |
| SPX spread | $1.00 per contract per leg per side (RH + Cboe index fees, conservative) = $4 per spread round trip | $0.10 per spread per fill (upper bound of brief) |
| SPY option | $0.03 per contract (regulatory) | $0.03 per contract per side |

### 7.4 Options pricing model (MODELED results)
- Underlying path: SPX 5m bars. Short-dated IV is anchored to VIX1D, with a put/call skew curve fitted per session from live RH chain snapshots. Where no snapshot exists, a fixed skew is fitted on the ~25 real sessions.
- Model error is measured on the real RH 5m option bars (Nov 2026). If median absolute pricing error on a spread is > $0.10, all SPX results carry an extra $0.10/fill haircut.
- Stress run: IV +20% at entry is *not* credited (sell at model −0.05). Exits are taken at the bar **high** of the spread mark (adverse), not the close.
- Forward data collection is recommended from day 1 (§10). It replaces the model with observed prices over time.

---

## 8. Spec defects found in the brief and how they were resolved

### 8.1 The SPX credit rule is internally inconsistent (most important)
For a vertical, `credit / W ≈ average risk-neutral ITM probability across [K_long, K_short] ≈ |Δ|` of the strike midpoint.
With the short Δ at 0.10–0.20, credit/width is roughly 0.08–0.18. **Requiring ≥ 20–25% of width at ≤ 0.20Δ is close to infeasible** outside panic IV, so the agent would barely trade and would fail the 150-trade gate by construction.
Resolution: the floor scales with delta, `credit ≥ max(0.10W, 0.85|Δ|W)`. Alternatively keep 20% and move the short to 0.20–0.30Δ. **User decision required.**

### 8.2 Break-even math of 50% take-profit with a 2× stop
Win = +0.5C − f and loss = −1.0C − f, so the break-even win rate is `p* = (C + f) / 1.5C`.
- f = 0.2C (realistic on a 5-wide): p* = **80%**.
- f = 0.1C (10-wide): p* = 73%.

A mark-based 2× stop on 0DTE is touched by ordinary intraday noise far more often than |Δ| suggests, because touch probability is about 2× the expiry probability and 0DTE marks are wide.
This is the **#1 kill risk** for Beta and Delta on SPX. It is why stop variant S-B (structure-based) is pre-registered alongside S-A.

### 8.3 IVR for 0DTE is undefined
Replaced with the VIX 252-day percentile. VIX1D is used for strike distance, not gating.

### 8.4 Ensemble "non-correlated" is mostly false at the factor level
All four agents trade the same factor (US large-cap intraday direction). Real diversification comes from **payoff shape**: short-gamma range income (Beta/Delta spreads) versus long-gamma trend capture (Alpha/Gamma). These are anti-correlated by regime, not by asset. The ensemble will be evaluated on daily P&L correlation by regime, not by instrument label.

### 8.5 Section 1256
SPY options are **not** Section 1256 (equity ETF options). The leaderboard reports the 1256 share = (SPX + MES + MNQ P&L) / total.

---

## 9. Mutation log
| # | Agent | Change | Data used | OOS touches after |
|---|---|---|---|---|
| — | — | (empty) | — | — |

## 10. Recommended forward data capture
Run a scheduled job every RTH 5 minutes that snapshots the SPXW 0DTE chain ±2% (mid, bid/ask, Δ, IV, OI) to disk. In about 6 months this replaces modeled SPX P&L with observed P&L. Without it, the SPX leg never graduates from `MODELED`.
