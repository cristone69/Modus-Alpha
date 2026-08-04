# SPDX-License-Identifier: GPL-3.0-or-later


def draw_mode_tools(layout, context, presentation="MENU"):
    if context.mode == 'EDIT_MESH':
        scene = context.scene.modus_modeling

        if presentation == 'MENU':
            topology = context.scene.modus_topology
            view_row = layout.row(align=True)
            view_row.operator(
                'modus.toggle_topology_highlight',
                text='Live N-gon/Tri',
                icon='OVERLAY',
                depress=topology.enabled,
            )
            view_row.operator(
                'modus.toggle_retopology_overlay',
                text='Retopo View',
                icon='MOD_SHRINKWRAP',
                depress=context.space_data.overlay.show_retopology,
            )
            layout.separator()

        mode_box = layout.column(align=True)
        mode_box.label(text='Edge Mode' if presentation == 'PANEL' else 'Weight Mode')
        mode_row = mode_box.row(align=True)
        mode_row.prop(scene, 'edge_weight_mode', expand=True)
        marking_row = layout.row(align=True)
        marking_row.operator('modus.assign_weights_by_angle', text='Assign by Angle', icon='MOD_BEVEL')
        marking_row.operator('modus.clear_edge_markings', text='Clear Markings', icon='X')
        if presentation == 'PANEL':
            if scene.edge_weight_mode == 'BEVEL':
                mode_box.prop(scene, 'bevel_weight_value')
            elif scene.edge_weight_mode == 'CREASE':
                mode_box.prop(scene, 'crease_weight_value')
        layout.separator()
        cleanup_row = layout.row(align=True)
        cleanup_row.operator('modus.relax', text='Relax', icon='MOD_SMOOTH')
        cleanup_row.operator('modus.multi_grid_fill', text='Multi Grid Fill', icon='MESH_GRID')
        layout.operator('modus.clean_up', text='Clean Up', icon='BRUSH_DATA')
        layout.operator('modus.origin_to_selected', text='Set Origin', icon='OBJECT_ORIGIN')
        axis_row = layout.row(align=True)
        axis_row.label(text='', icon='MOD_MIRROR')
        for axis in ('X', 'Y', 'Z'):
            op = axis_row.operator('modus.toggle_mirror_axis', text=axis)
            op.axis = axis
    elif context.mode == 'OBJECT':
        layout.operator('modus.add_bevel', text='Bevel', icon='MOD_BEVEL')
        row = layout.row(align=True)
        row.operator('modus.apply_mirror_boolean', text='Apply M/B', icon='MOD_BOOLEAN')
        row.operator('modus.apply_bevel', text='Finalize Bevel', icon='MOD_BEVEL')
        layout.separator()
        from .. import uv_preview
        preview_row = layout.row(align=True)
        uv_preview.draw_material_preview(preview_row, context)
        uv_preview.draw_viewport_control(preview_row, context)
