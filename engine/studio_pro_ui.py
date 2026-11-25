"""Driving Studio Pro's own menus and dialogs through UI Automation.

`studio_pro.py` opens and closes the IDE and refuses to touch a dialog. That
rule is not relaxed here, it is drawn in a different place. The distinction this
module rests on is **initiating an operation** versus **answering a question**:

- Picking `Version Control > Commit` is initiating. Nothing is lost if it turns
  out to be the wrong menu - the operation puts its own dialog up and that
  dialog can be cancelled.
- Clicking `Yes` on *"Do you want to save the changes to ..."* is answering.
  The wrong answer is unrecoverable and no later step can undo it.

So: this module initiates, and when it meets a question it did not put there, it
stops and hands the text over, exactly as `studio_pro.read_dialog` already does.
Nothing here ticks a checkbox called `Do not ask me again`, and there is no
`force`.

Why UI Automation at all, rather than something supported: the Extensions API
ships `IVersionControlService` with exactly three members - `GetCurrentBranch`,
`GetHeadCommit`, `IsProjectVersionControlled` - all of them reads. There is no
`Commit`, `Pull` or `Push` to call, in-process or out. `mx.exe` has no app
lifecycle either. Measured against the 10.24.13 XML documentation on 2026-11-24.

Measured on Studio Pro 10.24.13 (MPRv2, Git-backed app), Windows 11, 2026-11-24.

## What the measurements forced into the design

**No control carries an `AutomationId`.** Every button, grid, radio and text box
in the version-control dialogs reports an empty id; only the window chrome
(`CloseWindow`, `MaximizeWindow`, `PART_LineUpButton`) has one. Every selector
here is therefore a `Name`, and a `Name` is whatever language the IDE is set to.
`check_language` exists because of this: a renamed menu produces "not found",
which reads like a broken selector when it is a settings difference, so the
failure says so.

**Menu item names end in `.` where the UI shows an ellipsis** - `Manage Branch
Lines.`, `History.`, `Select.`. Matching is by prefix for that reason; equality
against what a person reads on screen would never match.

**An owned window is not a question.** A `Progress` window appears first for
anything that talks to the server, and `Notification Stack` is a toast. Waiting
for "a new owned window" returns the progress window, every time - measured at
459 ms, well before the dialog. `wait_for_dialog` skips both by title and keeps
waiting.

**`IsEnabled` carries real state, and it is cheaper than re-deriving it.**
`Push (0 commit(s)).` comes back disabled with the count inside its own name, and
`Delete` / `Switch branch` are disabled while the current branch is selected.
Preconditions can be read off the IDE instead of reimplemented against git and
hoped to agree.

**Dialogs are fast.** Expanding a menu, opening the branch manager and opening a
dialog from it measured 343-653 ms each. Nothing here needs the minute-long
timeouts that opening an app does.

The one-way rule still holds: nothing here imports MCP.
"""

import json
import os
import subprocess

# Titles that are furniture rather than questions. Studio Pro shows the first
# for every server round-trip and the second for toasts; both are owned windows,
# which is why `GW_OWNER` alone cannot be the filter.
FURNITURE_TITLES = ("Progress", "Notification Stack", "")

# Generous because the first dialog of a session may wait on the version control
# server, and stingy compared to `studio_pro.DEFAULT_OPEN_TIMEOUT` because
# nothing here loads a model.
DEFAULT_UI_TIMEOUT = 120

# The PowerShell side of every sequence. It is one script per operation rather
# than one per step on purpose: a window handle is only valid while the window
# lives, and handing handles back and forth across processes would mean a
# half-driven dialog is normal. A sequence either runs to its stopping point or
# reports where it stopped, in one process.
PREAMBLE = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Collections.Generic;
public class SpWin {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr l);
  public delegate bool EnumWindowsProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr h, uint cmd);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int max);
  public static List<IntPtr> ByPid(uint want) {
    var res = new List<IntPtr>();
    EnumWindows((h,l) => { uint p; GetWindowThreadProcessId(h, out p);
      if (p == want && IsWindowVisible(h)) res.Add(h); return true; }, IntPtr.Zero);
    return res;
  }
  public static string Txt(IntPtr h) { var sb = new System.Text.StringBuilder(512); GetWindowText(h, sb, 512); return sb.ToString(); }
  public static bool Owned(IntPtr h) { return GetWindow(h, 4) != IntPtr.Zero; }
}
"@
$AE = [System.Windows.Automation.AutomationElement]
$TS = [System.Windows.Automation.TreeScope]
$CT = [System.Windows.Automation.ControlType]
$TRUE_COND = [System.Windows.Automation.Condition]::TrueCondition

function Emit($obj) { $obj | ConvertTo-Json -Compress -Depth 6 }
function Step($name, $detail) { Emit ([pscustomobject]@{ kind='step'; step=$name; detail="$detail" }) }
function Stop-Here($reason, $detail, $dialogs) {
  Emit ([pscustomobject]@{ kind='stop'; reason=$reason; detail="$detail"; dialogs=$dialogs })
  exit 0
}

