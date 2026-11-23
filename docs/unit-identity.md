# A unit's identity is its file name

Measured on Mendix 10.24.13, MPRv2, on a 200 MB app of 5,771 units.

## The invariant

A unit lives at `mprcontents/<ab>/<cd>/<uuid>.mxunit` and has a row in the
`.mpr` keyed on `UnitID`. That `<uuid>`, that `UnitID`, and the **root
object's `$ID` inside the blob** are the same value. Not a convention, not a
label — one identity written down in three places.

Measured: 5,766 of 5,771 units agree. All five exceptions were written by a
script.

## Nothing notices

The mismatch is invisible to every check that exists:

| | |
|---|---|
| `.mpr` row | keys on the **file name**, so the unit is found |
| Studio Pro load | opens the model, shows the document, runs it |
| `mx check` | 0 errors, exit 0 |
| runtime | the document executes normally |
| references to it | a microflow reached from a domain model event handler is cited by **qualified name**, not by `$ID` — so no pointer dangles |

A unit written this way is, for every practical purpose, a working unit. That
is what makes it dangerous: there is no moment at which somebody is told.

## Where it surfaces

The first time Studio Pro **saves** the unit. The save normalises the root
`$ID` to the file name — silently, as part of an edit the author made for some
unrelated reason. From then on the committed blob and the working copy have
different roots, and opening that row in the **Changes** tab throws:

```
System.InvalidOperationException: baseObjects does not contain the same root
   at Mendix.Modeler.MergeLib.ContentsAlgorithmNewStyle.DifferenceComputer.ComputeDifferences()
   at Mendix.Modeler.DiffLib.DifferencesRenderer.GetModificationRows()
   at Mendix.Modeler.VersionControl.Status.States.DocumentLevelStateSelector.GetState(...)
```

The diff loads the base blob by path and compares roots before it compares
anything else. So the cost is not a broken unit — it is a unit whose **history
cannot be reviewed**, landing on whoever opens the Changes tab days later
rather than on whoever wrote it.

The whole stack is in the diff renderer, reached by double-clicking the row.
The Changes list itself still renders, and committing does not go through that
path. Committing is also the repair: once the normalised root is in the base,
the next diff has two roots that agree.

⚠️ A unit that has **not** been touched since the bad commit diffs fine today —
base and working copy are equally wrong, so the comparison succeeds. It breaks
on the first save. A sweep is the only way to find those before they go off.

## How it gets written

Not by hand-editing a blob — by **creating** one. The shape is always the same:
a function that takes the id and the document as two arguments.

```python
def create_unit(unit_id, document):      # two values, nothing ties them
    path = unit_path(mpr, unit_id)       # <- one of them decides the name
    ...                                  #    the other is already in the blob
```

Give it a document built by cloning a template, and the clone helper does the
rest. A correct clone replaces **every** 16-byte identity so the copy shares
nothing with its template — which includes the root. The root `$ID` is then a
fresh `uuid4`, the file name is whatever constant the caller passed, and the
two were never going to match.

Both halves are individually right. The defect only exists in the gap between
them, which is why no guard inside either half can see it.

## The fix is structural

`engine.storage.create_unit` takes **no `unit_id`**. It derives the id from
`document["$ID"]`, so the file name, the row key and the root object cannot be
given different values:

```python
unit_id = str(uuid.UUID(bytes_le=document["$ID"]))
```

`engine.storage.assert_root_identity` covers the other direction — editing a
unit that is already wrong — and runs inside `write_unit`, where the id does
arrive separately because the unit already has one.

Neither helps a unit already on disk. For those, the sweep:

```python
# the root $ID is always the FIRST element of the BSON document,
# so the first 30 bytes are enough - no decode, no full read
head = open(path, "rb").read(30)
assert head[4] == 0x05 and head[5:9] == b"$ID\x00"
root = head[14:30]                       # compare with the file name
```

Fast enough to run over a whole app in seconds, which is what makes it
reasonable as a routine check rather than an investigation.

## Why the primitive was missing

`write_unit` refuses to create, correctly: it updates a row that exists, and a
new unit needs the row inserted plus a container to insert it under
(containment lives only in the `.mpr` — a blob does not record who holds it).
So creating stayed out of the engine, on the reasoning that it was session
work rather than a shaper operation.

The result was fifteen scripts across ten sessions, each with its own
`INSERT INTO Unit` and its own idea of which invariants were worth checking.
Several got it right. The ones that did not produced the five units above.

🔑 **A primitive that every caller reimplements is not out of scope — it is
missing.** The guards in `storage.py` are only worth having if every path to
disk goes through them, and a path that does not exist yet is still a path.

For reference, the row those fifteen scripts each wrote by hand:

```sql
INSERT INTO Unit (UnitID, ContainerID, ContainmentName,
                  TreeConflict, ContentsHash, ContentsConflicts)
VALUES (?, ?, ?, ?, ?, ?)
```

`UnitID` and `ContainerID` are little-endian UUID bytes; `ContainmentName` is
`'Documents'` for a microflow or page and `'Folders'` for a folder;
`TreeConflict` is `0` and `ContentsConflicts` is `''`.

## Repairing one already on disk

Cheap, and worth knowing before deciding whether to bother: `$ID` is a
fixed-width 16-byte binary, so correcting it does not change the document's
length and no BSON length prefix has to be recomputed. Measured on two units:
3,725 → 3,725 and 6,207 → 6,207 bytes.

Sweep for the wrong value used as a **pointer** before replacing it. It should
come back empty — nothing inside a unit references its own root, and other
units cite a document by qualified name — but that is a property to confirm per
model rather than assume, and it is the one thing that would turn a leaf edit
into a graph edit. Measured on this app: zero occurrences across 5,772 units.

Then assign the right value and go through `write_unit`, so the `ContentsHash`
in the `.mpr` is updated with it.
