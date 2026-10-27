"""Check that a model still loads and still makes sense, after a write.

Two passes, because neither catches what the other does.

`mx check` computes the consistency errors Studio Pro shows (CE####). It finds
a reference broken *by name* - CE1613, "the selected microflow no longer
exists". It does **not** find a reference broken by GUID: a pointer at an id
that is not in the model is not a consistency error, it is a crash inside the
loader, and `check` never gets far enough to report anything.

So the loader runs first. `mx dump-mpr` reads every unit and resolves its
references, which makes its exit code a usable canary: if the model cannot be
opened, nothing else is worth measuring. Its output is 200 MB of JSON nobody
wants here, so it goes to the null device - the exit code is the signal.
"""

import json
import os
from collections import Counter
from datetime import datetime

from tools._utils import (
    resolve_project,
    require_mpr,
    run_mx,
    temp_file,
)

# `mx check`'s exit code is a bitmask, not success/failure: 1 errors,
# 2 warnings, 4 deprecations, 8 best practice, OR'd together. Exit 1 means "it
# found errors", which is a successful run of the command.
_EXIT_ERRORS = 1


def _run_loader(proj: dict) -> dict:
    mpr = proj["mpr_path"]
    with open(os.devnull, "w") as sink:
        result = run_mx(proj, "dump-mpr", mpr, stdout=sink)
    if result.returncode == 0:
        return {"loads": True}
    stderr = (result.stderr or b"")
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    return {
        "loads": False,
        "error": stderr.strip()[:2000],
        "hint": (
            "The model does not open. A KeyNotFoundException naming a GUID "
            "means a reference points at a unit that is not there; the message "
            "does not say which unit holds it. Restore with git rather than "
            "hunting for it."
        ),
    }


def _run_check(proj: dict, key: str) -> dict:
    report_path = temp_file("checks", f"{key}.json")
    result = run_mx(proj, "check", proj["mpr_path"], "--json", report_path)

    if not os.path.exists(report_path):
        stderr = result.stderr or ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        raise RuntimeError(f"mx check produced no report: {stderr.strip()[:2000]}")

    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    errors = report.get("errors", []) or []
    return {
        "exit_code": result.returncode,
        "found_errors": bool(result.returncode & _EXIT_ERRORS),
        "errors": len(errors),
        "by_code": dict(Counter(e.get("code", "?") for e in errors).most_common()),
        # locations[0] is where the error is anchored; its keys are hyphenated
        # (`module-name`, `document-name`), which is easy to get wrong.
        "by_module": dict(Counter(
            (e.get("locations") or [{}])[0].get("module-name", "?")
            for e in errors
        ).most_common(10)),
        "report_path": report_path,
    }


def _baseline_path(key: str) -> str:
    return temp_file("baselines", f"{key}.json")


def validate_model(project: str = None, save_as_baseline: bool = False) -> dict:
    """Loader canary, consistency check, and the delta against the baseline."""
    try:
        key, proj = resolve_project(project)
        require_mpr(proj, key)
    except (ValueError, FileNotFoundError) as e:
        return {"success": False, "error": str(e)}

    loader = _run_loader(proj)
    if not loader["loads"]:
        # No point measuring consistency on a model that will not open, and
        # `check` would fail in a way that reads like a different problem.
        return {"success": True, "project": key, "loads": False, **loader}

    try:
        check = _run_check(proj, key)
    except (RuntimeError, OSError, json.JSONDecodeError) as e:
        return {"success": False, "project": key, "loads": True, "error": str(e)}

    result = {"success": True, "project": key, "loads": True, **check}

    baseline_path = _baseline_path(key)
    if os.path.exists(baseline_path):
        with open(baseline_path, encoding="utf-8") as f:
            baseline = json.load(f)
        before = baseline.get("by_code", {})
        after = check["by_code"]
        result["baseline"] = {
            "taken_at": baseline.get("taken_at"),
            "errors_then": baseline.get("errors", 0),
            "errors_now": check["errors"],
            "delta": check["errors"] - baseline.get("errors", 0),
            # Which codes appeared is the part that attributes a regression. A
            # net delta of zero can still hide one error fixed and another
            # introduced.
            "codes_introduced": {
                c: n - before.get(c, 0)
                for c, n in after.items() if n > before.get(c, 0)
            },
            "codes_cleared": {
                c: before[c] - after.get(c, 0)
                for c in before if before[c] > after.get(c, 0)
            },
        }
    else:
        result["baseline"] = None
        result["note"] = (
            "No baseline stored for this project, so these counts say nothing "
            "on their own - an app can carry hundreds of pre-existing errors. "
            "Call again with save_as_baseline=true on a known-good model, "
            "before writing, to make later runs attributable."
        )

    if save_as_baseline:
        with open(baseline_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    # The model's own mtime, not the clock: it identifies the
                    # state that was measured, which is what a later run needs
                    # to know it is comparing against the right thing.
                    "taken_at": datetime.fromtimestamp(
                        os.path.getmtime(proj["mpr_path"])
                    ).isoformat(timespec="seconds"),
                    "errors": check["errors"],
                    "by_code": check["by_code"],
                },
                f,
                indent=2,
            )
        result["baseline_saved"] = baseline_path

    return result
