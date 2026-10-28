# Editing alongside Studio Pro

Measured on Studio Pro 10.24.13, MPRv2, a Git-backed app.

The top-level README says to close Studio Pro before writing. That is still the
simple rule, and it is the right one when you are writing into the working copy
the IDE has open. It is not the only route, and the alternative turns out to be
cheap enough to use while iterating.

## Why writing underneath the IDE does not show up

Studio Pro owns the model while it is open. The `.mpr` and the blobs under
`mprcontents/` are the persisted state, not the live one. A write made
underneath the IDE does not appear on screen, and is overwritten the moment
somebody saves there.

Two things that look like they should refresh it, and do not:

- **`App > Synchronize App Directory` (F4)** scans the *external resource*
  directories — `widgets/`, `javasource/`, `userlib/`, `resources/`. Those live
  outside the model and the model only references them. `mprcontents/` is not
  in scope.
- **`git pull` in a terminal** only changes who wrote to the disk. The IDE is
  still looking at memory.

The rule underneath both: **a write under the IDE is safe when it moves the
disk toward the state memory already has, and dangerous when it moves it away.**
Same operation, opposite directions. A `git reset --hard` back to the commit
the IDE holds is a legitimate undo of an accidental write, as long as nothing
has been saved since.

## What does work: Update App

`App > Update App` — the Team Server update — brings an outside commit into a
running IDE. It is the same path a colleague's commit travels, and Studio Pro
is the thing that knows how to merge MPRv2.

**The IDE has to be the agent of the operation.** That is the whole trick.

**The commit has to come from outside the working copy the IDE has open.**
Committing in that same working copy leaves nothing to pull — its git is
already at the commit — and what you end up exercising is the old case, a write
under the IDE, not the update.

A detached worktree is the cheap way to get "outside":

```
git worktree add --detach C:\path\to\some\scratch\worktree HEAD
```

Measured at **about nine seconds** for a 7,000-file working copy. It shares the
`.git` directory, and touches neither the user's working copy nor their HEAD.
This was the part assumed to be expensive, and it is not.

The full cycle, then:

1. `git worktree add --detach <tmp> HEAD` from the IDE's working copy
2. write the unit there, through `engine/storage.py`
3. commit and push the branch from the worktree
4. `App > Update App` in Studio Pro

### Measured outcomes

- **The update came through as a fast-forward** — no merge commit, working copy
  clean, equal to the remote. So while nobody else is committing on the branch,
  the cycle does not pay for an MPRv2 merge at all.
- **The edit survived it intact**, including a change that spanned two parts of
  the same unit, and the IDE treated the written objects as its own.
- Everything but the click in the IDE is a matter of seconds, which makes this
  usable for iterating and not only for batches.

## Two things to keep in mind

**A click in the IDE is not on disk.** After using the domain model editor to
repair something, `git status` stayed clean: the change was in Studio Pro's
in-memory model and nothing had been written yet. If you want to capture what
the IDE did — to compare your own output against it — you have to save first,
and if the IDE closes without saving it is gone.

**The write guard only points one way.** `assert_writable` takes SQLite's write
lock before writing, so it refuses when something else is writing to the
`.mpr`. That protects this server from Studio Pro. It does not protect Studio
Pro from this server, and it does not detect an IDE that merely has the model
open in memory — which is the case that actually loses work. Worth knowing in
the other direction too: this server taking the write lock, or `mx check`
competing for it, is a plausible cause of a "database lock" failure in an open
IDE, and that failure is invisible until the IDE is reopened, because nothing
re-reads the disk to contradict the screen.
