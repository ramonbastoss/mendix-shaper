# Notes on the model

What Studio Pro does that this server has to reproduce, and what Mendix
notices when it does not.

A server that writes `.mxunit` files directly has no metamodel behind it. The
Model SDK knows that creating an attribute is also an access-rule change;
`bson.encode` does not, and never will. So every rule this server respects is
a rule somebody measured once and wrote down. That is what this folder is for.

| | |
|---|---|
| [risk-profile.md](risk-profile.md) | **start here** — what Mendix checks and what it lets through, which mutations are exposed, and where a failure can still report success |
| [adding-an-attribute.md](adding-an-attribute.md) | an attribute is also an access-rule change, and what happens when you skip that half |
| [duplicate-identities.md](duplicate-identities.md) | `$ID` and `GUID` are unique within a unit — how a shallow copy breaks that, and why only the IDE's *save* ever says so |
| [unit-identity.md](unit-identity.md) | a unit's file name *is* its root `$ID` — nothing checks it, the IDE repairs it silently, and the unit's diff is what breaks |
| [changing-an-attribute.md](changing-an-attribute.md) | renaming and retyping one: four citation forms and only one is qualified, and why `mx check` is the oracle a sweep cannot replace |
| [editing-alongside-studio-pro.md](editing-alongside-studio-pro.md) | getting a write made outside the IDE into an IDE that is already open |
| [renaming-across-the-model.md](renaming-across-the-model.md) | references are qualified name strings, so a rename is a rewrite — what the IDE propagates, what it leaves, and the three traps |
| [moving-an-association-end.md](moving-an-association-end.md) | re-parenting is one leaf and a long tail: every crossing path gains a hop, a data source takes more than one, and reverse navigation stops being an edit |
| [replacing-an-association-with-a-path.md](replacing-an-association-with-a-path.md) | deleting one association in favour of a longer route: XPath will not walk a long path from a variable, and the replacing member carries its rule's default rather than the rights the old one was granted |

## What belongs here

Findings, and only findings. What is *not built yet* belongs in `BACKLOG.md`;
what the server *is* belongs in the top-level `README.md`. A note here answers
"how does the model actually behave", which outlives both.

`risk-profile.md` is the one exception, and it earns it by adding nothing: it
only organises findings already measured in the other notes, by what breaks
rather than by what you were doing. A new measurement goes in its own note
first; the risk profile cites it.

Three conventions make these notes worth trusting later:

- **Say how it was measured, and on what.** Every finding carries the
  observation that produced it and the Studio Pro version it was produced on.
  Behaviour measured on one version is not a promise about the next.
- **Separate a measurement from a guess.** A hypothesis is welcome, labelled as
  one. One of the findings below exists *because* a plausible guess was written
  down as if it were a fact and later turned out to be wrong in the half that
  mattered; keeping the correction visible is cheaper than keeping the
  confidence.
- **Examples are placeholders.** `MyModule.MyEntity`, `MyAttribute`,
  `C:\path\to\...`. Nothing here is copied out of an app that is not ours to
  publish.
