# Renaming something, and everything that points at it

Measured on Studio Pro 10.24.13, MPRv2, on an app of roughly 6,000 units.
Numbers below come from one rename carried through to a clean `mx check`.

## Why a rename is a string rewrite

Mendix stores references between documents as **qualified name strings**, not
as ids. A microflow call holds the text `MyModule.MyMicroflow`. An XPath names
its associations inline. A page parameter mapping holds
`MyModule.MyPage.MyParameter`.

So renaming a thing and renaming every reference to it are the same operation,
and the model stays consistent as long as the rewrite is complete. This is what
makes a rename reachable without a metamodel - and what makes a partial one a
broken model.

It also sets the failure modes. A reference left pointing at a name that no
longer exists is `CE1613` at check time, which is loud and survivable. A
reference rewritten onto a name that already belonged to something else
produces no error at all, and the model compiles meaning something different.
Only the second one needs a check before writing.

## What Studio Pro propagates by itself, and what it leaves

Renaming an entity in the IDE does not rename everything that carries its name.
Measured by taking a rename done by hand in Studio Pro and comparing it, leaf by
leaf, against the untouched model:

| | |
|---|---|
| Entity type references (`Entity`, `DestinationEntity`, `Attribute`, and the same names inside XPath and expression text) | **propagated** |
| Associations whose name **begins** with the entity name (`MyEntity_Other`) | **propagated** - the name is derived from the owning side |
| The same association from the other end (`Other_MyEntity`) | **not propagated** |
| Document names that embed the entity name (`ACT_MyEntity_Open`) | not propagated |
| Local variable and parameter names, and the expressions reading them | not propagated |
| Folder names | not propagated |
| `ExposedName` / `ExposedAssociationName` of a published OData service | not propagated - and should not be, see below |
| Anything inside a document with `Excluded = true` | **not propagated** |

That association asymmetry is worth holding on to, because it means **the size
of the job depends on where you start**. Counting the work on a model Studio Pro
has already half-renamed gives a smaller number than counting it on an untouched
one; neither is wrong, they answer different questions. In the measured case the
difference was 37 associations versus 44.

## Three traps

### Members inherited from `System.$Entity` are not in `Attributes`

`createdDate`, `changedDate`, `owner` and `changedBy` are real members of every
persistable entity and appear in references as
`MyModule.MyEntity.createdDate` - but they are nowhere in that entity's
`Attributes` list, because the entity does not declare them.

Any check of the shape *"does this member reference resolve?"* that looks the
member up will call those references dead. In the measured case this silently
excluded four live references from the rewrite; nothing in `mx check` complained,
because leaving a reference untouched is only wrong relative to an intention.
Resolve a member reference **on its entity**, never on the member.
`engine/model.py` does this and says so.

### Documents excluded from the build accumulate dead references

Studio Pro does not propagate renames into a document with `Excluded = true`, so
those documents keep pointing at whatever the model held when they were
excluded. In the measured app, 13 excluded documents held 45 references to an
entity that had moved to another module long before.

They are already broken and `mx check` ignores them, so they are harmless where
they are. Rewriting them is **not** neutral: it can turn a dangling reference
into a live one and quietly re-attach dead code to a real object. The default in
`engine/rename.py` is to leave any reference that does not resolve today exactly
where it is.

### A published name is a contract, not a name

An OData service exposes `ExposedName` and `ExposedAssociationName` separately
from the model's own names, precisely so that an internal rename does not reach
consumers. Rewriting them is a breaking API change wearing the costume of a
refactor, and nothing in the model will tell you - the service keeps publishing,
with different names.

Frozen by default in `engine/rename.py`. A caller that really means it passes
its own set.

## Checks that belong before the write

- **Global collisions.** For every existing qualified name containing the old
  token, does the renamed form already exist? Across entities, associations,
  attributes and documents, and across kinds - a new association name landing
  on an existing document name is still a collision.
- **Local collisions.** Inside one flow or page: a document holding both
  `OldName` and `NewName` ends up with two things answering to one name, and an
  expression silently reads the other one's value. Qualified-name checks do not
  see this; local names are not qualified.

Both were zero in the measured rename, which is the only reason a blanket
rewrite was defensible there.

## Verifying afterwards

Three independent checks, in increasing cost:

1. **Round-trip and hash.** Every unit re-encodes to the bytes on disk and every
   `ContentsHash` in the `.mpr` matches its blob. Catches a damaged write, not a
   wrong one.
2. **A rename already done by hand.** If the same rename exists anywhere as a
   partial, manual Studio Pro change, it is an oracle: every leaf Studio Pro
   touched must be reproduced, and the only permitted difference is the extra
   leaves the IDE does not propagate. In the measured case this was 1,283 leaves
   - 1,136 identical and 147 where the result was the IDE's with the remaining
   renames applied on top. It is what caught the `createdDate` trap above.
3. **`mx check` against a baseline.** 0 errors means nothing on its own; an app
   can carry hundreds. Record the baseline on the untouched model first and
   compare the delta.

## Scale, for sizing

One entity renamed across the measured app: 439 units, 3,574 string leaves, 730
distinct values rewritten. Spread over microflows, pages, nanoflows, snippets,
domain models, one folder and one published OData service.

The point of the number is not the number. It is that a rename of this shape is
not reviewable by reading a diff - `git` reports `Bin 16361 -> 16307 bytes` per
file and nothing else - so the record of what changed has to come from the
operation itself. `engine/rename.plan()` returns it per unit for exactly this
reason.