function Get-Windows([int]$ProcId) {
  [SpWin]::ByPid([uint32]$ProcId) | ForEach-Object {
    [pscustomobject]@{ hwnd=[int64]$_; title=[SpWin]::Txt($_); owned=[SpWin]::Owned($_) }
  }
}
function Get-MainWindow([int]$ProcId) {
  Get-Windows $ProcId | Where-Object { -not $_.owned } | Select-Object -First 1
}

# Furniture is skipped by title rather than by "has buttons": the progress
# window has a Cancel of its own, so buttons do not separate a question from
# the loader doing its job.
function Wait-ForDialog([int]$ProcId, [int64[]]$Known, [int]$TimeoutMs) {
  $sw = [Diagnostics.Stopwatch]::StartNew()
  while ($sw.ElapsedMilliseconds -lt $TimeoutMs) {
    $c = Get-Windows $ProcId | Where-Object {
      $_.owned -and ($Known -notcontains $_.hwnd) -and ($FURNITURE -notcontains $_.title) }
    if ($c) { return ($c | Select-Object -First 1) }
    Start-Sleep -Milliseconds 250
  }
  return $null
}

function El([int64]$hwnd) {
  try { return $AE::FromHandle([IntPtr]$hwnd) } catch { return $null }
}
function ByType($Root, $Type) {
  $c = New-Object System.Windows.Automation.PropertyCondition($AE::ControlTypeProperty, $Type)
  $Root.FindAll($TS::Descendants, $c)
}
function Named($Root, $Type, $Name, [switch]$Prefix) {
  foreach ($e in (ByType $Root $Type)) {
    $n = $e.Current.Name
    if ($Prefix) { if ($n -like "$Name*") { return $e } }
    elseif ($n -eq $Name) { return $e }
  }
  return $null
}
function Invoke-El($El) {
  $El.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
}
function Expand-El($El) {
  $p = $El.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
  if ($p.Current.ExpandCollapseState -ne 'Expanded') { $p.Expand(); Start-Sleep -Milliseconds 500 }
  return $p
}
function Select-El($El) {
  $El.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select()
}
function Get-Value($El) {
  $El.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value
}
function Set-Value($El, $Text) {
  $El.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).SetValue($Text)
}

