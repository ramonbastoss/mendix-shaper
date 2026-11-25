"""Reading and writing one `.mxunit`, and keeping the `.mpr` in step.

The bottom of the engine: bytes and files, and no Mendix semantics at all.
Nothing here knows what an entity is. Everything that edits a Mendix model
goes through `write_unit` or `create_unit` below, so the guards live in one
place instead of in every caller.

That promise was false for a while, and it cost something. `create_unit` did
not exist, so every session that needed a new document wrote its own - fifteen
scripts across ten sessions, each with its own `INSERT INTO Unit` and its own
idea of which invariants were worth checking. Five units reached a commit with
a root `$ID` that did not match the name of the file holding them, because the
hand-rolled version took the id and the document as separate arguments and
nothing tied them together. See `assert_root_identity`.

**`engine/` never imports MCP.** The dependency runs one way - `tools/` calls
in here, nothing in here knows a server exists - because that is what makes
this half testable on its own. An operation can be run a thousand times over a
corpus and compared against what Studio Pro produced; a tool behind a stdio
transport cannot.

A unit is a BSON document on disk and a row in the `.mpr`, and the two have to
agree. The row carries `ContentsHash` = `base64(sha256(blob))`. Mendix will
*not* complain if they disagree - a blob edited without its hash loads
normally, because that field exists for conflict detection during a merge and
not to catch a bad write. So the hash update is this module's responsibility
and nobody else's.

`bson` comes from **pymongo**. The PyPI package named `bson` is an unrelated
project and will not work here - see requirements.txt.
"""

import base64
import hashlib
import os
import sqlite3
import uuid

import bson


def unit_path(mpr_path: str, unit_id: str) -> str:
    """Where a unit's blob lives, from its id.

    Mendix shards `mprcontents/` by the first two pairs of characters of the
    id, so a six-thousand-unit app does not become a six-thousand-entry
    directory.
    """
    root = os.path.dirname(os.path.abspath(mpr_path))
    uid = str(unit_id).lower()
    return os.path.join(root, "mprcontents", uid[0:2], uid[2:4], f"{uid}.mxunit")


