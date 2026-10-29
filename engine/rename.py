"""Renaming a name everywhere it appears, as a function over the model.

Mendix stores references between documents as **qualified name strings**, not
as ids: a microflow call is the text `MyModule.MyMicroflow`, an XPath names its
associations inline. So renaming a thing and renaming every reference to it are
the same operation - rewrite the string wherever it occurs - and the model stays
consistent as long as the rewrite is complete.

That is why this is possible at all without a metamodel. It is also why it is
dangerous: nothing distinguishes the one leaf that names the object from the
hundreds that point at it, so a partial rewrite is a broken model and an
over-eager one is a model that compiles while meaning something else.

Everything here is a function over a loaded `Model`. Nothing writes. A caller
that wants the dry run reads the plan; a caller that wants the edit passes each
entry to `storage.write_unit`. That split is the whole point - see
`documents.py`.

## What this does not know

Studio Pro renames some things on its own when an entity is renamed, and the
rule is not symmetric: an association whose name *begins* with the entity name
is derived from the owner and follows the rename, while the same association
seen from the other end (`Other_MyEntity`) does not. Measured on 10.24.13.
A rename that starts from a model Studio Pro has already half-renamed sees a
different set of work than one starting from an untouched model; neither count
is wrong, they are answers to different questions.
"""

from engine import documents

# Names a published contract exposes. An OData service deliberately decouples
# the name consumers see from the name inside the model, so renaming the model
# must not move it - that is a breaking API change wearing the costume of a
# refactor. Frozen by default; a caller that really means to break the contract
# passes its own set.
PUBLISHED_NAME_KEYS = frozenset({"ExposedName", "ExposedAssociationName"})

# Keys whose value names something local to one document - a variable, a
# parameter, the object produced by an activity. These are not qualified, so
# they can only collide with their own siblings inside the same unit.
LOCAL_NAME_KEYS = frozenset({
    "Name", "ResultVariableName", "VariableName", "ChangeVariableName",
    "ValidationVariableName", "AggregateVariableName", "StartVariableName",
    "ListVariableName", "LoopVariableName", "OutputVariableName",
    "ErrorVariableName", "CommitVariableName", "DeleteVariableName",
    "ReturnVariableName", "ListName",
})


def collisions(model, old, new):
    """Qualified names that the rename would land on top of.

    A rename that produces a name already in use does not fail loudly - it
    produces two things answering to one name, and whichever reference is
    resolved last wins. Check before writing; there is no check afterwards that
    catches it.

    Returns `{kind: [(existing, would_become), ...]}`, empty when safe.
    """
    found = {}
    for kind, names in model.names.items():
        clashing = [(name, name.replace(old, new)) for name in sorted(names)
                    if old in name and name.replace(old, new) in names]
        if clashing:
            found[kind] = clashing
    return found


def local_collisions(model, old, new):
    """Same question, one scope down: two variables in one flow.

    A flow holding both `MyThing` and `MyThingList` is fine. A flow holding
    `OldName` and `NewName` is fine until the rename makes them the same name,
    and then one expression silently reads the other's value.

    Returns `[(unit_id, existing, would_become), ...]`.
    """
    found = []
    for unit_id, document in model.documents():
        local = set()
        for path, value in documents.walk(document):
            if (isinstance(value, str)
                    and documents.leaf_key(path) in LOCAL_NAME_KEYS):
                local.add(value)
        for name in local:
            if old in name and name.replace(old, new) in local:
                found.append((unit_id, name, name.replace(old, new)))
    return found


def plan(model, old, new, frozen_keys=PUBLISHED_NAME_KEYS,
         keep_broken_references=True):
    """What the rename would do, unit by unit, without doing any of it.

    Yields `(unit_id, new_document, changes)` for each unit that would change.
    Pass each one to `storage.write_unit` to apply it; read them and stop to
    get the dry run.

    `keep_broken_references` leaves alone any reference that does not resolve
    today. Those are dead - typically in documents excluded from the build,
    where Studio Pro does not propagate renames, so they keep pointing at
    whatever the model held years ago. Renaming them is not neutral: it can
    turn a dangling reference into a live one and quietly re-attach dead code
    to a real object. Leaving them puts the archaeology where it was.
    """
    for unit_id, document in model.documents():
        def rewrite(key, value, _unit=unit_id):
            if old not in value:
                return value
            if key in frozen_keys:
                return value
            if keep_broken_references:
                resolves = model.resolves_reference(key, value)
                if resolves is False:
                    return value
            return value.replace(old, new)

        new_document, changes = documents.map_strings(document, rewrite)
        if changes:
            yield unit_id, new_document, changes
