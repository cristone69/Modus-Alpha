# Retopology Settings

## Symmetry

Enable one object-local symmetry axis:

- X
- Y
- Z

The axes are mutually exclusive. The engine splits, mirrors, and welds across the selected axis.

## Sharp Edges as Boundaries

Uses Blender edges marked **Sharp** as Quad Engine boundaries.

## Pre Smooth

Smooths a temporary copy of the source before processing.

- Off by default
- 0–30 iterations
- Default when enabled: 15

The original object is not destructively pre-smoothed.

## Multiresolution

Adds Multiresolution and Shrinkwrap modifiers to the result for subdivision-surface projection.

## Debug options

The collapsed Debug section includes:

- import the post-QEM triangle mesh;
- enable or disable quad smoothing;
- quad-smoothing iterations from 1 to 300.

Debug settings are primarily intended for diagnosing where detail or sharp features were lost.
