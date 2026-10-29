"""Walking and rewriting one decoded unit.

The middle of the engine: a decoded `.mxunit` is a tree of dicts, lists and
scalars, and everything an edit does is reached by walking it. Nothing here
touches disk and nothing here knows what an entity is - that is `storage` below
and `model` above.

The reason this is its own layer is the dry-run problem in `BACKLOG.md`. An
operation written as a function from one decoded document to another costs
nothing to run without committing to it: not calling `storage.write_unit` *is*
the dry run. That only holds while transformations stay here, pure, and the
single path to disk stays there.

**Lists carry a leading integer.** A list in a `.mxunit` starts with an int
that is not an element - it is a marker Mendix puts there, not a count of what
follows. Any code reading `Entities[0]` as an object gets an `int` instead and
fails somewhere later with a confusing error. `walk` steps over scalars so it
never notices; callers that index into a list by hand have to filter with
`isinstance(x, dict)` themselves.
"""


def walk(document, path=""):
    """Yield `(path, value)` for every leaf, in document order.

    The path is dotted, with list positions in brackets -
    `Entities[3].Attributes[1].Name` - which makes it readable in a report and,
    more usefully, lets two decodes of the same unit be compared position by
    position.
    """
    if isinstance(document, dict):
        for key, value in document.items():
            yield from walk(value, f"{path}.{key}" if path else key)
    elif isinstance(document, list):
        for index, value in enumerate(document):
            yield from walk(value, f"{path}[{index}]")
    else:
        yield path, document


def flatten(document):
    """The whole document as `{path: leaf}`.

    Two flattened documents compared key by key answer "what exactly changed",
    which is the only readable diff a BSON unit has. `git diff` reports
    `Bin 16361 -> 16307 bytes` and stops there - the change is tracked, but
    nobody can review it. This is how it gets reviewed.
    """
    return dict(walk(document))


def leaf_key(path):
    """The last key of a path, brackets removed: `Entities.Name` -> `Name`.

    Mendix decides what a string *means* by the key holding it, not by where it
    sits in the tree: `Entity` is a qualified entity name, `Caption` is prose,
    `ExposedName` is a published contract. A transformation that keys off this
    can treat them differently; one that only looks at the value cannot.
    """
    return path.rsplit(".", 1)[-1].split("[")[0]


def map_strings(document, fn):
    """Rebuild `document` with every string leaf passed through `fn(key, value)`.

    `fn` returns the replacement, or the value unchanged to leave it alone.
    Returns `(new_document, changes)` where changes is a list of
    `(key, before, after)` - the record that makes a binary write reviewable
    afterwards.

    Key order is preserved because it has to be: BSON keeps insertion order, so
    a document rebuilt with keys in a different order encodes to different
    bytes with identical content. That passes a structural comparison and fails
    a byte-for-byte one against what Studio Pro produces.
    """
    changes = []

    def rec(node, key):
        if isinstance(node, dict):
            return {k: rec(v, k) for k, v in node.items()}
        if isinstance(node, list):
            # the key travels down into the list: in `Entities[2].Name` the
            # string's key is `Name`, and the index tells us nothing
            return [rec(v, key) for v in node]
        if isinstance(node, str):
            new = fn(key, node)
            if new != node:
                changes.append((key, node, new))
            return new
        return node

    return rec(document, None), changes
