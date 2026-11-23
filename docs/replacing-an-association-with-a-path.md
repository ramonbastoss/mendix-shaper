# Replacing an association with a longer path

Measured while deleting a direct association `A --> C` from a 5,700-unit app and
re-pointing every reader at the equivalent two-hop path `A --> B --> C`.
75 leaves across 21 live units. `mx check` went **0 → 3 → 57 → 0**.

The mechanical part — swap the name everywhere — is not the hard part. Two
things bite, and neither is visible until `mx check` runs.

## 1. In XPath, a long path must start at the current entity

A microflow or page **expression** dereferences freely:

```
$MyObject/MyModule.First_Assoc/MyModule.Middle/MyModule.Second_Assoc/MyModule.Target
```

An **XPath constraint** does not. A `$variable` reference there accepts exactly
one member:

```
[MyModule.Some_Assoc = $MyObject/MyModule.First_Assoc]        -- fine
[MyModule.Some_Assoc = $MyObject/MyModule.First_Assoc
                       /MyModule.Middle/MyModule.Second_Assoc] -- CE0161
```

`CE0161` is just `"Error(s) in XPath constraint."` with no detail about which
part is wrong, anchored on the widget or activity rather than on the text.

The fix is to invert the direction so the long navigation starts at the entity
the constraint is evaluated on, and the variable stays a bare object:

```
[MyModule.Some_Assoc/MyModule.Target
   /MyModule.Second_Assoc/MyModule.Middle
   /MyModule.First_Assoc = $MyObject]
```

This is only a reformulation when the reverse path is the **exact inverse** of
the forward one. If a sibling association happens to agree with it today — say
both `Parent_Child` and `Child_Owner` usually point at the same parent — using
the convenient one instead makes readers and writers disagree the first time
they diverge. Walk back the same associations you walked forward.

🔑 A negated comparison must become `not(... = ...)`, not `!=`, when any hop in
the middle is 1:N. `!=` over a multi-valued navigation asks "is some element
different", which is true almost always.

## 2. A new association starts at the rule's default, and `None` is a common default

The Studio Pro behaviour recorded in `adding-an-attribute.md` — a new member
gets a `MemberAccess` in every access rule of the entity, carrying that rule's
`DefaultMemberAccessRights` — has a consequence that only shows up in a
replacement.

The association being **removed** may hold rights somebody granted explicitly,
years after the rule was written. The association **replacing** it holds
whatever its rule's default was on the day it was created. In the app measured,
8 of 11 rules on the entity defaulted to `None`, so the new association was
unreadable in all 8 while the legacy one was `ReadOnly` or `ReadWrite` in 5 of
them.

Delete the legacy member and those 5 rules silently lose read access. Here it
surfaced as **57 `CE2729`** ("No read access to association ... for user role
..."), one per role × screen. That is the good case: it is an error, not a
warning, so the build stays broken until somebody looks.

🔴 Do not fix it by granting `ReadOnly` across the board — that hands out access
the model never granted. Read the **old** member's rights out of version control
and copy them rule by rule, matching on the rule's `$ID`:

```python
before = decode(git_show(f"HEAD:{unit_relative_path}"))
# {(entity_name, rule_$ID): AccessRights} for the member being removed
```

then raise the new member to `max(current, legacy)` per rule, over the ordering
`None < ReadOnly < ReadWrite`. Rules where the legacy member was also `None` stay
`None` — that is information, not an oversight.

⚠️ Specializations have their own rules, and an inherited member appears in all
of them, so the same sweep has to cover every child entity's domain model — in
another module, which means another unit.

## What `mx check` does not catch

A published OData association end that still points at the member being removed
**resolves**, so the check passes. It is the consumer that breaks, and only once
somebody notices the data stopped changing. Treat the publishing document as
part of the same edit, not as a follow-up.

Where the whole replacement chain is already published as separate entity types,
the fix is usually just to re-point the one published end at the first hop of the
new path: consumers reach the far end by traversing, and no exposed name has to
change for them.
