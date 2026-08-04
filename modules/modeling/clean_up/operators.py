# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import bmesh
from mathutils import Vector
from mathutils.geometry import tessellate_polygon
import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from bpy.props import BoolProperty, FloatProperty, IntProperty


_MULTI_GRID_OVERLAY_HANDLE = None
_MULTI_GRID_OVERLAY = {
    'object_name': None,
    'master_positions': (),
    'master_batch': None,
    'gpu_dirty': False,
    'primitive': 'TRIS',
}
_MULTI_GRID_OVERLAY_SHADER = None

# Shared visual language with Modus' live triangle/n-gon highlighter.
# Use Blender's current selection accent exactly, without hue/value changes.
def _master_highlight_color(context):
    try:
        base = context.preferences.themes[0].view_3d.edge_select
        # Keep Blender's selection RGB exactly, but use a translucent fill so
        # the master grid edges remain clearly visible through the overlay.
        return (float(base[0]), float(base[1]), float(base[2]), 0.32)
    except (AttributeError, IndexError, TypeError):
        return (1.0, 0.25, 0.55, 0.32)


def _faces_to_world_positions(obj, faces):
    matrix = obj.matrix_world
    positions = []
    for face in faces:
        if not face.is_valid or len(face.verts) < 3:
            continue
        try:
            polygon = [vert.co.copy() for vert in face.verts]
            tessellation = tessellate_polygon([polygon])
        except (ReferenceError, RuntimeError, ValueError):
            continue
        for triangle in tessellation:
            if len(triangle) != 3:
                continue
            for item in triangle:
                # Blender versions may return either coordinate vectors or
                # polygon-local vertex indices from tessellate_polygon().
                coordinate = polygon[item] if isinstance(item, int) else item
                world = matrix @ coordinate
                positions.append((world.x, world.y, world.z))
    return tuple(positions)


def _edges_to_world_positions(obj, edges):
    matrix = obj.matrix_world
    positions = []
    for edge in edges:
        if not edge.is_valid:
            continue
        for vert in edge.verts:
            world = matrix @ vert.co
            positions.append((world.x, world.y, world.z))
    return tuple(positions)


def _set_multi_grid_overlay(obj, master_faces=None, master_edges=None):
    if master_edges is not None:
        positions = _edges_to_world_positions(obj, master_edges)
        primitive = 'LINES'
    else:
        positions = _faces_to_world_positions(obj, master_faces or ())
        primitive = 'TRIS'
    _MULTI_GRID_OVERLAY['object_name'] = obj.name
    _MULTI_GRID_OVERLAY['master_positions'] = positions
    _MULTI_GRID_OVERLAY['master_batch'] = None
    _MULTI_GRID_OVERLAY['gpu_dirty'] = True
    _MULTI_GRID_OVERLAY['primitive'] = primitive


def _clear_multi_grid_overlay():
    _MULTI_GRID_OVERLAY['object_name'] = None
    _MULTI_GRID_OVERLAY['master_positions'] = ()
    _MULTI_GRID_OVERLAY['master_batch'] = None
    _MULTI_GRID_OVERLAY['gpu_dirty'] = False
    _MULTI_GRID_OVERLAY['primitive'] = 'TRIS'


def _commit_multi_grid_overlay_batch():
    if not _MULTI_GRID_OVERLAY.get('gpu_dirty'):
        return
    positions = _MULTI_GRID_OVERLAY.get('master_positions', ())
    try:
        _MULTI_GRID_OVERLAY['master_batch'] = (
            batch_for_shader(
                _MULTI_GRID_OVERLAY_SHADER,
                _MULTI_GRID_OVERLAY.get('primitive', 'TRIS'),
                {'pos': positions},
            )
            if positions else None
        )
        _MULTI_GRID_OVERLAY['gpu_dirty'] = False
    except (ReferenceError, RuntimeError, ValueError):
        # Retry on a later draw with a fully active GPU context.
        pass


def _draw_multi_grid_overlay():
    context = bpy.context
    obj = context.active_object
    if (
        context.mode != 'EDIT_MESH'
        or context.area is None
        or obj is None
        or obj.name != _MULTI_GRID_OVERLAY['object_name']
    ):
        return

    _commit_multi_grid_overlay_batch()
    batch = _MULTI_GRID_OVERLAY.get('master_batch')
    if batch is None:
        return

    # Match the proven live topology viewer drawing method. Drawing without a
    # depth test makes the master readable through the normal selection tint.
    gpu.state.depth_test_set('NONE')
    gpu.state.face_culling_set('BACK')
    gpu.state.blend_set('ALPHA_PREMULT')
    try:
        if _MULTI_GRID_OVERLAY.get('primitive') == 'LINES':
            gpu.state.line_width_set(4.0)
        _MULTI_GRID_OVERLAY_SHADER.bind()
        _MULTI_GRID_OVERLAY_SHADER.uniform_float(
            'color', _master_highlight_color(context)
        )
        batch.draw(_MULTI_GRID_OVERLAY_SHADER)
    finally:
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set('NONE')
        gpu.state.face_culling_set('NONE')
        gpu.state.depth_test_set('NONE')


