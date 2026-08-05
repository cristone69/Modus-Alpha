# Installation

This page explains how to install, update, and remove Modus.

!!! note "Alpha software"
    
## Requirements

- Blender 5.2 or newer
- A packaged Modus `.zip` release
- Permission to install Blender extensions
- Windows to use the current bundled Modus Quad Engine

!!! tip
    Use the packaged release ZIP rather than GitHub's automatic **Source code ZIP**.

## Method 1: Drag the ZIP into Blender

1. Open Blender.
2. Locate the Modus `.zip` file in File Explorer.
3. Drag the ZIP directly into the Blender window.
4. Blender displays an installation prompt.
5. Select **OK**.
6. Wait for installation to finish.
7. Confirm that Modus is enabled.

!!! note
    Drag the ZIP file itself into Blender. Do not extract it first.

## Method 2: Install from Blender Preferences

1. Open Blender.
2. Select **Edit → Preferences**.
3. Open **Get Extensions**.
4. Open the menu in the upper-right corner.
5. Select **Install from Disk**.
6. Choose the Modus ZIP.
7. Select **Install from Disk**.
8. Confirm that Modus is enabled.

## Verify the installation

1. Open a 3D Viewport.
2. Press `N`.
3. Confirm that the **Modus** tab appears.
4. Switch between Object Mode and Edit Mode.
5. Press `Shift + Q` to confirm that the context-aware Modus menu opens.

## Windows executable notice

The current package contains:

```text
Modus Quad Engine.exe
```

The executable runs locally, receives temporary mesh data from Blender, generates a processed mesh, and returns the result to Blender. It does not provide a separate interface and is not required by most Python-only Modus tools.

Only install packages obtained from an official Modus release location.

## Updating Modus

A clean uninstall is recommended during alpha development.

1. Save your Blender project.
2. Open **Edit → Preferences → Get Extensions**.
3. Locate Modus and uninstall it.
4. Restart Blender.
5. Install the newer ZIP using either method above.

## Removing Modus

1. Open **Edit → Preferences → Get Extensions**.
2. Locate Modus.
3. Open its options menu.
4. Select **Uninstall**.
5. Restart Blender.

Removing Modus does not remove ordinary Blender objects, modifiers, materials, node groups, or geometry already saved in a `.blend` file.
