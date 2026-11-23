"""Creating a unit, and the guard that keeps its root `$ID` honest.

Builds a throwaway two-row `.mpr` in a temp directory, so no app is required.
Run it directly:

    python tests/test_storage.py

Prints one line per case and exits non-zero on failure, the same shape as
tests/test_associations.py — no test runner is a dependency of this repo yet.

The case that matters is `write_unit refuses a changed root $ID`. That is the
mistake this guard exists for: a unit created with a root `$ID` other than the
one it is stored as, which Mendix loads, `mx check` passes, and Studio Pro
repairs on the next save — at which point the unit's diff against its
committed version throws `baseObjects does not contain the same root` and its
history stops being reviewable. See docs and `storage.assert_root_identity`.
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import storage  # noqa: E402

CONTAINER = str(uuid.uuid4())

PASSED = FAILED = 0
WORKSPACE = None
MPR = None


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


def fresh_model():
    """A temp app: an empty `.mpr` holding only the container row."""
    global WORKSPACE, MPR
    if WORKSPACE:
        shutil.rmtree(WORKSPACE, ignore_errors=True)
    WORKSPACE = tempfile.mkdtemp(prefix="shaper-test-")
    MPR = os.path.join(WORKSPACE, "Test.mpr")
    connection = sqlite3.connect(MPR)
    try:
        connection.execute(
            "CREATE TABLE Unit(UnitID BLOB NOT NULL, ContainerID BLOB, "
            "ContainmentName TEXT, TreeConflict LONG, ContentsHash TEXT, "
            "ContentsConflicts TEXT, PRIMARY KEY(UnitID))")
        connection.execute(
            "INSERT INTO Unit VALUES (?, NULL, '', 0, '', '')",
            (uuid.UUID(CONTAINER).bytes_le,))
        connection.commit()
    finally:
        connection.close()


def document(root=None, name="SUB_Something"):
    """A minimal unit: whatever Mendix needs is irrelevant to storage."""
    return {
        "$ID": root if root is not None else uuid.uuid4().bytes_le,
        "$Type": "Microflows$Microflow",
        "Name": name,
        "Documentation": "",
    }


def row(unit_id):
    connection = sqlite3.connect(f"file:{MPR}?mode=ro", uri=True)
    try:
        return connection.execute(
            "SELECT ContainerID, ContainmentName, TreeConflict, ContentsHash, "
            "ContentsConflicts FROM Unit WHERE UnitID = ?",
            (uuid.UUID(unit_id).bytes_le,)).fetchone()
    finally:
        connection.close()


# -- create_unit --------------------------------------------------------------

def creates():
    fresh_model()
    doc = document()
    result = storage.create_unit(MPR, doc, CONTAINER)

    expected_id = str(uuid.UUID(bytes_le=doc["$ID"]))
    assert result["unit_id"] == expected_id, "id was not derived from the root"
    assert os.path.exists(result["path"]), "no blob on disk"

    # the invariant the whole function exists for
    on_disk = storage.decode(storage.read_blob(result["path"]))
    name = os.path.basename(result["path"])[:-len(".mxunit")]
    assert name == expected_id, f"file named {name}, root is {expected_id}"
    assert on_disk["$ID"] == doc["$ID"], "root $ID changed on the way to disk"

    stored = row(expected_id)
    assert stored is not None, "no row in the .mpr"
    assert stored[0] == uuid.UUID(CONTAINER).bytes_le, "wrong container"
    assert stored[1] == "Documents", f"containment was {stored[1]!r}"
    assert stored[2] == storage.NO_TREE_CONFLICT, "tree conflict not empty"
    assert stored[3] == storage.content_hash(storage.read_blob(result["path"])), \
        "ContentsHash does not match the blob"
    assert stored[4] == storage.NO_CONTENTS_CONFLICTS, "conflicts not empty"


case("create_unit derives the id from the root and writes both halves",
     creates, False)


def creates_folder():
    fresh_model()
    storage.create_unit(MPR, document(name="MyFolder"), CONTAINER,
                        containment_name="Folders")


case("create_unit takes a containment other than Documents",
     creates_folder, False)


def accepts_container_as_bytes():
    fresh_model()
    storage.create_unit(MPR, document(), uuid.UUID(CONTAINER).bytes_le)


case("create_unit takes the container as raw bytes too",
     accepts_container_as_bytes, False)


def no_root():
    fresh_model()
    doc = document()
    del doc["$ID"]
    storage.create_unit(MPR, doc, CONTAINER)


case("create_unit refuses a document with no root $ID", no_root, True)


def short_root():
    fresh_model()
    storage.create_unit(MPR, document(root=b"\x01\x02\x03"), CONTAINER)


case("create_unit refuses a root $ID that is not 16 bytes", short_root, True)


def duplicate():
    fresh_model()
    doc = document()
    storage.create_unit(MPR, doc, CONTAINER)
    storage.create_unit(MPR, doc, CONTAINER)


case("create_unit refuses to overwrite a unit that exists", duplicate, True)


def unknown_container():
    fresh_model()
    storage.create_unit(MPR, document(), str(uuid.uuid4()))


case("create_unit refuses a container with no row", unknown_container, True)


def duplicate_identity():
    fresh_model()
    shared = uuid.uuid4().bytes_le
    doc = document()
    doc["Flows"] = [{"$ID": shared, "$Type": "Microflows$SequenceFlow"},
                    {"$ID": shared, "$Type": "Microflows$SequenceFlow"}]
    storage.create_unit(MPR, doc, CONTAINER)


case("create_unit runs the same identity guard as write_unit",
     duplicate_identity, True)


def no_orphan_row():
    """A refused create must leave nothing behind in the .mpr."""
    fresh_model()
    doc = document()
    try:
        storage.create_unit(MPR, doc, str(uuid.uuid4()))
    except Exception:                              # noqa: BLE001 - expected
        pass
    unit_id = str(uuid.UUID(bytes_le=doc["$ID"]))
    assert row(unit_id) is None, "refused create still inserted a row"
    assert not os.path.exists(storage.unit_path(MPR, unit_id)), \
        "refused create still wrote a blob"


case("a refused create writes neither half", no_orphan_row, False)


# -- assert_root_identity, via write_unit -------------------------------------

def rewrites():
    fresh_model()
    doc = document()
    storage.create_unit(MPR, doc, CONTAINER)
    doc["Documentation"] = "changed"
    result = storage.write_unit(MPR, str(uuid.UUID(bytes_le=doc["$ID"])), doc)
    assert result["changed"], "write reported no change"
    assert row(str(uuid.UUID(bytes_le=doc["$ID"])))[3] == result["contents_hash"], \
        "the .mpr hash was not updated"


case("write_unit still edits a unit create_unit made", rewrites, False)


def changed_root():
    fresh_model()
    doc = document()
    storage.create_unit(MPR, doc, CONTAINER)
    stored_as = str(uuid.UUID(bytes_le=doc["$ID"]))
    doc["$ID"] = uuid.uuid4().bytes_le          # what the clone helper did
    storage.write_unit(MPR, stored_as, doc)


case("write_unit refuses a changed root $ID", changed_root, True)


def repaired():
    """The documented repair: put the id it is stored as back on the root."""
    fresh_model()
    doc = document()
    storage.create_unit(MPR, doc, CONTAINER)
    stored_as = str(uuid.UUID(bytes_le=doc["$ID"]))
    doc["$ID"] = uuid.uuid4().bytes_le
    doc["$ID"] = uuid.UUID(stored_as).bytes_le
    doc["Documentation"] = "repaired"
    storage.write_unit(MPR, stored_as, doc)


case("a repaired root $ID is accepted again", repaired, False)


if WORKSPACE:
    shutil.rmtree(WORKSPACE, ignore_errors=True)

print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