# Read a dialog the way `studio_pro.read_dialog` does, so a stop from here and a
# blocked close report the same shape. Labels appear twice - once as the control
# and once as the Text inside it - so buttons win and the text list excludes
# anything already named as a button.
function Read-Dialog($Win) {
  $e = El $Win.hwnd
  if ($null -eq $e) { return [pscustomobject]@{ hwnd=$Win.hwnd; title=$Win.title; text=''; buttons=@() } }
  $texts = @(); $buttons = @(); $seen = @{}
  foreach ($x in $e.FindAll($TS::Descendants, $TRUE_COND)) {
    $n = $x.Current.Name
    if (-not $n) { continue }
    $k = ($x.Current.ControlType.ProgrammaticName -replace 'ControlType\.','')
    if ($seen.ContainsKey("$k|$n")) { continue }
    $seen["$k|$n"] = $true
    if ($k -eq 'Button') { $buttons += $n } elseif ($k -eq 'Text') { $texts += $n }
  }
  [pscustomobject]@{
    hwnd = $Win.hwnd; title = $Win.title
    text = (($texts | Where-Object { $buttons -notcontains $_ }) -join "`n")
    buttons = $buttons
  }
}
function Other-Dialogs([int]$ProcId, [int64[]]$Known) {
  @(Get-Windows $ProcId | Where-Object {
      $_.owned -and ($Known -notcontains $_.hwnd) -and ($FURNITURE -notcontains $_.title) } |
    ForEach-Object { Read-Dialog $_ })
}
"""


def _run(body: str, timeout: int = DEFAULT_UI_TIMEOUT) -> list[dict]:
    """Run one UI sequence and return the records it emitted.

    `stdin=DEVNULL` and `-NonInteractive` for the same reason as everywhere else
    in this server: under the stdio transport this process's stdin is the
    JSON-RPC pipe, and a child that inherits it and waits on it hangs the server.

    A sequence that dies partway still returns the steps it managed to emit,
    which is the whole point of emitting per step - the caller needs to know how
    far into a dialog chain the IDE was left, not just that something failed.
    """
    script = PREAMBLE + "\n" + body
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            text=True,
        )
    except subprocess.TimeoutExpired as expired:
        records = _records(expired.stdout or "")
        records.append({
            "kind": "stop",
            "reason": "ui_timed_out",
            "detail": (
                f"The UI sequence did not finish within {timeout}s. Studio Pro "
                "may be waiting on a dialog that this run did not expect. "
                "Nothing further was clicked."
            ),
        })
        return records

    records = _records(result.stdout or "")
    if result.returncode != 0 and not any(r.get("kind") == "stop" for r in records):
        records.append({
            "kind": "stop",
            "reason": "script_failed",
            "detail": (result.stderr or "").strip()[:2000],
        })
    return records


def _records(text: str) -> list[dict]:
    """One JSON object per line.

    PowerShell's `ConvertTo-Json` renders a one-element collection as a bare
    object and a longer one as an array, so a caller piping a collection gets a
    different shape depending on how many things it found. One compact object
    per line sidesteps that, and it is also what makes a partial run readable.
    """
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _ps_string(value: str) -> str:
    """A PowerShell single-quoted literal.

    Single quotes rather than double: inside `"..."` PowerShell expands `$` and
    backticks, and a branch name or a Windows path is exactly the kind of string
    that can carry one. Doubling the quote is the whole escape rule for `'...'`.
    """
    return "'" + str(value).replace("'", "''") + "'"


def resolve_instance(mpr_path: str, git_branch: str | None = None) -> dict:
    """Which running Studio Pro holds a working copy, cross-checking three signals.

    `studio_pro.instances` derives this from the process command line alone, and
    that is measurably not enough: an app opened from inside the IDE leaves
    `studiopro.exe` running with no argument at all, so the command line is bare
    and the lookup returns nothing while the app sits on screen. A false
    negative, and it lands on the guard that protects writing.

    Three signals exist and none is sufficient alone:

    - `<app>.mpr.lock` next to the `.mpr` says *some* instance holds this
      working copy. It does not say which process.
    - The command line gives the exact path, but only when the IDE was launched
      with the `.mpr` as an argument.
    - The window title reads `<AppName> (<branch>, Git)`: it carries the branch,
      never the folder. On a checkout whose folder, branch and app name were
      three different strings, the title matched none of them.

    Crossing the lock with the branch is what resolves it, so the caller passes
    the branch it read from git for this working copy. Without that, a machine
    running two instances cannot be disambiguated and this says so rather than
    guessing.
    """
    from engine import studio_pro

    found = studio_pro.instances(mpr_path)
    if len(found) == 1:
        return {"status": "found", "via": "command_line", **found[0]}
    if len(found) > 1:
        return {
            "status": "ambiguous",
            "instances": found,
            "detail": (
                f"{len(found)} instances report {os.path.basename(mpr_path)} on "
                "their command line. Nothing was driven."
            ),
        }

    lock = os.path.abspath(mpr_path) + ".lock"
    if not os.path.exists(lock):
        return {
            "status": "not_open",
            "detail": (
                f"No instance reports {os.path.basename(mpr_path)} and there is "
                f"no {os.path.basename(lock)} next to it, so no Studio Pro has "
                "this working copy open."
            ),
        }

    # The lock says it is open and the command line did not - the bare-argv case.
    # The branch is the only remaining discriminator.
    if not git_branch:
        return {
            "status": "needs_user_decision",
            "reason": "cannot_identify_instance",
            "instances": studio_pro.instances(),
            "detail": (
                f"{os.path.basename(lock)} exists, so this working copy is open, "
                "but no running instance names it on its command line - which "
                "happens when the app was opened from inside the IDE. Pass the "
                "branch this working copy is on so the window title can be "
                "matched. Nothing was driven."
            ),
        }

    suffix = f"({git_branch}, Git)"
    matches = [i for i in studio_pro.instances() if i["title"].endswith(suffix)]
    if len(matches) == 1:
        return {"status": "found", "via": "lock_and_title", **matches[0]}
    return {
        "status": "needs_user_decision",
        "reason": "cannot_identify_instance",
        "instances": studio_pro.instances(),
        "detail": (
            f"{len(matches)} instances have a title ending in '{suffix}'. "
            "Working copies are reused by branch name, so two checkouts of one "
            "branch produce identical titles and nothing here can tell them "
            "apart. Nothing was driven."
        ),
    }


def check_language(pid: int, timeout: int = 30) -> dict:
    """Whether the menus carry the English names every selector here assumes.

    Worth its own call because of how the alternative fails. With no
    `AutomationId` anywhere, a Studio Pro set to another language turns every
    lookup in this module into "element not found", which reads as a broken
    selector or a changed Studio Pro version. Naming the real cause once, up
    front, is cheaper than diagnosing it from a stack of empty finds.
    """
    body = f"""
$FURNITURE = @({','.join(_ps_string(t) for t in FURNITURE_TITLES)})
$main = Get-MainWindow {pid}
if ($null -eq $main) {{ Stop-Here 'no_main_window' 'That process has no main window.' @() }}
$m = El $main.hwnd
$names = @()
foreach ($e in (ByType $m $CT::MenuItem)) {{ if ($e.Current.Name) {{ $names += $e.Current.Name }} }}
Emit ([pscustomobject]@{{ kind='menus'; names=$names }})
"""
    records = _run(body, timeout=timeout)
    names = next((r["names"] for r in records if r.get("kind") == "menus"), None)
    if names is None:
        stop = next((r for r in records if r.get("kind") == "stop"), {})
        return {"status": "unknown", "records": records, **stop}
    if "Version Control" in names:
        return {"status": "ok", "menus": names}
    return {
        "status": "wrong_language",
        "menus": names,
        "detail": (
            "The menu bar has no item called 'Version Control'. Every selector "
            "in this module is an English control name, because Studio Pro "
            "exposes no AutomationId to match on instead, so a non-English IDE "
            f"cannot be driven from here. The menus found were: {names}."
        ),
    }
