# Changelog

## 1.2.1

- Added Pre Smooth to Auto Retopology.
- Changed naming conventions to Auto Retopology.

## 1.2.0

- Added Modus Quad Engine to the Object Mode Modus N-panel.
- Added Fast Retopology and Slow Retopology.
- Added Quick Decimate.
- Added symmetry, sharp boundaries, quad smoothing, and Multiresolution/Shrinkwrap projection.
- Added background processing, cancellation, automatic import, and hidden storage for original meshes.

## 1.1.10

- Fixed Flick Symmetrize so invoking it no longer depends on the active object origin being projectable inside the viewport.
- Rebuilt the Flick Symmetrize direction guide around the invocation cursor using the active object's local X, Y, and Z axes, with signed axis labels and a viewport-safe vertical settings panel.
- Made the Flick Symmetrize Alt+X shortcut registration deterministic and prevented duplicate Modus keymap entries after add-on reloads.
- Added Clear Edge Markings beside Assign by Angle. It clears the enabled Bevel Weight, Crease, Seam, and Sharp markings from every edge of all meshes currently in Edit Mode.
- Added independent Bevel Weight, Crease, Seam, and Sharp controls to Clear Edge Markings in Adjust Last Operation.
- Updated Force Loop with a vertical backed-panel HUD
- Added a modal C toggle and Center at Target Edge redo option for placing a forced loop at the exact midpoint of the hovered edge.
- Removed the redundant Object Mode Subdivision command and its custom modifier-management operator

## 1.1.9

- Refined Multi Grid Fill 
- Multi Grid Fill now limits Span to the valid range for the detected loop size, preventing unsupported values from producing an empty result.
- Updated Multi Grid Fill labels, descriptions, comments, and completion messages to accurately describe the current behavior.

## 1.1.8

- Added Force Loop on Shift+Alt+R for straight, connected 3D plane cuts through quads, triangles, and n-gons.
- Force Loop derives its cutting plane from the hovered edge, remains perpendicular in world space, and does not jump to disconnected mesh regions.
- Expanded Flick Symmetrize with optional center triangle-to-quad cleanup and hidden center-plane face removal.
- Added persistent Flick Symmetrize defaults for center cleanup and context-based or fixed selection scope.
- Refined the Flick Symmetrize HUD and changed its axis guide to global scene orientation.


## 1.1.7

- Improved Multi Grid Fill rotation alignment for dense matching loops, including 32-edge circles.
- Flick Symmetrize now places its directional UI at the object origin and follows the object local rotation.
- Flick Symmetrize now checks selection context on every invocation: no selection defaults to the whole mesh, while any mesh selection defaults to Selected Only.


## 1.1.6

- Multi Grid Fill now makes a best-effort attempt to align the rotation of matching fills.


## 1.1.5

- Changed Quad Sphere and Quad Cylinder to use flat shading by default.
- Added persistent Triangle Color and N-gon Color preferences for the live topology highlighter.

## 1.1.4

- Added Live N-gon/Tri and Retopo View controls to the top of the Edit Mode Shift+Q menu.
- Expanded Edge Mode with Bevel, Crease, Seam, and Sharp controls on one row.
- Added Sharp assignment to Assign by Angle.
- Added Multi Grid Fill beside Relax for filling matching closed loops with shared Span and Offset settings.

## 1.1.3

- Expanded Finalize Bevel's T-junction detection to support the same diagonal-connected layout when an additional loop intersects the diagonal.


## 1.1.2

- Added T-junction bevel repair for diagonal-connected quad layouts.
- Finalize Bevel now creates two outer connections to the surviving diagonal endpoint and dissolves the obsolete center diagonal.
- Changed the minimum supported Blender version to 5.0.

## 1.1.1

- Added Assign by Angle beneath Weight Mode in the Edit Mode Shift+Q menu.
- Added Adjust Last Operation controls for Bevel Weight, Crease, or Seam assignment.
- Added a 30° default face-angle threshold with an editable Angle control.
- Matching edges are selected and assigned using the current Modus bevel or crease value.


## 1.1.0

- Added Finalize Bevels
- Added supported n-gon repair for applied two-segment Bevel modifiers.
- Added modifier compatibility warnings in the 3D Viewport.
- Added optional Mark UVs center-line seam creation, off by default.
- Added optimized spatial seam matching for fast UV seam transfer.
- 


## 1.0.0

- Initial public release of Modus.