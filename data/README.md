# Data

All inputs are **illustrative assumptions for a hypothetical mid-size company**. They are
not vendor quotes or industry benchmarks; replace them with your own numbers.

| File | What it holds |
|---|---|
| `scenarios.csv` | Risk scenarios and their annualized loss expectancy (ALE). The ALEs are the simulated means from the companion [`cyber-risk-monte-carlo`](https://github.com/casildata/cyber-risk-monte-carlo) project. |
| `controls.csv` | Control catalogue: NIST CSF function, CIS Controls v8.1 safeguard(s) where one applies (optional column), annual cost, analyst hours per year (negative = saves time, e.g. SOAR automation), shared platform, mutually exclusive group, prerequisite. |
| `control_effects.csv` | Share of a scenario's remaining loss that a control removes (0–1). Effects combine multiplicatively: two controls with 40% and 30% leave 0.6 × 0.7 = 42% of the loss. |
| `policy.json` | Budget, analyst capacity, MSSP option, platform fees, mandated controls, CSF coverage minimum, Protect-to-other ratio. |

`load_instance()` validates the files (unknown IDs, effects outside 0–1, missing platforms,
duplicate rows) and stops with a list of problems.
