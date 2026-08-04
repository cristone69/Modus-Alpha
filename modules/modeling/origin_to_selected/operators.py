# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import bmesh
import bpy
from mathutils import Matrix, Vector


def _average(vectors):
    result = Vector((0.0, 0.0, 0.0))
    for vector in vectors:
        result += vector
    return result / len(vectors)


def _world_normal(matrix_world, normal):
    return (matrix_world.inverted_safe().transposed().to_3x3() @ normal).normalized()


def _safe_frame(normal: Vector, direction: Vector) -> Matrix:
    z_axis = normal.normalized()
    y_axis = direction.normalized()

    # Remove any component pointing along the normal.
    y_axis = y_axis - z_axis * y_axis.dot(z_axis)
    if y_axis.length_squared < 1e-12:
        fallback = Vector((0.0, 1.0, 0.0))
        if abs(fallback.dot(z_axis)) > 0.999:
            fallback = Vector((1.0, 0.0, 0.0))
        y_axis = fallback - z_axis * fallback.dot(z_axis)
    y_axis.normalize()

    x_axis = y_axis.cross(z_axis).normalized()
    y_axis = z_axis.cross(x_axis).normalized()

    rotation = Matrix.Identity(4)
    rotation.col[0].xyz = x_axis
    rotation.col[1].xyz = y_axis
    rotation.col[2].xyz = z_axis
    return rotation


def _vertex_frame(obj, vert):
    mx3 = obj.matrix_world.to_3x3()
    normal = _world_normal(obj.matrix_world, vert.normal)

    if vert.link_edges:
        edge = max(vert.link_edges, key=lambda item: item.calc_length())
        direction = (mx3 @ (edge.other_vert(vert).co - vert.co)).normalized()
    else:
        direction = mx3 @ Vector((0.0, 1.0, 0.0))

    return _safe_frame(normal, direction)


def _edge_frame(context, obj, edge):
    mx3 = obj.matrix_world.to_3x3()
    direction = (mx3 @ (edge.verts[1].co - edge.verts[0].co)).normalized()

    if edge.link_faces:
        normal = _average([_world_normal(obj.matrix_world, face.normal) for face in edge.link_faces]).normalized()
    else:
        normal = (mx3 @ Vector((0.0, 0.0, 1.0))).normalized()
        if abs(normal.dot(direction)) > 0.999:
            normal = (mx3 @ Vector((1.0, 0.0, 0.0))).normalized()

    # Keep edge orientation visually stable relative to the viewport.
    region_3d = getattr(context.space_data, 'region_3d', None)
    if region_3d:
        view_up = region_3d.view_rotation @ Vector((0.0, 1.0, 0.0))
        if direction.dot(view_up) < 0.0:
            direction.negate()

    return _safe_frame(normal, direction)


def _face_frame(context, obj, face):
    mx3 = obj.matrix_world.to_3x3()
    normal = _world_normal(obj.matrix_world, face.normal)

    try:
        tangent_local = face.calc_tangent_edge_pair()
    except ValueError:
        tangent_local = face.calc_tangent_edge()
    direction = (mx3 @ tangent_local).normalized()

    region_3d = getattr(context.space_data, 'region_3d', None)
    if region_3d:
        view_up = region_3d.view_rotation @ Vector((0.0, 1.0, 0.0))
        if direction.dot(view_up) < 0.0:
            direction.negate()

    return _safe_frame(normal, direction)


class MODUS_OT_origin_to_selected(bpy.types.Operator):
    bl_idname = 'modus.origin_to_selected'
    bl_label = 'Set Origin to Selected'
    bl_description = 'Set the object origin and orientation from the active selected vertex, edge, or face'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return context.mode == 'EDIT_MESH' and obj is not None and obj.type == 'MESH'

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)
        bm.normal_update()
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        select_mode = tuple(context.scene.tool_settings.mesh_select_mode)
        old_world = obj.matrix_world.copy()

        if select_mode == (True, False, False):
            selected = [vert for vert in bm.verts if vert.select]
            if not selected:
                return {'CANCELLED'}
            center_local = _average([vert.co for vert in selected])
            active = bm.select_history[-1] if bm.select_history and isinstance(bm.select_history[-1], bmesh.types.BMVert) else selected[0]
            rotation = _vertex_frame(obj, active)

        elif select_mode == (False, True, False):
            selected = [edge for edge in bm.edges if edge.select]
            if not selected:
                return {'CANCELLED'}
            center_local = _average([(edge.verts[0].co + edge.verts[1].co) * 0.5 for edge in selected])
            active = bm.select_history[-1] if bm.select_history and isinstance(bm.select_history[-1], bmesh.types.BMEdge) else selected[0]
            rotation = _edge_frame(context, obj, active)

        elif select_mode == (False, False, True):
            selected = [face for face in bm.faces if face.select]
            if not selected:
                return {'CANCELLED'}
            center_local = _average([face.calc_center_median_weighted() for face in selected])
            active = bm.faces.active if bm.faces.active in selected else selected[0]
            rotation = _face_frame(context, obj, active)

        else:
            # Mixed component modes do not provide an unambiguous orientation.
            return {'CANCELLED'}

        location = old_world @ center_local
        scale = old_world.to_scale()
        scale_matrix = Matrix.Diagonal((scale.x, scale.y, scale.z, 1.0))
        new_world = Matrix.Translation(location) @ rotation @ scale_matrix

        child_world_matrices = {child: child.matrix_world.copy() for child in obj.children}
        delta = new_world.inverted_safe() @ old_world

        obj.matrix_world = new_world
        bmesh.ops.transform(bm, verts=bm.verts, matrix=delta)
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        obj.data.update()

        for child, matrix_world in child_world_matrices.items():
            child.matrix_world = matrix_world

        return {'FINISHED'}


CLASSES = (MODUS_OT_origin_to_selected,)
