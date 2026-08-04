# SPDX-License-Identifier: GPL-3.0-or-later

KEYMAP_DEFINITIONS = (
    {
        'group': 'SYMMETRIZE',
        'label': 'Symmetrize',
        'keymap': 'Mesh',
        'idname': 'modus.symmetrize',
        'type': 'X',
        'value': 'PRESS',
        'alt': True,
        # Alt+X is also used by some modeling add-ons. Insert Modus at the
        # front of the addon Mesh keymap so startup order cannot randomly win.
        'head': True,
    },
)
