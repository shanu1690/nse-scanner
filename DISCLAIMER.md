# Disclaimer

nse-scanner is a research and education tool. It is **not investment advice**,
and its output is not a recommendation to buy, sell, or hold any security or
derivative contract.

- **Not SEBI registered.** This project is not run by a SEBI-registered
  investment adviser or research analyst. Nothing it produces should be
  treated as advice from one.
- **Decision support only.** The system never places orders, never connects
  to a broker's write API, and never executes trades. Every pick is a
  candidate for a human to evaluate, not an instruction to act on.
- **Options can lose 100% of the premium paid.** Every long option idea this
  project surfaces states this plainly; it is not a hidden risk. Debit
  spreads cap the loss at the net premium paid, which can still be a full
  loss of the amount committed to that idea.
- **Past performance, including everything in the Backtest tab and the
  Journal's scorecard, does not predict future results.** Walk-forward
  results are reported with their sample size, window, and baseline
  specifically so they can be judged honestly — a result from one
  historical window is not a guarantee for any future one.
- **Data can be wrong, late, or missing.** Prices, option chains, and
  fundamentals come from third-party sources (Angel One SmartAPI, NSE, BSE,
  Yahoo Finance) this project does not control. The dashboard marks its own
  data as stale when it is, but "not marked stale" is not a guarantee of
  correctness.
- **You are responsible for your own decisions.** Position sizing, risk
  tolerance, and the decision to enter or exit any trade are yours alone.
  The risk-management layer in this project enforces the limits configured
  in `config.yaml`; it does not know your actual financial situation.

If you are unsure whether a security or strategy is suitable for you, consult
a SEBI-registered investment adviser.
