# Two objects, one identity

*Measured on Studio Pro 10.24.13.86719, against a 200 MB app of 5,740 units.*

Every object in a `.mxunit` carries a `$ID`, and some also carry a `GUID`.
Within one unit **both are unique**: across all 5,740 units of the app measured,
no unit repeated either value — in any unit nobody had hand-edited.

The two keys are counted separately and never against each other. An object's
own `$ID` and `GUID` are allowed to be the same 16 bytes, and three attributes
across two domain models that Studio Pro wrote itself are exactly that. A guard
that compared the keys against each other would reject them.

## How the invariant gets broken

Inserting an activity into a microflow needs a new `SequenceFlow`. The cheap way
to build one is to copy a neighbour, since the shape is fiddly:

```python
flow = dict(model_flow)             # <- shallow
flow["$ID"] = uuid.uuid4().bytes_le
flow["OriginPointer"] = new_activity["$ID"]
flow["DestinationPointer"] = old_target["$ID"]
```

That looks complete and is not. A `SequenceFlow`'s keys are:

```
$ID, $Type, CaseValues, DestinationConnectionIndex, DestinationPointer,
IsErrorHandler, Line, OriginConnectionIndex, OriginPointer
```

`Line` is **a nested object with its own `$ID`** — the curve drawn between the
two activities, a `Mendix.Modeler.Microflows.BezierCurve`. `dict()` copies the
reference, so the new flow and the one it was copied from share one `Line`.
Renewing the top-level `$ID` hides this: the flow looks new, and the curve
inside it is not.

Six flows across five microflows were written this way before anything noticed.

## 🔴 Nothing downstream notices

This is the reason the finding is worth a note of its own rather than a line in
a commit message.

| check | result |
|---|---|
| The model opens in Studio Pro | ✅ fine |
| `mx check` | ✅ **0 errors** |
| Opening the affected microflow in the IDE | ✅ renders normally |
| **Studio Pro saving that microflow** | 🔴 `System.InvalidOperationException` |

```
System.InvalidOperationException: Duplicate Guid in unit microflow
'MyModule.MyMicroflow' . Object types: Mendix.Modeler.Microflows.BezierCurve,
Mendix.Modeler.Microflows.BezierCurve.
   at Mendix.Modeler.Storage.Operations.Savior.SaveUnit(...)
```

`mx check` validates that references **resolve**; it has no opinion on whether
two objects claim one identity, because nothing is dangling. The integrity check
lives in `Savior.SaveUnit`, which only runs when the IDE writes the unit back.

So the symptom appears in the IDE, at save time, possibly days later, on a unit
the author has stopped thinking about — and the message names the unit and the
object *type*, never the paths. That is the worst available shape: a write that
reports success and fails somewhere else.

🔑 **The general rule this is an instance of:** a reference that resolves to the
*wrong* thing produces no error at all. Dangling is loud (`CE1613`, or the
loader crash in [risk-profile.md](risk-profile.md)); wrong-but-present is
silent. Identity collision is the same class, one layer down.

## The guard

`storage.assert_unique_identities` runs inside `write_unit`, so it covers every
write without a caller having to remember it. It raises rather than repairing:
an identity may also be in use as a pointer, and the guard cannot know which
claimant is the original. Deciding that belongs to whoever built the document.

Repairing by hand is cheap once you know: give every copy a fresh identity
**recursively**, and check that the set of 16-byte values in the copy and the
set in the original do not intersect at all.

```python
def renew(node):
    if isinstance(node, dict):
        return {k: (fresh_id() if k in ("$ID", "GUID") else renew(v))
                for k, v in node.items()}
    if isinstance(node, list):
        return [renew(v) for v in node]
    return node
```

⚠️ **Renewing by key is only safe for a self-contained subtree.** Cloning a
whole *unit* is different: the `Flows` hold `OriginPointer` and
`DestinationPointer` carrying the `$ID` **of an activity**, so renewing the
declarations alone leaves every curve pointing at the original unit's objects.
There the rewrite has to be by value, through one map, so that whoever declares
an identity and whoever points at it both land on the same new value — see
[renaming-across-the-model.md](renaming-across-the-model.md) for the same shape
in a different guise.

## What is not known

- Which other object types carry a nested identity the way `SequenceFlow`
  carries `Line`. Measured for flows only; the guard is general because it keys
  off `$ID`/`GUID` rather than off a type list, but the *inventory* of shallow-
  copy traps is not mapped.
- Whether `Savior` reports more than the first colliding type per unit. All
  observed failures had a single type.
