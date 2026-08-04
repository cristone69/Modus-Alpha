# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from ...core import header
from ...core.redraw import tag_view3d_redraw

DEFAULT_TRI_COLOR = (0.08, 0.80, 0.18, 0.40)
DEFAULT_NGON_COLOR = (0.015, 0.055, 0.22, 0.68)
POLL_INTERVAL = 0.05
IDLE_INTERVAL = 0.5

_draw_handle = None
_cache = {}
_shader = None
_timer_registered = False



def _highlight_colors(context):
    try:
        addon = context.preferences.addons.get(__package__.split('.modules', 1)[0])
        prefs = addon.preferences if addon else None
        if prefs is not None:
            return tuple(prefs.topology_tri_color), tuple(prefs.topology_ngon_color)
    except (AttributeError, ReferenceError, RuntimeError):
        pass
    return DEFAULT_TRI_COLOR, DEFAULT_NGON_COLOR

def _mesh_key(obj):
    return obj.data.as_pointer()


def _refresh_topology():
    _cache.clear()
    tag_view3d_redraw()


def _highlight_enabled():
    for scene in getattr(bpy.data, 'scenes', ()):
        settings = getattr(scene, 'modus_topology', None)
        if settings and settings.enabled:
            return True
    return False


def _topology_settings_update(settings, _context):
    if settings.enabled:
        _refresh_topology()
    else:
        _cache.clear()
        tag_view3d_redraw()


def _poll_edit_meshes():
    """Continuously snapshot Edit Mode through regular Mesh data."""
    if not _timer_registered:
        return None

    if not _highlight_enabled():
        if _cache:
            _cache.clear()
            tag_view3d_redraw()
        return IDLE_INTERVAL

    changed = False
    seen = set()

    for obj in getattr(bpy.data, 'objects', ()):
        if obj.type != 'MESH' or obj.mode != 'EDIT':
            continue
        key = _mesh_key(obj)
        if key in seen:
            continue
        seen.add(key)
        if _rebuild_object_snapshot(obj):
            changed = True

    for key in tuple(_cache):
        if key not in seen:
            _cache.pop(key, None)
            changed = True

    if changed:
        tag_view3d_redraw()
    return POLL_INTERVAL


def _rebuild_object_snapshot(obj):
    """Synchronize Edit Mode, then copy regular Mesh data into Python values."""
    key = _mesh_key(obj)
    if not obj.visible_get() or obj.mode != 'EDIT':
        return _cache.pop(key, None) is not None

    try:
        obj.update_from_editmode()
        mesh = obj.data
        mesh.calc_loop_triangles()
        matrix_world = obj.matrix_world.copy()

        tri_positions = []
        ngon_positions = []
        triangle_faces = set()
        ngon_faces = set()

        for triangle in mesh.loop_triangles:
            polygon_index = triangle.polygon_index
            if polygon_index >= len(mesh.polygons):
                continue
            polygon = mesh.polygons[polygon_index]
            if polygon.hide:
                continue
            side_count = polygon.loop_total
            if side_count == 3:
                destination = tri_positions
                triangle_faces.add(polygon_index)
            elif side_count > 4:
                destination = ngon_positions
                ngon_faces.add(polygon_index)
            else:
                continue

            for vertex_index in triangle.vertices:
                if vertex_index >= len(mesh.vertices):
                    continue
                world_co = matrix_world @ mesh.vertices[vertex_index].co
                destination.append((world_co.x, world_co.y, world_co.z))
    except (AttributeError, ReferenceError, RuntimeError, ValueError, IndexError):
        # Keep the previous valid snapshot and retry on the next timer pass.
        return False

    previous = _cache.get(key, {})
    tri_positions = tuple(tri_positions)
    ngon_positions = tuple(ngon_positions)
    triangle_count = len(triangle_faces)
    ngon_count = len(ngon_faces)

    if (
        previous.get('tri_positions') == tri_positions
        and previous.get('ngon_positions') == ngon_positions
        and previous.get('triangle_count') == triangle_count
        and previous.get('ngon_count') == ngon_count
    ):
        return False

    _cache[key] = {
        'tri_positions_pending': tri_positions,
        'ngon_positions_pending': ngon_positions,
        'tri_positions': tri_positions,
        'ngon_positions': ngon_positions,
        'tri_batch': previous.get('tri_batch'),
        'ngon_batch': previous.get('ngon_batch'),
        'triangle_count': triangle_count,
        'ngon_count': ngon_count,
        'gpu_dirty': True,
    }
    return True


def _commit_pending_gpu_batches():
    """Upload already-copied coordinates; never access Blender mesh data here."""
    for item in tuple(_cache.values()):
        if not item.get('gpu_dirty'):
            continue
        tri_positions = item.pop('tri_positions_pending', ())
        ngon_positions = item.pop('ngon_positions_pending', ())
        try:
            item['tri_batch'] = batch_for_shader(
                _shader, 'TRIS', {'pos': tri_positions}
            ) if tri_positions else None
            item['ngon_batch'] = batch_for_shader(
                _shader, 'TRIS', {'pos': ngon_positions}
            ) if ngon_positions else None
            item['gpu_dirty'] = False
        except (ReferenceError, RuntimeError, ValueError):
            # Leave it dirty so a later draw can retry with an active context.
            item['tri_positions_pending'] = tri_positions
            item['ngon_positions_pending'] = ngon_positions


