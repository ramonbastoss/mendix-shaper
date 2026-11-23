"""Opening and closing the IDE that holds the model.

Writing into a working copy Studio Pro has open is the one thing this server
cannot make safe on its own. The IDE owns the model *in memory*; the `.mpr` and
the blobs under `mprcontents/` are only the persisted state. `assert_writable`
in `storage.py` takes SQLite's write lock and refuses when something else is
writing, but an IDE that merely has a model open holds no lock at all, so that
guard never sees the case that actually loses work. Closing the IDE first is
the rule. This module is what turns that rule from a sentence in a README into
a step a caller can take.

It controls a process, not a model, so it touches none of the `.mxunit`/`.mpr`
machinery and shares nothing with the rest of the engine. The one-way rule
still holds: nothing here imports MCP.

**Nothing here ever answers a dialog.** Not by clicking, not by killing the
process, not behind a `force=True`. Both failure modes this module reports are
modal questions whose wrong answer is unrecoverable - one discards unsaved work,
the other opens a second IDE on a model Mendix says must not be opened twice -
and an option that exists is an option something will eventually take. So a
blocked close returns what the dialog says and stops. A person answers it.

Measured on Studio Pro 10.24.13 (MPRv2, Git-backed app), Windows 11, on
2026-11-23. Numbers come from a ~6,000-unit app.
"""

import ctypes
import json
import os
import re
import subprocess
import time
from ctypes import wintypes

# Win32 constants. `GW_OWNER` is the one that matters: a modal dialog is an
# *owned* window, and its main window is not owned by anything. That is how a
# dialog is told apart from the app here - not by matching its title, which
# changes with the dialog, the Studio Pro language and the app.
WM_CLOSE = 0x0010
GW_OWNER = 4

# Studio Pro paints its window before the model is in it, and the title stays
# at the bare product name until the app has finished loading. So "the window
# exists" is not "the app is open"; the title changing is. A window still
# showing this after the timeout means the load never completed - usually
# because a dialog is sitting on top of it.
UNLOADED_TITLE = "Mendix Studio Pro"

# Loading measured at 42-57 s. The ceiling is deliberately far above that: an
# app larger than the one measured, or a cold file cache, pushes it up, and a
# premature timeout here reads like a failure when it is only slowness.
DEFAULT_OPEN_TIMEOUT = 300

# Closing measured at 2.1 s with nothing unsaved. Anything beyond a few seconds
# means a dialog is up, which is a question for a person and not something to
# wait longer for.
DEFAULT_CLOSE_TIMEOUT = 20

_PS_TIMEOUT = 60

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

_PROCESS_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0x0


# --- shelling out ----------------------------------------------------------

def _powershell(script: str) -> str:
    """Run PowerShell and return stdout.

    `stdin=DEVNULL` is not optional, for the same reason as `run_mx` in
    `tools/_utils.py`: under the stdio transport this process's stdin is the
    JSON-RPC pipe, and a child that inherits it and waits on it hangs the whole
    server. `-NonInteractive` is the second half of that - it makes a prompt an
    error instead of a wait.
    """
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=_PS_TIMEOUT,
        text=True,
    )
    return result.stdout or ""


def _json_lines(text: str) -> list[dict]:
    """Parse one JSON object per line.

    PowerShell's `ConvertTo-Json` serialises a one-element array as a bare
    object, so a caller that pipes a collection gets a list sometimes and a
    dict other times, depending on how many things it found. Emitting one
    compact object per line sidesteps that entirely.
    """
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


# --- windows ---------------------------------------------------------------

def _windows_of(pid: int) -> list[dict]:
    """Every visible top-level window belonging to `pid`.

    A process-level `MainWindowTitle` is not enough: when Studio Pro puts up a
    modal, the main window keeps its own title and the modal is a separate
    top-level window that no process-level property mentions. A caller looking
    only at the process sees a healthy app and an operation that never
    finishes. Enumerating is the only way the dialog is visible at all.
    """
    found = []

    def callback(hwnd, _lparam):
        window_pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if window_pid.value == pid and _user32.IsWindowVisible(hwnd):
            length = _user32.GetWindowTextLengthW(hwnd) + 1
            buffer = ctypes.create_unicode_buffer(length)
            _user32.GetWindowTextW(hwnd, buffer, length)
            found.append({
                "hwnd": hwnd,
                "title": buffer.value,
                "is_dialog": bool(_user32.GetWindow(hwnd, GW_OWNER)),
            })
        return True

    _user32.EnumWindows(_ENUM_PROC(callback), 0)
    return found


