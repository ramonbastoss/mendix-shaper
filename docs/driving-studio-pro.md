# Driving Studio Pro's menus and dialogs

Measured on Studio Pro 10.24.13, MPRv2, Git-backed app, Windows 11, 2026-11-24.

`controlling-studio-pro.md` is about the process — opening it, closing it,
telling which working copy it holds. This note is about operating the IDE once
it is open: picking a menu item, filling a dialog in, reading what came back.

## Why bother, when git is right there

A commit made with plain `git commit` is a real commit and Studio Pro will list
it in history. What it will **not** carry is the note Studio Pro attaches under
its own ref:

```
refs/notes/mx_metadata
{"BranchName":"MyBranch","ModelerVersion":"10.24.13.xxxxx",
 "ModelChanges":[{"Status":"Modified","UnitID":"a1b2c3d4-...",
                  "UnitType":"Forms$Page","UnitName":"MyPage",
                  "Module":"MyModule"}],
 "RelatedStories":[],"MPRFormatVersion":"Version2","HasModelerVersion":true}
```

`Status` / `UnitName` / `Module` / `UnitType` are the columns the IDE shows for
a commit, and `ModelChanges.length` is the count next to it. The IDE does not
compute that list by diffing — MPRv2 has no supported external diff — it reads
it from the note. Measured against a repository on 2026-11-24: a commit touching
one unit carried a 318-character note, one touching many carried 3,210, and
every commit made through the IDE over the last 300 revisions had one.

So a commit from outside the IDE arrives with nothing to list. The same note is
what the build service reads to assemble a deployment package, so that revision
does not build either, until somebody commits through the IDE on top of it.

**That is the whole reason this note exists.** Making the IDE the agent of a
version-control operation is not a preference; it is the only way the operation
produces what the platform expects.

## There is no API to call instead

The Extensions API ships `IVersionControlService` with exactly three members:

```
GetCurrentBranch(IModel)
GetHeadCommit(IModel, IBranch)
IsProjectVersionControlled(IModel)
```

All reads. `ICommit` exists as a read-only shape (`ID`, `Author`, `Date`,
`Message`). There is no `Commit`, `Pull`, `Push` or `CreateBranch`, in-process
or out, and `mx.exe` has no equivalent. Grepped from the shipped XML
documentation on 2026-11-24. An extension can tell you where you are; it cannot
move you.

Which leaves UI Automation, and the rest of this note is what that costs.

## UI Automation works, and it is not the fragile part

`[AutomationElement]::FromHandle($hwnd)` plus `FindAll` reads the tree;
`InvokePattern`, `ExpandCollapsePattern`, `SelectionItemPattern` and
`ValuePattern` operate it. No screen capture, no coordinates, **no focus
stealing** — none of the sequences below brought a window to the front.

That last point is not a nicety. One of the dialogs measured here reported its
bounding rectangle at `x = -1787`: it was on a monitor to the left of the
primary one. Anything driving this by coordinates would have been clicking
empty desktop, and anything screenshotting it would have captured whatever else
was on that screen.

### What *is* fragile: there are no automation ids

Every button, grid, radio button, combo box and text box in the version-control
dialogs reports an **empty** `AutomationId`. The only ids present belong to the
window chrome — `CloseWindow`, `MaximizeWindow`, `PART_LineUpButton`,
`PageUp`/`PageDown`.

So every selector is a control **`Name`**, and a `Name` is in whatever language
the IDE is set to. Studio Pro has a `Language` menu. This is the single largest
risk in driving it, and the failure mode is bad: every lookup turns into
"element not found", which reads like a broken selector or a changed Studio Pro
version rather than a settings difference. `studio_pro_ui.check_language` exists
to name the real cause once, up front, instead of leaving it to be diagnosed
from a stack of empty finds.

### Menu names end in `.` where the UI shows an ellipsis

Measured: `Manage Branch Lines.`, `History.`, `Select.`,
`Download from Version Control Server.`. Match by prefix. Equality against the
text a person reads on screen never matches.

### An owned window is still not a question

`controlling-studio-pro.md` already records that the splash, the `Open App`
progress list and the `Notification Stack` toast are all owned windows, so
`GetWindow(hwnd, GW_OWNER)` does not separate a question from furniture. This
note adds that the same trap applies to **operations**, not just to opening:

> Invoking `Manage Branch Lines.` produced a window titled **`Progress`** after
> 459 ms. The actual dialog came later. A wait written as "return the first new
> owned window" returns the progress window, every single time.

Skip `Progress` and `Notification Stack` **by title** and keep waiting.

### `IsEnabled` carries real state

Worth reading rather than re-deriving:

- `Push (0 commit(s)).` comes back **disabled, with the count inside its own
  name**. Whether there is anything to push is readable without touching git.
- In the branch manager, `Delete` and `Switch branch` are **disabled while the
  current branch is the selected row** — you cannot delete or switch to where
  you already are. The disabled state is semantic, not "nothing is selected".

