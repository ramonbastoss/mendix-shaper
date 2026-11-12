"""Moving one end of an association, with everything that has to move with it.

Re-parenting an association looks like a one-leaf edit — write a new
`ParentPointer` and you are done — and that is exactly why it is worth having
an operation for. Two things travel with that leaf and neither announces
itself:

- the **`GUID`**, which is the primary key of the association's row in the
  database catalogue. Moving the storage without a fresh one makes the
  database synchronisation fail on a duplicate key, long after `mx check` has
  passed. `storage.assert_storage_identity` refuses the write; this module
  makes it impossible to reach.
- the **`MemberAccess` entries**, because an association is a member of the
  entity it hangs off, so it appears in that entity's access rules. Leave them
  on the old owner and `mx check` reports `CE0066`.

Everything here is a function from a decoded document to a new one. Nothing
writes — pass the result to `storage.write_unit` to apply it, or read it and
stop to get the dry run. See `documents.py` for why that split is the whole
design.

What this does **not** do is the long tail: every path in the app that crossed
the association now needs one more hop, and that is a rewrite across pages,
microflows, XPath, OQL and published contracts. See
`docs/moving-an-association-end.md`. This module moves the association; it does
not pretend to have moved the app.
"""

import copy
import uuid

from engine import storage


def _objects(items):
    """Drop the leading integer marker a `.mxunit` list carries."""
    return [x for x in items if isinstance(x, dict)]


def find(document, name):
    """The association called `name`, or `None`.

    `name` is the bare name as it sits in the domain model (`A_B`), not the
    qualified one — that form only appears in references *to* it.
    """
    for bucket in storage.ASSOCIATION_BUCKETS:
        for assoc in _objects(document.get(bucket, [])):
            if assoc.get("Name") == name:
                return assoc
    return None


def member_accesses(entity, association_qualified_name):
    """Every `MemberAccess` in `entity`'s rules that names this association."""
    found = []
    for rule in _objects(entity.get("AccessRules", [])):
        for access in _objects(rule.get("MemberAccesses", [])):
            if access.get("Association") == association_qualified_name:
                found.append((rule, access))
    return found


def move_end(document, name, new_parent_id, module,
             rights_from=None, new_guid=None):
    """Move `name`'s owning end to the entity identified by `new_parent_id`.

    Returns `(new_document, changes)`; writes nothing. `changes` is a list of
    human-readable lines, which is the only reviewable diff a BSON unit has.

    `module` is the domain model's own module name, needed to build the
    qualified name the access rules use (`MyModule.A_B`).

    `rights_from` is the name of a sibling member on the new owner — another
    association of the same nature — whose `AccessRights` are copied rule by
    rule. This is the question the operation cannot answer on its own: the two
    entities rarely have the same number of rules, so there is no positional
    mapping, and the rule's `DefaultMemberAccessRights` is often wrong (a rule
    whose default is `None` but whose members are all explicitly `ReadOnly`
    would silently close the association off). Without it, the default is used
    and the caller is told so.

    `new_guid` exists for tests and for reproducing a known-good result. Leave
    it alone in real use: a fresh one is the point.
    """
    document = copy.deepcopy(document)
    changes = []

    assoc = find(document, name)
    if assoc is None:
        raise ValueError(f"No association named {name!r} in this domain model")

    old_parent = assoc.get("ParentPointer")
    if old_parent == new_parent_id:
        raise ValueError(
            f"{name!r} already hangs off that entity. Moving an end to where "
            f"it already is would still burn a new GUID and make the database "
            f"drop and recreate the column, losing every value in it."
        )

    entities = {e.get("$ID"): e for e in _objects(document.get("Entities", []))}
    old_entity = entities.get(old_parent)
    new_entity = entities.get(new_parent_id)
    if new_entity is None:
        raise ValueError(
            f"No entity with that $ID in this domain model, so the new parent "
            f"would be a dangling pointer. An association can only hang off an "
            f"entity declared in the same module as itself."
        )

    # 1. the leaf everybody thinks about
    assoc["ParentPointer"] = new_parent_id
    changes.append(f"ParentPointer: {_name_of(old_entity)} -> "
                   f"{new_entity.get('Name')}")

    # 2. the identity of the storage, which moved with it
    old_guid = assoc.get("GUID")
    assoc["GUID"] = new_guid or uuid.uuid4().bytes_le
    changes.append(f"GUID: {_readable(old_guid)} -> "
                   f"{_readable(assoc['GUID'])}")

    # 3. the access rules of both entities
    qualified = f"{module}.{name}"
    if old_entity is not None:
        removed = 0
        for rule in _objects(old_entity.get("AccessRules", [])):
            keep = [x for x in rule.get("MemberAccesses", [])
                    if not (isinstance(x, dict)
                            and x.get("Association") == qualified)]
            removed += len(rule.get("MemberAccesses", [])) - len(keep)
            rule["MemberAccesses"] = keep
        changes.append(f"MemberAccess: {removed} removed from "
                       f"{old_entity.get('Name')}")

    sibling = f"{module}.{rights_from}" if rights_from else None
    added = []
    for rule in _objects(new_entity.get("AccessRules", [])):
        accesses = rule.setdefault("MemberAccesses", [])
        rights = None
        if sibling:
            match = next((x for x in _objects(accesses)
                          if x.get("Association") == sibling), None)
            rights = match["AccessRights"] if match else None
        if rights is None:
            rights = rule.get("DefaultMemberAccessRights") or "None"
        accesses.append({
            "$ID": uuid.uuid4().bytes_le,
            "$Type": "DomainModels$MemberAccess",
            "AccessRights": rights,
            "Association": qualified,
            "Attribute": "",
        })
        added.append(rights)
    source = (f"mirroring {rights_from}" if rights_from
              else "from each rule's DefaultMemberAccessRights")
    changes.append(f"MemberAccess: {len(added)} added to "
                   f"{new_entity.get('Name')} ({source}): {', '.join(added)}")

    return document, changes


def _name_of(entity):
    return entity.get("Name") if entity else "<unknown entity>"


def _readable(identity):
    """A 16-byte identity as the UUID it is, for a change line."""
    if isinstance(identity, bytes) and len(identity) == 16:
        return str(uuid.UUID(bytes_le=identity))
    return repr(identity)
