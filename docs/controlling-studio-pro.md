# Opening and closing Studio Pro

Measured on Studio Pro 10.24.13 (build 86719), Windows 11, MPRv2, a Git-backed
app of about 6,000 units.

Closing the IDE is the precondition for writing into a working copy it has
open. This note is what that step costs, how to tell which model an instance
holds, and the two ways it refuses to happen.

## There is no supported way to ask

Neither of the two interfaces Mendix ships has an app lifecycle.

- **`mx.exe`** has 28 commands — `check`, `convert`, `dump-mpr`, `merge` and
  so on. They all operate on a `.mpr` from outside. None of them talks to a
  running IDE.
- **The Studio Pro Extensions API** (`Mendix.StudioPro.ExtensionsAPI.dll`,
  shipped in the install) exposes `Model`, `UI`, `VersionControl`, `Runtime`
  and `BackgroundJobs`. Searching its XML documentation for lifecycle members
  returns `ClosePane`, `CloseTab` and `CloseDialog` — panes, tabs and dialogs.
  There is no `OpenApp` and no `CloseApp`. An extension cannot close its own
  app either.

So opening is launching the executable, and closing is `WM_CLOSE` — the
message the window's X button sends.

## Opening

```
studiopro.exe "C:\path\to\MyApp\MyApp.mpr"
```

**Measured 42 s, 45 s, 57 s and 64 s** across four runs of the same app. The
first run of the day was the slowest and a reopen minutes later the fastest,
which is consistent with the OS file cache; a second IDE already running on
another app pushed it back up.

**The window appears long before the model is in it.** Until the app is
loaded, the title is the bare product name, `Mendix Studio Pro`. The title
changing is the only cheap signal that the load finished — and even that is the
window being usable, not Studio Pro having finished indexing behind it.

### Take the IDE from the configured `mx.exe`, not from the file association

`.mpr` files are associated with the Mendix Version Selector, which reads the
version an app was last edited with and launches whichever install of that
version it knows about. That is one install too few to be safe: a machine can
carry **two installs of the same version number** — a stock one under
`C:\Program Files\Mendix\<version>\` and a patched or vendor-specific one
elsewhere — and the selector has no way to know which one the app is meant to
be opened with.

`studiopro.exe` sits next to `mx.exe` in every install. Deriving it from the
`mx_path` a project is already configured with means the IDE that opens the
model is the same build that runs its consistency checks.

## Which model an instance holds

**Not the window title.** The title reads `<AppName> (<branch>, Git)`, so it
carries the app's name and the *branch* — and neither has to resemble the
folder on disk. Measured on a checkout whose folder, branch and app name were
three different strings: nothing in the title identified the working copy.

The command line does:

```powershell
(Get-CimInstance Win32_Process -Filter "ProcessId=$pid").CommandLine
```

Compare the `.mpr` paths case-insensitively — Windows paths are, and the
casing in a command line comes from whoever typed it.

Two more observations about instances:

- **An idle instance is replaced; a loaded one is not.** A Studio Pro sitting
  on the start page with no app disappears when an app opens. Two instances
  each holding a different app coexist happily, at roughly 2 GB each.
- **An open working copy carries a `<app>.mpr.lock`** next to the `.mpr`
  (hidden; `-Force` to see it). Cheap to check, but whether it survives a crash
  was not measured, so the command line remains the reliable signal.

## Closing

`WM_CLOSE` to the main window. **Measured 2.1 s, 2.8 s and 3.0 s** with nothing
unsaved — closing is cheap, and essentially all of the open/close cycle's
~45-65 s is the opening half.

### The model is in memory, and only there

With edits pending in the IDE, the working copy on disk was **completely
untouched**: `git status` clean, every `.mxunit` and the `.mpr` still carrying
the mtime of the checkout. Studio Pro had written nothing.

That is the whole argument against terminating the process. A kill closes the
IDE and takes the unsaved model with it, and there is nothing on disk to
recover from, because nothing was ever written.

## The two refusals

Both appear as a modal, and a modal is invisible to anything checking the
process: the main window keeps its own title and no process-level property
mentions the dialog. Only enumerating the process's windows shows it.

### A model that is already open

Launching a second instance on an open model **does not fail**. Studio Pro
starts, does not load the app, and puts up a `Warning`:

> It seems that the app located at `...` is already open in another instance of
> Mendix Studio Pro. Opening an app more than once is not supported.
> Do you want to open the app anyway? — **Open anyway** / **Cancel**

There is no exit code and no failed launch. Something polling for the title
waits forever. Note that it is a *warning*, not a block: **Open anyway** exists,
so two instances on one model is a reachable state.

The useful consequence: check before launching, not after.

### Work that is not saved

Closing an edited app puts up a `Question`:

> Do you want to save the changes to Domain Model [MyEntity]?
> ☐ Do not ask me again — **Save** / **Discard** / **Cancel**

**It asks once per edited document**, naming that document, so answering one
can be followed by another, and how many are queued is not visible from
outside.

The checkbox deserves attention. Ticked once, Studio Pro stops asking and
applies the remembered answer — silently, including discarding.

## Telling a question from the furniture

A modal dialog is an **owned** window: `GetWindow(hwnd, GW_OWNER)` returns its
owner, where a main window returns null. That is what separates dialogs from
app windows, and it holds regardless of titles, which change with the dialog,
the app and the IDE's language.

**Owned does not mean blocking, though.** Measured on the way up and while
idle, all owned:

| window | what it is |
|---|---|
| splash, carrying the version string | startup, transient |
| `Open App` — "Check app version", "Load app", "Initializing app..." | the loader's own progress, **and it has a Cancel button** |
| `Notification Stack` | Studio Pro's toast |

So neither "a dialog is up" nor "it has buttons" distinguishes a question from
the IDE doing its job. During a load, an owned window means nothing; what a
real question does is keep the title from ever arriving.

## Reading what a dialog says

Studio Pro is WPF, so a dialog is **one window handle with no child handles** —
Win32 gives the title and nothing more. UI Automation reads the inside of it:

```powershell
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$el = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$hwnd)
$el.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition)
```

Each label appears twice, once as its control and once as the `Text` inside it,
so build the button list from `ControlType.Button` rather than from everything
carrying a name.

UIA can also invoke a button. `engine/studio_pro.py` deliberately never does:
the two questions above have answers that cannot be undone — one discards work
that exists nowhere else, the other opens a model Mendix says must not be
opened twice — so the module reports the text and stops. A person answers it in
the IDE.
