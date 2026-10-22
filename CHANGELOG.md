# Changelog: from the 2022 original to this release

## 2026-10-22: robustness, baseline and CI

- Control costs and effects now match the cyber-risk-monte-carlo bundles (EDR + backups $150k and 75% of ransomware loss, MFA + call-back $60k and 73% of BEC, DLP + encryption $120k and 55% of breach loss); every result was regenerated. At $600k the optimizer now spends $550k, because analyst hours bind first.
- Scenario ALEs are read from the cyber-risk-monte-carlo v1.0 release asset (SHA-256 checked), with `scenarios.csv` as the offline fallback.
- Effect uncertainty: triangular ranges in `control_effects.csv`, a 200-draw re-optimization sweep with selection frequency and regret, and a new figure.
- Greedy "bang for the buck" baseline, compared with the optimizer at eight budgets.
- Exhaustive-search checks for `max_net_benefit` and `min_cost` (tests and `run_all.py`), 19 tests in all.
- CI: tests, pipeline and a `docs/results.md` drift check on pinned versions (`requirements-lock.txt`), tests on Python 3.10, gitleaks.

## 2026-10-22: review fixes

- PuLP/CBC cross-check now runs (PuLP 2.7–3.x) and matches HiGHS; PuLP 4 changed its API, so it is detected and skipped with a message instead of crashing `run_all.py`.
- `docs/results.md` no longer contains solve timings (printed to the console instead), so a rerun reproduces it exactly.
- `check_requirements.py` no longer installs packages; it prints the pip command and stops.
- `cis_safeguard` column in `controls.csv` (CIS Controls v8.1) and a NIST CSF 2.0 / CIS mapping in the README.
- README math written in LaTeX; runtime corrected; link to the cyber-risk-monte-carlo v1.0 release.

## Rebuild for publication (2026)

**Reframed** two earlier optimization studies, a constrained-selection case study and a modeling study, around security investment. The original problem statements came from the graduate program and are not redistributed. Their modeling patterns carry over:

| Original pattern | Where it lives now |
|---|---|
| Binary selection under a budget with "only one", "at least one", "if A then B", ratio and "all of" rules (case study) | Exclusive groups, CSF coverage, prerequisites, Protect ratio, mandates in `model.py` |
| Minimize cost subject to a count target (case study, part 2) | `min_cost` objective with a risk-reduction target |
| Fixed set-up cost with a binary switch (modeling study) | Platform fee `y` linked to suite modules |
| Either/or capacity constraint with big-M (modeling study) | In-house vs. MSSP analyst capacity |
| Slack and shadow prices from PuLP (modeling study) | Constraint slack, LP-relaxation duals, plus the price of each rule by re-optimization |

**What changed in the code**
- One data-driven, solver-neutral model (SciPy/HiGHS and PuLP/CBC) with inputs in CSV/JSON, validated on load.
- Exact multiplicative risk model via McCormick linearization, lexicographic tie-breaking, efficient frontier, price-of-policy analysis, exhaustive-search verification and a test suite.

**Design decisions**
- Replaced an additive risk model (first draft) that favored stacks of cheap minor controls over EDR and backups.
- Modeled money in $ thousands to keep solver coefficients well scaled (big-M vs. small effects); a regression test sweeps budgets against exhaustive search.
- Spend is computed from the model's own cost terms, so there is no separate price list to drift out of sync.

**Not included**
- Third-party case materials and problem statements from the original program.

## Original work (2022)
- Team optimization case study, graduate Simulation & Optimization, Texas Tech University; this repository reuses only my own modeling approach
- Modeling study: linear and integer programming with PuLP
