"""Activate and verify a real FICO Xpress license, for use before any Xpress-solver
execution (both the Python `xpress` bindings used by PyPSA/linopy, and the
antares-problem-generator/benders binaries used by Antares-Xpansion).

Both consumers read the license through the same channel: the `XPAUTH_PATH`
environment variable pointing at an `xpauth.xpr` file. Setting it here, before any
subprocess is launched or any xpress.problem() is created, is sufficient for both --
subprocess.run() (used elsewhere in this directory) inherits the parent environment
unless overridden, and the Python xpress module reads XPAUTH_PATH at import/init time.

Without a real license, `xpress` falls back to the free Community edition, which is
capped at a small problem size and raises a clear SolverError only once a problem
over that cap is actually solved -- so a real network's solve would otherwise fail
deep into a Benders iteration or a PyPSA build, instead of failing clearly up front.
This module's `activate_and_verify()` catches that immediately: it builds a trivial LP
deliberately larger than the Community cap and confirms it solves without hitting that
size-limit error.

Usage (as a library):
    from xpress_license import activate_and_verify
    activate_and_verify(Path("/path/to/xpauth.xpr"))  # raises RuntimeError if not a real license

Usage (as a standalone check):
    uv run python tests/local_benchmark/xpress_license.py /path/to/xpauth.xpr
"""

import os
import sys
from pathlib import Path

# Community edition cap (FICO Xpress 9.x): 5000 rows + 5000 columns for LP/MIP. Comfortably
# exceeding both in one dummy problem is enough to distinguish community from a real license.
_COMMUNITY_SIZE_CAP = 5000
_PROBE_SIZE = _COMMUNITY_SIZE_CAP + 500


def activate_and_verify(license_path: Path) -> None:
    """Point Xpress at `license_path` and confirm it's a real (non-Community) license.

    Raises FileNotFoundError if the path doesn't exist, and RuntimeError if Xpress
    still falls back to the Community edition (wrong/expired/unreachable license) or
    fails to initialize for any other reason.
    """
    if not license_path.is_file():
        raise FileNotFoundError(f"Xpress license file not found: {license_path}")

    os.environ["XPAUTH_PATH"] = str(license_path)

    import xpress as xp

    try:
        xp.init(str(license_path))
    except Exception as exc:
        raise RuntimeError(f"xpress.init() failed with license file {license_path}: {exc}") from exc

    problem = xp.problem()
    variables = [xp.var(name=f"x{i}", lb=0, ub=1) for i in range(_PROBE_SIZE)]
    problem.addVariable(variables)
    # One constraint per pair of variables, comfortably over the row cap too.
    problem.addConstraint(variables[i] + variables[i + 1] <= 1 for i in range(_PROBE_SIZE - 1))
    problem.setObjective(sum(variables))
    try:
        problem.solve()
    except xp.SolverError as exc:
        if "maximum is" in str(exc).lower() or "too many rows and columns" in str(exc).lower():
            raise RuntimeError(
                f"Xpress is still running under the Community license after pointing XPAUTH_PATH at "
                f"{license_path} (probe problem with {_PROBE_SIZE} vars/constraints, over the "
                f"Community cap of {_COMMUNITY_SIZE_CAP}, hit the Community size limit: {exc}). "
                "Check the license file is valid, not expired, and reachable from this machine."
            ) from exc
        raise

    print(f"Xpress license activated and verified as non-Community: {license_path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(f"Usage: {sys.argv[0]} /path/to/xpauth.xpr")
    activate_and_verify(Path(sys.argv[1]))