def read_dialog(hwnd: int) -> dict:
    """What a Studio Pro dialog says, and which buttons it offers.

    Studio Pro is WPF, so a dialog is a single window handle with no child
    handles - Win32 alone gives the title and nothing else. UI Automation is
    what reads the inside of it, and it is reached through PowerShell because
    that is where the .NET assemblies already are.

    Read-only on purpose. UIA can click too, and this function deliberately
    does not: see the module docstring.
    """
    script = f"""
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$el = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]{hwnd})
if ($null -eq $el) {{ exit }}
$all = $el.FindAll(
  [System.Windows.Automation.TreeScope]::Descendants,
  [System.Windows.Automation.Condition]::TrueCondition)
foreach ($e in $all) {{
  $name = $e.Current.Name
  if ($name) {{
    [pscustomobject]@{{
      kind = ($e.Current.ControlType.ProgrammaticName -replace 'ControlType\\.', '')
      name = $name
    }} | ConvertTo-Json -Compress
  }}
}}
"""
    text_parts, buttons, seen = [], [], set()
    for item in _json_lines(_powershell(script)):
        kind, name = item.get("kind"), item.get("name")
        if (kind, name) in seen:
            continue
        seen.add((kind, name))
        # Every label shows up twice, once as the control and once as the Text
        # inside it, so the two lists are built from the control types rather
        # than from everything that carries a name.
        if kind == "Button":
            buttons.append(name)
        elif kind == "Text" and name not in buttons:
            text_parts.append(name)
    return {
        "hwnd": hwnd,
        "text": "\n".join(text_parts),
        "buttons": buttons,
    }


# --- processes -------------------------------------------------------------

def _mpr_of(command_line: str) -> str | None:
    """The `.mpr` a Studio Pro instance was launched with, from its argv.

    This is the only reliable way to tell which working copy an instance holds.
    The window title looks like it would do - and it does not: it reads
    `<AppName> (<branch>, Git)`, so it carries the app's name and the *branch*,
    and neither has to resemble the name of the folder on disk. Measured on a
    checkout whose folder, branch and app name were three different strings:
    the title identified none of them.
    """
    if not command_line:
        return None
    match = re.search(r'"([^"]+\.mpr)"|(\S+\.mpr)', command_line, re.IGNORECASE)
    if not match:
        return None
    return match.group(1) or match.group(2)


def _same_path(left: str | None, right: str | None) -> bool:
    """Whether two paths name the same file, Windows-style."""
    if not left or not right:
        return False
    return (os.path.normcase(os.path.abspath(left))
            == os.path.normcase(os.path.abspath(right)))


def instances(mpr_path: str = None) -> list[dict]:
    """Running Studio Pro instances, optionally only those holding `mpr_path`.

    An instance launched with no app at all reports `mpr: None`. It is worth
    keeping in the list rather than filtering out, because it behaves
    differently from a loaded one: an idle instance is *replaced* when an app
    opens, a loaded one is not.
    """
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='studiopro.exe'\" | "
        "ForEach-Object { [pscustomobject]@{ pid = $_.ProcessId; "
        "exe = $_.ExecutablePath; cmd = $_.CommandLine } | "
        "ConvertTo-Json -Compress }"
    )
    out = []
    for row in _json_lines(_powershell(script)):
        pid = row.get("pid")
        if not pid:
            continue
        mpr = _mpr_of(row.get("cmd") or "")
        if mpr_path and not _same_path(mpr, mpr_path):
            continue
        windows = _windows_of(pid)
        main = next((w for w in windows if not w["is_dialog"]), None)
        out.append({
            "pid": pid,
            "exe": row.get("exe"),
            "mpr": mpr,
            "title": main["title"] if main else "",
            "loaded": bool(main and main["title"]
                           and main["title"] != UNLOADED_TITLE),
            "dialogs": [w for w in windows if w["is_dialog"]],
        })
    return out


def _open_handle(pid: int):
    """A SYNCHRONIZE handle on a process, or 0 if it is gone.

    `OpenProcess` also returns 0 on access-denied, which would read as "it
    exited" - but these are Studio Pro instances this same user is running, and
    one it could not open is one it could not have closed either.
    """
    return _kernel32.OpenProcess(_PROCESS_SYNCHRONIZE, False, pid)


def _is_alive(pid: int) -> bool:
    handle = _open_handle(pid)
    if not handle:
        return False
    _kernel32.CloseHandle(handle)
    return True


def _wait_for_exit(pid: int, timeout: float) -> bool:
    """Block until the process exits, or until `timeout`. True if it exited.

    A wait rather than a poll, so the ordinary close returns the moment the IDE
    is gone instead of on the next tick. When a dialog holds the IDE open this
    does run the full timeout - which is the correct shape anyway, since
    nothing about a modal changes by looking at it sooner.
    """
    handle = _open_handle(pid)
    if not handle:
        return True
    try:
        result = _kernel32.WaitForSingleObject(handle, int(timeout * 1000))
        return result == _WAIT_OBJECT_0
    finally:
        _kernel32.CloseHandle(handle)