A precondition read off the IDE cannot disagree with the IDE. One
reimplemented against git can.

### Dialogs are fast

| step | measured |
|---|---|
| expand a top-level menu | ~500 ms |
| `Manage Branch Lines.` → `Progress` | 459 ms |
| `Progress` → `Branch Line Manager` | next poll |
| `New` → `Create Branch Line` | 460 ms |
| `Select.` → `Select Revision` | 653 ms |
| `Download from Version Control Server.` → dialog | 343 ms |

Nothing here needs the minute-long timeouts that opening a model does
(42–57 s). A timeout in this range that expires means a question is on screen,
not that the IDE is slow.

## The branch-line chain, as measured

```
Version Control              Expand()
  Manage Branch Lines.       Invoke()   -> Progress -> Branch Line Manager
    [DataGrid of branch lines]          21 rows: [name | last commit date]
    New                      Invoke()   -> Create Branch Line
      ( ) Main line  (o) Branch line  ( ) Tagged version
      Base branch line  [ComboBox]
      Revision          [Edit]  Select. -> Select Revision
                                            [DataGrid] 19 rows:
                                            [sha8 | author | date | message]
                                            + Show more, OK, Close
      Branch name       [Edit]
      Existing branch lines [Edit, read-only, newline-separated]
      OK / Cancel
```

Three things in that chain do work a caller would otherwise have to redo:

- **Selecting a row in the manager prefills the create dialog.** The
  `Branch line` radio comes up already selected and `Base branch line` comes up
  already set to the selected row. Verify it anyway — a wrong base is the one
  mistake here that yields a branch that looks correct and carries the wrong
  history, and nothing surfaces it until someone merges.
- **`Revision` comes prefilled with the head of the base branch**, and row 0 of
  `Select Revision` is that same commit. That is what lets "revision 0" mean
  head without a special case.
- **`Existing branch lines` is the IDE's own list of every branch name the
  server knows**, in a read-only multi-line box. A name collision is therefore
  checkable *before* `OK`.

### Pairing an unnamed box with its label

With no `AutomationId` and no `LabeledBy`, the three `Edit` boxes above are
indistinguishable by identity. They are distinguishable by **row**: measured,
a label sits 6 px below the top of the control it names, on the same line.

| label | label `y` | control `y` |
|---|---|---|
| `Revision` | 1087 | 1081 |
| `Branch name` | 1185 | 1179 |
| `Existing branch lines` | 1227 | 1221 |

Matching a label to the `Edit` whose `y` is within ~20 px is both robust and
self-documenting. Indexing the boxes in document order would silently pick a
different one if the layout ever changed.

### History is paged

`Select Revision` loaded 19 rows and offers `Show more`. The page size was not
measured and should not be assumed: ask for more until the wanted index is
reachable **or the row count stops growing**, and treat the latter as the end
of history. That is the only honest way to answer "that revision does not
exist".

## Where a working copy may be downloaded to

Two separate problems, and they fail differently.

**Length.** Windows resolves most paths against 260 characters. Measured on a
real checkout on 2026-11-24, the deepest file inside it sat **182 characters
below the working copy root** — inside the `node_modules` tree a JavaScript
dependency brings with it (`javascriptsource/<lib>/actions/node_modules/...`).
That is not the model and nothing in Studio Pro controls it; it arrives with the
dependency.

So the budget for the root is `260 - 182 = 78` characters, and the headroom is
thinner than it looks: a root like `C:\Users\<name>\Mendix\<App>-<Branch>`
already spends about 50 of it. Nothing refuses a long root — the checkout
succeeds, and then a build, an `npm install` or a git operation fails on **one
file**, with an error naming that file and not the root that caused it.

`branch_lines.check_download_path` warns past 60 and refuses past 78, and says
in the refusal that the eventual failure will name the wrong thing.

**Characters outside ASCII.** ⚠️ **Not measured — refused as a conservative
rule.** A working copy is a Java source tree and a node project as well as a
model, and those toolchains do not agree on the filesystem encoding. A path with
an accented character has not been tested here, so this is a guess, labelled as
one. What *has* been seen in this stack is the same class of problem one layer
over: a bullet character coming back as an inverted question mark after a round
trip through a mis-declared charset.

## What this does not do

It initiates operations. It does not **answer** questions.

Picking `Version Control > Commit` is initiating: nothing is lost if it is the
wrong menu, because the operation puts up its own dialog and that dialog
cancels. Clicking `Yes` on *"Do you want to save the changes to ..."* is
answering, and the wrong answer is unrecoverable. `BACKLOG.md` carries the full
reasoning for why the second is left to a person; this note only records that
drawing the line between the two is what made driving the UI acceptable at all.

A sequence that meets a dialog it did not open stops, reports its text and
buttons, and leaves it on screen — closing it would throw away the thing the
person needs to see.
