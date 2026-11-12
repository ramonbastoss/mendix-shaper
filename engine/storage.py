"""Reading and writing one `.mxunit`, and keeping the `.mpr` in step.

The bottom of the engine: bytes and files, and no Mendix semantics at all.
Nothing here knows what an entity is. Everything that edits a Mendix model
goes through `write_unit` below, so the guards live in one place instead of in
every caller.

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


def _unit_key(unit_id: str) -> bytes:
    """A unit id as the `.mpr` stores it: the UUID in little-endian bytes."""
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


# The leaves that decide *where and how* an association's data is stored:
# which table the column lives on, which table it points at, and whether the
# association is a column or a join table. A change to any of them relocates
# the storage. `Name` is deliberately absent - renaming an association is an
# update in the catalogue, not a relocation.
ASSOCIATION_STORAGE_KEYS = ("ParentPointer", "Child", "StorageFormat", "Type")

# Where a domain model keeps its associations.
ASSOCIATION_BUCKETS = ("Associations", "CrossAssociations")


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


def assert_storage_identity(before: dict, after: dict) -> None:
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
    """
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


def write_unit(mpr_path: str, unit_id: str, document: dict) -> dict:
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
    surfaces much later and somewhere else. No two objects may claim one
    identity (`assert_unique_identities` - Studio Pro only says so on save),
    and an association may not relocate its storage while keeping its `GUID`
    (`assert_storage_identity` - the database only says so at sync).
    """
    assert_writable(mpr_path)

    path = unit_path(mpr_path, unit_id)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Unit {unit_id} not found at {path}")

    before = read_blob(path)
    assert_round_trips(before)
    assert_unique_identities(document)
    assert_storage_identity(decode(before), document)

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
