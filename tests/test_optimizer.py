"""Optimizer tests. Run with `pytest`, or `python tests/test_optimizer.py` without it.

The key test compares the MILP against exhaustive enumeration of every control subset
at many budgets: the enumeration applies the policy rules directly, so it checks the
model encoding (McCormick chain, big-M either/or, fixed charges) independently.
"""
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from optimizer import (brute_force_max_reduction, build_model, effect_uncertainty,  # noqa: E402
                       feasible_subsets, greedy_portfolio, load_instance, lp_relaxation,
                       optimal_portfolio, pulp_unavailable, release_ales, residual_ale,
                       solve_pulp, solve_scipy, summarize)
from optimizer.analysis import _triangular  # noqa: E402
from optimizer.companion import ales_from_file, sha256  # noqa: E402
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


def test_net_benefit_matches_exhaustive_search():
    _, reduction, cost = feasible_subsets(INST)
    nb = summarize(INST, solve_scipy(build_model(INST, "max_net_benefit")))
    assert abs(nb["net_benefit"] - (reduction - cost).max()) < 1.0


def test_min_cost_matches_exhaustive_search():
    _, reduction, cost = feasible_subsets(INST)
    for share in (0.4, 0.5, 0.6, 0.7):
        sol = solve_scipy(build_model(INST, "min_cost", target_share=share))
        expected = cost[reduction >= share * INST.total_ale - 1e-6].min()
        assert abs(summarize(INST, sol)["spend"] - expected) < 1.0, share


def test_greedy_never_beats_the_optimizer_while_keeping_the_rules():
    for budget in range(300_000, 1_200_001, 100_000):
        g = greedy_portfolio(INST, budget)
        assert g["spend"] <= budget + 1e-6
        assert "MFA_PR" in g["controls"]
        best = summarize(INST, optimal_portfolio(INST, budget))["reduction"]
        if not g["broken"]:
            assert g["reduction"] <= best + 1.0, budget
        manual = INST.total_ale - residual_ale(INST, g["controls"]).sum()
        assert abs(manual - g["reduction"]) < 1e-6


def test_greedy_reports_the_coverage_it_skips():
    # At $300k the best bang-for-the-buck picks fund no Identify control
    g = greedy_portfolio(INST, 300_000)
    assert "Identify coverage" in g["broken"]
    assert not INST.controls.loc[g["controls"], "csf_function"].eq("Identify").any()


def test_triangular_draws_stay_in_range_with_the_right_mean():
    rng = np.random.default_rng(1)
    lo, mode, hi = (np.full(200_000, v) for v in (0.1, 0.25, 0.7))
    x = _triangular(rng, lo, mode, hi)
    assert x.min() >= 0.1 and x.max() <= 0.7
    assert abs(x.mean() - (0.1 + 0.25 + 0.7) / 3) < 1e-3
    fixed = _triangular(rng, mode, mode, mode)   # zero-width range returns the mode
    assert (fixed == 0.25).all()


def test_effect_uncertainty_without_spread_has_no_regret():
    fixed = replace(INST, effect_min=INST.effects, effect_max=INST.effects)
    u = effect_uncertainty(fixed, BUDGET, draws=3)
    assert u["share_nominal_optimal"] == 1.0
    assert u["regret"].max() < 1.0
    assert set(u["frequency"][u["frequency"] == 1.0].index) == set(u["nominal"])


def test_bad_effect_range_is_rejected():
    with tempfile.TemporaryDirectory() as d:
        shutil.copytree(REPO_ROOT / "data", d, dirs_exist_ok=True)
        effects = pd.read_csv(Path(d) / "control_effects.csv")
        effects.loc[0, "reduction_max"] = effects.loc[0, "reduction"] - 0.01
        effects.to_csv(Path(d) / "control_effects.csv", index=False)
        try:
            load_instance(Path(d))
        except ValueError as e:
            assert "reduction_min <= reduction <= reduction_max" in str(e)
        else:
            raise AssertionError("an effect above its reduction_max was accepted")


def test_release_file_is_checked_and_averaged():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "annual_loss_by_year.csv.gz"
        years = pd.DataFrame({"year": [1, 2], "Ransomware": [0, 1001], "DDoS outage": [10, 20]})
        years.assign(Total=years["Ransomware"] + years["DDoS outage"]).to_csv(path, index=False)
        means = ales_from_file(path, sha256(path))
        assert means.to_dict() == {"Ransomware": 500.0, "DDoS outage": 15.0}  # rounded half-even
        try:
            ales_from_file(path, "0" * 64)
        except ValueError as e:
            assert "SHA-256" in str(e)
        else:
            raise AssertionError("a file with the wrong checksum was accepted")


def test_release_means_match_scenarios_csv():
    ales, note = release_ales()
    if ales is None:
        skip(note)
    from_release = load_instance(ales=ales).scenarios["ale"]
    assert (from_release == INST.scenarios["ale"]).all()


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
