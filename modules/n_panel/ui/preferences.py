# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations


def draw_preferences(prefs, _context, layout):
    section = layout.box()
    section.label(text='N-Panel Filtering')

    controls = section.column(align=True)
    controls.enabled = prefs.npanel_feature_enabled
    controls.prop(prefs, 'npanel_hide_uncategorized')
    section.separator()

    for category_index, category in enumerate(prefs.npanel_categories):
        box = section.box()
        row = box.row(align=True)
        row.prop(
            category,
            'expanded',
            text='',
            icon='TRIA_DOWN' if category.expanded else 'TRIA_RIGHT',
            emboss=False,
        )
        row.prop(category, 'name', text='')
        icon_name = category.icon or 'BOOKMARKS'
        icon_op = row.operator(
            'modus.npanel_open_icon_picker',
            text='',
            icon=icon_name,
        )
        icon_op.category_index = category_index

        move_up = row.operator('modus.npanel_move_category', text='', icon='TRIA_UP')
        move_up.category_index = category_index
        move_up.direction = -1
        move_down = row.operator('modus.npanel_move_category', text='', icon='TRIA_DOWN')
        move_down.category_index = category_index
        move_down.direction = 1
        remove = row.operator('modus.npanel_remove_category', text='', icon='X')
        remove.category_index = category_index

        if category.expanded:
            body = box.column(align=True)
            for tab_index, tab in enumerate(category.tabs):
                tab_row = body.row(align=True)
                tab_row.label(text=tab.name, icon='RIGHTARROW')
                op = tab_row.operator('modus.npanel_remove_tab', text='', icon='X')
                op.category_index = category_index
                op.tab_index = tab_index
            add = body.operator('modus.npanel_add_tab', text='Add N-Panel Tabs', icon='ADD')
            add.category_index = category_index

    section.separator()
    section.operator('modus.npanel_add_category', text='Create Category', icon='ADD')
