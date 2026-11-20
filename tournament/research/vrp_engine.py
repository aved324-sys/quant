"""Gen 3 H1/H2 engine: direction-free SPXW 0DTE iron condor (variance risk premium test).

Mirrors pine/gen3_h1h2_condor_vrp.pine rule for rule, and adds what the Pine report cannot give:
per-day P&L, bootstrap confidence intervals, a stress list of the worst days, and the
pre-registered kill-criterion verdicts.

Pricing is MODELED: Black-Scholes, ATM IV = VIX1D x atm_scale, linear put skew in moneyness,
flat calls (calibrated 2026-11-20 from real SPXW 0DTE trades). Time convention: T = minutes to
16:00 / 390 / 252, the same as the Pine script.

Usage:
    python3 vrp_engine.py --spx SPX_5m.csv --vix1d VIX1D_5m.csv --vix VIX_5m.csv --vix3m VIX3M_5m.csv \
        [--events events.txt] [--atm-scale 1.0] [--out results.json]

CSV input: TradingView "Export chart data" files (columns time, open, high, low, close, ...);
time may be unix seconds or ISO-8601. Bars are interpreted as 5-minute bars labelled by open time.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ET = ZoneInfo("America/New_York")
OOS_START = date(2026, 8, 1)


# ----------------------------------------------------------------------------- pricing
def ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def iv_at(S: float, K: float, base: float, is_put: bool, put_skew: float, call_skew: float) -> float:
    m = (S - K) / S * 100.0 if is_put else (K - S) / S * 100.0
    mult = 1.0 + put_skew * m if is_put else 1.0 - call_skew * m
    return base * max(mult, 0.6)


def bs(S: float, K: float, T: float, iv: float, is_put: bool) -> tuple[float, float]:
    """Black-Scholes price and delta, r = q = 0."""
    if T <= 1e-7 or iv <= 0:
        if is_put:
            return max(K - S, 0.0), (-1.0 if S < K else 0.0)
        return max(S - K, 0.0), (1.0 if S > K else 0.0)
    sd = iv * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * iv * iv * T) / sd
    d2 = d1 - sd
    if is_put:
        return K * ncdf(-d2) - S * ncdf(-d1), ncdf(d1) - 1.0
    return S * ncdf(d1) - K * ncdf(d2), ncdf(d1)


def vertical(S, Ks, Kl, is_put, T, base, ps, cs) -> float:
    a, _ = bs(S, Ks, T, iv_at(S, Ks, base, is_put, ps, cs), is_put)
    b, _ = bs(S, Kl, T, iv_at(S, Kl, base, is_put, ps, cs), is_put)
    return a - b


def find_short(S, is_put, T, base, ps, cs, d_max) -> float | None:
    k0 = math.floor(S / 5.0) * 5.0 if is_put else math.ceil(S / 5.0) * 5.0
    for i in range(201):
        k = k0 - 5.0 * i if is_put else k0 + 5.0 * i
        _, dl = bs(S, k, T, iv_at(S, k, base, is_put, ps, cs), is_put)
        if abs(dl) <= d_max:
            return k
    return None


def intrinsic(S, kps, kpl, kcs, kcl) -> float:
    return max(kps - S, 0) - max(kpl - S, 0) + max(S - kcs, 0) - max(S - kcl, 0)


# ----------------------------------------------------------------------------- data
def load_tv_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    t = df["time"]
    if np.issubdtype(t.dtype, np.number):
        idx = pd.to_datetime(t, unit="s", utc=True)
    else:
        idx = pd.to_datetime(t, utc=True)
    df.index = idx.dt.tz_convert(ET)
    return df[["open", "high", "low", "close"]].astype(float).sort_index()


def build_frame(spx: pd.DataFrame, vix1d: pd.DataFrame, vix: pd.DataFrame, vix3m: pd.DataFrame | None) -> pd.DataFrame:
    """One row per SPX 5m RTH bar; volatility indices aligned as of the same bar close (forward-filled, no lookahead)."""
    f = spx.copy()
    f = f[(f.index.time >= time(9, 30)) & (f.index.time < time(16, 0))]
    for name, src in (("vix1d", vix1d), ("vix", vix), ("vix3m", vix3m)):
        if src is None:
            f[name] = np.nan
            continue
        f[name] = src["close"].reindex(f.index, method="ffill")
    return f


# ----------------------------------------------------------------------------- simulation
@dataclass
class BookCfg:
    name: str
    put_skew: float
    filtered: bool
    call_skew: float = 0.0
    wing: float = 10.0
    d_max: float = 0.15
    slip_vert: float = 0.10
    fee_leg: float = 1.0
    capital: float = 100_000.0
    risk_pct: float = 0.01
    atm_scale: float = 1.0


@dataclass
class BookResult:
    cfg: BookCfg
    days: dict = field(default_factory=dict)      # date -> pnl (0 when no trade)
    trades: list = field(default_factory=list)    # dicts
    skipped: int = 0


def daily_atr(f: pd.DataFrame) -> pd.Series:
    d = f.groupby(f.index.date).agg(h=("high", "max"), l=("low", "min"), c=("close", "last"), o=("open", "first"))
    pc = d["c"].shift(1)
    tr = pd.concat([d["h"], pc], axis=1).max(axis=1) - pd.concat([d["l"], pc], axis=1).min(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    return pd.DataFrame({"atr_prev": atr.shift(1), "prev_close": pc, "open": d["o"]})


def simulate(f: pd.DataFrame, cfg: BookCfg, events: set[date]) -> BookResult:
    res = BookResult(cfg)
    eq = cfg.capital
    dinfo = daily_atr(f)
    for day, g in f.groupby(f.index.date):
        if day >= OOS_START:
            break
        pnl_day = 0.0
        entry = g[g.index.time == time(9, 55)]
        if entry.empty or np.isnan(entry["vix1d"].iloc[0]):
            res.days[day] = 0.0
            continue
        row = entry.iloc[0]
        if cfg.filtered:
            di = dinfo.loc[day]
            gap_big = (not np.isnan(di["atr_prev"])) and abs(di["open"] - di["prev_close"]) > di["atr_prev"]
            term_inv = (not np.isnan(row["vix3m"])) and row["vix"] > row["vix3m"]
            if day in events or gap_big or term_inv:
                res.skipped += 1
                res.days[day] = 0.0
                continue
        S = row["close"]
        base = row["vix1d"] / 100.0 * cfg.atm_scale
        T = (960 - (9 * 60 + 55 + 5)) / 390.0 / 252.0
        kps = find_short(S, True, T, base, cfg.put_skew, cfg.call_skew, cfg.d_max)
        kcs = find_short(S, False, T, base, cfg.put_skew, cfg.call_skew, cfg.d_max)
        if kps is None or kcs is None:
            res.days[day] = 0.0
            continue
        kpl, kcl = kps - cfg.wing, kcs + cfg.wing
        mid = vertical(S, kps, kpl, True, T, base, cfg.put_skew, cfg.call_skew) + \
            vertical(S, kcs, kcl, False, T, base, cfg.put_skew, cfg.call_skew)
        credit = mid - 2 * cfg.slip_vert
        max_loss = (cfg.wing - credit) * 100.0
        n = math.floor(eq * cfg.risk_pct / max_loss) if max_loss > 0 else 0
        if credit <= 0 or n < 1:
            res.days[day] = 0.0
            continue
        exit_kind, exit_val, exit_time = None, None, None
        after = g[g.index > entry.index[0]]
        for ts, b in after.iterrows():
            mins = ts.hour * 60 + ts.minute
            Tb = max(960 - (mins + 5), 0) / 390.0 / 252.0
            bb = b["vix1d"] / 100.0 * cfg.atm_scale if not np.isnan(b["vix1d"]) else base
            v = vertical(b["close"], kps, kpl, True, Tb, bb, cfg.put_skew, cfg.call_skew) + \
                vertical(b["close"], kcs, kcl, False, Tb, bb, cfg.put_skew, cfg.call_skew)
            if v <= 0.5 * credit:
                exit_kind, exit_val, exit_time = "PT", v, ts
                break
            if ts.time() >= time(15, 25):
                exit_kind, exit_val, exit_time = "1530", v, ts
                break
        if exit_kind is None:  # half day: settle at the last close
            v = intrinsic(g["close"].iloc[-1], kps, kpl, kcs, kcl)
            pnl = (credit - v) * 100 * n - 4 * n * cfg.fee_leg
            exit_kind, exit_val, exit_time = "settle", v, g.index[-1]
        else:
            pnl = (credit - exit_val - 2 * cfg.slip_vert) * 100 * n - 8 * n * cfg.fee_leg
        eq += pnl
        pnl_day += pnl
        res.trades.append({"date": str(day), "spot": round(S, 2), "kps": kps, "kcs": kcs, "credit": round(credit, 3),
                           "n": n, "exit": exit_kind, "exit_val": round(exit_val, 3), "exit_time": str(exit_time.time()),
                           "pnl": round(pnl, 2), "day_low": round(g["low"].min(), 2), "day_high": round(g["high"].max(), 2)})
        res.days[day] = pnl_day
    return res


# ----------------------------------------------------------------------------- metrics
def metrics(res: BookResult, n_boot: int = 5000, seed: int = 7) -> dict:
    cap = res.cfg.capital
    d = np.array(list(res.days.values()), dtype=float)
    pnl = np.array([t["pnl"] for t in res.trades], dtype=float)
    out = {"book": res.cfg.name, "put_skew": res.cfg.put_skew, "filtered": res.cfg.filtered, "days": int(len(d)),
           "trades": int(len(pnl)), "skipped": res.skipped}
    if len(pnl) == 0:
        return out
    eq = cap + np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[cap], eq]))[1:]
    gw, gl = pnl[pnl > 0].sum(), -pnl[pnl <= 0].sum()
    srt = np.sort(d)
    k = max(1, math.ceil(0.05 * len(d)))
    downside = math.sqrt(np.mean(np.minimum(d, 0) ** 2)) if len(d) else float("nan")
    rng = np.random.default_rng(seed)
    boot = rng.choice(d, size=(n_boot, len(d)), replace=True).mean(axis=1)
    out.update({
        "net_pct": round(pnl.sum() / cap * 100, 3),
        "win_rate": round(float((pnl > 0).mean() * 100), 1),
        "pf": round(gw / gl, 3) if gl > 0 else None,
        "worst_day_pct": round(srt[0] / cap * 100, 3),
        "cvar95_pct": round(srt[:k].mean() / cap * 100, 3),
        "max_dd_pct": round(float(((peak - eq) / peak).max() * 100), 3),
        "sortino": round(d.mean() / downside * math.sqrt(252), 3) if downside > 0 else None,
        "mean_day_usd": round(float(d.mean()), 2),
        "mean_day_ci95_usd": [round(float(np.percentile(boot, 2.5)), 2), round(float(np.percentile(boot, 97.5)), 2)],
        "pt_exits": sum(t["exit"] == "PT" for t in res.trades),
        "worst_5": sorted(res.trades, key=lambda t: t["pnl"])[:5],
    })
    return out


def verdicts(ms: list[dict]) -> dict:
    """Pre-registered Gen 3 kill criteria (tournament state, gen_3.config.gates)."""
    h1 = [m for m in ms if not m["filtered"]]
    h2 = {m["put_skew"]: m for m in ms if m["filtered"]}
    nets = [m.get("net_pct", 0.0) for m in h1]
    mid = sorted(h1, key=lambda m: m["put_skew"])[len(h1) // 2]
    sign_flips = len({n > 0 for n in nets}) > 1
    h1_ok = mid.get("net_pct", 0.0) > 0 and not sign_flips
    h2_rows = []
    for m in h1:
        f = h2.get(m["put_skew"])
        if not f or "cvar95_pct" not in f or "cvar95_pct" not in m:
            continue
        cv_imp = 1 - f["cvar95_pct"] / m["cvar95_pct"] if m["cvar95_pct"] < 0 else 0.0
        wd_imp = 1 - f["worst_day_pct"] / m["worst_day_pct"] if m["worst_day_pct"] < 0 else 0.0
        h2_rows.append({"put_skew": m["put_skew"], "cvar95_improvement": round(cv_imp, 3), "worst_day_improvement": round(wd_imp, 3),
                        "pass": cv_imp >= 0.25 and wd_imp >= 0.25})
    return {
        "H1": {"pass": h1_ok, "mid_book_net_pct": mid.get("net_pct"), "net_pct_by_skew": nets, "sign_depends_on_skew": sign_flips,
               "rule": "kill if mid-skew net <= 0 or the sign of net P&L changes across skew books"},
        "H2": {"pass": bool(h2_rows) and all(r["pass"] for r in h2_rows), "by_skew": h2_rows,
               "rule": "CVaR(95) and worst day must both improve by >= 25% vs the unfiltered book"},
    }


def run(f: pd.DataFrame, events: set[date], atm_scale: float, skews=(0.10, 0.20, 0.30)) -> dict:
    books = [BookCfg(f"h1_sk{s:.2f}", s, False, atm_scale=atm_scale) for s in skews] + \
            [BookCfg(f"h2_sk{s:.2f}", s, True, atm_scale=atm_scale) for s in skews]
    ms = [metrics(simulate(f, b, events)) for b in books]
    return {"atm_scale": atm_scale, "books": ms, "verdicts": verdicts(ms)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spx", required=True)
    ap.add_argument("--vix1d", required=True)
    ap.add_argument("--vix", required=True)
    ap.add_argument("--vix3m")
    ap.add_argument("--events", help="text file, one YYYY-MM-DD per line (FOMC, CPI, NFP, PCE)")
    ap.add_argument("--atm-scale", type=float, default=1.0)
    ap.add_argument("--out", default="h1h2_results.json")
    a = ap.parse_args()
    f = build_frame(load_tv_csv(a.spx), load_tv_csv(a.vix1d), load_tv_csv(a.vix), load_tv_csv(a.vix3m) if a.vix3m else None)
    ev = set()
    if a.events:
        ev = {date.fromisoformat(x.strip()) for x in open(a.events) if x.strip()}
    out = run(f, ev, a.atm_scale)
    json.dump(out, open(a.out, "w"), indent=1, default=str)
    print(json.dumps(out["verdicts"], indent=1))


if __name__ == "__main__":
    main()
