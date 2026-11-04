# Changing an attribute

Measured on Studio Pro 10.24.13, MPRv2, on an app of roughly 5,700 units.

[adding-an-attribute.md](adding-an-attribute.md) covers creating one. This note
covers changing one that already exists — renaming it, and changing its type —
and the verification loop both of those need. Those two mutations look small and
reach further than a rename of a document does, because an attribute is named
in four different ways and only one of them says which entity it belongs to.

## An attribute is cited in four forms, and only one is qualified

| form | leaf key | shape | a qualified-name rewrite reaches it? |
|---|---|---|---|
| model reference | `Attribute` | `MyModule.MyEntity.MyAttribute` | **yes** |
| published contract | `ExposedName` | `MyAttribute` | **must not** — it is the API |
| expression | `Expression`, `InitialValue`, `Value` | `$var/MyAttribute` | no |
| XPath and OQL | `XpathConstraint`, `XPathConstraint`, `Oql` | `[MyAttribute = ...]`, `alias.MyAttribute` | no |

`rename.plan()` rewrites the first row and freezes the second. That is the whole
of what a string rewrite can do correctly, and it is not a limitation to work
around: the other two rows name the attribute without naming its entity, so the
same text means a different attribute depending on what `$var` is bound to.
Rewriting them by name alone renames the attribute on every entity that happens
to share it.

In the measured app, one attribute rename was 78 qualified leaves across 47
units — and 13 further sites in the other two forms, spread over pages,
microflows, string-built OQL, and two OQL view entities.

## 🔴 The oracle is `mx check`, not a sweep of the decoded model

This is the finding, and it is written here because the measured session got it
backwards.

Three successive sweeps were written over the decoded model to enumerate where
the attribute was named. Each had a filter bug. Together they found **10 of the
13** unqualified sites. The remaining three surfaced from `mx check` in seconds,
as `CE0117` ×2 and `CE0161` ×2, and each took under a minute to locate once the
error class pointed at what to search for.

A sweep is for **sizing** the work — how many units, which modules, what can be
automated. It cannot declare coverage, because the thing being tested is the
author's own filter. `mx check` has no filter in it.

**The loop:**

1. `mx check` **before writing anything**, and keep the count.
2. Write.
3. `mx check`. Every new error is yours.
4. Fix what it names. Repeat until the count is back to the baseline.

⚠️ Step 1 is not optional and is the easiest to skip. In the measured session it
was run for the first time with 62 units already written; separating the new
errors from pre-existing ones then required a throwaway
`git worktree add --detach <tmp> HEAD` and a second full check. The baseline
happened to be 0, which made the mistake free. It usually will not be.

⚠️ `mx check` does **not** evaluate a step marked `Disabled`, nor a document
marked `Excluded`. An app can sit at 0 errors with a disabled activity
referencing a module that no longer exists. Those are also exactly the
references `keep_broken_references` leaves alone, so the two agree — but do not
read 0 errors as "every reference in the file resolves".

## Changing the type

The type is the `$Type` of the attribute's **`NewType`**, not a key called
`Type`:

```python
{"$ID": ..., "$Type": "DomainModels$Attribute",
 "Documentation": "...", "ExportLevel": "Hidden", "GUID": ...,
 "Name": "MyAttribute",
 "NewType": {"$ID": ..., "$Type": "DomainModels$IntegerAttributeType"},
 "Value":   {"$ID": ..., "$Type": "DomainModels$StoredValue",
             "DefaultValue": ""}}
```

Swapping `DomainModels$AutoNumberAttributeType` for
`DomainModels$IntegerAttributeType` is a one-leaf edit — neither type carries
extra keys — plus clearing `Value.DefaultValue`, which an AutoNumber uses as its
seed.

### AutoNumber projects as **Long**, Integer projects as Integer

An OQL view entity column selecting an AutoNumber is created as
`DomainModels$LongAttributeType`. Retyping the source attribute does **not**
update the column, and the OQL text does not change either:

```
[error] [CE6770] "OQL View Entity is out of sync with the OQL Query."
        at Entity 'MyModule.MyView'
```

The fix is to set that column's `NewType` to match the new source type. If the
attribute was *also* renamed, the same view produces a second, different error
first, which is the one that names the problem out loud:

```
[error] [CE0174] "Error(s) in OQL query: The path 'alias.MyAttribute' does not
        resolve to a column or association." at Entity 'MyModule.MyView'
```

