# SPDX-License-Identifier: GPL-3.0-or-later
"""Rotate selected edge loops while keeping their vertices on the mesh."""

from math import atan2

import bmesh
import bpy
from bpy_extras.view3d_utils import (
    region_2d_to_origin_3d,
    region_2d_to_vector_3d,
)
from mathutils import Quaternion, Vector

from .helpers import (
    mean_position,
    ordered_edge_chains,
    tangent_at,
    unique_directions,
)


_EPSILON = 1.0e-10
_PLANAR_TOLERANCE = 1.0e-6


class MODUS_OT_loop_rotate(bpy.types.Operator):
    bl_idname = "modus.loop_rotate"
    bl_label = "Loop Rotate"
    bl_description = "Rotate selected edge loops along the surrounding surface"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if context.mode != 'EDIT_MESH' or not obj or obj.type != 'MESH':
            return False

        mesh = bmesh.from_edit_mesh(obj.data)
        return any(edge.select and not edge.hide for edge in mesh.edges)

    def invoke(self, context, event):
        self._object = context.active_object
        self._mesh = bmesh.from_edit_mesh(self._object.data)
        self._mesh.normal_update()
        self._mesh.verts.ensure_lookup_table()
        self._mesh.edges.ensure_lookup_table()

        selected_edges = [
            edge for edge in self._mesh.edges
            if edge.select and not edge.hide
        ]

        try:
            self._chains = ordered_edge_chains(selected_edges)
        except ValueError as error:
            self.report({'WARNING'}, str(error))
            return {'CANCELLED'}

        if not self._chains:
            self.report({'WARNING'}, "Select an edge loop")
            return {'CANCELLED'}

        self._selected_edges = set(selected_edges)
        self._world = self._object.matrix_world.copy()
        self._world_inverse = self._world.inverted_safe()
        self._normal_matrix = self._world_inverse.transposed().to_3x3()

        self._start_local = {
            vert: vert.co.copy()
            for chain in self._chains
            for vert in chain["verts"]
        }
        self._start_world = {
            vert: self._world @ coordinate
            for vert, coordinate in self._start_local.items()
        }

        self._pivot_world = self._find_pivot(context)
        self._view_normal = -region_2d_to_vector_3d(
            context.region,
            context.region_data,
            (context.region.width * 0.5, context.region.height * 0.5),
        ).normalized()

        mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        self._mouse_start = self._mouse_on_rotation_plane(context, mouse)
        if self._mouse_start is None:
            return {'CANCELLED'}

        if (self._mouse_start - self._pivot_world).length_squared <= _EPSILON:
            self.report({'WARNING'}, "Move the mouse away from the pivot and try again")
            return {'CANCELLED'}

        self._prepare_surface_guides()
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if context.area:
            context.area.tag_redraw()

        if event.type == 'MOUSEMOVE':
            mouse = Vector((event.mouse_region_x, event.mouse_region_y))
            current = self._mouse_on_rotation_plane(context, mouse)
            if current is not None:
                self._apply_rotation(current)
            return {'RUNNING_MODAL'}

        if event.type in {'LEFTMOUSE', 'SPACE', 'RET', 'NUMPAD_ENTER'}:
            if event.value == 'PRESS':
                return {'FINISHED'}

        if event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
            self._restore_start()
            return {'CANCELLED'}

        return {'RUNNING_MODAL'}

    def _find_pivot(self, context):
        pivot_mode = context.scene.tool_settings.transform_pivot_point

        if pivot_mode == 'CURSOR':
            return context.scene.cursor.location.copy()

        if pivot_mode == 'ACTIVE_ELEMENT':
            active = self._mesh.select_history.active
            if isinstance(active, bmesh.types.BMVert) and active.select:
                return self._world @ active.co
            if isinstance(active, bmesh.types.BMEdge) and active.select:
                return self._world @ ((active.verts[0].co + active.verts[1].co) * 0.5)
            if isinstance(active, bmesh.types.BMFace) and active.select:
                return self._world @ active.calc_center_median_weighted()

            face = self._mesh.faces.active
            if face and face.select:
                return self._world @ face.calc_center_median_weighted()

        return mean_position(list(self._start_world.values()))

    def _mouse_on_rotation_plane(self, context, mouse):
        ray_origin = region_2d_to_origin_3d(
            context.region,
            context.region_data,
            mouse,
        )
        ray_direction = region_2d_to_vector_3d(
            context.region,
            context.region_data,
            mouse,
        )
        denominator = ray_direction.dot(self._view_normal)
        if abs(denominator) <= _EPSILON:
            return None

        distance = (
            self._pivot_world - ray_origin
        ).dot(self._view_normal) / denominator
        return ray_origin + ray_direction * distance

    def _prepare_surface_guides(self):
        for chain in self._chains:
            vertices = chain["verts"]
            cyclic = chain["cyclic"]
            chain["flat"] = self._surface_is_flat(vertices)

            for index, vert in enumerate(vertices):
                tangent = tangent_at(
                    vertices,
                    index,
                    cyclic,
                    self._start_local,
                )

                edge_guides = [
                    edge.other_vert(vert).co - self._start_local[vert]
                    for edge in vert.link_edges
                    if edge not in self._selected_edges and not edge.hide
                ]

                face_guides = []
                if not edge_guides and tangent.length_squared > _EPSILON:
                    for face in vert.link_faces:
                        direction = face.normal.cross(tangent)
                        if direction.length_squared > _EPSILON:
                            face_guides.append(direction)

                local_guides = unique_directions(edge_guides or face_guides)
                chain.setdefault("guides", {})[vert] = [
                    self._world.to_3x3() @ direction
                    for direction in local_guides
                ]

    def _surface_is_flat(self, vertices):
        if len(vertices) < 2:
            return True

        points = [self._start_world[vert] for vert in vertices]
        center = mean_position(points)
        normals = []

        for vert in vertices:
            normal = self._normal_matrix @ vert.normal
            if normal.length_squared > _EPSILON:
                normals.append(normal.normalized())

        if not normals:
            return False

        reference = mean_position(normals)
        if reference.length_squared <= _EPSILON:
            return False
        reference.normalize()

        scale = max((point - center).length for point in points)
        tolerance = max(_PLANAR_TOLERANCE, scale * _PLANAR_TOLERANCE)

        if any(abs((point - center).dot(reference)) > tolerance for point in points):
            return False

        return all(abs(normal.dot(reference)) >= 0.999 for normal in normals)

    def _rotation_from_mouse(self, current):
        initial = self._mouse_start - self._pivot_world
        updated = current - self._pivot_world

        if initial.length_squared <= _EPSILON or updated.length_squared <= _EPSILON:
            return Quaternion()

        initial.normalize()
        updated.normalize()
        sine = self._view_normal.dot(initial.cross(updated))
        cosine = max(-1.0, min(1.0, initial.dot(updated)))
        return Quaternion(self._view_normal, atan2(sine, cosine))

    def _apply_rotation(self, current):
        rotation = self._rotation_from_mouse(current)
        desired = {
            vert: self._pivot_world
            + rotation @ (start - self._pivot_world)
            for vert, start in self._start_world.items()
        }

        for chain in self._chains:
            vertices = chain["verts"]
            cyclic = chain["cyclic"]

            for index, vert in enumerate(vertices):
                tangent = tangent_at(vertices, index, cyclic, desired)
                target = desired[vert]

                if chain["flat"]:
                    plane_normal = tangent.cross(self._view_normal)
                else:
                    radial = target - self._pivot_world
                    plane_normal = tangent.cross(radial)
                    if plane_normal.length_squared <= _EPSILON:
                        plane_normal = tangent.cross(self._view_normal)

                guides = chain["guides"].get(vert, ())
                constrained = self._best_guide_position(
                    self._start_world[vert],
                    target,
                    plane_normal,
                    guides,
                )

                vert.co = self._world_inverse @ constrained

        self._mesh.normal_update()
        bmesh.update_edit_mesh(self._object.data, loop_triangles=False, destructive=False)

    @staticmethod
    def _best_guide_position(start, target, plane_normal, guides):
        if not guides:
            return target

        movement = target - start
        if movement.length_squared <= _EPSILON:
            return start

        movement_direction = movement.normalized()
        preferred = [
            guide for guide in guides
            if guide.dot(movement_direction) > 1.0e-5
        ]
        candidates = preferred or list(guides)

        best_position = None
        best_error = float("inf")

        for guide in candidates:
            if guide.length_squared <= _EPSILON:
                continue

            direction = guide.normalized()
            denominator = plane_normal.dot(direction)

            if (
                plane_normal.length_squared > _EPSILON
                and abs(denominator) > _EPSILON
            ):
                distance = plane_normal.dot(target - start) / denominator
            else:
                distance = movement.dot(direction)

            candidate = start + direction * distance
            error = (candidate - target).length_squared
            if error < best_error:
                best_position = candidate
                best_error = error

        return best_position if best_position is not None else target

    def _restore_start(self):
        for vert, coordinate in self._start_local.items():
            if vert.is_valid:
                vert.co = coordinate

        self._mesh.normal_update()
        bmesh.update_edit_mesh(self._object.data, loop_triangles=False, destructive=False)
