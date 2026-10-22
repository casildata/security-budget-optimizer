"""Load and validate the problem instance (controls, risk scenarios, policy)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CSF_FUNCTIONS = ["Identify", "Protect", "Detect", "Respond", "Recover"]


@dataclass
class Instance:
    controls: pd.DataFrame   # indexed by control_id
    scenarios: pd.DataFrame  # indexed by scenario_id, column `ale`
    effects: pd.DataFrame    # control_id x scenario_id, share of scenario ALE removed
    policy: dict = field(default_factory=dict)
    effect_min: pd.DataFrame | None = None  # same shape as effects; None if no ranges given
    effect_max: pd.DataFrame | None = None
    ale_source: str = "data/scenarios.csv"

    @property
    def total_ale(self) -> float:
        return float(self.scenarios["ale"].sum())


def load_instance(data_dir: Path = DATA_DIR, ales: pd.Series | None = None,
                  ale_source: str | None = None, **policy_overrides) -> Instance:
    """Read the inputs. `ales` (indexed by scenario name) replaces the ALEs in scenarios.csv."""
    text = ["platform", "exclusive_group", "requires", "cis_safeguard"]
    controls = pd.read_csv(data_dir / "controls.csv",
                           dtype=dict.fromkeys(text, str)).set_index("control_id")
    for col in text:  # cis_safeguard is optional in a user's own catalogue
        controls[col] = controls[col].fillna("") if col in controls else ""
    scenarios = pd.read_csv(data_dir / "scenarios.csv").set_index("scenario_id")
    raw_effects = pd.read_csv(data_dir / "control_effects.csv")
    policy = json.loads((data_dir / "policy.json").read_text())
    policy.update(policy_overrides)

    if ales is not None:
        missing = set(scenarios["scenario"]) - set(ales.index)
        if missing:
            raise ValueError(f"no ALE for scenario(s): {sorted(missing)}")
        scenarios["ale"] = scenarios["scenario"].map(ales).astype(float)

    _validate(controls, scenarios, raw_effects, policy)

    def table(col):
        return (raw_effects.pivot(index="control_id", columns="scenario_id", values=col)
                .reindex(index=controls.index, columns=scenarios.index).fillna(0.0))

    ranged = {"reduction_min", "reduction_max"} <= set(raw_effects.columns)
    return Instance(controls, scenarios, table("reduction"), policy,
                    table("reduction_min") if ranged else None,
                    table("reduction_max") if ranged else None,
                    ale_source or "data/scenarios.csv")


def _validate(controls, scenarios, raw_effects, policy) -> None:
    problems = []
    if controls.index.duplicated().any():
        problems.append("duplicate control_id")
    bad_fn = set(controls["csf_function"]) - set(CSF_FUNCTIONS)
    if bad_fn:
        problems.append(f"unknown CSF function(s): {sorted(bad_fn)}")
    unknown_req = set(controls["requires"]) - set(controls.index) - {""}
    if unknown_req:
        problems.append(f"`requires` points to unknown control(s): {sorted(unknown_req)}")
    unknown_platform = set(controls["platform"]) - set(policy.get("platforms", {})) - {""}
    if unknown_platform:
        problems.append(f"platform(s) missing from policy.json: {sorted(unknown_platform)}")
    unknown_mandatory = set(policy.get("mandatory", [])) - set(controls.index)
    if unknown_mandatory:
        problems.append(f"mandatory control(s) not in controls.csv: {sorted(unknown_mandatory)}")
    if not set(raw_effects["control_id"]) <= set(controls.index):
        problems.append("control_effects.csv references unknown controls")
    if not set(raw_effects["scenario_id"]) <= set(scenarios.index):
        problems.append("control_effects.csv references unknown scenarios")
    if not raw_effects["reduction"].between(0, 1).all():
        problems.append("reductions must be between 0 and 1")
    if {"reduction_min", "reduction_max"} <= set(raw_effects.columns):
        lo, mode, hi = (raw_effects[c] for c in ("reduction_min", "reduction", "reduction_max"))
        if not (lo.between(0, 1) & hi.between(0, 1) & (lo <= mode) & (mode <= hi)).all():
            problems.append("effect ranges need 0 <= reduction_min <= reduction <= reduction_max <= 1")
    if raw_effects.duplicated(["control_id", "scenario_id"]).any():
        problems.append("duplicate (control_id, scenario_id) in control_effects.csv")
    if (controls["annual_cost"] < 0).any():
        problems.append("negative annual_cost")
    if problems:
        raise ValueError("Invalid input data:\n  - " + "\n  - ".join(problems))