⚠️ Every view that projects the attribute has to be found. `CE6770` names the
view but not the column, so on a wide view it is a hunt.

### AutoNumber cannot be written

A change activity targeting one is refused:

```
[error] [CE0852] "Attribute 'MyModule.MyEntity.MyAttribute' cannot be changed,
        because it is an auto-generated number."
```

This is the same rule as `CE6592` refusing `ReadWrite` on an AutoNumber in an
access rule. If the point of a retype is to make a derived value writable, this
error is the confirmation that it was necessary — and its absence after the
retype is the confirmation that it worked.

## ❌ A hypothesis that was wrong: the type does not break expressions

Recorded because it was plausible and cost real time.

With four errors left after a retype, the hypothesis was that `Integer + String`
is invalid in an expression where `AutoNumber + String` had been fine — the
retype being the most exotic change in the batch.

**Refuted by reverting one variable.** The type was set back to AutoNumber (and
the view column back to Long), `mx check` was run, and the four errors came back
**identical**, plus one new `CE0852`. They were missed references, not type
errors. A counter-example was already in the app: `$str + $obj/MyIntegerAttr`
passes the check and always had.

🔑 One-variable revert against an oracle that marks its own work beats reasoning
about the metamodel. It took about three minutes.

## Validation rules live on the **entity**, not on the attribute

A `unique` rule is an entry in the entity's `ValidationRules`, keyed by the
attribute's qualified name — so **renaming an attribute also rewrites its own
validation rules**, and `rename.plan()` does that for free since `Attribute` is
the leaf key there too.

```python
{"$ID": ..., "$Type": "DomainModels$ValidationRule",
 "Attribute": "MyModule.MyEntity.MyAttribute",
 "Message":  {"$ID": ..., "$Type": "Texts$Text", "Items": [3]},
 "RuleInfo": {"$ID": ..., "$Type": "DomainModels$UniqueRuleInfo"}}
```

## `AccessRights` is never an absent key — "None" is a string

Measured across every `MemberAccess` in the app: **14,588 of 14,619** carry the
key, with values `ReadWrite` (6,265), `ReadOnly` (5,137) and **`"None"`
(3,217)**. "No access" is the literal string, not omission. The remaining 31 are
legacy objects with the same keys in a different order.

```python
{"$ID": ..., "$Type": "DomainModels$MemberAccess",
 "AccessRights": "ReadOnly", "Association": "",
 "Attribute": "MyModule.MyEntity.MyAttribute"}
```

⚠️ This reads as a bug in Python: `ma.get("AccessRights")` returning the string
`"None"` prints identically to the absent key. A guard asserting "some member in
this rule has no `AccessRights` key, so the shape exists" fires correctly here —
and the correct response is to believe the guard, not to weaken it.

## ⚠️ Resolving a bare reference in a page: the data source is a *sibling*

Given a leaf at `....Widgets[7].Object.Properties[3].Value...Expression` holding
`$currentObject/MyAttribute`, the entity that governs `$currentObject` is **not**
on an ancestor path. A widget keeps its caption expression and its data source in
different branches of the same `Object`.

Two heuristics that were tried and should not be used:

1. *"the `Entity` leaf whose path is a prefix of the hit path"* — finds nothing
   in a page, because the data source does not contain the expression.
2. *"the `Entity` leaf with the longest common prefix"* — finds something, and is
   **silently wrong**. In the measured app it named one entity for a combo box
   whose options came from a different one.

What worked: truncate the hit path at its last `.Object.` and look for
`Entity` / `DestinationEntity` under that same prefix — that is the owning
widget. In a microflow, `Action.RetrieveSource.Entity` on an ancestor resolves
directly.

🔑 A heuristic that is wrong in silence is worse than one that refuses. If the
owning entity cannot be established, the site belongs in a list handed back to
the caller, not in the rewrite.

## What an editing tool here owes its caller

- Run `mx check` before and after, and report the **delta**. A count alone says
  nothing.
- Rewrite the qualified half, which is deterministic.
- **Return the unqualified sites as unfinished work**, named and located, rather
  than guessing at them. Resolving `$var` needs a metamodel this server does not
  have, and pretending otherwise turns a partial rename into a silent one.
- Assert the expected number of occurrences for every textual replacement. In the
  measured session two replacements would otherwise have matched a different
  count than intended and gone unnoticed.
