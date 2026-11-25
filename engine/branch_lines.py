"""Creating a branch line through Studio Pro, because git alone cannot.

A branch created with `git push origin <sha>:refs/heads/<name>` is a real branch
and Studio Pro will list it. What raw git cannot produce is the note Studio Pro
attaches to a commit under `refs/notes/mx_metadata`:

    {"BranchName":"...","ModelerVersion":"...",
     "ModelChanges":[{"Status":"Modified","UnitID":"...","UnitType":"Forms$Page",
                      "UnitName":"...","Module":"..."}], ...}

`ModelChanges` is where the IDE's change list for a commit comes from - it is
read from the note, not computed by diffing, because MPRv2 has no supported
external diff. A commit made by raw git therefore shows up in history with
nothing to list, and the same note is what the build service reads to assemble a
deployment package, so that revision does not build either. Making the IDE the
agent of the operation is the only way to get the note, and this module is the
IDE being driven.

Measured on Studio Pro 10.24.13 (MPRv2, Git-backed app), Windows 11, 2026-11-24.

## The dialog chain, as measured

    Version Control              Expand()                    ~500 ms
      Manage Branch Lines.       Invoke()  -> Progress ->      459 ms
        Branch Line Manager      a DataGrid of branch lines
          New                    Invoke()                     460 ms
            Create Branch Line   radios, base combo, revision, name
              Select.            Invoke()                     653 ms
                Select Revision  a DataGrid of commits, newest first

Three things in that chain do work this module would otherwise have to redo:

- Selecting a row in the manager **prefills** `Create Branch Line`: the
  `Branch line` radio comes up selected and `Base branch line` comes up set to
  the row that was selected. Verified rather than assumed, because a silent
  wrong base is the one mistake here that produces a plausible-looking branch
  off the wrong history.
- `Revision` comes prefilled with the head of the base branch, and row 0 of
  `Select Revision` is that same commit. That is what makes `revision=0` mean
  head without a special case.
- `Existing branch lines` is a read-only multi-line box holding every branch
  name the server knows. It is the IDE's own list, so a name collision can be
  caught before `OK` instead of after.
"""

import os
import re
import unicodedata

from engine import studio_pro_ui as ui

# Windows resolves most paths against a 260-character limit, and a Mendix working
# copy is deep. Measured on a real checkout on 2026-11-24: the longest path
# inside it ran 182 characters past the working copy root, inside the
# `node_modules` tree that a JavaScript dependency brings along
# (`javascriptsource/<lib>/actions/node_modules/...`). That is not the model and
# nothing in Studio Pro controls it - it arrives with the dependency.
#
# So the usable budget for the root is what is left of MAX_PATH after that, and
# the headroom is thinner than it looks: a root like
# `C:\Users\<name>\Mendix\<AppName>-<Branch>` already spends about 50 of it.
MAX_PATH = 260
MEASURED_DEEPEST_RELATIVE = 182
ROOT_BUDGET = MAX_PATH - MEASURED_DEEPEST_RELATIVE  # 78

# Comfortable rather than merely legal. A root at the hard budget leaves exactly
# zero for a dependency deeper than the one measured, and `npm install` inside
# `javascriptsource` is how that happens.
ROOT_COMFORTABLE = 60


