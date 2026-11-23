# Risk profile

Measured on Studio Pro 10.24.13, MPRv2.

Writing `.mxunit` files directly sounds reckless in a specific way — hand-edited
binary, no schema, no API — and that framing points the worry at the wrong
layer. The format layer is the part that has been measured at scale and has a
self-check. The risk lives one level up, in the rules Mendix enforces unevenly
and in the rules this server has not measured yet.

This note exists so the worry can be aimed. It is a summary of what the other
notes in this folder measured, organised by *what breaks* rather than by *what
you were doing*.

## The format layer is not where the risk is

An edit here is never a byte patch. It is `decode` → edit a tree of typed
objects → `encode`:

```python
d = bson.decode(blob)
# ... edit the dict ...
blob = bson.encode(d)
```

Two properties follow from that, and both matter more than they look:

- **Offsets are never computed by hand.** BSON length prefixes are **nested** —
  each document is an int32 that counts itself and its terminator — so changing
  the length of one string obliges a correction in every ancestor up to the
  root. `bson.encode` does all of them. The entire class of bug "I wrote at the
  wrong offset / I broke a length prefix" does not exist in this design. It
  would exist in an in-place byte editor, which is why this server is not one.
- **The round trip is exact, and it is verified at call time.** Decoding and
  re-encoding every unit of a ~6,000-unit app (199.5 MB) reproduced all of them
  **byte for byte**, with no failures. `assert_round_trips` repeats that check
  on the single unit being written — it re-encodes the untouched decode and
  refuses if the result differs from the bytes on disk — so a unit whose format
  this server does not reproduce faithfully is refused rather than rewritten.

"The encode will corrupt the file" is therefore a settled question, not an open
one. What is not settled is whether the *content* this server writes means what
it intended.

## What Mendix checks, and what it does not

There is no integrity checking on the blobs at all — no checksum is validated
on load. There is a lot of *consistency* checking on the model, and it is
strong where references are names and absent where references are ids. That
asymmetry is the whole picture:

| Mutation | What Mendix does |
|---|---|
| Value edited, hash updated | accepted — `mx check` 0 errors |
| Blob edited, `ContentsHash` left stale | 🔴 **not noticed** — the model loads normally |
| Reference to a qualified **name** that does not exist | ✅ `CE1613`, exit 1 |
| Access rules out of date (attribute added, `MemberAccess` not) | ✅ `CE0066`, exit 1, and the IDE repairs it |
| Reference by **GUID** pointing at nothing | 🔴 **loader crash** — the app does not open |
| Two objects in a unit sharing one `$ID` | 🔴 **`mx check` 0 errors**, then the IDE refuses to *save* that unit |
| Root `$ID` not the id the unit is stored as | 🔴 **`mx check` 0 errors**, the IDE *repairs it silently on save* — and that unit's **diff** throws from then on, see [unit-identity.md](unit-identity.md) |
| Association's storage moved, `GUID` kept | 🔴 **`mx check` 0 errors**, the build succeeds, then the *database sync* dies on a duplicate key |

Rows three and five are the same mistake — a dangling reference — with opposite
outcomes, decided only by how the reference is spelled.

The last row is a third shape, and the nastiest: nothing is dangling, so no
consistency check fires, and the model opens and checks clean. The integrity
check lives in `Savior.SaveUnit` and runs only when Studio Pro writes the unit
back, so the failure surfaces in the IDE, later, on a unit nobody is looking at.
A shallow copy of one object with a nested child is all it takes —
[duplicate-identities.md](duplicate-identities.md). `assert_unique_identities`
now refuses it at write time.

The row after it is a fourth shape, and it reaches further than the IDE. An
association's `GUID` is the **primary key of its row in the database
catalogue**, so moving the association to another entity while keeping that
`GUID` makes the model build cleanly and the *database synchronisation* fail on
a duplicate key — on Oracle, after the preceding DDL has already committed
itself. Nothing between the edit and the deploy has an opinion.
[moving-an-association-end.md](moving-an-association-end.md).
`assert_storage_identity` now refuses it at write time, and
`engine/associations.move_end` makes it unreachable by construction.

🔑 **Both of these were found the same way: by doing the damage.** The guard
list is a list of mistakes this project has made, which is also why neither was
predictable from the format alone — the oracle that catches them lives outside
`mx check`, in the IDE's save path and in the database's own catalogue.

**`ContentsHash` is not an integrity guard.** It is
`base64(sha256(blob))` in the `.mpr`'s single `Unit` table, and a blob edited
without updating it loaded with no complaint. It exists for conflict and merge
detection, not for detecting modification. So the hash update is this server's
obligation and nothing downstream will report that it was skipped. *Hypothesis,
not measured: the cost of a stale hash is paid later, in a merge that compares
against the wrong fingerprint. A latent failure with a delayed symptom is the
worst shape available, which is reason enough to treat the hash write as part
of the write and not as a step after it.*

**A dangling GUID takes the loader down, and says almost nothing:**

