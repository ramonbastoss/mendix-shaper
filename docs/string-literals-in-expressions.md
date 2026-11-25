# String literals in an expression

A single-quoted string literal in a microflow expression **can contain literal
newlines**. Measured on Studio Pro 10.24.13: a multi-line literal pasted into
the expression editor is accepted, and `mx check` reports nothing against it.

So a block of data embedded in a flow — a lookup table handed to an import
mapping, a template, any fixed document — can be written as one literal, laid
out over as many lines as it needs:

```
'[
    { "id": 1, "name": "..." },
    { "id": 2, "name": "..." }
]'
```

## Why this is written down: the opposite was assumed

Before it was measured, an embedded JSON document was built as a concatenation
of one literal per record — `'[' + '{...},' + '{...}' + ']'` — specifically to
keep every newline *outside* the quotes. That form works and stays readable,
but it is not required, and it puts a `+` on every line of what is otherwise
just data.

🔑 **The guess was avoidable, and that is the lesson worth keeping.**
`mx check` reports `CE0117 "Error(s) in expression"`, so "does the editor
accept this?" was always one check away from being an answer instead of an
assumption. Routing around an unmeasured doubt is the right call when measuring
is expensive; here it cost a couple of minutes. Prefer measuring whenever the
oracle is this cheap — see [risk-profile.md](risk-profile.md) for the ones that
are not.

## Not the same thing: newlines *between* tokens

A newline around `if` / `then` / `else`, or between operands, is also accepted.
That one was never in doubt — it can be observed in flows the IDE itself wrote,
rather than assumed — and it is what makes the concatenation form readable in
the first place. The measurement above is about newlines **inside** the quotes,
which is a different question with a different answer source.

## Scope

Measured on the expression editor of a microflow activity. Not measured: page
expressions, which are more restrictive than microflow expressions in at least
one other respect, and validation-rule expressions.
