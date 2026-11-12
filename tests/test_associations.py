"""Moving an association's end: the guard and the operation.

Synthetic domain model, no app required. Run it directly:

    python tests/test_associations.py

Prints one line per case and exits non-zero on the first failure, which is the
same shape as the stdio handshake check — no test runner is a dependency of
this repo yet.

The case that matters is the first one. It is the mistake that produced this
guard: an association moved to another entity with its `GUID` left alone, which
`mx check` accepts, the build accepts, and the database synchronisation rejects
with a duplicate primary key. See docs/moving-an-association-end.md.
"""

import copy
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import associations, storage  # noqa: E402

OLD_PARENT = uuid.uuid4().bytes_le
NEW_PARENT = uuid.uuid4().bytes_le
ASSOC_ID = uuid.uuid4().bytes_le
ASSOC_GUID = uuid.uuid4().bytes_le

PASSED = FAILED = 0


def rule(default, sibling_rights):
    """One access rule carrying the sibling member the operation mirrors."""
    return {
        "$ID": uuid.uuid4().bytes_le,
        "$Type": "DomainModels$AccessRule",
        "AllowCreate": False,
        "AllowDelete": False,
        "DefaultMemberAccessRights": default,
        "MemberAccesses": [2, {
            "$ID": uuid.uuid4().bytes_le,
            "$Type": "DomainModels$MemberAccess",
            "AccessRights": sibling_rights,
            "Association": "MyModule.MyOtherEntity_Sibling",
            "Attribute": "",
        }],
        "XPathConstraint": "",
    }


def model():
    """A domain model with two entities and one association between them."""
    return {
        "$ID": uuid.uuid4().bytes_le,
        "$Type": "DomainModels$DomainModel",
        "CrossAssociations": [2, {
            "$ID": ASSOC_ID,
            "$Type": "DomainModels$CrossAssociation",
            "Child": "OtherModule.MyTarget",
            "GUID": ASSOC_GUID,
            "Name": "MyEntity_MyTarget",
            "Owner": "Both",
            "ParentPointer": OLD_PARENT,
            "StorageFormat": "Column",
            "Type": "Reference",
        }],
        "Entities": [2,
            {"$ID": OLD_PARENT, "$Type": "DomainModels$Entity",
             "Name": "MyEntity",
             "AccessRules": [2, {
                 "$ID": uuid.uuid4().bytes_le,
                 "$Type": "DomainModels$AccessRule",
                 "DefaultMemberAccessRights": "ReadWrite",
                 "MemberAccesses": [2, {
                     "$ID": uuid.uuid4().bytes_le,
                     "$Type": "DomainModels$MemberAccess",
                     "AccessRights": "ReadWrite",
                     "Association": "MyModule.MyEntity_MyTarget",
                     "Attribute": "",
                 }],
                 "XPathConstraint": "",
             }]},
            {"$ID": NEW_PARENT, "$Type": "DomainModels$Entity",
             "Name": "MyOtherEntity",
             "AccessRules": [2, rule("ReadWrite", "ReadWrite"),
                             rule(None, "ReadOnly")]},
        ],
    }


def case(title, fn, expect_error):
    global PASSED, FAILED
    try:
        fn()
        raised, message = False, ""
    except Exception as exc:                       # noqa: BLE001 - that is the assertion
        raised, message = True, str(exc)
    if raised == expect_error:
        PASSED += 1
        print(f"[PASS] {title}")
    else:
        FAILED += 1
        print(f"[FAIL] {title}")
        print(f"       expected {'an error' if expect_error else 'no error'}, "
              f"got {'an error' if raised else 'none'}")
        if message:
            print(f"       {message[:200]}")


def moved(**overrides):
    """The model with the association re-parented, plus any overrides."""
    after = copy.deepcopy(model())
    assoc = associations.find(after, "MyEntity_MyTarget")
    assoc["ParentPointer"] = NEW_PARENT
    assoc.update(overrides)
    return after


# -- the guard ----------------------------------------------------------------

case("guard refuses a moved end that kept its GUID",
     lambda: storage.assert_storage_identity(model(), moved()), True)

case("guard accepts the same move with a fresh GUID",
     lambda: storage.assert_storage_identity(
         model(), moved(GUID=uuid.uuid4().bytes_le)), False)

case("guard accepts an untouched document",
     lambda: storage.assert_storage_identity(model(), copy.deepcopy(model())),
     False)


def renamed():
    after = copy.deepcopy(model())
    associations.find(after, "MyEntity_MyTarget")["Name"] = "MyEntity_Renamed"
    storage.assert_storage_identity(model(), after)


case("guard ignores a rename — that is an update, not a relocation",
     renamed, False)


def retyped():
    after = copy.deepcopy(model())
    associations.find(after, "MyEntity_MyTarget")["Type"] = "ReferenceSet"
    storage.assert_storage_identity(model(), after)


case("guard catches Reference -> ReferenceSet with the GUID kept",
     retyped, True)

# -- the operation ------------------------------------------------------------


def operation():
    after, changes = associations.move_end(
        model(), "MyEntity_MyTarget", NEW_PARENT, module="MyModule",
        rights_from="MyOtherEntity_Sibling")

    assoc = associations.find(after, "MyEntity_MyTarget")
    assert assoc["ParentPointer"] == NEW_PARENT, "end did not move"
    assert assoc["GUID"] != ASSOC_GUID, "GUID was not reissued"

    entities = {e["$ID"]: e for e in after["Entities"] if isinstance(e, dict)}
    left = associations.member_accesses(entities[OLD_PARENT],
                                        "MyModule.MyEntity_MyTarget")
    arrived = associations.member_accesses(entities[NEW_PARENT],
                                           "MyModule.MyEntity_MyTarget")
    assert not left, f"{len(left)} MemberAccess left on the old owner"
    assert len(arrived) == 2, f"expected 2 MemberAccess, got {len(arrived)}"

    rights = [a["AccessRights"] for _, a in arrived]
    assert rights == ["ReadWrite", "ReadOnly"], f"mirrored wrong: {rights}"

    # whatever the operation produces has to satisfy the guard
    storage.assert_storage_identity(model(), after)
    assert len(changes) == 4, f"expected 4 change lines, got {changes}"


case("move_end re-parents, reissues the GUID and moves the access rules",
     operation, False)

case("move_end refuses moving an end to where it already is",
     lambda: associations.move_end(model(), "MyEntity_MyTarget", OLD_PARENT,
                                   module="MyModule"), True)

case("move_end refuses a parent that is not an entity in this model",
     lambda: associations.move_end(model(), "MyEntity_MyTarget",
                                   uuid.uuid4().bytes_le, module="MyModule"),
     True)

case("move_end refuses an association that does not exist",
     lambda: associations.move_end(model(), "NoSuchAssociation", NEW_PARENT,
                                   module="MyModule"), True)


def falls_back():
    after, changes = associations.move_end(
        model(), "MyEntity_MyTarget", NEW_PARENT, module="MyModule")
    entities = {e["$ID"]: e for e in after["Entities"] if isinstance(e, dict)}
    arrived = associations.member_accesses(entities[NEW_PARENT],
                                           "MyModule.MyEntity_MyTarget")
    rights = [a["AccessRights"] for _, a in arrived]
    # no sibling named, so each rule's own default is used - and the rule whose
    # default is absent gets "None" rather than a crash
    assert rights == ["ReadWrite", "None"], f"fell back wrong: {rights}"
    assert "DefaultMemberAccessRights" in changes[-1], "fallback not reported"


case("move_end falls back to each rule's default, and says so",
     falls_back, False)


print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
