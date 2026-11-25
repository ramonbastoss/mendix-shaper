# A stale `ContentsHash`

Measured on Studio Pro 10.24.13, MPRv2, a Git-backed app of ~7,300 units.
One occurrence, made by accident; what is and is not established is marked.

## What happened

An agent changed one expression in one microflow by hand, outside this
server's engine: `bson.decode`, edit the leaf, `bson.encode`,
`open(path, "wb")`. It had round-tripped the blob byte for byte first, so the
encoding was faithful. It did not touch the `.mpr`, so the unit's
`ContentsHash` still described the old blob.

Everything this repository's tooling looked at said the write was fine:

| Check | Result |
|---|---|
| `mx dump-mpr` (loader canary) | ✅ loads |
| `mx check` against the baseline | ✅ 0 errors, delta 0 |
| `git status` | `M mprcontents/…/<unit>.mxunit` — and the `.mpr` **not** modified |

Then the app was opened in Studio Pro:

- **The edit was not in the Changes pane.** Nothing was listed, although
  `git status` listed the blob. *Measured* — reported by the person looking at
  the IDE.
- **Closing the IDE raised an internal error** before any save prompt:
  `Cannot rollback transaction while UndoManager is not enabled or in
  just-do.`, followed by a `Decision` dialog. *Observed once. Not shown to be
  caused by the stale hash* — what the person did in the IDE before closing
  is not known.

The repair was to set that row's `ContentsHash` to
`base64(sha256(blob))`, with the IDE closed. `mx check` stayed at 0 errors and
the IDE reopened without a dialog; the `.mpr` now shows as modified in
`git status`, which is what a save from the IDE also produces.

## What this changes

The risk profile said a stale hash is *not noticed*, with a hypothesis that its
cost would be paid later, in a merge. The loader and `mx check` do not notice
it — that half stands. The IDE does, immediately, and silently: it appears to
decide what changed by comparing hashes in the `.mpr` rather than by reading
the blob, so an edit whose hash was never written is an edit the IDE does not
know about. *That mechanism is a hypothesis*; the symptom is measured.

The practical consequence is worse than a late merge conflict. The person
reviewing the change in the IDE sees nothing to review, and a commit made
from the IDE — the only kind the build service accepts, see
[driving-studio-pro.md](driving-studio-pro.md) — has no reason to include it.

## What guards it now

- `storage.stale_hashes(mpr_path)` compares every row's `ContentsHash` with
  its blob. About a second on 7,300 units.
- `tool_validate_model` runs it on every call and reports `stale_hashes`,
  with the units and a hint, whatever the two `mx` passes say.

Neither prevents the mistake — the server still exposes no write tool, so an
agent asked to edit a model will improvise one. They make it visible before
the app is handed back. The rule for anything that writes a blob is the one
`engine/storage.py` already states: go through `write_unit`, which writes the
hash in the same step.
