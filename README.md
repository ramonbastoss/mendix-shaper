# mendix-shaper

MCP server that edits a Mendix app's model from outside Studio Pro, by writing
the `.mxunit` files directly — no Model SDK, no model server, no working copy.

The write-side counterpart to
[mendix-mapper](https://github.com/ramonbastoss/mendix-mapper), which reads the
same model and never changes it. Two servers rather than one on
purpose: the split between reading and writing has to be visible in the tool
name that reaches the model, not buried in documentation. `mcp__mendix-mapper__`
answers questions; `mcp__mendix-shaper__` changes things.

## Why not the Model SDK

The Mendix Model SDK is the supported way to mutate a model, and where it is
available it is the better tool: it knows the metamodel, so it cannot write a
structurally invalid document.

It is not always available. `createTemporaryWorkingCopy()` talks to a project
service and a model server, and a private Mendix Platform deployment may ship
the repository service without them — or without a token scope that reaches
them. When that happens the SDK is not slow, it is simply off, and every script
built on it stops.

This server depends on none of that. It needs the `.mpr` and the `mprcontents/`
folder sitting on disk, which is what a plain `git clone` already gives you.
What it gives up in exchange is the metamodel: nothing here knows that a
microflow needs a start event, so nothing here stops you from writing one that
does not have it. See **Safety** below for what that costs and how it is
contained.

What partly fills the gap is measurement. [`docs/`](docs/) collects what Studio
Pro actually does to a model and what Mendix notices when a write does not do
the same — starting with the one that is easy to get half right, since creating
an attribute is also an access-rule change. Those notes are what an editing
tool has to encode, because nothing in the format will enforce it.

## How a Mendix model is stored

Two files, and both have to stay in step:

- **`mprcontents/<2>/<2>/<unit-id>.mxunit`** — one **BSON** document per unit
  (a module, a microflow, a page, a domain model). Plain BSON: `bson.decode`
  and `bson.encode` round-trip it byte for byte.
- **`<App>.mpr`** — SQLite with a single table, `Unit`, holding one row per
  unit with its `ContainerID`, its `ContainmentName`, and a `ContentsHash`
  that is `base64(sha256(blob))`.

So writing a unit is three steps: decode, edit the dictionary, encode; write
the blob; update that row's `ContentsHash`. `engine/storage.py` is that, and
nothing more.

## How the repository is laid out

An MCP server is a contract between something that improvises and something
that does not. The deterministic half is usually an API somebody else wrote.
Here there is none to call — so it has to be built, and kept separable from
the server that exposes it.

| | |
|---|---|
| `engine/` | does the work. Knows the model, knows nothing about MCP. |
| `tools/` | one module per exposed tool: validate arguments, call the engine, shape the answer. |
| `server.py` | registration, and the docstring each tool is read through. |
| `docs/` | what Studio Pro does, measured — see [`docs/`](docs/). |
| `tests/` | one file per module, run directly (`python tests/test_storage.py`). No runner is a dependency. |

The dependency runs one way, and that is the whole point of the split: the
engine can be exercised directly, over a corpus, against what Studio Pro
actually produced, which is the only way "deterministic" gets to mean anything
here. Behind a stdio transport none of that is reachable.

Inside the engine the same rule repeats, each layer knowing less than the one
above it:

| | |
|---|---|
| `engine/storage.py` | one unit's bytes: decode, encode, hash, the guards. No Mendix semantics. |
| `engine/documents.py` | one decoded unit as a tree: walk it, flatten it, rewrite its string leaves. No disk. |
| `engine/model.py` | every unit at once: containment, which module a document lives in, and whether a qualified name points at anything. |
| `engine/rename.py` | the first operation built on all three. |
| `engine/associations.py` | moving one end of an association, with the storage identity and the access rules that travel with it. |

An operation is a **function from one decoded document to another**, and
`storage` is the only thing that touches disk. That is what makes a dry run
free — it is simply not calling the writer — and it is why `rename.plan()`
returns what it *would* change instead of changing it.

## Safety

The failure modes here are not symmetrical, and the design leans on the
difference.

**Editing a value** — a string, a flag, a number already present in the
document — is cheap to verify and hard to get catastrophically wrong.

**Editing structure** is not. A reference is a GUID, and a GUID pointing at
nothing does not produce a consistency error: it takes the model loader down
with a `KeyNotFoundException` that names the missing key and nothing else — not
the unit it was found in. In a six-thousand-unit app, finding it again is a
sweep.

Four things follow from that:

1. **`ContentsHash` is not an integrity check.** A blob edited without updating
   its hash loads normally and passes `mx check`; the field exists for
   conflict detection, not for catching a bad write. Nothing here may rely on
   Mendix noticing a mistake. Studio Pro *does* act on it, silently: the edit
   is left out of its Changes pane. So never write a blob by hand —
   `storage.write_unit` writes the hash in the same step — and
   `tool_validate_model` reports any unit whose hash is stale
   ([docs/stale-contents-hash.md](docs/stale-contents-hash.md)).
2. **Round-trip before write.** Before a unit is modified, it is re-encoded
   from its own untouched decode and compared with the bytes on disk. If they
   differ, the write is refused: an encoder that is not faithful for this unit
   would smear unrelated changes across the document.
3. **Validate after write, in two passes.** `mx check` catches a reference
   broken *by name* (`CE1613`). It does not catch a broken GUID — only loading
   the model does. `tool_validate_model` therefore runs `mx dump-mpr` first, as
   a loader canary, and `mx check` second.
4. **Rollback is git.** The model lives in a git working copy; every write is
   reversible with `git checkout` on `mprcontents/` and the `.mpr`. This server
   does not run that for you, by design — reverting is a destructive action and
   belongs to whoever is watching.

**A tool refuses an argument it does not declare.** FastMCP's default is to
drop one, so a caller that misspells `save_as_baseline` gets a run that saves
no baseline and a reply that reads like success. Every registered tool is
hardened at startup — pydantic raises on the extra key, and the published
schema says `additionalProperties: false` so a client can refuse it before
sending. A call that was meant to be governed by an argument should fail, not
quietly run on the default; that matters most for the tools this server does
not have yet.

**Close Studio Pro before writing.** It holds the `.mpr` open and keeps the
model in memory; saving from it afterwards would overwrite the edit. There is a
best-effort guard — the writer tries to take SQLite's write lock first and
refuses if it cannot — but it is a guard, not a guarantee: an IDE that merely
has a model open takes no lock at all, so the guard never sees the case that
loses work. `tool_close_app` makes that step something a caller can take, and
`tool_open_app` hands the app back afterwards.

Neither of them answers a dialog. When Studio Pro asks whether to save, or
warns that a model is already open, the tool reports what is on screen and
stops — there is no force flag. Both questions have an answer that cannot be
undone, so they go back to the user. See
[`docs/controlling-studio-pro.md`](docs/controlling-studio-pro.md).

## What it does today

| tool | |
|---|---|
| `tool_list_projects` | the working copies configured on this machine |
| `tool_close_app` | close the Studio Pro instance holding a model, or report the dialog stopping it |
| `tool_open_app` | open a model and wait for it to load, refusing if something already has it open |
| `tool_validate_model` | loader canary + consistency check, with the delta against a saved baseline |

**No editing tool is registered yet**, and that is deliberate rather than
unfinished: the write primitive is proven, the shape of the editing surface is
not. The four above do not edit a model — they report on one, check one, or
move the IDE out of the way of one — which is why they can exist while the
question stays open. Deciding what gets exposed — and how narrow each tool's reach should be —
is the open question in `BACKLOG.md`. It is a question about `tools/` only,
which is why the engine does not have to wait for it.

An absolute error count means nothing on its own. `tool_validate_model` stores a
baseline per project so that a run after a write reports *what the write did*
rather than what the app already looked like.

## Setup

```
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
copy projects.example.json projects.json
```

Call `.\venv\Scripts\python.exe` explicitly rather than activating the
environment: the MCP client launches that exact executable, so it is the one
worth proving works.

Fill in `projects.json` with the path to your `.mpr` and to the `mx.exe` of the
matching Studio Pro version, then register the server:

```json
{
  "mcpServers": {
    "mendix-shaper": {
      "command": "C:\\path\\to\\mendix-shaper\\venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\mendix-shaper\\server.py"]
    }
  }
}
```

Requirements: Python 3.10+, Studio Pro installed (for `mx.exe`), Windows.

## Status

Early, but no longer only at the byte level. The storage format and the write
recipe are measured — a byte-for-byte round-trip across 5,999 units of a 200 MB
app, and a write that `mx check` accepts with zero errors.

On top of them there is now one real operation. `engine/rename.py` carried a
single entity rename through an app of ~6,000 units: **439 units, 3,574 string
leaves**, verified three ways — against the same rename done by hand in Studio
Pro (1,283 leaves reproduced, no divergence), against an `mx check` baseline
(0 errors, exit 0), and against every `ContentsHash` in the `.mpr`. What it
taught is in [`docs/renaming-across-the-model.md`](docs/renaming-across-the-model.md).

The tool surface on top of all of it is still not built, and still on purpose.

## License

[MIT](LICENSE). Fork it, rename it, change it, ship it, sell it — the one condition is that the copyright and permission notice travel with the copy, so keep `LICENSE` in your fork.
