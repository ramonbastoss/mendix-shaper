"""Matching a running Studio Pro to the working copy it holds.

No IDE required: these cover the parsing, which is where this can be wrong
without anybody noticing. Run it directly:

    python tests/test_studio_pro.py

Same shape as the other tests here - one line per case, non-zero exit on the
first failure, no runner as a dependency.

Why this is the part worth testing: identifying the instance is what every
other decision rests on. Get it wrong in one direction and the server reports
"not open" for a model somebody has open, then writes underneath a live IDE.
Get it wrong in the other and it refuses to open a model nothing is holding.
Neither failure announces itself.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import studio_pro  # noqa: E402

PASSED = FAILED = 0

EXE = r"C:\path\to\Mendix\10.24.13\modeler\studiopro.exe"
MPR = r"C:\path\to\MyApp\MyApp.mpr"


def check(title, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"[PASS] {title}")
    else:
        FAILED += 1
        print(f"[FAIL] {title}")
        print(f"       got  {got!r}")
        print(f"       want {want!r}")


# --- which model an instance holds -----------------------------------------

check("quoted .mpr is read out of the command line",
      studio_pro._mpr_of(f'"{EXE}" "{MPR}"'), MPR)

# Launched by hand from a shell, or by anything that does not quote.
check("unquoted .mpr is read too",
      studio_pro._mpr_of(rf"{EXE} C:\apps\MyApp\MyApp.mpr"),
      r"C:\apps\MyApp\MyApp.mpr")

# An IDE sitting on the start page. Not a parse failure - a real state, and the
# one that gets replaced when an app opens.
check("an instance with no app reports no model",
      studio_pro._mpr_of(f'"{EXE}"'), None)

check("an empty command line is not a crash",
      studio_pro._mpr_of(""), None)

# The .exe path contains "modeler" and the app path contains ".mpr"; a pattern
# anchored on the wrong one picks up the install directory instead.
check("the executable is not mistaken for the model",
      studio_pro._mpr_of(f'"{EXE}" "{MPR}"').endswith("MyApp.mpr"), True)


# --- comparing two paths ---------------------------------------------------

# Windows paths are case-insensitive, and the command line's casing comes from
# whoever typed it, not from the configuration. Comparing them as plain strings
# is how an open IDE goes unnoticed.
check("case does not make it a different model",
      studio_pro._same_path(MPR, MPR.upper()), True)

check("a relative path resolves to the same model",
      studio_pro._same_path(
          os.path.join(os.path.dirname(MPR), "sub", "..", "MyApp.mpr"), MPR),
      True)

check("two different models are not the same",
      studio_pro._same_path(MPR, r"C:\path\to\Other\Other.mpr"), False)

check("a missing side is never a match",
      studio_pro._same_path(None, MPR), False)


# --- PowerShell output -----------------------------------------------------

# ConvertTo-Json renders a one-element collection as a bare object, so a tool
# that pipes a list gets a different shape depending on how many instances are
# running. One object per line removes the difference.
check("one instance parses as a list of one",
      studio_pro._json_lines('{"pid": 1234}'), [{"pid": 1234}])

check("several instances parse in order",
      [r["pid"] for r in studio_pro._json_lines('{"pid": 1}\n{"pid": 2}\n')],
      [1, 2])

check("no instances is an empty list, not an error",
      studio_pro._json_lines("\n  \n"), [])

# PowerShell writes warnings and progress into the same stream often enough
# that a strict parser would turn a cosmetic message into a failed lookup.
check("a non-JSON line is skipped rather than fatal",
      studio_pro._json_lines('WARNING: something\n{"pid": 7}'), [{"pid": 7}])


# --- finding the IDE next to mx.exe ----------------------------------------

try:
    studio_pro.studiopro_exe("mx")
    raised = False
except FileNotFoundError:
    raised = True
check("a bare 'mx' cannot locate an IDE, and says so", raised, True)


print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
