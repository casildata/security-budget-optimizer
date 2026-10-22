# Data

All inputs are **illustrative assumptions for a hypothetical mid-size company**. They are
not vendor quotes or industry benchmarks; replace them with your own numbers.

| File | What it holds |
|---|---|
| `scenarios.csv` | Risk scenarios and their annualized loss expectancy (ALE). `run_all.py` replaces the ALEs with the means of the per-year losses in the companion [`cyber-risk-monte-carlo`](https://github.com/casildata/cyber-risk-monte-carlo) v1.0 release (matched by scenario name); the values here are the same means, used when the release can't be downloaded. |
| `controls.csv` | Control catalogue: NIST CSF function, CIS Controls v8.1 safeguard(s) where one applies (optional column), annual cost, analyst hours per year (negative = saves time, e.g. SOAR automation), shared platform, mutually exclusive group, prerequisite. |
| `control_effects.csv` | Share of a scenario's remaining loss that a control removes (0–1), as `reduction` (most likely value) with an optional triangular range `reduction_min`–`reduction_max` for the uncertainty sweep. Effects combine multiplicatively: two controls with 40% and 30% leave 0.6 × 0.7 = 42% of the loss. |
| `policy.json` | Budget, analyst capacity, MSSP option, platform fees, mandated controls, CSF coverage minimum, Protect-to-other ratio. |

`load_instance()` validates the files (unknown IDs, effects outside 0–1 or outside their range,
missing platforms, duplicate rows) and stops with a list of problems.

## Consistency with cyber-risk-monte-carlo

That project prices three control bundles, one scenario each. Here each bundle is split into
its two controls so the optimizer can fund them separately, with costs and effects chosen so
that the pair reproduces the bundle at its most likely values:

| Bundle (scenario) | There: cost, loss removed | Here: controls | Combined |
|---|---|---|---|
| EDR + immutable backups (ransomware) | $150k; 50% frequency × 50% severity = 75% | EDR_A $100k, 50% · IMM_BACKUP $50k, 50% | $150k, 1 − 0.5 × 0.5 = 75% |
| Phishing-resistant MFA + payment call-back (BEC) | $60k; 70% frequency, 10% severity = 73% | MFA_PR $45k, 40% · PAY_CALLBACK $15k, 55% | $60k, 1 − 0.6 × 0.45 = 73% |
| DLP + database encryption (customer data breach) | $120k; 25% frequency, 40% severity = 55% | DLP $85k, 25% · DB_ENC $35k, 40% | $120k, 1 − 0.75 × 0.6 = 55% |

The ransomware and breach ranges reuse that project's triangular min/max; every other range
is the most likely value ±40%. Controls here can also affect other scenarios (EDR also cuts
breach and insider losses), which the companion project doesn't model.