def check_download_path(path: str) -> dict:
    """Whether a folder is safe to download a working copy into.

    Two separate problems, and they fail differently, which is why both are
    checked here rather than left to the IDE to complain about:

    **Length.** Nothing refuses a long root. The checkout succeeds, and then a
    tool that walks the tree - a build, an `npm install`, a `git` operation on a
    deep file - fails on one file with a path error that names that file and not
    the root that caused it.

    **Characters outside ASCII.** The model is fine with them; the toolchain
    around it is where they bite, because a Mendix working copy is also a Java
    source tree, a node project and a git repository, each with its own idea of
    the filesystem encoding. Accented characters in a working copy *path* have
    not been measured here, so this refuses them as a conservative rule rather
    than reporting a measurement - but the same class of problem has been seen
    inside this stack, where a bullet character came back as an inverted question
    mark after a round trip through a mis-declared charset.

    Returns `ok`, `warn` or `refuse` with the reason, and never touches the disk.
    """
    path = os.path.abspath(path)
    problems, warnings = [], []

    non_ascii = sorted({c for c in path if ord(c) > 127})
    if non_ascii:
        shown = ", ".join(f"{c!r} ({unicodedata.name(c, 'unnamed')})"
                          for c in non_ascii[:5])
        problems.append(
            f"The path contains characters outside ASCII: {shown}. A working "
            "copy is a Java source tree and a node project as well as a model, "
            "and those toolchains do not agree on the filesystem encoding."
        )

    # Reserved on Windows regardless of what created them.
    illegal = sorted(set(re.findall(r'[<>:"|?*]', path[2:])))
    if illegal:
        problems.append(
            f"The path contains characters Windows reserves: {illegal}."
        )

    if len(path) > ROOT_BUDGET:
        problems.append(
            f"The path is {len(path)} characters. A working copy measured on "
            f"2026-11-24 carried a file {MEASURED_DEEPEST_RELATIVE} characters "
            f"below its root, so a root beyond {ROOT_BUDGET} pushes that file "
            f"past the {MAX_PATH}-character limit. The failure will name that "
            "file, not this path."
        )
    elif len(path) > ROOT_COMFORTABLE:
        warnings.append(
            f"The path is {len(path)} characters, within the {ROOT_BUDGET} that "
            f"fits the deepest file measured but leaving under "
            f"{ROOT_BUDGET - len(path)} to spare. One dependency deeper than "
            "that one puts it over."
        )

    if " " in path:
        warnings.append(
            "The path contains a space. Nothing here has measured a failure "
            "from it, but the build toolchain is a long chain of quoting."
        )

    if problems:
        return {"status": "refuse", "path": path, "problems": problems,
                "warnings": warnings}
    return {"status": "warn" if warnings else "ok", "path": path,
            "warnings": warnings}


def _ps(value) -> str:
    return ui._ps_string(value)


def create_branch_line(mpr_path: str,
                       source_branch: str,
                       new_branch_name: str,
                       revision: int = 0,
                       git_branch: str | None = None,
                       download: bool = False,
                       download_to: str | None = None,
                       dry_run: bool = False,
                       timeout: int = ui.DEFAULT_UI_TIMEOUT) -> dict:
    """Create a branch line off `source_branch`, through the IDE holding `mpr_path`.

    `revision` indexes the `Select Revision` grid, newest first, so `0` is the
    head of `source_branch` - the same thing the dialog prefills. A larger index
    walks back through history, and `Show more` is clicked as needed to reach it.

    `dry_run` fills the dialog in completely and stops before `OK`, leaving it on
    screen. That is a real dry run rather than a flag that skips work: every
    precondition this function can check has been checked by that point, the
    chain is cancellable from there, and the only thing not exercised is the one
    irreversible click.

    Stops rather than guesses when:

    - the IDE cannot be identified from the working copy, or two could match
    - the menus are not in English, since there is no `AutomationId` to fall
      back on
    - `source_branch` is not in the manager's list
    - `Base branch line` does not come up on `source_branch` after `New`
    - `new_branch_name` is already in `Existing branch lines`
    - `revision` is past the end of history even after `Show more`
    - any dialog this function did not open is on screen

    A stop leaves the IDE exactly as it was found in every case except the last
    two, where the dialog chain is left open for a person to look at - closing it
    would throw away the thing they need to see.
    """
    # Everything that can be rejected without touching the IDE is rejected
    # first. Putting a modal on someone's screen and then telling them the name
    # was invalid is the wrong order.
    if not new_branch_name or not new_branch_name.strip():
        return {"status": "refused", "reason": "empty_name",
                "detail": "A branch name is required."}
    if revision < 0:
        return {"status": "refused", "reason": "bad_revision",
                "detail": "revision is an index into history; 0 is the head."}

    path_check = None
    if download_to is not None:
        path_check = check_download_path(download_to)
        if path_check["status"] == "refuse":
            return {"status": "refused", "reason": "bad_download_path",
                    "path_check": path_check,
                    "detail": "Nothing was driven. " + " ".join(path_check["problems"])}
    if download and download_to is None:
        return {"status": "refused", "reason": "no_download_path",
                "detail": ("download=True needs download_to: Studio Pro asks "
                           "where to put the working copy and this function "
                           "does not pick a folder on anyone's behalf.")}

    instance = ui.resolve_instance(mpr_path, git_branch=git_branch)
    if instance["status"] != "found":
        return {"status": "refused", "reason": "instance_" + instance["status"],
                "instance": instance, "detail": instance.get("detail", "")}
    pid = instance["pid"]

    language = ui.check_language(pid)
    if language["status"] != "ok":
        return {"status": "refused", "reason": "ui_language",
                "language": language, "detail": language.get("detail", "")}

    records = ui._run(_sequence(pid, source_branch, new_branch_name, revision,
                                dry_run), timeout=timeout)
    return _interpret(records, instance, dry_run, download, download_to,
                      path_check)


