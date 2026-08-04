# Installation

This page explains how to install, update, and remove Modus.

!!! warning "Alpha software"


## Requirements

Before installing Modus, confirm that you have:

- Blender 5.2 or newer
- A packaged Modus `.zip` release
- Permission to install Blender extensions
- Windows if you intend to use the current Modus Quad Engine build


!!! tip
    Use the packaged Modus release ZIP rather than GitHub's automatically generated **Source code ZIP**. The packaged release is prepared for installation in Blender.

## Method 1: Drag the ZIP into Blender

This is the fastest installation method.

1. Open Blender.
2. Locate the Modus `.zip` file in File Explorer.
3. Drag the ZIP file directly into the Blender window.
4. Blender will display an installation prompt.
5. Select **OK**.
6. Wait for Blender to complete the installation.
7. Confirm that Modus is enabled.

!!! note
    Drag the ZIP file itself into Blender. Do not drag an extracted folder.

## Method 2: Install from Blender Preferences

1. Open Blender.
2. Select **Edit → Preferences**.
3. Open the **Get Extensions** section.
4. Open the menu in the upper-right corner.
5. Select **Install from Disk**.
6. Locate the downloaded Modus `.zip` file.
7. Select the ZIP and press **Install from Disk**.
8. Confirm that Modus is enabled.

After installation, close the Preferences window.

## Opening Modus

### Modus N-panel

1. Move the mouse over the 3D Viewport.
2. Press `N` to open the sidebar.
3. Select the **Modus** tab.

Some panels only appear in the Blender modes where they can be used. For example, the Modus Quad Engine appears in Object Mode.

### Modus menus and shortcuts

Modus includes context-sensitive menus and shortcuts. Their availability may depend on the active mode, selection, and enabled components.

See [Keyboard Shortcuts](shortcuts.md) for the current shortcut list.

## Windows executable notice

The current Modus package contains:

```text
Modus Quad Engine.exe
```

This executable performs external mesh processing for the Modus retopology tools.


The Quad Engine:

- runs locally on your computer;
- receives temporary mesh data from Blender;
- generates a retopology result;
- returns the result to Blender;
- does not provide a separate user interface;
- is not required by most Python-based Modus tools.

Only install Modus packages obtained from an official Modus release location.

!!! info "Source and licensing"
    The Modus Quad Engine is based on GPL-covered open-source technology. Matching source and license notices will be provided with official distributed releases.


## Verify the installation

After installation:

1. Open a 3D Viewport.
2. Press `N`.
3. Confirm that the **Modus** tab appears.
4. Switch between Object Mode and Edit Mode to check context-sensitive panels.


## Updating Modus

A clean uninstall is recommended but not required.

1. Save your Blender project.
2. Open **Edit → Preferences → Get Extensions**.
3. Locate Modus.
4. Uninstall the installed version.
5. Restart Blender.
6. Install the new Modus ZIP using either installation method above.

!!! warning
    Installing a new alpha over an older alpha may leave obsolete files behind. A clean reinstall is recommended but not required.

## Removing Modus

1. Open **Edit → Preferences**.
2. Open **Get Extensions**.
3. Locate Modus.
4. Open its options menu.
5. Select **Uninstall**
6. Restart Blender.

Removing Modus does not remove standard Blender objects, modifiers, materials, node groups, or geometry already saved inside a `.blend` file.

## Installation troubleshooting

### Blender reports that Modus is incompatible

Confirm that you are using Blender 5.2 or newer.

### Drag-and-drop does nothing

Confirm that:

- you dragged the `.zip` file, not an extracted folder;
- the mouse was released over the Blender window;
- the ZIP is a packaged Modus release;
- Blender is not currently showing another blocking dialog.

You can use **Install from Disk** as an alternative.

### The ZIP cannot be installed

Confirm that:

- the download completed successfully;
- the ZIP contains a valid `blender_manifest.toml`;
- the package is not wrapped inside an extra outer folder;
- you did not use GitHub's automatic source archive in place of the packaged extension ZIP.

### Modus is installed, but the interface is missing

Try the following:

1. Restart Blender.
2. Confirm that Modus is enabled.
3. Open a 3D Viewport.
4. Press `N` and look for the Modus tab.
5. Switch to Object Mode or Edit Mode.
6. Check Blender's system console for an error.

### The Quad Engine does not run

Confirm that:

- you are using a supported Windows build;
- the complete Modus package was installed;
- the selected object is a supported mesh object.