# Changelog: coursework → public repository

## Rebuild for publication (2026)

**Reframed** two ISQS 7339 assignments around security investment. The case study
and homework problems were course-provided and are not included. Their modeling
patterns carry over:

| Coursework pattern | Where it lives now |
|---|---|
| Binary selection under a budget with "only one", "at least one", "if A then B", ratio and "all of" rules (case study) | Exclusive groups, CSF coverage, prerequisites, Protect ratio, mandates in `model.py` |
| Minimize cost subject to a count target (case study, part 2) | `min_cost` objective with a risk-reduction target |
| Fixed set-up cost with a binary switch (homework) | Platform fee `y` linked to suite modules |
| Either/or capacity constraint with big-M (homework) | In-house vs. MSSP analyst capacity |
| Slack and shadow prices from PuLP (homework) | Constraint slack, LP-relaxation duals, plus the price of each rule by re-optimization |

**What changed in the code**
- One data-driven, solver-neutral model (SciPy/HiGHS and PuLP/CBC) with inputs in CSV/JSON, validated on load.
- Exact multiplicative risk model via McCormick linearization, lexicographic tie-breaking, efficient frontier, price-of-policy analysis, exhaustive-search verification and a test suite.

**Design decisions**
- Replaced an additive risk model (first draft) that favored stacks of cheap minor controls over EDR and backups.
- Modeled money in $ thousands to keep solver coefficients well scaled (big-M vs. small effects); a regression test sweeps budgets against exhaustive search.
- Spend is computed from the model's own cost terms, so there is no separate price list to drift out of sync.

**Not included**
- Course-provided case materials and homework problems.

## Original submissions (Spring 2022)
- ISQS 7339 group case study 2 (optimization); this repository reuses only my own code's modeling approach
- ISQS 7339 Homework 1: linear and integer programming with PuLP
