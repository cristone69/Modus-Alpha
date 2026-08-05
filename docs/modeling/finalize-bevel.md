# Finalize Bevel

Finalize Bevel applies a supported Bevel modifier and attempts to repair specific n-gons introduced by a two-segment bevel workflow.

## Intended workflow

The supported setup is based around:

- a Bevel modifier;
- two bevel segments;
- profile 1;
- supported corner and T-junction layouts.

## Mark UVs

The optional **Mark UVs** setting creates center-line seams while finalizing the bevel. It is disabled by default.

## Limitations

Finalize Bevel is not a universal n-gon remover. It targets recognized bevel-generated patterns and reports compatibility warnings when the modifier or topology is unsupported.

Save the file or duplicate the object before using it on production geometry.
