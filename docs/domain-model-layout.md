# The domain model canvas

Laying out a domain model well is not a modelling problem, it is a geometry
problem — and the model stores only about half the geometry. This note is what
the `.mxunit` keeps, what it leaves to Studio Pro to compute, and the
measurements that recover enough of the missing half to draw with.

Measured on Studio Pro 10.24.13, on an app of ~5,750 units with 73 domain
models, 634 entities and 688 associations. The canvas figures come from
purpose-built entities in a scratch module, moved by hand in the IDE; the
distributions come from sweeping every domain model in the app.

## What is stored, in full

An **entity** carries exactly one piece of geometry:

```python
{
  "$Type": "DomainModels$EntityImpl",
  "Name": "MyEntity",
  "Location": "290;240",      # the TOP-LEFT corner, 'X;Y', semicolon
  # ... no Width, no Height, no bounding box of any kind
}
```

An **annotation** carries a width, and only a width:

```python
{
  "$Type": "DomainModels$Annotation",
  "Caption": "some text",
  "Location": "0;3380",
  "Width": 250,               # no Height — it follows from the text wrapping
}
```

An **association** carries where its line meets each box:

```python
{
  "$Type": "DomainModels$Association",
  "Name": "MyEntity_MyOtherEntity",
  "ParentConnection": "0;86",     # percent of the parent box's edge
  "ChildConnection": "100;36",    # percent of the child box's edge
}
```

That is the whole of it. There is no waypoint list, no route, no label
position, no font size and no zoom. Everything else on screen is computed by
the IDE at paint time and never written down.

## The grid step is 10

Two identical empty entities, same `X`, the second nudged down by the smallest
move the IDE allows:

```
Step1   '350;350'
Step2   '350;360'
```

Coordinates are absolute, not offsets, and the step is **10** in both axes. The
sweep agrees: of 634 entities, 631 sit on a multiple of 10 in `X` and 629 in
`Y`. The handful that do not are older than some grid change and are not worth
reading anything into.

## The anchor is a percentage, and `Y` grows downward

Both axes run 0–100 as a percentage of the box's own edge. Across 938 measured
values, **none** fell outside that range in either axis.

The direction was fixed by a deliberate edit: both ends of one association were
dragged **downward** in the IDE, and the before/after came out of git.

```
parent end    '0;0'     ->  '0;86'
child end     '100;14'  ->  '100;36'
```

Both `Y` values rose, so:

| | |
|---|---|
| `X` = 0 | left edge |
| `X` = 100 | right edge |
| `Y` = 0 | top edge |
| `Y` = 100 | bottom edge |

Dragging along an edge leaves the other axis untouched — the two are
independent. Worth stating because the opposite convention (`Y` upward from the
baseline) is the one a plotting library would use, and guessing it backwards
costs a whole layout.

## 🔴 The anchor is frozen: moving an entity does not recalculate it

This is the finding that matters, and it contradicts the obvious assumption
that the line is derived from the two positions.

Against the before/after oracle for entity moves — one case moving a single
entity, one moving four at once — **not one anchor changed**, including all 22
associations that touch the four moved entities. The anchor is wherever
somebody dragged it when the association was drawn, and it survives every move
afterwards.

The consequence is sharp: **aligning the tops of two boxes does not give a
horizontal line.** With real stored anchors like `('0;100', '100;0')` — leaving
the bottom of one box and arriving at the top of the other — the line is
diagonal no matter how perfectly the boxes line up. In the sweep, only 349 of
938 anchors sat at the top edge; 165 sat at the bottom and 424 somewhere in the
middle.

So a layout pass that writes `Location` alone is working on the wrong number
for the thing it is trying to fix.

## ⚠️ A `CrossAssociation` has no anchor at all

219 of the 688 associations measured — **32%** — are `CrossAssociation`, and
they carry neither `ParentConnection` nor `ChildConnection`, only `Child` and
`ParentPointer` (see [moving-an-association-end.md](moving-an-association-end.md)
for why those two ends are stored differently). For those the line really is
derived, and there is nothing to write. Any layout work that depends on setting
anchors covers at most the two-thirds of associations that stay inside one
module.

## Box height: not stored, but recoverable

Width and height are computed by the IDE from the font and the text. Height can
be measured anyway, with a ruler: park a second entity directly below the
first, as close as it goes without overlapping, and read the gap. Because both
sit on the grid, each reading is an interval, not an equality:

```
gap = 10 * ceil(height / 10)
```

Three rulers, against entities differing only in attribute count:

| attributes | gap | note |
|---|---|---|
| 0 | 80 | ruler visually touching |
| 4 | 100 | ruler at the closest non-overlapping step |
| 5 | 110 | ruler visually touching |

Solving all three together — sweeping base height, plateau and increment in
steps of 0.5 — leaves 61 surviving combinations:

```
base height : 79.0 .. 80.0
plateau     : [3]            <- the only value that survives
increment   : 14.5 .. 15.5
```

```python
height(n) = 80 + 15 * max(0, n - 3)
```

A box reserves room for three attribute rows before it starts growing, so an
entity with 0, 1, 2 or 3 attributes is exactly as tall. **Growth from the first
attribute is refuted, not merely unlikely**: it survives zero combinations. The
reading that kills it is the 4-attribute one — linear growth would put that box
at ~104 and its ruler at 110, and the ruler is at 100.

### ⚠️ The increment is not a multiple of the grid, so rounding is unavoidable

15 against a grid of 10 makes the leftover alternate, and it never cancels:

| attributes | height | ruler lands on | leftover |
|---|---|---|---|
| 0–3 | 80 | 80 | 0 |
| 4 | 95 | 100 | **5** |
| 5 | 110 | 110 | 0 |
| 6 | 125 | 130 | **5** |
| 7 | 140 | 140 | 0 |

Any caller that reasons about where a box *ends* has to accept being up to 5
out, on every entity with an even attribute count from 4 up. That is a fact
about the canvas, not a defect in the measurement — there is no grid-aligned
answer to round to.

Two things stay open, and neither is guessed at here:

- The increment is pinned only to **14.5–15.5**. A ruler under a 16-attribute
  entity separates those three (270 / 280 / 290); 20 attributes separates them
  comfortably (330 / 340 / 350).
- **Width is not measured at all.** It depends on the longest string in the box,
  and a generalization from another module is drawn as a bar *above* the box,
  wider than the box, carrying a qualified name — so the widest string is often
  not the entity's own name. Nothing here should be used to predict width.

## What this buys

To make a line horizontal, do not try to predict where the boxes end — **write
the anchors**. `'100;0'` on the left-hand box and `'0;0'` on the right-hand one
puts both ends at the top edge, which is horizontal whenever the two `Location`
values share a `Y`. It needs no height at all, which is the point: it trades
the quantity the model does not store for one it does.

🔴 **Writing an anchor has never been tested against Studio Pro.** Everything
above is read-side measurement. Whether the IDE accepts a hand-written
`ParentConnection`, redraws from it, or quietly recomputes it on the next open
is unknown, and it needs its own before/after case before any of it becomes an
operation.