# --- the two operations ----------------------------------------------------

def studiopro_exe(mx_path: str) -> str:
    """The IDE that belongs to the `mx` a project is configured with.

    Derived rather than configured, for the same reason `project_root` derives
    the working copy from the `.mpr`: two settings that must agree will
    eventually disagree. Here the stake is higher than a wrong folder. A
    machine can carry several installs of *the same version number* - a stock
    one under Program Files and a patched one elsewhere - and `.mpr` files are
    associated with the Mendix Version Selector, which resolves the version and
    picks whichever install it knows about. Taking the IDE from the `mx.exe`
    this project already runs its checks with removes the choice: the model is
    opened by the same build that validates it.
    """
    folder = os.path.dirname(os.path.abspath(mx_path))
    exe = os.path.join(folder, "studiopro.exe")
    if not os.path.exists(exe):
        raise FileNotFoundError(
            f"No studiopro.exe next to {mx_path}. 'mx_path' has to be the full "
            "path to the mx.exe inside a Studio Pro install, because the IDE "
            f"is taken from the same folder. Looked for: {exe}"
        )
    return exe


def open_app(mpr_path: str, mx_path: str,
             timeout: int = DEFAULT_OPEN_TIMEOUT) -> dict:
    """Open a model in Studio Pro and wait until it has actually loaded.

    Refuses outright if the model is already open somewhere, and that refusal
    is the point of the function rather than a detail of it. Launching a second
    instance on an open model does not fail: Studio Pro comes up with no app,
    puts a `Warning` modal on screen - *"already open in another instance ...
    Opening an app more than once is not supported"* with **Open anyway** and
    **Cancel** - and waits. There is no exit code and no failed launch; a
    caller polling for the title simply never sees one. So the check happens
    before the launch, where it can still be reported as a question.
    """
    already = instances(mpr_path)
    if already:
        return {
            "status": "needs_user_decision",
            "reason": "already_open",
            "mpr_path": mpr_path,
            "instances": already,
            "question": (
                f"Studio Pro already has {os.path.basename(mpr_path)} open "
                f"(pid {already[0]['pid']}). Nothing was launched. Use the "
                "instance that is open, or close it first?"
            ),
            "why_not_automatic": (
                "Launching anyway would not open a second copy - it would put "
                "a modal on the user's screen and hang there unseen, because "
                "Mendix does not support opening one app twice."
            ),
        }

    exe = studiopro_exe(mx_path)
    started = time.monotonic()
    # Detached and with stdin closed: this child outlives the call by design,
    # and must not inherit the JSON-RPC pipe.
    process = subprocess.Popen(
        [exe, mpr_path],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS,
    )

    # The wait watches this one pid through the Win32 API only. Re-running the
    # CIM query every tick would cost a PowerShell process per second for the
    # best part of a minute, to re-learn a command line that cannot change.
    while time.monotonic() - started < timeout:
        if not _is_alive(process.pid):
            return {
                "status": "failed",
                "error": "Studio Pro exited before the model finished loading.",
                "pid": process.pid,
                "seconds": round(time.monotonic() - started, 1),
            }
        # An owned window is *not* treated as a block here, unlike in
        # `close_app`. Studio Pro shows two of them on the way up, measured:
        # a splash carrying the version string, then an "Open App" progress
        # list ("Check app version", "Load app", "Initializing app...") which
        # carries a Cancel button of its own. So neither "a dialog is up" nor
        # "it has buttons" distinguishes a question from the loader doing its
        # job, and an early return on either never lets an app finish opening.
        # What a real question does instead is keep the title from arriving,
        # which the timeout below catches, and reports with its text.
        main = next((w for w in _windows_of(process.pid)
                     if not w["is_dialog"]), None)
        if main and main["title"] and main["title"] != UNLOADED_TITLE:
            return {
                "status": "open",
                "pid": process.pid,
                "title": main["title"],
                "mpr_path": mpr_path,
                "seconds": round(time.monotonic() - started, 1),
            }
        time.sleep(1)

    # Out of time with no title. Whatever is on screen now is read and handed
    # over, because after this long it is more likely a question nobody saw
    # than a slow load - and if it is only the progress dialog, its own text
    # says which step it is stuck on.
    on_screen = [w for w in _windows_of(process.pid) if w["is_dialog"]]
    app = os.path.basename(mpr_path)
    if not on_screen:
        return {
            "status": "needs_user_decision",
            "reason": "open_timed_out",
            "pid": process.pid,
            "seconds": round(time.monotonic() - started, 1),
            "question": (
                f"Studio Pro has been opening {app} for {timeout}s without "
                "finishing, and there is nothing on screen to explain it. "
                "Nothing was closed - leave it running, or stop waiting?"
            ),
        }

    blocked = _dialog_block({"pid": process.pid, "dialogs": on_screen},
                            "open_timed_out", started)
    said = "\n\n".join(d["text"] for d in blocked["dialogs"] if d["text"])
    blocked["question"] = (
        f"Studio Pro has been opening {app} for {timeout}s without finishing, "
        f"and nothing was clicked. What is on screen says:\n\n"
        f"{said or '(no text could be read)'}\n\n"
        "If that is the loader's own progress window it is merely slow; if it "
        "is a question, it needs an answer in the IDE."
    )
    return blocked


