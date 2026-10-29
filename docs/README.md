# Notes on the model

What Studio Pro does that this server has to reproduce, and what Mendix
notices when it does not.

A server that writes `.mxunit` files directly has no metamodel behind it. The
Model SDK knows that creating an attribute is also an access-rule change;
`bson.encode` does not, and never will. So every rule this server respects is
a rule somebody measured once and wrote down. That is what this folder is for.

| | |
|---|---|
| [adding-an-attribute.md](adding-an-attribute.md) | an attribute is also an access-rule change, and what happens when you skip that half |
| [editing-alongside-studio-pro.md](editing-alongside-studio-pro.md) | getting a write made outside the IDE into an IDE that is already open |
| [renaming-across-the-model.md](renaming-across-the-model.md) | references are qualified name strings, so a rename is a rewrite — what the IDE propagates, what it leaves, and the three traps |

## What belongs here

Findings, and only findings. What is *not built yet* belongs in `BACKLOG.md`;
what the server *is* belongs in the top-level `README.md`. A note here answers
"how does the model actually behave", which outlives both.

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
