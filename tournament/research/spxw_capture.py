"""Build the daily SPXW 0DTE bars document from saved Robinhood tool results.

Usage:
    python3 spxw_capture.py --date 2026-11-20 --index INDEX_FILE --options OPT_FILE [OPT_FILE ...] \
        --snap tournament/data/spxw/2026-11-20_snap.json --out tournament/data/spxw/2026-11-20_bars.json

INDEX_FILE: get_index_historicals result (any intraday interval; resampled to 10-minute bars).
OPT_FILE:   get_option_historicals results (10-minute bars). Duplicated contracts across files are de-duplicated.
--snap:     the same day's snapshot document; supplies strike/type for each contract id.
"""
import argparse
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

ET = ZoneInfo("America/New_York")


def load(path):
    return json.load(open(path))["data"]["results"]


def to_et(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(ET)


def index_10m(results, symbol):
    r = next(x for x in results if x["symbol"] == symbol)
    df = pd.DataFrame([{"ts": to_et(b["begins_at"]), "o": float(b["open_value"]), "h": float(b["high_value"]),
                        "l": float(b["low_value"]), "c": float(b["close_value"])}
                       for b in r["bars"] if not b.get("interpolated")]).set_index("ts")
    agg = df.resample("10min", label="left", closed="left").agg({"o": "first", "h": "max", "l": "min", "c": "last"}).dropna()
    agg = agg[(agg.index.time >= datetime(2000, 1, 1, 9, 30).time()) & (agg.index.time < datetime(2000, 1, 1, 16, 0).time())]
    return {"t": [t.strftime("%H:%M") for t in agg.index], "o": agg["o"].round(2).tolist(), "h": agg["h"].round(2).tolist(),
            "l": agg["l"].round(2).tolist(), "c": agg["c"].round(2).tolist()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--index", required=True)
    ap.add_argument("--options", nargs="+", required=True)
    ap.add_argument("--snap", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    snap = json.load(open(a.snap))
    meta = {c["id"]: c for c in snap["contracts"]}
    idx = load(a.index)
    spx = index_10m(idx, "SPX")
    vix = index_10m(idx, "VIX")
    seen, contracts, failures = set(), [], []
    for f in a.options:
        for r in load(f):
            cid = r["instrument_id"]
            if cid in seen:
                continue
            seen.add(cid)
            m = meta.get(cid)
            if m is None:
                failures.append(f"unknown id {cid}")
                continue
            bars = r["bars"]
            contracts.append({
                "id": cid, "strike": m["strike"], "type": m["type"],
                "t": [to_et(b["begins_at"]).strftime("%H:%M") for b in bars],
                "o": [float(b["open_price"]) for b in bars], "h": [float(b["high_price"]) for b in bars],
                "l": [float(b["low_price"]) for b in bars], "c": [float(b["close_price"]) for b in bars],
                "interp": [i for i, b in enumerate(bars) if b.get("interpolated")],
            })
    missing = [c["id"] for c in snap["contracts"] if c["id"] not in seen]
    failures += [f"no bars for {i}" for i in missing]
    contracts.sort(key=lambda c: (c["type"], c["strike"]))
    doc = {"date": a.date, "kind": "bars", "interval": "10minute",
           "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "spx": spx, "vix": {"t": vix["t"], "c": vix["c"]}, "contracts": contracts, "failures": failures}
    s = json.dumps(doc, separators=(",", ":"))
    open(a.out, "w").write(s)
    bars = sorted({len(c["c"]) for c in contracts})
    print(f"{a.date}: {len(contracts)} contracts, bars per contract {bars}, spx bars {len(spx['t'])}, "
          f"{len(s)} bytes, failures {failures or 'none'}")


if __name__ == "__main__":
    main()