def close_app(mpr_path: str, timeout: int = DEFAULT_CLOSE_TIMEOUT) -> dict:
    """Close the Studio Pro instance holding a model, and say whether it went.

    Sends `WM_CLOSE` - the same message the window's X button sends, so the IDE
    runs its own shutdown, including asking about work that is not saved. There
    is no stronger option here on purpose. Terminating the process would also
    close it, and would throw away a model that exists only in Studio Pro's
    memory; measured on an edited app, the working copy on disk was untouched
    and `git status` was clean, so nothing on disk would have survived the kill.

    A close that does not complete is therefore never escalated. It is reported
    with the dialog's own text, and the caller is expected to put the question
    to the user.
    """
    holding = instances(mpr_path)
    if not holding:
        return {
            "status": "not_open",
            "mpr_path": mpr_path,
            "note": "No Studio Pro instance has this model open.",
        }
    if len(holding) > 1:
        # Reachable: the "already open" warning offers **Open anyway**, so a
        # person can end up with two IDEs on one model. Which one to close is
        # not this function's call to make.
        return {
            "status": "needs_user_decision",
            "reason": "several_instances",
            "instances": holding,
            "question": (
                f"{len(holding)} Studio Pro instances have "
                f"{os.path.basename(mpr_path)} open "
                f"(pids {', '.join(str(i['pid']) for i in holding)}). "
                "Nothing was closed. Which one should go?"
            ),
        }

    instance = holding[0]
    windows = _windows_of(instance["pid"])
    main = next((w for w in windows if not w["is_dialog"]), None)
    if main is None:
        return {
            "status": "needs_user_decision",
            "reason": "no_main_window",
            "instances": holding,
            "question": (
                f"Studio Pro (pid {instance['pid']}) has no main window to "
                "close - it may still be starting up. Nothing was sent."
            ),
        }

    started = time.monotonic()
    _user32.PostMessageW(main["hwnd"], WM_CLOSE, 0, 0)

    if _wait_for_exit(instance["pid"], timeout):
        return {
            "status": "closed",
            "pid": instance["pid"],
            "mpr_path": mpr_path,
            "seconds": round(time.monotonic() - started, 1),
        }

    refreshed = dict(instance)
    refreshed["dialogs"] = [w for w in _windows_of(instance["pid"])
                            if w["is_dialog"]]
    return _dialog_block(refreshed, "unsaved_changes", started)


def _dialog_block(instance: dict, reason: str, started: float) -> dict:
    """A blocked operation, with whatever the dialog on screen actually says.

    The text is read out rather than guessed at, because the answer depends on
    it. Studio Pro asks **per document** - "Do you want to save the changes to
    Domain Model [MyEntity]?" - so answering one can be followed by the next,
    and the number of pending questions is not known from here.
    """
    # Titled, because "owned window" catches more than questions: Studio Pro's
    # own toast ("Notification Stack") and its loader are owned too. Showing
    # the title next to the text is what lets a reader tell the question from
    # the furniture, rather than this guessing on their behalf by filtering on
    # titles that change with the Studio Pro language.
    dialogs = [{**read_dialog(w["hwnd"]), "title": w["title"]}
               for w in instance.get("dialogs", [])]
    buttons = [b for d in dialogs for b in d["buttons"]]
    said = "\n\n".join(d["text"] for d in dialogs if d["text"])
    return {
        "status": "needs_user_decision",
        "reason": reason,
        "pid": instance["pid"],
        "seconds": round(time.monotonic() - started, 1),
        "dialogs": dialogs,
        "question": (
            f"Studio Pro (pid {instance['pid']}) is waiting on a dialog and "
            f"nothing was clicked. It says:\n\n{said or '(no text could be read)'}"
            + (f"\n\nButtons: {', '.join(buttons)}." if buttons else "")
            + "\n\nThe IDE is still running and the model is untouched. This "
              "needs an answer from the user, in the IDE."
        ),
        "warning": (
            "If the dialog offers 'Do not ask me again', leaving it unticked "
            "keeps later closes asking. Ticked once, Studio Pro stops asking "
            "and applies the remembered answer silently - including discarding "
            "work."
        ),
    }
