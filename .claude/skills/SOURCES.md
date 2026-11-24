# Vendored skills

| Skills | Source | Commit | License |
| --- | --- | --- | --- |
| `arena/` | https://github.com/Jakeschincariol/arena-skill | df07b8e | MIT |
| `socratic-tutor/` | https://github.com/janestreet/agent_skills (`socratic-tutor/`) | 469868f | MIT |
| `trader-*` (11) | https://github.com/Jakeschincariol/day-trader-skill | 3d0f4ef | MIT |
| all others | https://github.com/tradermonty/claude-trading-skills (`skills/`) | 3bd4b7a | MIT |

Copied verbatim; `tests/` folders and caches were left out, except the day-trader tests, which live in
`tests/day-trader/` (run `python3 -m unittest discover -s tests/day-trader`). License texts are in `LICENSES/`.
To update, re-clone and copy over.

## Local changes to the day-trader skills

Re-apply these if you update from upstream.

- **Slippage in the max loss** (`trader-budget/size.py`, `trader-risk/check.py`, `config.example.json`):
  new `slippage_per_share` (default 0.02) is added to the risk a share. Sizing and the risk check's
  loss and daily-loss limits now use it. Upstream assumed every stop fills exactly at its price, which
  understates the loss most when the stop is tight and the share count is high (0.10 stop, 500 shares:
  $50 at the stop, $60 with 0.02 of slippage).
- **Gap fills** (`trader-timemachine/timemachine.py`): a bar that opens past the stop fills at the
  open, in the replays and the history. Upstream counted every stop as exactly -1R.
- **History check** (same file): the history's target-first share gets a 95 % Wilson interval
  against break-even, plus the number of trades needed to see a 10-point edge.
- **Monte Carlo caveat** (same file): every run now says that the replays draw bars independently,
  so they model recent drift and volatility but cannot show whether the setup has an edge.
- Tests updated to match, plus new ones: `python3 -m unittest discover -s tests/day-trader`.
