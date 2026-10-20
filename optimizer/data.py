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

    @property
    def total_ale(self) -> float:
        return float(self.scenarios["ale"].sum())


def load_instance(data_dir: Path = DATA_DIR, **policy_overrides) -> Instance:
    controls = pd.read_csv(data_dir / "controls.csv", dtype={"platform": str,
                           "exclusive_group": str, "requires": str}).set_index("control_id")
    controls[["platform", "exclusive_group", "requires"]] = (
        controls[["platform", "exclusive_group", "requires"]].fillna(""))
    scenarios = pd.read_csv(data_dir / "scenarios.csv").set_index("scenario_id")
    raw_effects = pd.read_csv(data_dir / "control_effects.csv")
    policy = json.loads((data_dir / "policy.json").read_text())
    policy.update(policy_overrides)

    _validate(controls, scenarios, raw_effects, policy)
    effects = (raw_effects.pivot(index="control_id", columns="scenario_id", values="reduction")
               .reindex(index=controls.index, columns=scenarios.index).fillna(0.0))
    return Instance(controls, scenarios, effects, policy)


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
    if raw_effects.duplicated(["control_id", "scenario_id"]).any():
        problems.append("duplicate (control_id, scenario_id) in control_effects.csv")
    if (controls["annual_cost"] < 0).any():
        problems.append("negative annual_cost")
    if problems:
        raise ValueError("Invalid input data:\n  - " + "\n  - ".join(problems))