def register_overlay():
    global _MULTI_GRID_OVERLAY_HANDLE, _MULTI_GRID_OVERLAY_SHADER
    if _MULTI_GRID_OVERLAY_SHADER is None:
        _MULTI_GRID_OVERLAY_SHADER = gpu.shader.from_builtin('UNIFORM_COLOR')
    if _MULTI_GRID_OVERLAY_HANDLE is None:
        _MULTI_GRID_OVERLAY_HANDLE = bpy.types.SpaceView3D.draw_handler_add(
            _draw_multi_grid_overlay, (), 'WINDOW', 'POST_VIEW'
        )


def unregister_overlay():
    global _MULTI_GRID_OVERLAY_HANDLE, _MULTI_GRID_OVERLAY_SHADER
    if _MULTI_GRID_OVERLAY_HANDLE is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(
                _MULTI_GRID_OVERLAY_HANDLE, 'WINDOW'
            )
        except (ReferenceError, RuntimeError, ValueError):
            pass
        _MULTI_GRID_OVERLAY_HANDLE = None
    _MULTI_GRID_OVERLAY_SHADER = None
    _clear_multi_grid_overlay()


def relax_bmesh_vertices(
    bm,
    vertices,
    *,
    pinned_vertices=(),
    iterations=5,
    strength=1.0,
    preserve_surface=True,
):
    """Relax a BMesh vertex set while keeping explicitly pinned vertices fixed.

    This is the shared implementation used by the interactive Relax operator
    and generated topology such as Quad Cylinder caps.
    """
    selected = {vert for vert in vertices if vert.is_valid and not vert.hide}
    pinned = {vert for vert in pinned_vertices if vert.is_valid}
    movable = [vert for vert in selected if vert not in pinned and vert.link_edges]

    for _iteration in range(max(0, iterations)):
        if preserve_surface:
            bm.normal_update()

        targets = {}
        for vert in movable:
            neighbours = [
                edge.other_vert(vert)
                for edge in vert.link_edges
                if edge.is_valid and not edge.other_vert(vert).hide
            ]
            if not neighbours:
                continue

            average = sum(
                (neighbour.co for neighbour in neighbours),
                start=vert.co.copy() * 0.0,
            ) / len(neighbours)
            offset = average - vert.co

            if preserve_surface and vert.normal.length_squared:
                normal = vert.normal.normalized()
                offset -= normal * offset.dot(normal)

            targets[vert] = vert.co + offset * strength

        for vert, target in targets.items():
            if vert.is_valid:
                vert.co = target

    if movable:
        bm.normal_update()

    return len(movable)


class MODUS_OT_clean_up(bpy.types.Operator):
    bl_idname = 'modus.clean_up'
    bl_label = 'Clean Up'
    bl_description = 'Remove exact duplicate vertices, loose edges, and loose vertices'
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.active_object is not None

    def execute(self, context):
        objects = {
            obj
            for obj in context.objects_in_mode_unique_data
            if obj.type == 'MESH' and obj.mode == 'EDIT'
        }
        if not objects:
            return {'CANCELLED'}

        removed_vertices = 0

        for obj in objects:
            bm = bmesh.from_edit_mesh(obj.data)
            before_vertices = len(bm.verts)

            # Zero distance means only vertices at exactly identical coordinates
            # are welded. Nearby vertices and tiny polygon details are preserved.
            if bm.verts:
                bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=0.0)

            loose_edges = [edge for edge in bm.edges if not edge.link_faces]
            if loose_edges:
                bmesh.ops.delete(bm, geom=loose_edges, context='EDGES')

            loose_verts = [vert for vert in bm.verts if not vert.link_edges]
            if loose_verts:
                bmesh.ops.delete(bm, geom=loose_verts, context='VERTS')

            removed_vertices += before_vertices - len(bm.verts)
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=True)

        noun = 'vertex' if removed_vertices == 1 else 'vertices'
        self.report({'INFO'}, f'Clean Up: Removed {removed_vertices} {noun}')
        return {'FINISHED'}