def read_blob(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def decode(blob: bytes) -> dict:
    """Decode a unit blob into a mutable document.

    Deliberately *not* the mapper's `to_serializable`, which turns `bytes` into
    hex strings so they can be put in a JSON response. That conversion is
    one-way: a document that went through it can never be encoded back into the
    bytes it came from. Anything that will be written has to keep its raw
    values.
    """
    return bson.decode(blob)


def encode(document: dict) -> bytes:
    """Encode a document back into a unit blob.

    The nested length prefixes BSON needs are the encoder's problem, not ours.
    """
    return bson.encode(document)


def content_hash(blob: bytes) -> str:
    """The `.mpr`'s `ContentsHash` for a blob: base64 of its sha256."""
    return base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii")


def _unit_key(unit_id) -> bytes:
    """A unit id as the `.mpr` stores it: the UUID in little-endian bytes.

    Takes the id in either of the two forms it circulates in - the string a
    caller reads off a path or a report, and the raw 16 bytes a decoded
    document carries. They are the same id in different clothes, and making
    the caller remember which one it is holding buys nothing but a `ValueError`
    deep inside a write.
    """
    if isinstance(unit_id, bytes) and len(unit_id) == 16:
        return unit_id
    return uuid.UUID(str(unit_id)).bytes_le


def assert_writable(mpr_path: str) -> None:
    """Refuse to write while something else holds the model open.

    Studio Pro keeps the `.mpr` open and the model in memory; an edit made
    underneath it is overwritten the moment somebody hits save there. Taking
    SQLite's write lock is the closest thing to a real test, because it asks
    exactly the question that matters - can this process write to the file.

    Best-effort, and the caller should say so to the user: Studio Pro may be
    holding only read locks, in which case this passes and the in-memory model
    still wins at save time. The reliable rule remains "close Studio Pro".
    """
    try:
        connection = sqlite3.connect(mpr_path, timeout=0)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        finally:
            connection.close()
    except sqlite3.OperationalError as e:
        raise RuntimeError(
            f"The model is locked by another process ({e}). Close Studio Pro "
            f"before writing: {mpr_path}"
        )


def assert_round_trips(blob: bytes) -> None:
    """Refuse to touch a unit this encoder cannot reproduce exactly.

    Re-encoding the untouched decode has to give back the bytes on disk. When
    it does not, the encoder disagrees with whatever wrote the file, and an
    edit would carry that disagreement into every other field of the document -
    a one-property change arriving as a unit-wide rewrite that no reviewer can
    read. Measured across 5,999 units of a 200 MB app without a single failure,
    which is what makes a failure here worth stopping for rather than working
    around.
    """
    if encode(decode(blob)) != blob:
        raise RuntimeError(
            "This unit does not round-trip through the encoder, so it cannot "
            "be edited safely. Nothing was written."
        )


def _identities(document: dict, key: str):
    """Yield `(path, value)` for every `key` leaf holding a 16-byte identity.

    A deliberate second walk rather than a call into `documents.walk`: the
    docstring at the top of this module promises that every path to disk goes
    through `write_unit` and that the guards live here, and that promise is
    worth more than saving six lines. `documents` sits *above* this module.
    """
    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                yield from walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                yield from walk(v, f"{path}[{i}]")
        elif (isinstance(node, bytes) and len(node) == 16
                and path.rsplit(".", 1)[-1].split("[")[0] == key):
            yield path, node

    yield from walk(document, "")


def assert_unique_identities(document: dict) -> None:
    """Refuse a document in which two objects claim the same identity.

    Within one unit every `$ID` is unique, and so is every `GUID`. Measured
    across 5,740 units of a 200 MB app: zero repeats of either, in any unit
    nobody had hand-edited.

    The two keys are counted **separately and never against each other**,
    because an object's own `$ID` and `GUID` are allowed to coincide - measured
    on three attributes across two domain models that Studio Pro wrote itself.

    This exists because of a real defect, and the shape of it is worth keeping:
    inserting an activity into a microflow needs a new `SequenceFlow`, and the
    cheap way to build one is to copy a neighbour and replace its `$ID`. That is
    a *shallow* copy. A `SequenceFlow` carries a nested `Line` - the curve drawn
    between two activities - which is its own object with its own `$ID`, so the
    copy keeps pointing at the original's. Six flows across five units were
    written that way.

    🔴 Nothing catches it downstream. The model loads, and `mx check` reports
    zero errors, because every reference still resolves. Mendix only notices in
    `Savior.SaveUnit`, which runs when **Studio Pro saves** - so the failure
    surfaces as a `System.InvalidOperationException` in the IDE, hours later,
    on a unit the author has stopped thinking about. That is exactly the kind of
    silent write this module is supposed to make impossible.

    Safe to raise rather than repair: an identity that is also used as a pointer
    cannot simply be reassigned, and this guard has no way to know which of the
    claimants is the original. Deciding that belongs to the caller.
    """
    for key in ("$ID", "GUID"):
        seen: dict = {}
        for path, value in _identities(document, key):
            seen.setdefault(value, []).append(path)
        clashes = {v: p for v, p in seen.items() if len(p) > 1}
        if clashes:
            detail = "; ".join(
                f"{value.hex()} at {', '.join(paths)}"
                for value, paths in clashes.items()
            )
            raise RuntimeError(
                f"{len(clashes)} duplicate {key} value(s) in this unit, so two "
                f"objects claim one identity. Mendix accepts this on disk and "
                f"rejects it when Studio Pro saves the unit. Give every copied "
                f"object a fresh identity - including the nested ones, which a "
                f"shallow copy shares. Nothing was written. {detail}"
            )


def assert_root_identity(unit_id: str, document: dict) -> None:
    """Refuse a document whose root `$ID` is not the id it is stored as.

    `mprcontents/<ab>/<cd>/<uuid>.mxunit` is not a naming convention. That
    `<uuid>` *is* the root object's `$ID`, and the `.mpr` row keys on the same
    value. Measured across the 5,771 units of a 200 MB app: 5,766 agree, and
    the five that do not were all written by the same hand-rolled creation
    helper, which took the id and the document as separate arguments.

    Nothing downstream asks. The `.mpr` indexes by the file name, so Studio Pro
    loads the unit, `mx check` reports zero errors, and the runtime runs it. No
    pointer dangles either: a microflow reached from a domain model event
    handler is referenced by **qualified name**, not by `$ID`.

    It surfaces the first time Studio Pro *saves* the unit. The save normalises
    the root `$ID` to the file name, the committed version keeps the old one,
    and opening that row in the Changes tab throws:

        System.InvalidOperationException: baseObjects does not contain the same root
           at ...DifferenceComputer.ComputeDifferences()
           at ...DifferencesRenderer.GetModificationRows()

    The diff loads the base blob by path and compares roots. So the unit works
    and its history is unreviewable - the failure lands on whoever opens the
    Changes tab days later, not on whoever wrote it. Same shape as the two
    guards below: a write this encoder produces happily, that `mx check` waves
    through, whose damage appears somewhere else entirely.

    To repair a unit already on disk, set `document["$ID"]` to the id it is
    stored as and write it. That is safe when nothing points at the stale value
    - the normal case for a document, and still worth checking rather than
    assuming, because a broken pointer by GUID does not produce a check error,
    it stops the model from loading at all.
    """
    expected = _unit_key(unit_id)
    actual = document.get("$ID")
    if actual == expected:
        return
    found = (str(uuid.UUID(bytes_le=actual))
             if isinstance(actual, bytes) and len(actual) == 16
             else repr(actual))
    raise RuntimeError(
        f"This document's root $ID is {found}, but it is being stored as "
        f"{unit_id}. The file name and the .mpr row key are the root $ID, not "
        f"a label for it. Mendix accepts the mismatch and Studio Pro repairs it "
        f"silently on the next save, which then breaks the diff of that unit "
        f"against its committed version. Set document['$ID'] to {unit_id}. "
        f"Nothing was written."
    )


# The leaves that decide *where and how* an association's data is stored:
# which table the column lives on, which table it points at, and whether the
# association is a column or a join table. A change to any of them relocates
# the storage. `Name` is deliberately absent - renaming an association is an
# update in the catalogue, not a relocation.
ASSOCIATION_STORAGE_KEYS = ("ParentPointer", "Child", "StorageFormat", "Type")

# Where a domain model keeps its associations.
ASSOCIATION_BUCKETS = ("Associations", "CrossAssociations")

# What a rename may not touch. Wider than the set above and missing `Child`,
# on purpose: a rename rewrites names, and `Child` is the one storage leaf
# that holds one. Everything else has to come through a rename identical -
# including `Owner` and `GUID`, which are not in the set above because the
# question there is "did the storage move", and the question here is "did
# anything other than the name move".
RENAME_FROZEN_KEYS = ("ParentPointer", "StorageFormat", "Type", "Owner", "GUID")


def associations_by_id(document: dict) -> dict:
    """`{$ID: association}` for every association in a domain model.

    Keyed on `$ID` because that is what survives an edit: the object is the
    same object, which is exactly the question the guard below asks.
    """
    found = {}
    for bucket in ASSOCIATION_BUCKETS:
        for item in document.get(bucket, []):
            if isinstance(item, dict) and "$ID" in item:
                found[item["$ID"]] = item
    return found


def _assert_renamed_only(before: dict, after: dict,
                         renamed_entities: dict) -> None:
    """The guard below, for an edit that is a rename and nothing else.

    `Child` holds the far end **by qualified name** - `ParentPointer` holds the
    near end by `$ID`, and that asymmetry is measured in
    `docs/moving-an-association-end.md`. So renaming an entity rewrites the
    `Child` of every association pointing at it, and the storage guard reads
    that as a relocation and refuses the write.

    🔴 The guard is right about what it measured and wrong about what it
    concluded, and the obvious repair is the actual defect: giving those
    associations a fresh `GUID` would insert a second row into
    `MendixSystem$Association` for an association that already exists - exactly
    the damage the guard is there to prevent, arrived at by obeying it.
    Measured on 2026-11-24 renaming one entity: 103 units, and the guard fired
    on one of them.

    So this path is **stricter, not looser**. A rename may change `Child`, and
    only from a name in `renamed_entities` to the name it maps to; every other
    storage leaf, plus `Owner` and `GUID`, has to be identical; and no
    association may appear or disappear. An edit that claims to be a rename and
    moves something is refused here even when the general guard would have let
    it through on a fresh `GUID`.

    The mapping is matched **whole**, not as substrings, which catches the
    other way a rename goes wrong: a sweep that rewrites `MyModule.MyThing`
    without a boundary on the right also rewrites `MyModule.MyThingDetail`, and
    the resulting `Child` is in neither the old set nor the new one. That is a
    refusal here and a model-wide corruption otherwise.
    """
    problems = []

    appeared = sorted(after.keys() - before.keys())
    vanished = sorted(before.keys() - after.keys())
    if appeared or vanished:
        problems.append(
            f"{len(appeared)} association(s) appeared and {len(vanished)} "
            f"disappeared; a rename creates and deletes nothing"
        )

    for object_id, after_assoc in after.items():
        before_assoc = before.get(object_id)
        if before_assoc is None:
            continue
        name = after_assoc.get("Name") or "<unnamed>"

        frozen = [k for k in RENAME_FROZEN_KEYS
                  if before_assoc.get(k) != after_assoc.get(k)]
        if frozen:
            problems.append(f"{name} changed {', '.join(frozen)}")

        old_child, new_child = before_assoc.get("Child"), after_assoc.get("Child")
        if old_child != new_child and renamed_entities.get(old_child) != new_child:
            problems.append(
                f"{name} points at {new_child!r}, which is not what renaming "
                f"{old_child!r} produces"
            )

    if problems:
        detail = "; ".join(problems)
        raise RuntimeError(
            f"This write was declared a rename of {len(renamed_entities)} "
            f"entity name(s), but {len(problems)} association(s) in this unit "
            f"changed in a way a rename does not produce. Either the edit is "
            f"doing more than renaming - in which case say so and drop the "
            f"renamed_entities argument, so the storage guard applies in full "
            f"- or the rewrite overshot its target. Nothing was written. "
            f"{detail}"
        )


def assert_storage_identity(before: dict, after: dict,
                            renamed_entities: dict = None) -> None:
    """Refuse to move an association's storage while keeping its `GUID`.

    An association's `GUID` is not decoration: it is the **primary key of its
    row** in the database's own catalogue, `MendixSystem$Association.ID`.
    Measured on an Oracle-backed app - the model carried
    `GUID = a1b2c3d4-…` and the catalogue row for it carried that same value
    in `ID`.

    When an association changes the table its column lives on, the runtime
    treats it as a new storage structure and emits `INSERT`, not `UPDATE`. Keep
    the `GUID` and that insert lands on the row that is still there:

        INSERT INTO "MENDIXSYSTEM$ASSOCIATION" ("ID", ...)
        ORA-00001: unique constraint violated

    🔴 Nothing upstream catches this. `mx check` reports zero errors, Studio Pro
    opens the model happily, the build succeeds - and the app dies at database
    synchronisation, after the DDL that precedes the insert has already run. On
    Oracle and MySQL that DDL is committed implicitly while the catalogue rows
    roll back, so the failure leaves the schema and the catalogue disagreeing in
    *both* directions. The same mistake on PostgreSQL leaves no trace at all,
    which makes it the kind of bug that gets written off as flaky.

    That is the same shape as `assert_unique_identities`: a write this encoder
    will happily produce, that no later check will question, and whose damage
    surfaces somewhere else entirely. Hence a guard rather than a note.

    `engine/associations.move_end` does the right thing by construction; this
    exists for every other path to disk, including a one-off script.

    `renamed_entities` is `{old qualified name: new qualified name}`, and it
    says the edit is a rename of those entities and nothing else. It does not
    relax this guard - it swaps it for a stricter one, because `Child` holds a
    qualified name and a rename therefore changes storage leaves without moving
    any storage. See `_assert_renamed_only`, which is where that is explained
    and measured. Omit it and the full check below applies, which is the right
    default for every other edit.
    """
    if renamed_entities:
        return _assert_renamed_only(
            associations_by_id(before), associations_by_id(after),
            renamed_entities,
        )

    old, new = associations_by_id(before), associations_by_id(after)
    offenders = []
    for object_id, after_assoc in new.items():
        before_assoc = old.get(object_id)
        if before_assoc is None:
            continue
        moved = [k for k in ASSOCIATION_STORAGE_KEYS
                 if before_assoc.get(k) != after_assoc.get(k)]
        if moved and before_assoc.get("GUID") == after_assoc.get("GUID"):
            offenders.append((after_assoc.get("Name"), moved))

    if offenders:
        detail = "; ".join(f"{name or '<unnamed>'} ({', '.join(keys)})"
                           for name, keys in offenders)
        raise RuntimeError(
            f"{len(offenders)} association(s) changed where their data is "
            f"stored but kept the same GUID. That GUID is the primary key of "
            f"the association's row in MendixSystem$Association, and a "
            f"relocation is written to the catalogue as an INSERT - so the "
            f"database synchronisation will fail on a duplicate key, long "
            f"after mx check has passed. Give each one a fresh GUID "
            f"(uuid.uuid4().bytes_le), or use engine.associations.move_end, "
            f"which does it for you. Nothing was written. {detail}"
        )


def write_unit(mpr_path: str, unit_id: str, document: dict,
               renamed_entities: dict = None) -> dict:
    """Write a document back as `unit_id`, and update the `.mpr` to match.

    The order matters on failure, not on success: the blob is written first and
    the hash second, so an interrupted write leaves a unit whose hash is stale
    - which Mendix loads - rather than a hash pointing at content that is not
    there.

    Returns what changed, for a caller that wants to report it. Nothing here
    validates the *model*: that a document is well-formed BSON says nothing
    about whether its references resolve. Run `tool_validate_model` after.

    What it does check are the invariants that belong to the document rather
    than to the model, and they share a reason for being here: `mx check` lets
    all of them through, so a caller that trusted it would write damage that
    surfaces much later and somewhere else. The root `$ID` must be the id the
    unit is stored as (`assert_root_identity` - Studio Pro only says so by
    quietly repairing it, and breaking the unit's diff in the process), no two
    objects may claim one identity (`assert_unique_identities` - Studio Pro
    only says so on save), and an association may not relocate its storage
    while keeping its `GUID` (`assert_storage_identity` - the database only
    says so at sync).

    `renamed_entities` declares this write to be a rename of `{old qualified
    name: new}` and nothing else, which replaces that last guard with a
    stricter one. It is needed because an association names its far end by
    qualified name, so renaming an entity legitimately changes a storage leaf -
    see `_assert_renamed_only`. Declaring it is not a way past the check: a
    rename that moves anything is refused on this path and would have been
    allowed on the other.
    """
    assert_writable(mpr_path)

    path = unit_path(mpr_path, unit_id)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Unit {unit_id} not found at {path}")

    before = read_blob(path)
    assert_round_trips(before)
    assert_root_identity(unit_id, document)
    assert_unique_identities(document)
    assert_storage_identity(decode(before), document, renamed_entities)

    after = encode(document)
    if after == before:
        return {"unit_id": str(unit_id), "changed": False, "path": path}

    with open(path, "wb") as f:
        f.write(after)

    new_hash = content_hash(after)
    connection = sqlite3.connect(mpr_path)
    try:
        cursor = connection.execute(
            "UPDATE Unit SET ContentsHash = ? WHERE UnitID = ?",
            (new_hash, _unit_key(unit_id)),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            raise RuntimeError(
                f"Unit {unit_id} has no row in the .mpr, so its hash could not "
                f"be updated. The blob on disk was already written and is now "
                f"inconsistent: restore it with git before continuing."
            )
        connection.commit()
    finally:
        connection.close()

    return {
        "unit_id": str(unit_id),
        "changed": True,
        "path": path,
        "bytes_before": len(before),
        "bytes_after": len(after),
        "contents_hash": new_hash,
    }


# What Studio Pro puts in the two columns that are not identity, content or
# containment. Measured across 5,772 rows of a 200 MB app: both uniform, no
# second value anywhere. They are merge bookkeeping, and a unit that has never
# been merged carries the empty form of each.
NO_TREE_CONFLICT = 0
NO_CONTENTS_CONFLICTS = ""


def create_unit(mpr_path: str, document: dict, container_id: str,
                containment_name: str = "Documents") -> dict:
    """Add a new unit to the model: the blob on disk and its row in the `.mpr`.

    Creating is not writing, which is why `write_unit` refuses it: that one
    updates a row that exists, and a new unit needs the row inserted and a
    container to insert it under. Containment lives only in the `.mpr` - a blob
    does not record who holds it - so the document alone is not a unit.

    🔑 **There is no `unit_id` argument, and that is the whole point.** The id
    is derived from `document["$ID"]`, so the file name, the row key and the
    root object cannot be given different values. Every hand-rolled version of
    this function took the two separately, and that is exactly how five units
    reached a commit with a root `$ID` that did not match their file name -
    the document came from a clone helper that regenerated every identity
    including the root, while the path came from a constant typed further up
    the script. See `assert_root_identity` for what that costs and when.

    `container_id` is the folder or module the document appears under, and
    `containment_name` is which collection of the container it joins.
    Measured distribution: `Documents` for a document in a module or folder
    (4,408 rows), `Folders` for a folder (1,070), and the rest are the one-per-
    module fixtures - `Modules`, `ModuleSettings`, `ModuleSecurity`,
    `DomainModel`. Every row in the app has a container; none is NULL.

    Refuses rather than overwrites when either half already exists, because the
    two failures it would otherwise produce are both silent. A blob written
    over a live unit replaces a document nobody asked to touch; a row inserted
    for an existing id cannot happen at all - `UnitID` is the primary key - so
    the blob would land and the insert would abort, leaving the model holding
    the new bytes under the old identity.

    On failure the order is blob first, row second, same as `write_unit` and
    for the same reason: an interrupted create leaves a blob with no row, which
    Mendix does not read and git shows as an untracked file, rather than a row
    pointing at a file that is not there, which stops the model from loading.
    """
    assert_writable(mpr_path)

    root = document.get("$ID")
    if not isinstance(root, bytes) or len(root) != 16:
        raise RuntimeError(
            f"A new unit needs a root $ID of 16 bytes to be stored as; this "
            f"document has {root!r}. Give it one with uuid.uuid4().bytes_le. "
            f"Nothing was written."
        )
    unit_id = str(uuid.UUID(bytes_le=root))
    container_id = str(uuid.UUID(bytes_le=container_id)
                       if isinstance(container_id, bytes) else container_id)

    # No `assert_root_identity` here: deriving `unit_id` from the root is what
    # makes it hold, and an assertion that cannot fail is documentation
    # pretending to be a check. The guard is for `write_unit`, where the id
    # does arrive separately because the unit already has one.
    assert_unique_identities(document)

    blob = encode(document)
    if decode(blob) != document:
        raise RuntimeError(
            "This document does not round-trip through the encoder, so what "
            "would land on disk is not what was built. Nothing was written."
        )

    path = unit_path(mpr_path, unit_id)
    if os.path.exists(path):
        raise FileExistsError(
            f"Unit {unit_id} already exists at {path}. Creating it would "
            f"overwrite a live document; edit it with write_unit instead. "
            f"Nothing was written."
        )

    connection = sqlite3.connect(mpr_path)
    try:
        if connection.execute("SELECT 1 FROM Unit WHERE UnitID = ?",
                              (_unit_key(unit_id),)).fetchone():
            raise RuntimeError(
                f"The .mpr already has a row for {unit_id}, but no blob at "
                f"{path}. The model is inconsistent and this is not the place "
                f"to repair it. Nothing was written."
            )
        if not connection.execute("SELECT 1 FROM Unit WHERE UnitID = ?",
                                  (_unit_key(container_id),)).fetchone():
            raise RuntimeError(
                f"Container {container_id} has no row in the .mpr, so the new "
                f"unit would have nothing holding it and would not appear in "
                f"Studio Pro. Nothing was written."
            )

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(blob)

        new_hash = content_hash(blob)
        connection.execute(
            "INSERT INTO Unit (UnitID, ContainerID, ContainmentName, "
            "TreeConflict, ContentsHash, ContentsConflicts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (_unit_key(unit_id), _unit_key(container_id), containment_name,
             NO_TREE_CONFLICT, new_hash, NO_CONTENTS_CONFLICTS),
        )
        connection.commit()
    finally:
        connection.close()

    return {
        "unit_id": unit_id,
        "created": True,
        "path": path,
        "container_id": container_id,
        "containment_name": containment_name,
        "bytes": len(blob),
        "contents_hash": new_hash,
    }
