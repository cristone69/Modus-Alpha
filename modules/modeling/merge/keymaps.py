# SPDX-License-Identifier: GPL-3.0-or-later

KEYMAP_DEFINITIONS = (
    {
        'group': 'MERGE',
        'keymap': 'Mesh',
        'space_type': 'EMPTY',
        'idname': 'modus.merge',
        'type': 'ONE',
        'value': 'PRESS',
        'shift': True,
        'properties': {'merge_type': 'LAST'},
        'label': 'Merge to Last',
    },
    {
        'group': 'MERGE',
        'keymap': 'Mesh',
        'space_type': 'EMPTY',
        'idname': 'modus.merge',
        'type': 'ONE',
        'value': 'PRESS',
        'alt': True,
        'properties': {'merge_type': 'CENTER'},
        'label': 'Merge to Center',
    },
)