class MODUS_OT_relax(bpy.types.Operator):
    bl_idname = 'modus.relax'
    bl_label = 'Relax'
    bl_description = 'Even out selected topology while preserving its surface'
    bl_options = {'REGISTER', 'UNDO'}

    iterations: IntProperty(
        name='Iterations',
        description='Number of relaxation passes',
        default=5,
        min=1,
        max=100,
    )
    strength: FloatProperty(
        name='Strength',
        description='Influence of each relaxation pass',
        default=1.0,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
    )
    preserve_surface: BoolProperty(
        name='Preserve Surface',
        description='Remove movement along the vertex normal to reduce shrinkage',
        default=True,
    )
    keep_mesh_boundary: BoolProperty(
        name='Keep Mesh Boundary',
        description='Do not move vertices on open or non-manifold mesh boundaries',
        default=True,
    )
    keep_selection_border: BoolProperty(
        name='Keep Selection Border',
        description='Do not move vertices along the outside border of the selection',
        default=True,
    )

    @classmethod
    def poll(cls, context):
        return (
            context.mode == 'EDIT_MESH'
            and context.active_object is not None
            and context.active_object.type == 'MESH'
        )

    def execute(self, context):
        objects = [
            obj
            for obj in context.objects_in_mode_unique_data
            if obj.type == 'MESH' and obj.mode == 'EDIT'
        ]

        selected_total = 0
        movable_total = 0

        for obj in objects:
            bm = bmesh.from_edit_mesh(obj.data)
            selected = {
                vert
                for vert in bm.verts
                if vert.select and not vert.hide
            }
            selected.update(
                vert
                for face in bm.faces
                if face.select and not face.hide
                for vert in face.verts
                if not vert.hide
            )
            selected_total += len(selected)
            if not selected:
                continue

            movable = [
                vert
                for vert in selected
                if self._can_move(vert, selected)
            ]
            movable_total += len(movable)

            relax_bmesh_vertices(
                bm,
                movable,
                iterations=self.iterations,
                strength=self.strength,
                preserve_surface=self.preserve_surface,
            )

            if movable:
                bmesh.update_edit_mesh(
                    obj.data,
                    loop_triangles=False,
                    destructive=False,
                )

        if not selected_total:
            self.report({'INFO'}, 'Relax: Select vertices or faces first')
            return {'CANCELLED'}

        if not movable_total:
            self.report(
                {'INFO'},
                'Relax: The selected vertices are protected by the boundary settings',
            )
            return {'FINISHED'}

        noun = 'vertex' if movable_total == 1 else 'vertices'
        self.report({'INFO'}, f'Relaxed {movable_total} {noun}')
        return {'FINISHED'}

    def _can_move(self, vert, selected):
        if self.keep_mesh_boundary and any(
            len(edge.link_faces) != 2
            for edge in vert.link_edges
        ):
            return False

        if self.keep_selection_border and any(
            edge.other_vert(vert) not in selected
            for edge in vert.link_edges
        ):
            return False

        return bool(vert.link_edges)


class MODUS_OT_multi_grid_fill_launcher(bpy.types.Operator):
    bl_idname = 'modus.multi_grid_fill'
    bl_label = 'Multi Grid Fill'
    bl_description = 'Open the live Multi Grid Fill settings menu'

    @classmethod
    def poll(cls, context):
        return (
            context.mode == 'EDIT_MESH'
            and context.active_object is not None
            and context.active_object.type == 'MESH'
        )

    def invoke(self, context, _event):
        # Launch directly from the original custom Shift-Q popup. Avoiding a
        # deferred timer lets Blender finish/dismiss the clicked parent popup
        # as part of the same UI event instead of leaving two popup windows.
        try:
            bpy.ops.modus.multi_grid_fill_menu('INVOKE_DEFAULT')
        except RuntimeError as exc:
            self.report({'WARNING'}, f'Could not open Multi Grid Fill: {exc}')
            return {'CANCELLED'}
        return {'FINISHED'}

    def execute(self, context):
        return self.invoke(context, None)


