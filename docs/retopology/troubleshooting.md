# Retopology Troubleshooting

## Buttons are disabled

Confirm that:

- Blender is in Object Mode;
- the active object is a mesh;
- a Quad Engine job is not already running.

## The engine does not start

Check that:

- the complete packaged extension was installed;

## The result loses detail

Try:

1. raising Target Remesh Tris;
2. using Slow Retopology;
3. reducing or disabling Pre Smooth;
5. inspecting the QEM debug mesh.

## Cancel an operation

Select **Cancel Active Operation** while a job is running.
