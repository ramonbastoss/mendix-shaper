# Backlog

What is not built yet, and what is deliberately not built.

## The open question: what the editing surface should be

The write primitive works. What is undecided is what sits on top of it, and the
decision matters more than the code does, because the shape of the tools is the
shape of the risk.

This is a question about `tools/`, not about `engine/`. The two are separate
folders so that the engine can be built and proven against what Studio Pro
produces while the surface question stays open — otherwise an undecided tool
name holds up the code that does the actual work.

- [ ] **Decide between a generic writer and narrow tools.** One
      `tool_set_value(unit, path, value)` would cover everything on day one and
      be impossible to review: the argument that distinguishes a safe edit from
      a model-breaking one is a dotted string nobody can read at the permission
      prompt. Narrow tools (`tool_rename_...`, `tool_set_...`) each carry their
      own validation and each say what they do in their own name, at the cost
      of one tool per kind of edit. The second reads better and is more work.
      Nothing should be registered before this is settled.
- [ ] **Start with value edits, not structure.** Changing a string, a flag or a
      number already present in a document is verifiable and reversible.
      Creating structure means minting GUIDs and wiring references, and a
      reference pointing at nothing takes down the loader with an error that
      names the missing id and not the unit holding it. The first registered
      tool should come from the first group.
- [ ] **Author edits from a measured diff, not from the metamodel.** Making the
      change once by hand in Studio Pro and decoding both sides turns "guess
      what the document should look like" into "replay a shape that is known to
      work". The mapper's `git_diff_commits` already produces exactly that
      diff. This is the cheapest way to get structural edits that are not
      guesses.
- [ ] **Resolve ids against the model, never generate them.** A reference
      should be looked up - the mapper's dump already has every unit and its
      id - and an edit whose target does not resolve should be refused before
      anything is written, not after. *The lookup now exists:
      `engine/model.Model.names` indexes every qualified name in the app and
      `resolve()` answers for one. What is missing is any tool that calls it.*

## Safety work that is missing

- [ ] **`assert_writable` is best-effort and says so in a docstring nobody
      reads at call time.** It takes SQLite's write lock, which proves another
      process is not writing; it does not prove Studio Pro has the model open
      in memory, which is the case that actually loses work. Either find a real
      detection or make the refusal louder.
- [ ] **A misspelled argument is dropped, not refused.** FastMCP builds each
      tool's schema from the function signature and leaves
      `additionalProperties` unset, so JSON Schema allows extras and anything
      the caller passes under a name the tool does not declare is silently
      discarded. On a reader that is harmless. On a write tool it means the
      call runs with a default in place of the argument that was meant to
      govern it - which is the kind of failure that looks like success in the
      report. Settle this before the first editing tool, not after: either
      tighten the schema or have each write tool reject unknown keys itself.
- [x] **No dry run.** ~~`write_unit` either writes or raises.~~ Settled the way
      the split pointed at, rather than with a flag: an operation is a function
      from one decoded document to another, `storage` is the only thing that
      touches disk, so the dry run is simply not calling it.
      `engine/rename.plan()` yields `(unit_id, new_document, changes)` and
      writes nothing; applying it is passing each entry to `write_unit`. Any
      operation added later has to be written the same way - this stops being
      true the moment one of them does its own I/O.
- [ ] **Rollback is documented, not offered.** Reverting is `git checkout` on
      `mprcontents/` and the `.mpr`, and this server does not run it, because
      destroying uncommitted work is not something a tool should do on its own
      initiative. Revisit only if real use makes the manual step the thing that
      goes wrong.
- [ ] **Nothing refuses to write to a dirty working copy.** Editing on top of
      uncommitted changes means `git checkout` can no longer separate this
      server's write from the user's own work, which quietly removes the
      rollback story. A check against `git status` is cheap.

## Driving the IDE's own version-control operations

**The intent, stated once so the rest of this section reads straight:** certain
operations have to be performed *by Studio Pro* rather than next to it, and the
server should be able to perform them. Not because the IDE is nicer, but because
a commit made with plain git does not carry the `refs/notes/mx_metadata` note —
so it shows no change list in history and the build service will not package
that revision. `docs/driving-studio-pro.md` has the measurement. The Extensions
API cannot help: its `IVersionControlService` is three read-only members, with no
`Commit`, `Pull` or `Push` to call. UI Automation is the only route, and
`engine/studio_pro_ui.py` is it.

The line that makes this acceptable, and the whole reason it does not contradict
the section below: **this initiates operations, it does not answer questions.**
Picking a menu item is recoverable — the operation raises its own dialog and
that dialog cancels. Clicking `Yes` on *"Do you want to save the changes to ..."*
is not.

