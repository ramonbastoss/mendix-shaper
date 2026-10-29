"""The whole app, read at once: what exists and what its name is.

The top of the engine. `storage` handles one unit's bytes, `documents` walks
one decoded unit; this reads every unit and answers questions that only make
sense across the model - which module a document lives in, and whether a
qualified name points at anything.

That second question is the one `BACKLOG.md` keeps coming back to: *an edit
whose target does not resolve should be refused before anything is written,
not after*. A reference by qualified name that points at nothing produces a
`CE1613` at check time, which is survivable. A reference that points at the
wrong thing produces nothing at all, which is not. Both need an index of what
exists, and that is what `Model.names` is.

Reading 6,000 units takes a few seconds and a few hundred MB. That is fine for
an operation that spans the model and wasteful for one that touches a single
unit - use `storage` directly for the latter.
"""

import os
import sqlite3
import uuid

from engine import storage

# The unit type for a module is `Projects$ModuleImpl`, not `Projects$Module`.
# Looking for the latter finds nothing and silently yields a model where every
# document has no module - which looks like a model with no modules rather
# than like a bug.
MODULE_TYPE = "Projects$ModuleImpl"

# Keys whose value is a qualified name, and what kind of thing it names.
# `member` is `Module.Entity.Member` and resolves differently - see `resolve`.
REFERENCE_KEYS = {
    "Entity": "entity",
    "DestinationEntity": "entity",
    "Attribute": "member",
    "Association": "association",
    "AssociationId": "association",
}


def _only_objects(items):
    """Drop the leading integer marker a `.mxunit` list carries.

    See the note in `documents.py`: the first element of a list is a marker,
    not an element. Iterating `Entities` without this gets an `int` where an
    entity was expected.
    """
    return [x for x in items if isinstance(x, dict)]


class Model:
    """Every unit of one app, decoded, with its containment tree."""

    def __init__(self, mpr_path):
        self.mpr_path = os.path.abspath(mpr_path)
        self.units = {}       # unit id -> decoded document
        self._parent = {}     # unit id -> container unit id (None at the root)
        self._module = {}     # unit id -> module name, memoised
        self.names = {}       # kind -> set of qualified names

    # ------------------------------------------------------------- reading --

    @classmethod
    def load(cls, mpr_path):
        model = cls(mpr_path)
        model._read_units()
        model._build_name_index()
        return model

    def _read_units(self):
        # Containment lives in the .mpr, not in the blobs: a unit does not
        # record who contains it, so the tree can only be read from here.
        connection = sqlite3.connect(f"file:{self.mpr_path}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "SELECT UnitID, ContainerID FROM Unit"
            ).fetchall()
        finally:
            connection.close()

        for unit_id, container_id in rows:
            uid = str(uuid.UUID(bytes_le=unit_id))
            self._parent[uid] = (str(uuid.UUID(bytes_le=container_id))
                                 if container_id else None)
            path = storage.unit_path(self.mpr_path, uid)
            if os.path.exists(path):
                self.units[uid] = storage.decode(storage.read_blob(path))

    # ------------------------------------------------------------ identity --

    def type_of(self, unit_id):
        return self.units.get(unit_id, {}).get("$Type")

    def name_of(self, unit_id):
        return self.units.get(unit_id, {}).get("Name")

    def module_of(self, unit_id):
        """The module a unit lives in, by walking containment upwards.

        A document can sit several folders deep, so this is a climb and not a
        lookup. The `seen` guard is not paranoia about real models - it keeps a
        damaged `.mpr` from turning a wrong answer into a hang.
        """
        if unit_id in self._module:
            return self._module[unit_id]
        seen, current = set(), unit_id
        while current and current not in seen:
            seen.add(current)
            if self.type_of(current) == MODULE_TYPE:
                self._module[unit_id] = self.name_of(current)
                return self._module[unit_id]
            current = self._parent.get(current)
        self._module[unit_id] = None
        return None

    def documents(self, unit_type=None):
        """Yield `(unit_id, document)`, optionally filtered by `$Type`."""
        for unit_id, document in self.units.items():
            if unit_type is None or self.type_of(unit_id) == unit_type:
                yield unit_id, document

    # --------------------------------------------------------------- names --

    def _build_name_index(self):
        index = {"entity": set(), "attribute": set(),
                 "association": set(), "document": set()}
        for unit_id, document in self.units.items():
            module = self.module_of(unit_id)
            if not module:
                continue
            name = self.name_of(unit_id)
            if name and self.type_of(unit_id) != MODULE_TYPE:
                index["document"].add(f"{module}.{name}")
            if self.type_of(unit_id) != "DomainModels$DomainModel":
                continue
            for entity in _only_objects(document.get("Entities", [])):
                index["entity"].add(f"{module}.{entity.get('Name')}")
                for attribute in _only_objects(entity.get("Attributes", [])):
                    index["attribute"].add(
                        f"{module}.{entity.get('Name')}.{attribute.get('Name')}")
            associations = (document.get("Associations", []) +
                            document.get("CrossAssociations", []))
            for association in _only_objects(associations):
                index["association"].add(f"{module}.{association.get('Name')}")
        self.names = index

    def resolve(self, value, kind):
        """Does this qualified name point at something that exists?

        `member` (`Module.Entity.Member`) resolves on the **entity**, not on
        the member. Members inherited from `System.$Entity` - `createdDate`,
        `changedDate`, `owner`, `changedBy` - are not listed in the entity's
        `Attributes`, so checking the member gives a false negative on a
        perfectly live reference. Measured: an operation that skipped such
        references left four of them behind on an entity that had been renamed,
        and only a comparison against what Studio Pro produced caught it.
        """
        if kind == "member":
            return value.rsplit(".", 1)[0] in self.names["entity"]
        return value in self.names[kind]

    def resolves_reference(self, key, value):
        """Whether a leaf that *is* a reference points at something live.

        Returns `None` for a key that does not hold a qualified name, which is
        different from `False` - callers that treat the two the same will skip
        every caption in the model.
        """
        kind = REFERENCE_KEYS.get(key)
        if kind is None:
            return None
        return self.resolve(value, kind)
