# Moving one end of an association

Re-parenting an association — the same relationship, now hanging off a
different entity — is a one-leaf edit followed by a long tail. The leaf is
cheap and safe. The tail is where the work is, and it is not optional: when an
end moves, **every path that crossed the association gains a hop**, and not one
of its citations stays valid.

Measured on Studio Pro 10.24.13, on an app of ~5,750 units, moving a 1-1
cross-module association from `MyModule.MyEntity` to `MyModule.MyOtherEntity`
(which already had a reference to `MyEntity`, so the new route is two hops).
`mx check` ran as the oracle against a 0-error baseline.

## The two ends are not stored alike

```python
{
  "$Type": "DomainModels$CrossAssociation",
  "Name": "MyEntity_MyTarget",
  "ParentPointer": b"...16 bytes...",      # the OWNING entity, by $ID
  "Child": "OtherModule.MyTarget",         # the far side, by qualified name
  "Owner": "Both",                         # Both = 1-1, Default = N-1
  "Type": "Reference",
  "StorageFormat": "Column",
}
```

The asymmetry matters for anything that searches the model by name: a sweep
over strings finds `Child` and never finds the parent, because the parent is a
pointer. Moving the end is writing a new `ParentPointer` — one leaf. Everything
below is the consequence.

> The pointer is the entity's `$ID`, in the little-endian byte form Mendix uses
> everywhere (`uuid.UUID(...).bytes_le`). An entity also carries a separate
> `GUID`; it is not what the pointer refers to.

## 🔴 The `GUID` is the database's primary key — give it a new one

`mx check` passes, the app builds, and then the **database sync** dies:

```
INSERT INTO "MENDIXSYSTEM$ASSOCIATION" ("ID", "ASSOCIATION_NAME", "TABLE_NAME", ...)
ORA-00001: unique constraint violated      -- SYS_C009751, the PK on ID
```

The association's `GUID` leaf and the `ID` column of the catalogue row are the
**same value**. Measured: the model carried
`GUID = a1b2c3d4-…` and the catalogue row for that association had the very
same value in `ID`, with `TABLE_NAME = 'MYMODULE$MYENTITY'`.

When an association changes the table its column lives in, the sync treats it
as a **new** storage structure and emits `INSERT`, not `UPDATE`. Keep the
`GUID` and that insert lands on the row that is still there. **Generate a fresh
`GUID` when moving an end** — the `GUID` is the identity of the *storage*, and
the storage moved.

> Check first that nothing else in the model carries that value. Unlike `$ID`,
> a `GUID` is not used as a pointer target anywhere this was measured — the
> association's own leaf was the only carrier — but a sweep costs one pass and
> an unnoticed second carrier is a dangling reference.

### What the failed attempt leaves behind depends on the DBMS

On Oracle and MySQL, DDL commits implicitly, so a sync that dies midway leaves
the schema partly migrated while the catalogue rows — ordinary DML — roll back.
The halves disagree in **both directions**, and that is the state to diagnose:

| | catalogue says | schema really has |
|---|---|---|
| old index/unique/FK | exist | dropped |
| new column/index/unique | absent | created |

On PostgreSQL the same failure leaves the database untouched, because DDL there
is transactional. Same bug, completely different cleanup — do not carry a
recovery recipe from one to the other.