def _draw_callback():
    context = bpy.context
    if context.mode != 'EDIT_MESH' or context.area is None:
        return
    settings = context.scene.modus_topology
    if not settings.enabled:
        return
    _commit_pending_gpu_batches()
    gpu.state.depth_test_set('NONE')
    gpu.state.face_culling_set('BACK')
    gpu.state.blend_set('ALPHA_PREMULT')
    try:
        tri_color, ngon_color = _highlight_colors(context)
        _shader.bind()
        _shader.uniform_float('color', tri_color)
        for item in _cache.values():
            if item['tri_batch'] is not None:
                item['tri_batch'].draw(_shader)
        _shader.uniform_float('color', ngon_color)
        for item in _cache.values():
            if item['ngon_batch'] is not None:
                item['ngon_batch'].draw(_shader)
    finally:
        gpu.state.blend_set('NONE')
        gpu.state.face_culling_set('NONE')
        gpu.state.depth_test_set('NONE')


def _topology_counts(context):
    if context.mode != 'EDIT_MESH':
        return (0, 0)
    return (
        sum(item.get('triangle_count', 0) for item in _cache.values()),
        sum(item.get('ngon_count', 0) for item in _cache.values()),
    )


class MODUS_TopologySettings(bpy.types.PropertyGroup):
    enabled: bpy.props.BoolProperty(
        name='Topology Highlight',
        default=False,
        update=_topology_settings_update,
    )
    show_counts: bpy.props.BoolProperty(name='Topology Counts', default=True, update=lambda _s, _c: tag_view3d_redraw())


class MODUS_OT_toggle_topology_highlight(bpy.types.Operator):
    bl_idname = 'modus.toggle_topology_highlight'
    bl_label = 'Toggle Topology Highlight'
    bl_description = 'Enable or disable live triangle and n-gon highlighting'
    bl_options = {'INTERNAL'}
    def execute(self, context):
        settings = context.scene.modus_topology
        settings.enabled = not settings.enabled
        tag_view3d_redraw()
        return {'FINISHED'}


class MODUS_OT_toggle_retopology_overlay(bpy.types.Operator):
    bl_idname = 'modus.toggle_retopology_overlay'
    bl_label = 'Toggle Retopology Overlay'
    bl_description = "Toggle Blender's Retopology viewport overlay"
    bl_options = {'INTERNAL'}
    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D' and context.mode == 'EDIT_MESH'
    def execute(self, context):
        overlay = context.space_data.overlay
        overlay.show_retopology = not overlay.show_retopology
        context.area.tag_redraw()
        return {'FINISHED'}


class VIEW3D_PT_modus_topology(bpy.types.Panel):
    bl_label = 'Topology Highlight'
    bl_idname = 'VIEW3D_PT_modus_topology'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Modus'
    bl_context = 'mesh_edit'
    def draw(self, context):
        layout = self.layout
        settings = context.scene.modus_topology
        layout.prop(settings, 'enabled')
        layout.prop(settings, 'show_counts')
        layout.separator()
        layout.prop(context.space_data.overlay, 'show_retopology', text='Retopology View')


def _draw_header(row, context, probe=False):
    if context.mode != 'EDIT_MESH':
        return False
    if probe:
        return True
    settings = context.scene.modus_topology
    row.operator('modus.toggle_topology_highlight', text='', icon='OVERLAY', depress=settings.enabled)
    row.operator('modus.toggle_retopology_overlay', text='', icon='MOD_SHRINKWRAP', depress=context.space_data.overlay.show_retopology)
    if settings.show_counts and settings.enabled:
        triangle_count, ngon_count = _topology_counts(context)
        row.separator(factor=0.35)
        row.label(text=f'T: {triangle_count}  N: {ngon_count}')
    return True


_CLASSES = (
    MODUS_TopologySettings,
    MODUS_OT_toggle_topology_highlight,
    MODUS_OT_toggle_retopology_overlay,
    VIEW3D_PT_modus_topology,
)


def register():
    global _draw_handle, _shader, _timer_registered
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.modus_topology = bpy.props.PointerProperty(type=MODUS_TopologySettings)
    _shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    _draw_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_callback, (), 'WINDOW', 'POST_VIEW')
    _timer_registered = True
    if not bpy.app.timers.is_registered(_poll_edit_meshes):
        bpy.app.timers.register(_poll_edit_meshes, first_interval=POLL_INTERVAL, persistent=True)
    header.register_entry('topology', _draw_header, order=10)
    _refresh_topology()


def unregister():
    global _draw_handle, _shader, _timer_registered
    header.unregister_entry('topology')
    _timer_registered = False
    if bpy.app.timers.is_registered(_poll_edit_meshes):
        bpy.app.timers.unregister(_poll_edit_meshes)
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    if hasattr(bpy.types.Scene, 'modus_topology'):
        del bpy.types.Scene.modus_topology
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    _cache.clear()
    _shader = None
