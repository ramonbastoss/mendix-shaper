"""Reading and writing one `.mxunit`, and keeping the `.mpr` in step.

This module is the engine, not a tool: nothing registers it with the server.
Everything that edits a Mendix model goes through `write_unit` below, so the
guards live in one place instead of in every caller.

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


def write_unit(mpr_path: str, unit_id: str, document: dict) -> dict:
    """Write a document back as `unit_id`, and update the `.mpr` to match.

    The order matters on failure, not on success: the blob is written first and
    the hash second, so an interrupted write leaves a unit whose hash is stale
    - which Mendix loads - rather than a hash pointing at content that is not
    there.

    Returns what changed, for a caller that wants to report it. Nothing here
    validates the *model*: that a document is well-formed BSON says nothing
    about whether its references resolve. Run `tool_validate_model` after.
    """
    assert_writable(mpr_path)

    path = unit_path(mpr_path, unit_id)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Unit {unit_id} not found at {path}")

    before = read_blob(path)
    assert_round_trips(before)

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
