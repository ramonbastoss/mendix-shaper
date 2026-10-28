# Adding an attribute

Measured on Studio Pro 10.24.13, MPRv2.

Creating an attribute looks like appending one document to `Attributes[]`. It
is not. Studio Pro makes a second change at the same time, in a different part
of the same unit, and an attribute that arrives without it leaves the app with
a consistency error.

## What Studio Pro actually writes

Two things, in the domain model unit that holds the entity:

1. The attribute, appended to the entity's `Attributes[]`.
2. **One `MemberAccess` appended to every access rule on that entity**, with
   `AccessRights` set to that rule's own `DefaultMemberAccessRights`.

New elements go at the **end** of both lists. An entity with two access rules
therefore produces three new objects in three places, not one.

```python
attribute = {
    "$ID": new_id(),                       # uuid4, little-endian bytes
    "$Type": "DomainModels$Attribute",
    "GUID": new_id(),                      # a second, different id
    "Name": "MyAttribute",
    "Documentation": "",
    "ExportLevel": "Hidden",
    "NewType": {"$ID": new_id(), "$Type": "DomainModels$IntegerAttributeType"},
    "Value": {
        "$ID": new_id(),
        "$Type": "DomainModels$StoredValue",
        "DefaultValue": "0",
    },
}

member_access = {
    "$ID": new_id(),
    "$Type": "DomainModels$MemberAccess",
    "AccessRights": rule["DefaultMemberAccessRights"],
    "Association": "",
    "Attribute": "MyModule.MyEntity.MyAttribute",   # qualified, three parts
}
```

Note that an attribute carries **two** identifiers — `$ID` and `GUID` — and
they are not the same value. A `MemberAccess` points at the attribute by
qualified *name*, not by id, which is why a rename is also a sweep over the
access rules.

### Key order, for any object you create

BSON preserves insertion order, so an object built with its keys in the wrong
order encodes to different bytes with identical content: it passes a structural
comparison and fails a byte-for-byte one.

Studio Pro's order is alphabetical and **case-insensitive**:

```python
{k: d[k] for k in sorted(d, key=str.lower)}   # not sorted(d)
```

`$ID` and `$Type` sort to the front on their own, because `$` is 0x24.

This is easy to get wrong in a way that hides: measured across 600 units of 24
different unit types, plain ASCII sorting misplaces keys in 14.5% of nested
documents, while case-insensitive sorting misplaces none. Domain models happen
to contain no keys that collide on case, so a check against domain models alone
confirms the wrong rule. Pages are where it shows (`AllowedTypes` next to
`AllowNonPersistableEntities`, `_Key` next to `Caption`).

## Skipping the propagation: what notices

The useful question is what happens to an app where step 1 was done and step 2
was not, because that is the shape of the bug a write tool will actually have.

**`mx check` catches it.** Measured 2026-10-28:

```
[error] [CE0066] "Entity access is out of date. Please update security by
clicking the 'Update security' button in the domain model editor."
  at Domain model of module 'MyModule'
The app contains: 1 errors.                                        (exit 1)
```

The measurement was built so that propagation was the only variable: the
attribute document itself was shape-checked against one Studio Pro had written
into the same entity, the `MemberAccess` lists were asserted unchanged, and the
same `mx check` on the same working tree without the mutation returned **0
errors, exit 0**.

This corrects a guess that had been written down as a fact — that an attribute
without propagation would load, pass the check, and sit there invisible to
every role. The first half holds: the model opens. The second does not. There
is a consistency check aimed at exactly this state, and it is an **error**, not
a warning, so the worst outcome — a deployable app with an attribute no role
can see — is not reachable this way.

**The IDE repairs it.** Clicking `Update security` in the domain model editor
filled in the missing `MemberAccess` entries and cleared the error. Studio Pro
treats an attribute written by this server as a first-class member: it does not
flag it as foreign and does not ask for anything else. (Observed in the IDE;
the repaired state was not byte-compared, because the click lives in Studio
Pro's in-memory model until a save — see
[editing-alongside-studio-pro.md](editing-alongside-studio-pro.md).)

## Removal is symmetric

Measured on the same entity, from a commit where Studio Pro deleted three
attributes in one go: **deleting an attribute also deletes its `MemberAccess`
from every access rule.** Nothing else moved — the rule count, the rules' own
keys and the entity's keys were all unchanged.

One of the three had been written without propagation, so it had no
`MemberAccess` to delete. It produced no orphan and no complaint; the rules
simply never mentioned it. That is worth knowing in both directions: a
`MemberAccess` is bound to its attribute by qualified name, so the pair is
created together, removed together, and a missing one is an absence rather
than a dangling reference.

So a delete tool has the same obligation as a create tool, in reverse. Dropping
the attribute and leaving its `MemberAccess` behind would be the mirror of the
bug above — except that this one points at a name that no longer resolves,
which is a different and probably louder kind of broken.

## What this means for a write tool

- **Propagate, or refuse.** An editing tool that appends to `Attributes[]` and
  stops has not added an attribute; it has broken the app's consistency check.
  Doing half the job and reporting success is worse than refusing.
- **`mx check` is a usable oracle for propagation.** The attribute itself
  produces no consistency error of its own, so on an app with a clean baseline
  the delta answers one question precisely: did the access rules get updated.
  That is a test that needs no Studio Pro.
- **`Update security` is not a substitute.** It is a repair, and it repairs the
  whole domain model at once using each rule's default rights — which may not
  be the rights that attribute should have had. It is also the only hint the
  error gives: `CE0066` names the domain model of the *module*, not the entity
  and not the attribute, so in a module with many entities the message says
  "something in here is stale" and the search is yours.

## Not exercised yet

- An access rule whose `DefaultMemberAccessRights` is `None`.
- An entity with no access rules at all — does `CE0066` still apply, or is
  there nothing to be out of date with?
- Attribute types other than integer, and `Value` kinds other than
  `StoredValue` (a calculated attribute has a different `Value` document).
- Renaming an attribute. A `MemberAccess` refers to it by qualified name, so a
  rename has to sweep the access rules too, but whether Studio Pro does only
  that has not been checked.
- Whether a `MemberAccess` left pointing at a deleted attribute produces a
  consistency error or takes the loader down. The symmetric delete above means
  Studio Pro never produces that state; a write tool could.
