# Backlog

What is not built yet, and what is deliberately not built.

## The open question: what the editing surface should be

The write primitive works. What is undecided is what sits on top of it, and the
decision matters more than the code does, because the shape of the tools is the
shape of the risk.

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
      anything is written, not after.

## Safety work that is missing

- [ ] **`assert_writable` is best-effort and says so in a docstring nobody
      reads at call time.** It takes SQLite's write lock, which proves another
      process is not writing; it does not prove Studio Pro has the model open
      in memory, which is the case that actually loses work. Either find a real
      detection or make the refusal louder.
- [ ] **No dry run.** `write_unit` either writes or raises. A caller cannot ask
      "what would this change" and get the diff back without committing to it.
      Worth having before the first editing tool, not after.
- [ ] **Rollback is documented, not offered.** Reverting is `git checkout` on
      `mprcontents/` and the `.mpr`, and this server does not run it, because
      destroying uncommitted work is not something a tool should do on its own
      initiative. Revisit only if real use makes the manual step the thing that
      goes wrong.
- [ ] **Nothing refuses to write to a dirty working copy.** Editing on top of
      uncommitted changes means `git checkout` can no longer separate this
      server's write from the user's own work, which quietly removes the
      rollback story. A check against `git status` is cheap.

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