Built so far: `engine/studio_pro_ui.py` (primitives, `resolve_instance`,
`check_language`) and `engine/branch_lines.py`
(`create_branch_line`, `check_download_path`). No tool is registered for either.

- [ ] **The confirming click has never been exercised.** `create_branch_line`
      runs end to end up to `OK` and `dry_run=True` stops there, verified. Every
      line after `Invoke-El $okBtn` is unrun code, because running it means
      creating a branch line on a real server. Create one deliberately, capture
      what Studio Pro puts on screen afterwards, and only then trust that path.
- [ ] **The post-create download flow is unmeasured, and the function says so.**
      `download=True` currently validates the destination, creates the branch,
      and then *reports* whatever dialog appears instead of clicking it — the
      same refusal shape as a blocked close. Finish it from a captured dialog,
      not from a guess. Note that `Download from Version Control Server` is a
      two-screen wizard whose first screen came up with an **empty** app list
      until refreshed, so the prompt after a create is probably a different
      dialog and has to be captured on its own.
- [ ] **No `tests/test_branch_lines.py`.** `check_download_path` and
      `_ps_string` are pure and cover the interesting cases — path budget,
      non-ASCII, reserved characters, quote escaping — and need no IDE.
      `_sequence` can at least be parse-checked: feeding the generated script to
      `[System.Management.Automation.Language.Parser]::ParseFile` catches a
      syntax error that would otherwise only surface mid-dialog. Both were run by
      hand on 2026-11-24 and passed; neither is committed as a test.
- [ ] **Every selector is an English control name and nothing can be done about
      it.** No control in these dialogs carries an `AutomationId`.
      `check_language` turns the resulting "element not found" into a diagnosis,
      which is mitigation, not a fix. If Studio Pro ever exposes ids, replace the
      name matching; until then this is a known ceiling, not a bug to chase.
- [ ] **Decide whether any of this becomes a tool.** It is engine-only today,
      deliberately — the same freeze that applies to editing applies here, and
      the surface question is harder for an operation that reaches a server than
      for one that writes a file. A tool that creates a branch line is a tool
      that writes to the Team Server on a caller's initiative.
- [ ] **`commit` and `pull` are the operations that motivated this, and neither
      is built.** The chain is measured (`Version Control > Commit` and
      `> Pull`, both `Invoke`-able, with `Push (0 commit(s)).` reporting its own
      count in its name) but a commit dialog has a message box and a file list,
      and a pull can raise a merge conflict. The conflict case is the one place
      in this whole area that is a modelling decision rather than an automation
      step, and it has to stop and ask.

## Deliberately not built: answering Studio Pro's dialogs

`tool_close_app` and `tool_open_app` stop at a modal and report its text. UI
Automation could click the button instead — `read_dialog` already walks the
dialog's tree, and invoking a button from there is a couple of lines. It is
left out, and the reasoning should be read before anyone adds it.

(This is the other side of the section above. Driving a menu was added; clicking
a question was not, and the two were separated on purpose rather than by
accident of what got written first.)

Both modals have an answer that cannot be undone. **Discard** throws away a
model that exists only in Studio Pro's memory, with nothing on disk to restore
from. **Open anyway** puts two IDEs on one model, which Mendix states is not
supported. Neither is a case where being wrong costs a retry.

A capability that exists is a capability something will reach for, and the
caller here is a language model under instruction to finish a task. The
argument that it would only click when told applies equally well to every tool
that has ever been called with the wrong argument. Not offering the option is
a stronger guarantee than documenting when not to use it.

- [ ] **Decide whether an explicit, user-confirmed answer is worth adding.** If
      it ever is, the shape that keeps the guarantee is one that cannot be
      invented by the caller: the exact text of the dialog and the exact button
      label, both echoed back from a previous blocked reply, refused if the
      dialog on screen has changed since. Until then the user clicks.
- [ ] **A dialog during a load is reported only at the timeout.** The loader's
      own progress window is owned and carries a Cancel button, so an owned
      window is not evidence of a question (see
      `docs/controlling-studio-pro.md`), and nothing cheaper than the timeout
      currently separates the two. A real question asked while opening is
      therefore surfaced late rather than immediately.

## Smaller things

- [ ] **`tool_validate_model` cannot be interrupted.** `mx check` runs for
      minutes and blocks the call. A long run that the user wants to abandon -
      because it is competing with their open Studio Pro - has no way out short
      of killing the server.
- [ ] **The baseline is keyed by project, not by branch.** One working copy per
      branch is the convention the config assumes, so this mostly works out,
      but a project entry that gets checked out to a different branch silently
      compares against the wrong baseline.
- [ ] **No `GETTING_STARTED.md`.** The README carries setup today. Split it out
      when the tool list is long enough that setup stops being the second thing
      a reader needs.
