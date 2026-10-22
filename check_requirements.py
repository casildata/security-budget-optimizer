"""Check that the packages in requirements.txt are installed and new enough.

Nothing is installed automatically: if a package is missing or too old, the
script stops and prints the pip command to run. Only plain `name>=version` lines and `sys_platform` markers are understood,
which is all the requirements files in this repository use.
"""
import re
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _requirements(path):
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        spec, _, marker = line.split("#", 1)[0].partition(";")
        if not spec.strip():
            continue
        platform = re.search(r"sys_platform\s*==\s*['\"](\w+)['\"]", marker)
        if platform and platform.group(1) != sys.platform:
            continue
        name, _, minimum = spec.partition(">=")
        yield name.strip(), minimum.strip() or None


def _version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def check_requirements(path):
    problems = []
    for name, minimum in _requirements(path):
        try:
            have = version(name)
        except PackageNotFoundError:
            problems.append(f"{name} (not installed)")
            continue
        if minimum and _version_tuple(have) < _version_tuple(minimum):
            problems.append(f"{name} {have} (need >= {minimum})")
    if not problems:
        return

    cmd = f'"{sys.executable}" -m pip install -r "{path}"'
    sys.exit("Missing or outdated packages:\n  " + "\n  ".join(problems)
             + "\nInstall them with:\n  " + cmd)
