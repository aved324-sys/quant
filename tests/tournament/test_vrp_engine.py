"""Tests for tournament/research/vrp_engine.py (Gen 3 H1/H2 engine)."""
import importlib.util
import math
import pathlib
import sys
from datetime import date

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("vrp_engine", ROOT / "tournament/research/vrp_engine.py")
ve = importlib.util.module_from_spec(spec)
sys.modules["vrp_engine"] = ve
spec.loader.exec_module(ve)


def synthetic_frame(n_days: int, realized: float, implied: float, seed: int, start="2026-01-05") -> pd.DataFrame:
    """GBM SPX 5m RTH bars with annualised realised vol `realized` (trading-time convention T = min/390/252)
    and a constant VIX1D quote of `implied`."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(start, periods=n_days)
    step_sd = realized * math.sqrt(5 / 390 / 252)
    rows, px = [], 7000.0
    for d in days:
        for i in range(78):
            ts = pd.Timestamp(d.year, d.month, d.day, 9, 30, tz=ve.ET) + pd.Timedelta(minutes=5 * i)
            o = px
            px = px * math.exp(rng.normal(0, step_sd) - 0.5 * step_sd ** 2)
            rows.append((ts, o, max(o, px), min(o, px), px))
    f = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"]).set_index("ts")
    f["vix1d"] = implied * 100
    f["vix"] = implied * 100
    f["vix3m"] = implied * 100 + 1
    return f


def h1_mean_day(f, skew=0.0):
    res = ve.simulate(f, ve.BookCfg("t", skew, False), set())
    return ve.metrics(res, n_boot=500)


def test_put_call_parity_and_atm_delta():
    S, K, T, v = 7000.0, 7000.0, 300 / 390 / 252, 0.15
    c, dc = ve.bs(S, K, T, v, False)
    p, dp = ve.bs(S, K, T, v, True)
    assert abs((c - p) - (S - K)) < 1e-9
    assert 0.45 < dc < 0.55 and -0.55 < dp < -0.45


def test_short_strike_respects_delta_cap():
    S, T = 7000.0, 360 / 390 / 252
    k = ve.find_short(S, True, T, 0.15, 0.2, 0.0, 0.15)
    _, d = ve.bs(S, k, T, ve.iv_at(S, k, 0.15, True, 0.2, 0.0), True)
    assert k < S and abs(d) <= 0.15
    _, d_next = ve.bs(S, k + 5, T, ve.iv_at(S, k + 5, 0.15, True, 0.2, 0.0), True)
    assert abs(d_next) > 0.15  # it is the FIRST strike under the cap, not an arbitrarily far one


def test_engine_measures_variance_premium_sign():
    """Implied well above realised -> the condor earns; implied well below -> it loses. Same seed, same path shape."""
    rich = h1_mean_day(synthetic_frame(120, realized=0.10, implied=0.16, seed=3))
    cheap = h1_mean_day(synthetic_frame(120, realized=0.16, implied=0.10, seed=3))
    assert rich["trades"] > 100 and cheap["trades"] > 100
    assert rich["mean_day_usd"] > 0, rich
    assert cheap["mean_day_usd"] < 0, cheap


def test_fair_vol_loses_to_costs():
    """With implied = realised there is no premium to harvest; slippage and fees should make it lose on average."""
    m = h1_mean_day(synthetic_frame(200, realized=0.14, implied=0.14, seed=11))
    assert m["mean_day_usd"] < 0, m


def test_oos_window_is_never_simulated():
    f = synthetic_frame(30, 0.10, 0.16, seed=5, start="2026-07-20")  # straddles OOS_START (2026-08-01)
    res = ve.simulate(f, ve.BookCfg("t", 0.0, False), set())
    assert res.days and max(res.days) < ve.OOS_START
    assert all(date.fromisoformat(t["date"]) < ve.OOS_START for t in res.trades)


def test_filters_skip_event_days_and_inverted_term_structure():
    f = synthetic_frame(20, 0.10, 0.16, seed=9)
    first = sorted(set(f.index.date))[3]
    f.loc[[ts for ts in f.index if ts.date() == sorted(set(f.index.date))[5]], "vix3m"] = 1.0  # inverted that day
    res = ve.simulate(f, ve.BookCfg("t", 0.0, True), {first})
    traded = {t["date"] for t in res.trades}
    assert str(first) not in traded
    assert str(sorted(set(f.index.date))[5]) not in traded
    assert res.skipped >= 2


def test_verdicts_flag_skew_sign_flip():
    ms = [{"put_skew": 0.1, "filtered": False, "net_pct": -1.0, "cvar95_pct": -1.0, "worst_day_pct": -1.0},
          {"put_skew": 0.2, "filtered": False, "net_pct": 0.5, "cvar95_pct": -1.0, "worst_day_pct": -1.0},
          {"put_skew": 0.3, "filtered": False, "net_pct": 2.0, "cvar95_pct": -1.0, "worst_day_pct": -1.0}]
    v = ve.verdicts(ms)
    assert v["H1"]["sign_depends_on_skew"] and not v["H1"]["pass"]