Recovering means putting the schema back into the shape the catalogue
describes, then re-running. The catalogue itself is the source for the names
and the delete rule: `CHILD_FKC_NAME`, `CHILD_FKC_ACTION` (2 = `SET NULL`,
confirmed against a sibling FK's `DELETE_RULE` in `dba_constraints`),
`PARENT_COLUMN_NAME`, `CHILD_COLUMN_NAME`.

**And the data does not travel.** The sync is DDL; it never moves values from
the old column to the new one. Whatever the association held is gone once the
old column is dropped, whichever `GUID` you use.

## `Owner: Both` means both entities carry the member

An association is a *member* of the entity it hangs off, so it appears in that
entity's access rules as a `MemberAccess`. On a 1-1 (`Owner: Both`), **both**
entities carry one, in every rule they have:

```python
{"$Type": "DomainModels$MemberAccess",
 "AccessRights": "ReadWrite", "Association": "MyModule.MyEntity_MyTarget",
 "Attribute": ""}
```

So moving the end means deleting those entries from every rule of the old
owner and adding one to every rule of the new owner. The far side is untouched.

The two entities rarely have the same number of rules (11 and 8, in the case
measured), so there is no positional mapping to lean on. What worked was
**mirroring a sibling member**: the new owner already had another association
of the same nature, and copying its `AccessRights` rule by rule produced a
coherent result in one pass. Falling back to the rule's
`DefaultMemberAccessRights` is the other option, and it is what the IDE's
*Update security* button does — but on a rule whose default is `None` and whose
members are all explicitly `ReadOnly`, the default is the wrong answer.

Skipping this half does not pass silently: it is `CE0066`, an error. See
[adding-an-attribute.md](adding-an-attribute.md) for the same mechanism from
the attribute side.

## Every path gains a hop, and paths are not all equally flexible

Citations found, by key, in the case measured — 79 leaves across 31 units:

| Key | What it is | Gains the hop by |
|---|---|---|
| `Association` inside `EntityRef.Steps[]` | a widget's path | inserting one more `EntityRefStep` |
| `Expression` | microflow/page expression | string rewrite: `/A/Target` → `/A/Mid/B/Target` |
| `AssociationId` in `RetrieveSource` | a Retrieve by association | **a second Retrieve activity** |
| `XPathConstraint` | a constraint | string rewrite; XPath handles multi-valued steps fine |
| `Oql` | a view entity's query | a join, not a rewrite |
| `Association` in `ODataPublish$PublishedAssociationEnd` | a published contract | re-point or remove |

### A data source path is *not* limited to one hop

This was the finding that changed the plan. The assumption going in was that a
data view "by association" accepts exactly one association, so every data
source crossing the moved end would need the page restructured. Measuring the
app instead of trusting the assumption:

```
DataSource.EntityRef    steps=1 -> 478    steps=2 -> 47    steps=3 -> 14    steps=4 -> 2
AttributeRef.EntityRef  steps=1 -> 552    steps=2 -> 185   steps=3 -> 30    steps=4 -> 12
```

Multi-step data sources exist, written by Studio Pro, in the same app. So a
data source path takes the extra hop like any other — append an
`EntityRefStep`, and `mx check` is clean.

> The leading integer of a `Steps` list is **not** a count. It is `2` whether
> the list holds one step or four (measured: 1,233 lists with one step, all
> marked `2`). Inserting a step does not mean updating it. This is the general
> rule for `.mxunit` lists, restated here because an insertion is exactly where
> somebody would reach for it.

### A Retrieve by association does not

`RetrieveSource` holds a single `AssociationId`, so a two-hop retrieve is two
activities. The cheap trick that avoids touching the rest of the flow:

**leave the existing retrieve where it is.** It stays valid on its own — Mendix
resolves the result type *from the association*, so after the move it simply
starts returning the intermediate entity. Rename its `ResultVariableName`, add
a second retrieve for the remaining hop, and give **that** one the original
variable name. Every downstream reference in the flow keeps working untouched.

Inserting the activity is three edits: append to
`ObjectCollection.Objects`, re-point the outgoing `SequenceFlow` (in `Flows`,
not in `Objects`) to the new activity, and append a flow from the old activity
to the new one. Shift `RelativeMiddlePoint` of everything to the right to make
room — geometry is cosmetic, a wrong layout breaks nothing.

Build the new flow's `Line` from scratch. Copying a neighbour's flow carries
the nested `Line`'s `$ID` along, which is the defect in
[duplicate-identities.md](duplicate-identities.md).

## Reverse navigation is a design change, not an edit

Everything above assumes the path runs *along* the association. The ones
running back the other way are different in kind, because the new intermediate
entity usually sits on the many side: one `MyEntity` to several `MyOtherEntity`.

- **XPath survives.** A constraint navigates multi-valued steps happily;
  `[not(A/B/C)]` still means "none of them".
- **OQL survives**, as a join rather than an inline navigation — join the
  intermediate with the predicate that picks the right row, then read the
  association off the join alias.
- **An expression does not.** There is no way to write `$obj/Reverse/Entity` in
  a Mendix expression when `Reverse` yields a list, so a conditional-visibility
  setting that crossed the association has no mechanical equivalent. It needs
  either a surrounding data source that resolves the single relevant row, or a
  derived attribute — both of which are decisions about behaviour, not edits.
- **A data view by association does not**, for the same reason.

Budget for this: the mechanical 95% lands clean, and what is left is a handful
of reverse-direction points that each need somebody to decide what the screen
should do.

## What this server cannot do here

**It cannot create a unit.** `write_unit` requires the file to exist and a row
in the `.mpr` to update. A fix that needs a new microflow — the usual answer
for a reverse-direction data source — is an IDE job, and the honest move is to
stop and say so rather than build something shaped like it.