class MODUS_OT_multi_grid_fill(bpy.types.Operator):
    bl_idname = 'modus.multi_grid_fill_menu'
    bl_label = 'Multi Grid Fill'
    bl_description = (
        'Grid fill 2 to 20 selected closed edge loops that have the same edge count'
    )
    bl_options = {'UNDO'}

    span: IntProperty(
        name='Span',
        description='Number of boundary edges used for one side of the grid',
        default=0,
        min=0,
        max=1000,
    )
    offset: IntProperty(
        name='Offset',
        description='Rotate the grid corners around each selected loop',
        default=0,
        min=-1000,
        max=1000,
    )
    clone_master_grid: BoolProperty(
        name='Clone Master Grid',
        description=(
            'Fill the last-selected master loop with Blender Grid Fill, then copy that exact grid '
            'topology onto the remaining loops'
        ),
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return (
            context.mode == 'EDIT_MESH'
            and context.active_object is not None
            and context.active_object.type == 'MESH'
        )

    def draw(self, _context):
        layout = self.layout
        layout.label(text='Multi Grid Fill', icon='MESH_GRID')
        layout.separator()

        settings = layout.column(align=True)
        settings.enabled = not self.clone_master_grid
        settings.prop(self, 'span')
        settings.prop(self, 'offset')

        options = layout.column(align=True)
        options.prop(self, 'clone_master_grid', toggle=True)

        if self.clone_master_grid:
            box = layout.box()
            box.label(text='Master grid topology will be copied exactly', icon='INFO')
        layout.separator()
        layout.label(text='Highlighted grid: last-selected clone master')

    @staticmethod
    def _selected_loops(bm):
        selected_edges = {
            edge for edge in bm.edges
            if edge.select and not edge.hide
        }
        if not selected_edges:
            return [], 'Select 2 to 20 closed edge loops'

        remaining = set(selected_edges)
        components = []
        while remaining:
            seed = remaining.pop()
            component = {seed}
            stack = [seed]
            while stack:
                edge = stack.pop()
                for vert in edge.verts:
                    for linked in vert.link_edges:
                        if linked in remaining:
                            remaining.remove(linked)
                            component.add(linked)
                            stack.append(linked)
            components.append(component)

        if not 2 <= len(components) <= 20:
            return [], f'Found {len(components)} loops; select between 2 and 20'

        loops = []
        for component in components:
            vertices = {vert for edge in component for vert in edge.verts}
            if len(vertices) != len(component):
                return [], 'Each selection must be one simple closed edge loop'
            for vert in vertices:
                selected_degree = sum(edge in component for edge in vert.link_edges)
                if selected_degree != 2:
                    return [], 'Each selection must be one simple closed edge loop'
            loops.append(component)

        edge_counts = {len(loop) for loop in loops}
        if len(edge_counts) != 1:
            counts = ', '.join(str(value) for value in sorted(edge_counts))
            return [], f'All loops must have the same edge count; found {counts}'

        edge_count = next(iter(edge_counts))
        if edge_count < 4 or edge_count % 2:
            return [], 'Grid Fill requires closed loops with an even edge count of at least 4'

        return loops, ''

    @staticmethod
    def _active_loop_index(bm, loops):
        """Return the component containing Blender's last selected element."""
        history = list(bm.select_history)
        for element in reversed(history):
            if isinstance(element, bmesh.types.BMEdge) and element.select and element.is_valid:
                for index, loop in enumerate(loops):
                    if element in loop:
                        return index
            elif isinstance(element, bmesh.types.BMVert) and element.select and element.is_valid:
                for index, loop in enumerate(loops):
                    if any(element in edge.verts for edge in loop):
                        return index
        return 0

    @staticmethod
    def _ordered_loop_vertices(loop_edges):
        adjacency = {}
        for edge in loop_edges:
            a, b = edge.verts
            adjacency.setdefault(a, []).append(b)
            adjacency.setdefault(b, []).append(a)
        if not adjacency or any(len(neighbors) != 2 for neighbors in adjacency.values()):
            return []

        start = min(adjacency, key=lambda vert: vert.index)
        ordered = [start]
        previous = None
        current = start
        while True:
            candidates = [vert for vert in adjacency[current] if vert is not previous]
            if not candidates:
                return []
            next_vert = min(candidates, key=lambda vert: vert.index) if previous is None else candidates[0]
            if next_vert is start:
                break
            if next_vert in ordered:
                return []
            ordered.append(next_vert)
            previous, current = current, next_vert
        return ordered if len(ordered) == len(adjacency) else []

    @staticmethod
    def _canonical_normal(normal):
        components = (normal.x, normal.y, normal.z)
        dominant = max(range(3), key=lambda index: abs(components[index]))
        if components[dominant] < 0.0:
            normal.negate()
        return normal

    @classmethod
    def _loop_frame(cls, loop_edges):
        vertices = cls._ordered_loop_vertices(loop_edges)
        if len(vertices) < 3:
            return None

        center = sum((vert.co for vert in vertices), Vector()) / len(vertices)
        normal = Vector()
        for index, vert in enumerate(vertices):
            current = vert.co
            following = vertices[(index + 1) % len(vertices)].co
            normal.x += (current.y - following.y) * (current.z + following.z)
            normal.y += (current.z - following.z) * (current.x + following.x)
            normal.z += (current.x - following.x) * (current.y + following.y)
        if normal.length_squared <= 1.0e-16:
            return None
        normal.normalize()
        cls._canonical_normal(normal)

        axes = (
            Vector((1.0, 0.0, 0.0)),
            Vector((0.0, 1.0, 0.0)),
            Vector((0.0, 0.0, 1.0)),
        )
        candidates = []
        for axis_index, axis in enumerate(axes):
            tangent = axis - normal * axis.dot(normal)
            candidates.append((tangent.length_squared, -axis_index, tangent))
        _length, _axis_order, primary = max(candidates, key=lambda item: item[:2])
        if primary.length_squared <= 1.0e-12:
            return None
        primary.normalize()
        secondary = normal.cross(primary)
        if secondary.length_squared <= 1.0e-12:
            return None
        secondary.normalize()

        def point_key(vert):
            radial = vert.co - center
            return (
                round(radial.dot(primary), 10),
                round(radial.dot(secondary), 10),
                -vert.index,
            )

        start_index = max(range(len(vertices)), key=lambda index: point_key(vertices[index]))
        forward = vertices[start_index:] + vertices[:start_index]
        reverse_raw = list(reversed(vertices))
        reverse_index = reverse_raw.index(vertices[start_index])
        reverse = reverse_raw[reverse_index:] + reverse_raw[:reverse_index]

        def sequence_key(sequence):
            return tuple(point_key(vert)[:2] for vert in sequence)

        canonical = max((forward, reverse), key=sequence_key)
        radius = sum((vert.co - center).length for vert in canonical) / len(canonical)
        return canonical, center, normal, primary, secondary, max(radius, 1.0e-8)

    @staticmethod
    def _select_only_loop(bm, loop_edges, ordered_vertices=None):
        for vert in bm.verts:
            vert.select = False
        for edge in bm.edges:
            edge.select = False
        for face in bm.faces:
            face.select = False
        for edge in loop_edges:
            edge.select = True
            edge.verts[0].select = True
            edge.verts[1].select = True
        bm.select_history.clear()
        if ordered_vertices and len(ordered_vertices) >= 2:
            first, second = ordered_vertices[0], ordered_vertices[1]
            anchor = next(
                (edge for edge in loop_edges if first in edge.verts and second in edge.verts),
                None,
            )
            if anchor is not None:
                bm.select_history.add(first)
                bm.select_history.add(anchor)

    @staticmethod
    def _loop_keys(loop):
        return [
            tuple(sorted((edge.verts[0].index, edge.verts[1].index)))
            for edge in loop
        ]

    @staticmethod
    def _resolve_loop(bm, keys):
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.verts.index_update()
        bm.edges.index_update()
        lookup = {
            tuple(sorted((edge.verts[0].index, edge.verts[1].index))): edge
            for edge in bm.edges
        }
        edges = [lookup.get(key) for key in keys]
        return None if any(edge is None for edge in edges) else edges

    @staticmethod
    def _capture_master_template(frame, boundary_vertices, pre_verts, new_faces):
        boundary_index = {vert: index for index, vert in enumerate(boundary_vertices)}
        interior = sorted(
            {
                vert for face in new_faces for vert in face.verts
                if vert not in boundary_index and vert not in pre_verts
            },
            key=lambda vert: vert.index,
        )
        interior_index = {vert: index for index, vert in enumerate(interior)}
        _ordered, center, normal, primary, secondary, radius = frame

        boundary_coords = []
        for vert in boundary_vertices:
            relative = vert.co - center
            boundary_coords.append((
                relative.dot(primary) / radius,
                relative.dot(secondary) / radius,
            ))

        coords = []
        for vert in interior:
            relative = vert.co - center
            coords.append((
                relative.dot(primary) / radius,
                relative.dot(secondary) / radius,
                relative.dot(normal) / radius,
            ))

        faces = []
        for face in new_faces:
            refs = []
            for vert in face.verts:
                if vert in boundary_index:
                    refs.append(('B', boundary_index[vert]))
                elif vert in interior_index:
                    refs.append(('I', interior_index[vert]))
                else:
                    return None
            faces.append(tuple(refs))
        return {
            'boundary_coords': tuple(boundary_coords),
            'coords': tuple(coords),
            'faces': tuple(faces),
        }

    @staticmethod
    def _signed_area(points):
        return 0.5 * sum(
            a[0] * b[1] - b[0] * a[1]
            for a, b in zip(points, points[1:] + points[:1])
        )

    @staticmethod
    def _segments_cross(a, b, c, d, epsilon=1.0e-9):
        def orient(p, q, r):
            return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

        o1 = orient(a, b, c)
        o2 = orient(a, b, d)
        o3 = orient(c, d, a)
        o4 = orient(c, d, b)
        return (
            ((o1 > epsilon and o2 < -epsilon) or (o1 < -epsilon and o2 > epsilon))
            and ((o3 > epsilon and o4 < -epsilon) or (o3 < -epsilon and o4 > epsilon))
        )

    @classmethod
    def _mapping_is_valid(cls, boundary_points, template):
        interior_points = [(co[0], co[1]) for co in template['coords']]

        def point(ref):
            kind, index = ref
            return boundary_points[index] if kind == 'B' else interior_points[index]

        face_sign = 0
        edges = []
        for face_refs in template['faces']:
            points = [point(ref) for ref in face_refs]
            area = cls._signed_area(points)
            if abs(area) <= 1.0e-8:
                return False
            sign = 1 if area > 0.0 else -1
            if face_sign == 0:
                face_sign = sign
            elif sign != face_sign:
                return False
            for index, ref_a in enumerate(face_refs):
                ref_b = face_refs[(index + 1) % len(face_refs)]
                if ref_a == ref_b:
                    return False
                edge_key = tuple(sorted((ref_a, ref_b)))
                if edge_key not in [item[0] for item in edges]:
                    edges.append((edge_key, point(ref_a), point(ref_b)))

        for index, (key_a, a, b) in enumerate(edges):
            refs_a = set(key_a)
            for key_b, c, d in edges[index + 1:]:
                if refs_a.intersection(key_b):
                    continue
                if cls._segments_cross(a, b, c, d):
                    return False
        return True

    @classmethod
    def _mapped_boundary(cls, frame, template):
        boundary, center, _normal, primary, secondary, radius = frame
        master = template.get('boundary_coords')
        if not master or len(master) != len(boundary):
            return None

        target_coords = []
        for vert in boundary:
            relative = vert.co - center
            target_coords.append((
                relative.dot(primary) / radius,
                relative.dot(secondary) / radius,
            ))

        candidates = []
        count = len(boundary)
        for reversed_order in (False, True):
            source_verts = list(reversed(boundary)) if reversed_order else list(boundary)
            source_coords = list(reversed(target_coords)) if reversed_order else list(target_coords)
            for shift in range(count):
                mapped_verts = source_verts[shift:] + source_verts[:shift]
                mapped_points = source_coords[shift:] + source_coords[:shift]
                if not cls._mapping_is_valid(mapped_points, template):
                    continue
                error = sum(
                    (target[0] - reference[0]) ** 2 + (target[1] - reference[1]) ** 2
                    for target, reference in zip(mapped_points, master)
                )
                candidates.append((error, reversed_order, shift, mapped_verts))

        if not candidates:
            return None
        return min(candidates, key=lambda item: (item[0], item[1], item[2]))[3]

    @classmethod
    def _clone_template(cls, bm, frame, template):
        _boundary, center, normal, primary, secondary, radius = frame
        boundary = cls._mapped_boundary(frame, template)
        if boundary is None:
            return None
        interior = []
        for x, y, z in template['coords']:
            interior.append(bm.verts.new(
                center + primary * (x * radius) + secondary * (y * radius) + normal * (z * radius)
            ))

        created_faces = []
        try:
            for face_refs in template['faces']:
                verts = [
                    boundary[index] if kind == 'B' else interior[index]
                    for kind, index in face_refs
                ]
                created_faces.append(bm.faces.new(verts))
        except (ValueError, RuntimeError):
            geom = [face for face in created_faces if face.is_valid]
            geom.extend(vert for vert in interior if vert.is_valid)
            if geom:
                bmesh.ops.delete(bm, geom=geom, context='VERTS')
            return None

        return created_faces

    def _remove_backup(self):
        backup = getattr(self, '_backup_mesh', None)
        if backup is not None:
            try:
                bpy.data.meshes.remove(backup)
            except (ReferenceError, RuntimeError):
                pass
        self._backup_mesh = None

    def _restore_backup(self, context):
        backup = getattr(self, '_backup_mesh', None)
        obj = getattr(self, '_preview_object', None)
        if backup is None or obj is None or obj.mode != 'EDIT':
            return False
        bm = bmesh.from_edit_mesh(obj.data)
        bm.clear()
        bm.from_mesh(backup)
        bm.normal_update()
        bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
        obj.data.update()
        if context.area is not None:
            context.area.tag_redraw()
        return True

    def _rebuild_preview(self, context):
        if getattr(self, '_building_preview', False):
            return
        self._building_preview = True
        try:
            if not self._restore_backup(context):
                return
            result = self._apply_geometry(context, preview=True)
            if 'FINISHED' not in result:
                self._restore_backup(context)
        finally:
            self._building_preview = False

    def check(self, context):
        # Blender calls check whenever a dialog property changes. Keep Span
        # inside the range supported by the selected loop size, matching the
        # default Grid Fill control instead of allowing a silent invalid state.
        span_max = max(1, int(getattr(self, '_span_max', 1)))
        clamped_span = min(max(int(self.span), 1), span_max)
        if self.span != clamped_span:
            self.span = clamped_span

        # Rebuild from the untouched snapshot so Span, Offset, and Clone are
        # always shown as a live, non-cumulative preview.
        self._rebuild_preview(context)
        return True

    def invoke(self, context, _event):
        objects = [
            obj for obj in context.objects_in_mode_unique_data
            if obj.type == 'MESH' and obj.mode == 'EDIT'
        ]
        if len(objects) != 1:
            self.report({'WARNING'}, 'Multi Grid Fill currently supports one edited object at a time')
            return {'CANCELLED'}

        obj = objects[0]
        bm = bmesh.from_edit_mesh(obj.data)
        loops, error = self._selected_loops(bm)
        if error:
            self.report({'WARNING'}, error)
            return {'CANCELLED'}

        edge_count = len(loops[0])
        active_loop_index = self._active_loop_index(bm, loops)
        self._master_loop_keys = tuple(self._loop_keys(loops[active_loop_index]))
        self._span_max = max(1, edge_count // 2 - 1)
        self.span = max(1, min(edge_count // 4, self._span_max))
        self.offset = 0
        self.clone_master_grid = False

        self._preview_object = obj
        self._backup_mesh = bpy.data.meshes.new('.modus_multi_grid_preview_backup')
        bm.to_mesh(self._backup_mesh)
        self._building_preview = False

        # Build the default settings immediately. Confirm only commits and
        # closes; it is not the point where geometry first appears.
        self._rebuild_preview(context)
        return context.window_manager.invoke_props_dialog(
            self, width=340, confirm_text='Confirm'
        )

    def cancel(self, context):
        self._restore_backup(context)
        self._remove_backup()
        _clear_multi_grid_overlay()
        if context.area is not None:
            context.area.tag_redraw()

    def execute(self, context):
        # The current mesh already is the live preview. Confirm simply accepts
        # it, removes transient state/highlighting, and closes the dialog.
        self._remove_backup()
        _clear_multi_grid_overlay()
        if context.area is not None:
            context.area.tag_redraw()
        return {'FINISHED'}

    def _apply_geometry(self, context, preview=False):
        objects = [
            obj for obj in context.objects_in_mode_unique_data
            if obj.type == 'MESH' and obj.mode == 'EDIT'
        ]
        if len(objects) != 1:
            self.report({'WARNING'}, 'Multi Grid Fill currently supports one edited object at a time')
            return {'CANCELLED'}

        obj = objects[0]
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.verts.index_update()
        bm.edges.index_update()
        loops, error = self._selected_loops(bm)
        if error:
            self.report({'WARNING'}, error)
            return {'CANCELLED'}

        edge_count = len(loops[0])
        half_count = edge_count // 2
        if not 1 <= self.span < half_count:
            self.report(
                {'WARNING'},
                f'Span must be between 1 and {half_count - 1} for {edge_count}-edge loops',
            )
            return {'CANCELLED'}

        def loop_sort_key(loop):
            frame = self._loop_frame(loop)
            if frame is None:
                return (0.0, 0.0, 0.0, min(edge.index for edge in loop))
            center = frame[1]
            return (
                round(center.x, 9), round(center.y, 9), round(center.z, 9),
                min(edge.index for edge in loop),
            )

        stored_master_keys = set(getattr(self, '_master_loop_keys', ()))
        active_loop_index = self._active_loop_index(bm, loops)
        if stored_master_keys:
            for index, loop in enumerate(loops):
                if set(self._loop_keys(loop)) == stored_master_keys:
                    active_loop_index = index
                    break
        master_loop = loops[active_loop_index]
        other_loops = [
            loop for index, loop in enumerate(loops)
            if index != active_loop_index
        ]
        other_loops.sort(key=loop_sort_key)
        # Process the last-selected loop first so it remains the reference grid.
        loops = [master_loop] + other_loops
        loop_keys = [self._loop_keys(loop) for loop in loops]
        filled = 0
        created_faces_count = 0
        cloned = 0
        fallbacks = 0
        template = None
        patch_records = []
        # Normal fills and the reference grid use the current live-preview settings.
        effective_offset = self.offset

        for loop_number, keys in enumerate(loop_keys):
            bm = bmesh.from_edit_mesh(obj.data)
            loop_edges = self._resolve_loop(bm, keys)
            if loop_edges is None:
                self.report({'WARNING'}, 'A loop changed before it could be filled')
                return {'CANCELLED'}
            frame = self._loop_frame(loop_edges)

            if self.clone_master_grid and loop_number > 0 and template is not None and frame is not None:
                cloned_faces = self._clone_template(bm, frame, template)
                if cloned_faces is not None:
                    valid_cloned_faces = [face for face in cloned_faces if face.is_valid]
                    if valid_cloned_faces:
                        bmesh.ops.recalc_face_normals(bm, faces=valid_cloned_faces)
                    patch_records.append({
                        'faces': list(valid_cloned_faces),
                        'frame': frame,
                        'is_master': False,
                    })
                    bm.normal_update()
                    created_faces_count += len(valid_cloned_faces)
                    filled += 1
                    cloned += 1
                    bmesh.update_edit_mesh(
                        obj.data, loop_triangles=True, destructive=True
                    )
                    obj.data.update()
                    if context.area is not None:
                        context.area.tag_redraw()
                    continue
                fallbacks += 1

            self._select_only_loop(bm, loop_edges, frame[0] if frame else None)
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            bm = bmesh.from_edit_mesh(obj.data)
            pre_verts = set(bm.verts)
            pre_faces = set(bm.faces)
            before_faces = len(pre_faces)
            try:
                result = bpy.ops.mesh.fill_grid(
                    span=self.span,
                    offset=effective_offset,
                    use_interp_simple=False,
                )
            except RuntimeError as exc:
                self.report({'WARNING'}, f'Grid Fill failed: {exc}')
                return {'CANCELLED'}
            if 'FINISHED' not in result:
                self.report({'WARNING'}, 'Grid Fill could not fill one of the loops')
                return {'CANCELLED'}

            bm = bmesh.from_edit_mesh(obj.data)
            new_faces = [face for face in bm.faces if face not in pre_faces]

            valid_new_faces = [face for face in new_faces if face.is_valid]
            if valid_new_faces:
                bmesh.ops.recalc_face_normals(bm, faces=valid_new_faces)
                patch_records.append({
                    'faces': list(valid_new_faces),
                    'frame': frame,
                    'is_master': loop_number == 0,
                })
            bm.normal_update()
            bmesh.update_edit_mesh(
                obj.data, loop_triangles=True, destructive=True
            )
            obj.data.update()
            if context.area is not None:
                context.area.tag_redraw()
            bm = bmesh.from_edit_mesh(obj.data)
            new_faces = [face for face in bm.faces if face not in pre_faces]
            created_faces_count += max(0, len(bm.faces) - before_faces)
            filled += 1

            if self.clone_master_grid and loop_number == 0 and frame is not None:
                # Capture the master Grid Fill exactly as previewed, preserving
                # its alignment, Span, and Offset for cloning.
                template = self._capture_master_template(
                    frame,
                    frame[0],
                    pre_verts,
                    new_faces,
                )
                if template is None:
                    self.report(
                        {'WARNING'},
                        'Could not capture the master grid; remaining loops will use normal Grid Fill',
                    )

        # Rebuild the final created-face and reference-grid groups.
        master_faces = []
        all_created_faces = []
        for record in patch_records:
            faces = [face for face in record['faces'] if face.is_valid]
            all_created_faces.extend(faces)
            if record['is_master']:
                master_faces.extend(faces)

        bm.normal_update()
        bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
        obj.data.update()

        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bm.faces.index_update()
        for vert in bm.verts:
            vert.select = False
        for edge in bm.edges:
            edge.select = False
        for face in bm.faces:
            face.select = False

        valid_all_faces = [face for face in all_created_faces if face.is_valid]
        valid_master_faces = [face for face in master_faces if face.is_valid]
        for face in valid_all_faces:
            face.select = True
            for edge in face.edges:
                edge.select = True
            for vert in face.verts:
                vert.select = True
        bm.select_history.clear()
        if valid_master_faces:
            master_face = valid_master_faces[0]
            bm.select_history.add(master_face)
            try:
                bm.faces.active = master_face
            except AttributeError:
                pass
        bm.normal_update()
        bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
        obj.data.update()
        if context.area is not None:
            context.area.tag_redraw()

        mode_text = 'normal Grid Fill; all grids selected and the last-selected grid kept active'
        if self.clone_master_grid:
            mode_text = f'master cloned to {cloned} loop(s)'
            if fallbacks:
                mode_text += f'; {fallbacks} fallback(s)'
        if preview:
            _set_multi_grid_overlay(obj, master_faces=valid_master_faces)
        else:
            _clear_multi_grid_overlay()
        if not preview:
            self.report(
                {'INFO'},
                f'Multi Grid Fill: Filled {filled} loops and created {created_faces_count} faces; {mode_text}',
            )
        return {'FINISHED'}

CLASSES = (
    MODUS_OT_clean_up,
    MODUS_OT_relax,
    MODUS_OT_multi_grid_fill_launcher,
    MODUS_OT_multi_grid_fill,
)
