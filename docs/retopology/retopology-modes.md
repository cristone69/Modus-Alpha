# Fast and Slow Retopology

Select one mesh object in Object Mode before running either operation.

## Fast Retopology

Fast Retopology uses a 1× QEM proxy and is intended for the quickest result.

Use it for:

- early testing;
- rough targets;
- faster iteration.

## Slow Retopology

Slow Retopology uses a 2× QEM proxy with greater detail preservation.

Use it when:

- the Fast result loses too much form;
- additional processing time is acceptable;
- the source contains finer surface information.

## Target Remesh Tris

This is an approximate final triangle budget. The engine automatically scales the working target when symmetry is enabled.

The default is 30,000, with a minimum of 1,000.