def _sequence(pid: int, source_branch: str, new_branch_name: str,
              revision: int, dry_run: bool) -> str:
    """The PowerShell body that drives the chain.

    One script rather than one per step: a window handle is valid only while its
    window lives, so splitting the chain across processes would make a
    half-driven dialog the normal case rather than the exception.
    """
    return f"""
$FURNITURE = @({','.join(_ps(t) for t in ui.FURNITURE_TITLES)})
$PROC = {pid}
$SOURCE = {_ps(source_branch)}
$NEWNAME = {_ps(new_branch_name)}
$REVISION = {int(revision)}
$DRYRUN = ${'true' if dry_run else 'false'}

# A label sits on the same row as the control it names - measured 6 px below its
# top - so pairing by row is how an unnamed Edit is identified. There is no
# AutomationId and no LabeledBy to use instead, and indexing the Edits by
# document order would silently pick a different box if the layout ever changed.
function Edit-ForLabel($Dialog, $Label) {{
  $lbl = $null
  foreach ($t in (ByType $Dialog $CT::Text)) {{ if ($t.Current.Name -eq $Label) {{ $lbl = $t; break }} }}
  if ($null -eq $lbl) {{ return $null }}
  $ly = $lbl.Current.BoundingRectangle.Y
  foreach ($e in (ByType $Dialog $CT::Edit)) {{
    if ([Math]::Abs($e.Current.BoundingRectangle.Y - $ly) -le 20) {{ return $e }}
  }}
  return $null
}}

$main = Get-MainWindow $PROC
if ($null -eq $main) {{ Stop-Here 'no_main_window' 'The IDE has no main window.' @() }}
$known = @(Get-Windows $PROC | ForEach-Object {{ $_.hwnd }})

# Anything already on screen is someone else's question. Driving a menu behind an
# open modal is how an operation ends up applied to a state nobody looked at.
$pre = Other-Dialogs $PROC $known
if ($pre.Count -gt 0) {{ Stop-Here 'dialog_already_open' 'A dialog was already on screen; nothing was clicked.' $pre }}

# --- Version Control > Manage Branch Lines ---------------------------------
$m = El $main.hwnd
$vc = Named $m $CT::MenuItem 'Version Control'
if ($null -eq $vc) {{ Stop-Here 'menu_not_found' 'No Version Control menu.' @() }}
[void](Expand-El $vc)
$mbl = Named $vc $CT::MenuItem 'Manage Branch Lines' -Prefix
if ($null -eq $mbl) {{ Stop-Here 'menu_not_found' 'No Manage Branch Lines item.' @() }}
Invoke-El $mbl
Step 'opened_menu' 'Version Control > Manage Branch Lines'

$mgrWin = Wait-ForDialog $PROC $known {int(ui.DEFAULT_UI_TIMEOUT * 1000)}
if ($null -eq $mgrWin) {{ Stop-Here 'manager_did_not_open' 'Branch Line Manager never appeared.' (Other-Dialogs $PROC $known) }}
$known += $mgrWin.hwnd
$mgr = El $mgrWin.hwnd
Step 'manager_open' $mgrWin.title

# --- select the source branch ----------------------------------------------
$grid = (ByType $mgr $CT::DataGrid) | Select-Object -First 1
if ($null -eq $grid) {{ Stop-Here 'no_branch_grid' 'The manager has no grid of branch lines.' (Other-Dialogs $PROC @($main.hwnd)) }}
$names = @(); $row = $null
foreach ($r in (ByType $grid $CT::DataItem)) {{
  $cell = (ByType $r $CT::Text) | Select-Object -First 1
  if ($null -eq $cell) {{ continue }}
  $names += $cell.Current.Name
  if ($cell.Current.Name -eq $SOURCE) {{ $row = $r }}
}}
Emit ([pscustomobject]@{{ kind='branches'; names=$names }})
if ($null -eq $row) {{
  Stop-Here 'source_branch_not_found' "No branch line called '$SOURCE'. The manager lists $($names.Count)." (Other-Dialogs $PROC @($main.hwnd))
}}
Select-El $row
Step 'source_selected' $SOURCE

# --- New --------------------------------------------------------------------
$newBtn = Named $mgr $CT::Button 'New'
if ($null -eq $newBtn) {{ Stop-Here 'no_new_button' 'The manager has no New button.' (Other-Dialogs $PROC @($main.hwnd)) }}
if (-not $newBtn.Current.IsEnabled) {{ Stop-Here 'new_disabled' 'The New button is disabled.' (Other-Dialogs $PROC @($main.hwnd)) }}
Invoke-El $newBtn
$cblWin = Wait-ForDialog $PROC $known {int(ui.DEFAULT_UI_TIMEOUT * 1000)}
if ($null -eq $cblWin) {{ Stop-Here 'create_did_not_open' 'Create Branch Line never appeared.' (Other-Dialogs $PROC $known) }}
$known += $cblWin.hwnd
$cbl = El $cblWin.hwnd
Step 'create_open' $cblWin.title

# --- verify the base, do not assume it ---------------------------------------
# Selecting the row prefills this, measured. Verified anyway: a wrong base is
# the one error here that yields a branch that looks right and carries the wrong
# history, and it would not surface until someone merged it.
$combo = (ByType $cbl $CT::ComboBox) | Select-Object -First 1
$sel = $combo.GetCurrentPattern([System.Windows.Automation.SelectionPattern]::Pattern).Current.GetSelection()
$base = if ($sel.Count) {{ $sel[0].Current.Name }} else {{ '' }}
Emit ([pscustomobject]@{{ kind='base'; value=$base }})
if ($base -ne $SOURCE) {{
  Stop-Here 'base_mismatch' "Base branch line came up as '$base', not '$SOURCE'. Nothing was confirmed; the dialog is open." (Other-Dialogs $PROC @($main.hwnd, $mgrWin.hwnd))
}}

# --- name collision, against the IDE's own list ------------------------------
$existingBox = Edit-ForLabel $cbl 'Existing branch lines'
$existing = @()
if ($null -ne $existingBox) {{ $existing = @((Get-Value $existingBox) -split "`r?`n" | Where-Object {{ $_ }}) }}
Emit ([pscustomobject]@{{ kind='existing'; names=$existing }})
if ($existing -contains $NEWNAME) {{
  Stop-Here 'name_exists' "A branch line called '$NEWNAME' already exists. Nothing was confirmed; the dialog is open." (Other-Dialogs $PROC @($main.hwnd, $mgrWin.hwnd))
}}

# --- revision ----------------------------------------------------------------
$revBox = Edit-ForLabel $cbl 'Revision'
if ($null -eq $revBox) {{ Stop-Here 'no_revision_box' 'No Revision box on Create Branch Line.' (Other-Dialogs $PROC @($main.hwnd, $mgrWin.hwnd)) }}
$prefilled = Get-Value $revBox
Emit ([pscustomobject]@{{ kind='revision_prefilled'; value=$prefilled }})

$selBtn = Named $cbl $CT::Button 'Select' -Prefix
if ($null -eq $selBtn) {{ Stop-Here 'no_select_button' 'No Select button next to Revision.' (Other-Dialogs $PROC @($main.hwnd, $mgrWin.hwnd)) }}
Invoke-El $selBtn
$pickWin = Wait-ForDialog $PROC $known {int(ui.DEFAULT_UI_TIMEOUT * 1000)}
if ($null -eq $pickWin) {{ Stop-Here 'picker_did_not_open' 'Select Revision never appeared.' (Other-Dialogs $PROC $known) }}
$known += $pickWin.hwnd
$pick = El $pickWin.hwnd
Step 'picker_open' $pickWin.title

$pgrid = (ByType $pick $CT::DataGrid) | Select-Object -First 1
if ($null -eq $pgrid) {{ Stop-Here 'no_revision_grid' 'Select Revision has no grid.' (Other-Dialogs $PROC @($main.hwnd, $mgrWin.hwnd, $cblWin.hwnd)) }}

# History is paged. Rather than guess the page size, ask for more until the
# index is reachable or the count stops growing - the latter is the end of
# history, and it is the only honest way to say "that revision does not exist".
$rows = @(ByType $pgrid $CT::DataItem)
$guard = 0
while ($rows.Count -le $REVISION -and $guard -lt 50) {{
  $more = Named $pick $CT::Button 'Show more' -Prefix
  if ($null -eq $more -or -not $more.Current.IsEnabled) {{ break }}
  $before = $rows.Count
  Invoke-El $more
  Start-Sleep -Milliseconds 900
  $rows = @(ByType $pgrid $CT::DataItem)
  if ($rows.Count -le $before) {{ break }}
  $guard++
}}
Emit ([pscustomobject]@{{ kind='revisions'; loaded=$rows.Count; pages=$guard }})
if ($rows.Count -le $REVISION) {{
  Stop-Here 'revision_out_of_range' "Asked for index $REVISION but history stops at $($rows.Count - 1) and Show more gave no more. Nothing was confirmed; the dialogs are open." (Other-Dialogs $PROC @($main.hwnd, $mgrWin.hwnd, $cblWin.hwnd))
}}

$target = $rows[$REVISION]
$cells = @((ByType $target $CT::Text) | ForEach-Object {{ $_.Current.Name }})
Select-El $target
Emit ([pscustomobject]@{{ kind='revision_chosen'; index=$REVISION; cells=$cells }})

$pickOk = Named $pick $CT::Button 'OK'
if ($null -eq $pickOk) {{ Stop-Here 'no_picker_ok' 'Select Revision has no OK.' (Other-Dialogs $PROC @($main.hwnd, $mgrWin.hwnd, $cblWin.hwnd)) }}
Invoke-El $pickOk
Start-Sleep -Milliseconds 600
$chosen = Get-Value $revBox
Emit ([pscustomobject]@{{ kind='revision_applied'; value=$chosen }})

# --- the name -----------------------------------------------------------------
$nameBox = Edit-ForLabel $cbl 'Branch name'
if ($null -eq $nameBox) {{ Stop-Here 'no_name_box' 'No Branch name box.' (Other-Dialogs $PROC @($main.hwnd, $mgrWin.hwnd)) }}
Set-Value $nameBox $NEWNAME
Start-Sleep -Milliseconds 300
$wrote = Get-Value $nameBox
Emit ([pscustomobject]@{{ kind='name_written'; value=$wrote }})
if ($wrote -ne $NEWNAME) {{
  Stop-Here 'name_not_accepted' "The box holds '$wrote' after writing '$NEWNAME' - the dialog may be filtering it. Nothing was confirmed." (Other-Dialogs $PROC @($main.hwnd, $mgrWin.hwnd))
}}

# --- the one irreversible click -------------------------------------------------
$okBtn = Named $cbl $CT::Button 'OK'
if ($null -eq $okBtn) {{ Stop-Here 'no_ok_button' 'Create Branch Line has no OK.' (Other-Dialogs $PROC @($main.hwnd, $mgrWin.hwnd)) }}
if (-not $okBtn.Current.IsEnabled) {{
  Stop-Here 'ok_disabled' 'OK is disabled, so the dialog is rejecting what was filled in. Nothing was confirmed.' (Other-Dialogs $PROC @($main.hwnd, $mgrWin.hwnd))
}}
if ($DRYRUN) {{
  Emit ([pscustomobject]@{{ kind='dry_run'; detail='Everything is filled in and OK is enabled. Not clicked. Cancel the dialogs to back out.' }})
  exit 0
}}

Invoke-El $okBtn
Step 'confirmed' 'OK clicked on Create Branch Line'
Start-Sleep -Milliseconds 1500

# Whatever is on screen now is reported, not answered. The post-create flow is
# the one link in this chain that has not been measured, for the obvious reason
# that measuring it means creating a branch.
$after = Other-Dialogs $PROC @($main.hwnd, $mgrWin.hwnd)
Emit ([pscustomobject]@{{ kind='after_confirm'; dialogs=$after }})
"""


