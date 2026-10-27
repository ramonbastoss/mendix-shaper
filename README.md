# mendix-shaper

MCP server that edits a Mendix app's model from outside Studio Pro, by writing
the `.mxunit` files directly — no Model SDK, no model server, no working copy.

The write-side counterpart to [mendix-mapper](../mendix-mapper-public), which
reads the same model and never changes it. Two servers rather than one on
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

## How a Mendix model is stored

Two files, and both have to stay in step:

- **`mprcontents/<2>/<2>/<unit-id>.mxunit`** — one **BSON** document per unit
  (a module, a microflow, a page, a domain model). Plain BSON: `bson.decode`
  and `bson.encode` round-trip it byte for byte.
- **`<App>.mpr`** — SQLite with a single table, `Unit`, holding one row per
  unit with its `ContainerID`, its `ContainmentName`, and a `ContentsHash`
  that is `base64(sha256(blob))`.

So writing a unit is three steps: decode, edit the dictionary, encode; write
the blob; update that row's `ContentsHash`. `tools/_mxunit.py` is that, and
nothing more.

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
   its hash loads normally; the field exists for conflict detection, not for
   catching a bad write. Nothing here may rely on Mendix noticing a mistake.
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

**Close Studio Pro before writing.** It holds the `.mpr` open and keeps the
model in memory; saving from it afterwards would overwrite the edit. There is a
best-effort guard — the writer tries to take SQLite's write lock first and
refuses if it cannot — but it is a guard, not a guarantee.

## What it does today

| tool | |
|---|---|
| `tool_list_projects` | the working copies configured on this machine |
| `tool_validate_model` | loader canary + consistency check, with the delta against a saved baseline |

**No editing tool is registered yet**, and that is deliberate rather than
unfinished: the write primitive is proven, the shape of the editing surface is
not. `tools/_mxunit.py` is the engine; deciding what gets exposed on top of it —
and how narrow each tool's reach should be — is the open question in
`BACKLOG.md`.

An absolute error count means nothing on its own. `tool_validate_model` stores a
baseline per project so that a run after a write reports *what the write did*
rather than what the app already looked like.

## Setup

```
python -m venv venv
venv\Scripts\pip install -r requirements.txt
copy projects.example.json projects.json
```

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

Early. The storage format and the write recipe are measured — a byte-for-byte
round-trip across 5,999 units of a 200 MB app, and a write that `mx check`
accepts with zero errors. The tool surface on top of them is not built.