```
ERROR: System.Collections.Generic.KeyNotFoundException:
       The given key 'deadbeef-...' was not present in the dictionary.
   at Mendix.Modeler.Storage.Operations.UnitContentsLoader.ConvertFromStorage(...)
```

The message gives the missing id and **not the unit holding the reference**.
Across thousands of blobs that is a sweep, not a lookup. (Measured with one
pointer kind; not generalised to the others.)

## Risk scales with the kind of mutation

This is the useful axis — not "binary file", which is constant, but what the
edit does:

| | Exposure | Worst realistic outcome |
|---|---|---|
| **Edit a scalar already present** (string, flag, number) | low | the wrong value, visible in the IDE, reverted by `git checkout` |
| **Create an object** | medium | missed propagation — loud, see `CE0066` below |
| **Delete an object, or re-point a reference** | high | a dangling id, and the loader crash above |

The middle row is better than it was assumed to be. An attribute written
without its `MemberAccess` entries does **not** produce a quietly invisible
attribute in a deployable app; it produces `CE0066`, an error, exit 1 — there
is a consistency check aimed at exactly that state, and `Update security`
repairs it from inside the IDE without undoing the write. The detail is in
[adding-an-attribute.md](adding-an-attribute.md). Loud and recoverable is a
good failure mode to design against.

The bottom row is the one with no oracle. `mx check` cannot report on an app it
cannot load.

## Where failure is still silent

Everything above is loud. These are the places where something can go wrong
and report success, which is the only risk class worth being afraid of. All
four are open items in [`BACKLOG.md`](../BACKLOG.md), and all four are
engineering, not fate:

1. **A misspelled argument is dropped, not refused.** FastMCP leaves
   `additionalProperties` unset, so an argument passed under a name the tool
   does not declare is discarded without a word, and the call proceeds with the
   default in place of the value meant to govern it. On a reader this is
   harmless. On a write tool it is a failure that looks like success in its own
   report. This is the reason the backlog says to settle it *before* the first
   editing tool.
2. **Nothing refuses to write to a dirty working copy.** The whole recovery
   story is `git checkout` separating this server's write from the user's own
   work, and on a dirty copy it stops doing that. The protection that makes
   every other risk survivable is the one nothing enforces.
3. **`assert_writable` is best-effort.** Taking SQLite's write lock proves no
   other process is writing. It does not prove Studio Pro holds the model in
   memory, which is the case that actually loses work — see
   [editing-alongside-studio-pro.md](editing-alongside-studio-pro.md).
4. **Invariant coverage is exactly as wide as what was measured.** The Model SDK
   knows the metamodel; this server knows adding an attribute, renaming, and
   moving an entity in the diagram. Anything else is an unmeasured invariant,
   and the gap does not announce itself — `bson.encode` will happily write a
   document that is well-formed and wrong.

## The failure this project has actually produced

Not a corrupted file. A **confidently wrong generalisation from a small
sample**: key ordering was checked against the six domain-model units in hand,
plain ASCII sorting produced zero mismatches, and the rule was written down.
Across 600 units of 24 unit types, plain ASCII sorting misplaces keys in 14.5%
of nested documents. Domain models simply contain no keys that collide on case.

Nothing was damaged — the error surfaced as bytes that differed with identical
content. But it is the honest shape of the danger here, and it generalises:
**a fact about the format has to be measured against variety of unit types, not
against the cases on the desk.** A small homogeneous sample confirms the wrong
hypothesis and feels like evidence.

The second one did reach a commit. `create_unit` did not exist, because
creating a unit was judged to be session work rather than a shaper operation —
`write_unit` only updates a row that already exists. So ten sessions wrote
their own, fifteen scripts in total, each with its own `INSERT INTO Unit` and
its own idea of which invariants mattered. Five units landed with a root `$ID`
that was not the id they were stored as, and the damage showed up weeks later
as a diff that would not open ([unit-identity.md](unit-identity.md)).

The guards in `storage.py` are only worth what their coverage is, and coverage
is measured over *every path to disk* — including the paths that do not exist
yet. 🔑 **A primitive that every caller reimplements is not out of scope. It is
missing**, and each reimplementation is a chance to omit a guard nobody will
notice is absent.

## The question to ask before registering a tool

For the mutation the tool performs: **if it goes wrong, do I find out, and can I
get back?**

- *Found out and recoverable* — routine. Value edits on a clean working copy, with
  `mx check` as the oracle, are this.
- *Found out but expensive to undo* — needs a measured before/after pair from
  Studio Pro before the tool exists, not after.
- *Not found out* — do not register it. Fix the detection first; the four items
  above are that list.

Nothing in this note argues the Model SDK is unnecessary. Binary MPRv2 merges
still belong to Studio Pro, and the `.mpr` is a single global file that every
edit touches. What this note argues is narrower: the reason to be careful is
incomplete invariant coverage, which is measurable and shrinkable — not the
fact that the file is binary, which is neither.