def _interpret(records: list[dict], instance: dict, dry_run: bool,
               download: bool, download_to: str | None,
               path_check: dict | None) -> dict:
    """Turn the step records into one answer.

    The records are kept in the result whatever happens. A chain this long is
    not debuggable from a status alone, and where it stopped is the thing a
    caller needs when a dialog is left open on someone's screen.
    """
    steps = [r for r in records if r.get("kind") == "step"]
    stop = next((r for r in records if r.get("kind") == "stop"), None)
    by_kind = {r["kind"]: r for r in records if "kind" in r}

    result = {
        "pid": instance["pid"],
        "mpr_path": instance.get("mpr"),
        "resolved_via": instance.get("via"),
        "steps": [s["step"] for s in steps],
        "records": records,
    }
    if path_check:
        result["path_check"] = path_check

    if stop:
        return {**result,
                "status": "needs_user_decision",
                "reason": stop.get("reason"),
                "dialogs": stop.get("dialogs") or [],
                "question": stop.get("detail", "")}

    if "dry_run" in by_kind:
        return {**result,
                "status": "dry_run",
                "base": by_kind.get("base", {}).get("value"),
                "revision": by_kind.get("revision_applied", {}).get("value"),
                "detail": by_kind["dry_run"]["detail"]}

    if "after_confirm" not in by_kind:
        return {**result,
                "status": "unknown",
                "detail": ("The sequence ended without reporting what happened "
                           "after OK. Look at the IDE before running this "
                           "again; it may have a dialog open.")}

    dialogs = by_kind["after_confirm"].get("dialogs") or []
    created = {**result,
               "status": "created",
               "base": by_kind.get("base", {}).get("value"),
               "revision": by_kind.get("revision_applied", {}).get("value"),
               "revision_cells": by_kind.get("revision_chosen", {}).get("cells")}

    if not dialogs:
        if download:
            return {**created,
                    "status": "created_download_not_attempted",
                    "download_to": download_to,
                    "detail": (
                        "The branch line was created and Studio Pro offered "
                        "nothing to answer, so there was no download prompt to "
                        "drive. Download it with Version Control > Download "
                        "from Version Control Server, into the folder that was "
                        "validated here.")}
        return created

    # Something is waiting. This is the expected shape when `download=True`, and
    # it is reported rather than clicked because the post-create flow was never
    # measured - see the note in `_sequence`. Answering an unmeasured modal is
    # exactly the thing `studio_pro.py` refuses to do, and the reason holds here.
    return {**created,
            "status": "created_needs_user_decision",
            "download_to": download_to,
            "dialogs": dialogs,
            "question": (
                "The branch line was created and Studio Pro is now waiting on a "
                "dialog that nothing here clicked. It says:\n\n"
                + "\n\n".join(d.get("text", "") for d in dialogs if d.get("text"))
                + "\n\nButtons: "
                + ", ".join(b for d in dialogs for b in d.get("buttons", []))
                + ("\n\nIf it is offering to download the branch, the folder "
                   f"checked for this run was: {download_to}" if download_to
                   else "")),
            }
