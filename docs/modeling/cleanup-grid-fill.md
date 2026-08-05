# Clean Up, Relax, and Multi Grid Fill

## Clean Up

**Shortcut:** `Shift + 3`

Clean Up removes:

- exact duplicate vertices;
- loose edges;
- loose vertices.

## Relax

Relax evens out selected topology while preserving its surface.

Its settings include iteration strength and protection for mesh boundaries or selection borders.

## Multi Grid Fill

Multi Grid Fill fills between 2 and 20 selected closed edge loops.

Requirements:

- every loop must be closed;
- every loop must have the same edge count;
- the loops must be selected in Edit Mode.

Settings include:

- **Span:** controls the grid layout;
- **Offset:** rotates grid corners around the loops;
- **Clone Master Grid:** fills the last-selected master loop, then copies that exact topology to the other loops.

Modus restricts Span to valid values for the detected loop size.
