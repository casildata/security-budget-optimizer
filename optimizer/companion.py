"""Scenario losses from the companion cyber-risk-monte-carlo release.

Release v1.0 publishes the simulated loss of every scenario in each of 20,000 years
(`annual_loss_by_year.csv.gz`). The file is downloaded once into outputs/, checked
against its SHA-256, and its per-scenario means become the ALEs. If it can't be fetched,
callers fall back to data/scenarios.csv, which holds the same means.
"""

from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path

import pandas as pd

RELEASE_URL = ("https://github.com/casildata/cyber-risk-monte-carlo/releases/download/"
               "v1.0/annual_loss_by_year.csv.gz")
RELEASE_SHA256 = "e4a9cf2f28c1171897cd33286ffbdbc49d387c7887911879ca08ca93eec53425"
CACHE = Path(__file__).resolve().parent.parent / "outputs" / "annual_loss_by_year.csv.gz"


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ales_from_file(path: Path, expected_sha256: str = RELEASE_SHA256) -> pd.Series:
    """Mean annual loss per scenario (whole dollars), after checking the file's hash."""
    got = sha256(path)
    if got != expected_sha256:
        raise ValueError(f"{path} has SHA-256 {got}, expected {expected_sha256}")
    losses = pd.read_csv(path).drop(columns=["year", "Total"])
    return losses.mean().round()


def release_ales(cache: Path = CACHE) -> tuple[pd.Series | None, str]:
    """(ALEs, note) from the release asset, or (None, why not) so callers can fall back."""
    try:
        if not cache.exists() or sha256(cache) != RELEASE_SHA256:
            cache.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(RELEASE_URL, timeout=30) as r:
                cache.write_bytes(r.read())
        return ales_from_file(cache), "cyber-risk-monte-carlo v1.0 release (SHA-256 checked)"
    except (OSError, ValueError) as e:
        return None, f"release asset unavailable ({e})"
