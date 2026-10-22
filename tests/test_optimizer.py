"""Optimizer tests. Run with `pytest`, or `python tests/test_optimizer.py` without it.

The key test compares the MILP against exhaustive enumeration of every control subset
at many budgets: the enumeration applies the policy rules directly, so it checks the
model encoding (McCormick chain, big-M either/or, fixed charges) independently.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from optimizer import (brute_force_max_reduction, build_model, load_instance,  # noqa: E402
                       lp_relaxation, optimal_portfolio, pulp_unavailable, residual_ale,
                       solve_pulp, solve_scipy, summarize)
from optimizer.data import CSF_FUNCTIONS  # noqa: E402

INST = load_instance()
BUDGET = INST.policy["budget"]


class Skipped(Exception):
    pass


def skip(reason):
    try:
        import pytest
    except ImportError:
        raise Skipped(reason) from None
    pytest.skip(reason)


def test_milp_matches_exhaustive_search_across_budgets():
    # $250k was once misreported as infeasible before money was scaled to $ thousands
    for budget in range(230_000, 1_200_001, 25_000):
        sol = solve_scipy(build_model(INST, budget=budget))
        expected, _ = brute_force_max_reduction(INST, budget)
        assert sol.optimal, f"solver failed at {budget}"
        assert abs(sol.objective - expected) < 1.0, f"{budget}: {sol.objective} vs {expected}"


def test_objective_equals_closed_form_residual():
    sol = solve_scipy(build_model(INST, budget=BUDGET))
    r = summarize(INST, sol)
    assert abs(r["reduction"] - sol.objective) < 1.0
    chosen = r["controls"]
    manual = INST.scenarios["ale"].sum() - residual_ale(INST, chosen).sum()
    assert abs(manual - r["reduction"]) < 1e-6


def test_solution_respects_every_policy_rule():
    sol = optimal_portfolio(INST, BUDGET)
    r = summarize(INST, sol)
    ctl = INST.controls.loc[r["controls"]]
    assert r["spend"] <= BUDGET + 1e-6
    for c in INST.policy["mandatory"]:
        assert c in r["controls"], f"mandatory control {c} missing"
    for g in INST.controls["exclusive_group"].unique():
        if g:
            assert (ctl["exclusive_group"] == g).sum() <= 1, f"two controls from group {g}"
    for c, req in ctl["requires"].items():
        if req:
            assert req in r["controls"], f"{c} chosen without prerequisite {req}"
    for fn in CSF_FUNCTIONS:
        assert (ctl["csf_function"] == fn).sum() >= INST.policy["min_per_function"], fn
    protect = (ctl["csf_function"] == "Protect").sum()
    other = ctl["csf_function"].isin(["Detect", "Respond", "Recover"]).sum()
    assert protect <= INST.policy["max_protect_ratio"] * other
    capacity = (INST.policy["mssp"]["hours_capacity"] if r["mssp"]
                else INST.policy["inhouse_hours"])
    assert r["analyst_hours"] <= capacity
    for c, p in ctl["platform"].items():
        if p:
            assert p in r["platforms"], f"{c} chosen without paying platform {p}"


def test_tie_break_returns_cheapest_optimal_portfolio():
    plain = summarize(INST, solve_scipy(build_model(INST, budget=BUDGET)))
    tidy = summarize(INST, optimal_portfolio(INST, BUDGET))
    assert abs(plain["reduction"] - tidy["reduction"]) < 1.0
    assert tidy["spend"] <= plain["spend"] + 1e-6


def test_frontier_is_monotone():
    previous = -1.0
    for budget in range(250_000, 1_200_001, 50_000):
        value = solve_scipy(build_model(INST, budget=budget)).objective
        assert value >= previous - 1e-6, f"risk reduction fell at {budget}"
        previous = value


def test_below_minimum_budget_is_infeasible():
    minimum = solve_scipy(build_model(INST, "min_cost", target_share=0.0)).objective
    assert not solve_scipy(build_model(INST, budget=minimum - 1_000)).optimal
    assert solve_scipy(build_model(INST, budget=minimum)).optimal


def test_min_cost_reaches_target():
    for share in (0.4, 0.6):
        sol = solve_scipy(build_model(INST, "min_cost", target_share=share))
        assert summarize(INST, sol)["reduction"] >= share * INST.total_ale - 1.0


def test_lp_relaxation_bounds_the_integer_optimum():
    model = build_model(INST, budget=BUDGET)
    assert lp_relaxation(model).objective >= solve_scipy(model).objective - 1.0


def test_invalid_input_is_rejected():
    try:
        load_instance(mandatory=["NOT_A_CONTROL"])
    except ValueError as e:
        assert "NOT_A_CONTROL" in str(e)
    else:
        raise AssertionError("unknown mandatory control was accepted")


def test_pulp_backend_matches_scipy():
    reason = pulp_unavailable()
    if reason:
        skip(reason)
    model = build_model(INST, budget=BUDGET)
    assert abs(solve_pulp(model).objective - solve_scipy(model).objective) < 1.0


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = skipped = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name}: {e}")
        except BaseException as e:  # our Skipped, or pytest's skip outcome
            if type(e).__name__ != "Skipped":
                raise
            skipped += 1
            print(f"SKIP  {name}: {e}")
    print(f"\n{len(tests) - failed - skipped}/{len(tests)} passed, {skipped} skipped")
    sys.exit(1 if failed else 0)
