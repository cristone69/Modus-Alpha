# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

import bpy
import bmesh
from bpy.props import BoolProperty, IntProperty, PointerProperty
from mathutils import Matrix


_SPINNER = ('◐', '◓', '◑', '◒')
_active_job = None
_last_status = 'Ready'
_symmetry_update_guard = False


def _exclusive_symmetry_update(active_axis):
    def update(settings, _context):
        global _symmetry_update_guard
        if _symmetry_update_guard or not getattr(settings, active_axis):
            return
        _symmetry_update_guard = True
        try:
            for axis in ('symmetry_x', 'symmetry_y', 'symmetry_z'):
                if axis != active_axis:
                    setattr(settings, axis, False)
        finally:
            _symmetry_update_guard = False
    return update


class _RetopologyJob:
    def __init__(
        self,
        source_name,
        source_matrix,
        job_dir,
        input_path,
        target_triangles,
        symmetry_axes,
        enable_smoothing,
        smoothing_iterations,
        use_sharp_boundaries,
        use_multiresolution,
        sharp_path,
        compact_detail_weight='4.0',
        debug_import_qem=False,
        decimate_only=False,
        proxy_multiplier='1',
    ):
        self.source_name = source_name
        self.source_matrix = source_matrix
        self.job_dir = job_dir
        self.input_path = input_path
        self.target_triangles = target_triangles
        self.symmetry_axes = symmetry_axes
        self.enable_smoothing = enable_smoothing
        self.smoothing_iterations = smoothing_iterations
        self.use_sharp_boundaries = use_sharp_boundaries
        self.use_multiresolution = use_multiresolution
        self.sharp_path = sharp_path
        self.compact_detail_weight = compact_detail_weight
        self.debug_import_qem = debug_import_qem
        self.decimate_only = decimate_only
        self.proxy_multiplier = proxy_multiplier
        self.status = 'Starting engine'
        self.spinner_index = 0
        self.process = None
        self.result_path = None
        self.qem_result_path = None
        self.sharp_result_path = None
        self.error = None
        self.done = False
        self.cancelled = False
        self.timings = {}
        self.cancel_requested = threading.Event()


class MODUS_RetopologySettings(bpy.types.PropertyGroup):
    target_triangles: IntProperty(
        name='Target Remesh Tris',
        description=(
            'Approximate final triangle budget; the engine automatically '
            'scales it for enabled symmetry axes'
        ),
        default=30000,
        min=1000,
        soft_max=200000,
        step=1000,
    )
    symmetry_x: BoolProperty(
        name='X',
        description='Split, mirror, and weld across object-local X',
        default=False,
        update=_exclusive_symmetry_update('symmetry_x'),
    )
    symmetry_y: BoolProperty(
        name='Y',
        description='Split, mirror, and weld across object-local Y',
        default=False,
        update=_exclusive_symmetry_update('symmetry_y'),
    )
    symmetry_z: BoolProperty(
        name='Z',
        description='Split, mirror, and weld across object-local Z',
        default=False,
        update=_exclusive_symmetry_update('symmetry_z'),
    )
    debug_import_qem: BoolProperty(
        name='Import QEM Debug Mesh',
        description=(
            'Import the post-QEM triangle mesh alongside the final quad result'
        ),
        default=False,
    )
    enable_smoothing: BoolProperty(
        name='Quad Smoothing',
        description=(
            'Smooth and project the raw quadrangulation; disable to inspect '
            'whether sharp edges were already lost during quad generation'
        ),
        default=True,
    )
    smoothing_iterations: IntProperty(
        name='Iterations',
        default=100,
        min=1,
        max=300,
    )
    show_debug: BoolProperty(name='Debug', default=False)
    use_sharp_boundaries: BoolProperty(
        name='Sharp Edges as Boundaries',
        description='Use Blender edges marked Sharp as Quad Engine boundaries',
        default=False,
    )
    use_pre_smooth: BoolProperty(
        name='Pre Smooth',
        description=(
            'Smooth a temporary copy of the source mesh before sending it '
            'to Quad Engine'
        ),
        default=False,
    )
    pre_smooth_iterations: IntProperty(
        name='Iterations',
        description='Number of Pre Smooth iterations',
        default=15,
        min=0,
        max=30,
    )
    use_multiresolution: BoolProperty(
        name='Multiresolution',
        description=(
            'Add Multiresolution and Shrinkwrap modifiers to the '
            'retopology result for subdivision surface projection'
        ),
        default=False,
    )


def _engine_directory():
    return (
        Path(__file__).resolve().parents[0]
        / 'binaries'
        / 'modus_quad_engine'
    )


def _engine_symmetry_axes(settings):
    """Map Blender axes to the OBJ/engine coordinate system (Y/Z swap)."""
    return ''.join(
        engine_axis
        for enabled, engine_axis in (
            (settings.symmetry_x, 'X'),
            (settings.symmetry_y, 'Z'),
            (settings.symmetry_z, 'Y'),
        )
        if enabled
    )


def _blender_symmetry_axes(engine_axes):
    return ''.join(
        blender_axis
        for engine_axis, blender_axis in (('X', 'X'), ('Z', 'Y'), ('Y', 'Z'))
        if engine_axis in engine_axes
    )


def _tag_view3d_redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _terminate_job(job):
    job.cancel_requested.set()
    process = job.process
    if process is not None and process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass


def _run_process(job, command, engine_dir, environment, log):
    if job.cancel_requested.is_set():
        raise InterruptedError
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    job.process = subprocess.Popen(
        command,
        cwd=str(engine_dir),
        env=environment,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=flags,
    )
    return_code = job.process.wait()
    job.process = None
    if job.cancel_requested.is_set():
        raise InterruptedError
    if return_code != 0:
        if return_code in (-1073741819, 3221225477):
            explanation = (
                'The engine encountered an internal memory-access fault. '
                'Blender and the source mesh are unaffected.'
            )
        else:
            explanation = f'The engine stopped with exit code {return_code}.'
        raise RuntimeError(
            f'{explanation} '
            f'See {job.job_dir / "pipeline.log"}'
        )


def _engine_worker(job):
    engine_dir = _engine_directory()
    engine = engine_dir / 'Modus Quad Engine.exe'
    prep_config = (
        engine_dir
        / 'config'
        / 'prep_config'
        / 'basic_setup_Organic.txt'
    )
    flow_config = (
        engine_dir
        / 'config'
        / 'main_config'
        / 'flow.txt'
    )
    log_path = job.job_dir / 'pipeline.log'

    environment = os.environ.copy()
    symmetry_divisor = 2 ** len(job.symmetry_axes)
    proxy_multiplier = 1 if job.decimate_only else int(job.proxy_multiplier)
    engine_target = max(
        100,
        job.target_triangles * proxy_multiplier // symmetry_divisor,
    )
    environment.pop('MODUS_HYBRID_COMPACT_FACES', None)
    environment.pop('MODUS_HYBRID_INTERMEDIATE', None)
    environment.pop('MODUS_EXTERNAL_TRIANGLE_PROXY', None)
    environment.update(
        {
            'MODUS_REMESH_TARGET_FACES': str(
                engine_target
            ),
            'MODUS_QUAD_SCALE_FACTOR': (
                '1.3372' if proxy_multiplier == 2 else '1.0'
            ),
            'MODUS_REMESH_MODE': 'compact',
            'MODUS_COMPACT_DUAL_MODE': '1',
            'MODUS_COMPACT_DETAIL_WEIGHT': job.compact_detail_weight,
            'MODUS_REFINE_LOW_RES_INPUT': '0' if job.decimate_only else '1',
            'MODUS_REMESH_QUALITY_ITERATIONS': '2',
            'MODUS_AUTO_DENSITY': '0',
            'MODUS_AUTO_SHARP_FEATURES': '0',
            'MODUS_PROTECT_MARKED_SHARPS': (
                '1' if job.use_sharp_boundaries else '0'
            ),
            'MODUS_CACHED_PROJECTION': '1',
            'MODUS_BACK_PROJECTION_INTERVAL': '5',
            'MODUS_SMOOTH_DAMP': '0.35',
            'MODUS_SMOOTH_STEPS': str(job.smoothing_iterations),
            'MODUS_DISABLE_QUAD_SMOOTHING': (
                '0' if job.enable_smoothing else '1'
            ),
            'MODUS_SMOOTH_V3': '1',
            'MODUS_POST_QEM_PROJECT_SOURCE': str(job.input_path),
            'MODUS_SYMMETRY_AXES': job.symmetry_axes,
            'MODUS_SYMMETRY_RELAX': '0',
            'MODUS_SIMPLIFY_SYMMETRY_BOUNDARY': (
                '1' if job.symmetry_axes else '0'
            ),
            'MODUS_FLOW_CONFIG': str(flow_config),
            'MODUS_DECIMATE_ONLY': '1' if job.decimate_only else '0',
            'MODUS_FLOW_GUIDES': '',
            'MODUS_FLOW_GUIDE_RINGS': '0',
            'MODUS_REQUIRED_LOOPS': '',
        }
    )

    try:
        with log_path.open('w', encoding='utf-8', errors='replace') as log:
            job.status = 'Running Modus Quad Engine'
            _run_process(
                job,
                [
                    str(engine),
                    str(job.input_path),
                    '1' if job.decimate_only else '3',
                    str(prep_config),
                ] + (
                    [str(job.sharp_path)]
                    if job.use_sharp_boundaries and job.sharp_path.exists()
                    else []
                ),
                engine_dir,
                environment,
                log,
            )

            if job.decimate_only:
                result_path = job.input_path.with_name(
                    f'{job.input_path.stem}_rem.obj'
                )
                if not result_path.exists():
                    raise RuntimeError('Compact QEM did not produce an output mesh')
                job.result_path = result_path
                job.sharp_result_path = result_path.with_suffix('.sharp')
                job.status = 'Importing decimated mesh'
                timing_pattern = re.compile(
                    r'^\[MODUS TIMING\]\s+([a-z_]+)=([0-9.]+)\s+ms$'
                )
                log.flush()
                with log_path.open(
                    'r', encoding='utf-8', errors='replace'
                ) as timing_log:
                    for line in timing_log:
                        match = timing_pattern.match(line.strip())
                        if match:
                            job.timings[match.group(1)] = float(match.group(2))
                return

            patch_path = job.input_path.with_name(
                f'{job.input_path.stem}_rem_p0.obj'
            )
            result_path = patch_path.with_name(
                f'{patch_path.stem}_0_quadrangulation_smooth.obj'
            )
            if not result_path.exists():
                raise RuntimeError('Quad generation did not produce an output mesh')
            job.result_path = result_path
            job.sharp_result_path = result_path.with_suffix('.sharp')
            qem_result_path = job.input_path.with_name(
                f'{job.input_path.stem}_rem.obj'
            )
            if job.debug_import_qem:
                if not qem_result_path.exists():
                    raise RuntimeError(
                        'QEM debug import was enabled but no remesh was produced'
                    )
                job.qem_result_path = qem_result_path
            job.status = 'Importing result'
        timing_pattern = re.compile(
            r'^\[MODUS TIMING\]\s+([a-z_]+)=([0-9.]+)\s+ms$'
        )
        with log_path.open('r', encoding='utf-8', errors='replace') as log:
            for line in log:
                match = timing_pattern.match(line.strip())
                if match:
                    job.timings[match.group(1)] = float(match.group(2))
    except InterruptedError:
        job.cancelled = True
        job.status = 'Cancelled'
    except Exception as exc:
        job.error = str(exc)
        job.status = 'Failed'
    finally:
        job.process = None
        job.done = True


def _move_to_original_meshes_collection(context, obj):
    collection_name = "Modus Original Meshes"
    collection = bpy.data.collections.get(collection_name)
    if collection is None:
        collection = bpy.data.collections.new(collection_name)
        context.scene.collection.children.link(collection)
    collection.hide_viewport = True
    collection.hide_render = True
    for coll in list(obj.users_collection):
        coll.objects.unlink(obj)
    collection.objects.link(obj)


def _pre_smooth_mesh(mesh, iterations=15):
    """Smooth vertex positions on a temporary mesh copy."""
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        vertices = list(bm.verts)
        for _iteration in range(iterations):
            bmesh.ops.smooth_vert(
                bm,
                verts=vertices,
                factor=0.5,
                use_axis_x=True,
                use_axis_y=True,
                use_axis_z=True,
            )
        bm.to_mesh(mesh)
        mesh.update()
    finally:
        bm.free()


def _export_input(
        context, source, input_path, use_pre_smooth=False, pre_smooth_iterations=15):
    view_layer = context.view_layer
    selected = list(context.selected_objects)
    previous_active = view_layer.objects.active
    temporary_object = source.copy()
    temporary_mesh = None
    if use_pre_smooth:
        temporary_mesh = source.data.copy()
        temporary_object.data = temporary_mesh
        _pre_smooth_mesh(temporary_mesh, iterations=pre_smooth_iterations)
    else:
        temporary_object.data = source.data
    temporary_object.matrix_world = Matrix.Identity(4)
    context.collection.objects.link(temporary_object)

    try:
        for obj in selected:
            obj.select_set(False)
        temporary_object.select_set(True)
        view_layer.objects.active = temporary_object
        bpy.ops.wm.obj_export(
            filepath=str(input_path),
            export_selected_objects=True,
            export_materials=False,
            export_uv=False,
            export_normals=False,
        )
        return source.data
    finally:
        bpy.data.objects.remove(temporary_object, do_unlink=True)
        if temporary_mesh is not None:
            bpy.data.meshes.remove(temporary_mesh)
        for obj in selected:
            if obj.name in view_layer.objects:
                obj.select_set(True)
        if previous_active and previous_active.name in view_layer.objects:
            view_layer.objects.active = previous_active


def _export_sharp_boundaries(mesh, sharp_path):
    sharp_edges = {
        tuple(sorted(edge.vertices))
        for edge in mesh.edges
        if getattr(edge, 'use_edge_sharp', False)
    }
    sharp_attribute = mesh.attributes.get('sharp_edge')
    if sharp_attribute is not None and sharp_attribute.domain == 'EDGE':
        sharp_edges.update(
            tuple(sorted(mesh.edges[index].vertices))
            for index, value in enumerate(sharp_attribute.data)
            if value.value
        )

    records = []
    triangle_index = 0
    for polygon in mesh.polygons:
        vertices = list(polygon.vertices)
        for corner in range(1, len(vertices) - 1):
            triangle = (vertices[0], vertices[corner], vertices[corner + 1])
            for edge_index in range(3):
                edge = tuple(sorted((
                    triangle[edge_index],
                    triangle[(edge_index + 1) % 3],
                )))
                if edge in sharp_edges:
                    records.append((1, triangle_index, edge_index))
            triangle_index += 1

    with sharp_path.open('w', encoding='ascii', newline='\n') as output:
        output.write(f'{len(records)}\n')
        for edge_type, face_index, edge_index in records:
            output.write(f'{edge_type},{face_index},{edge_index}\n')


def _clear_sharp_edges(mesh):
    """Ensure a result contains no Blender sharp-edge markings."""
    for edge in mesh.edges:
        if hasattr(edge, 'use_edge_sharp'):
            edge.use_edge_sharp = False

    sharp_attribute = mesh.attributes.get('sharp_edge')
    if (
        sharp_attribute is not None
        and sharp_attribute.domain == 'EDGE'
        and sharp_attribute.data_type == 'BOOLEAN'
    ):
        for value in sharp_attribute.data:
            value.value = False

    mesh.update()


def _apply_result_sharp_edges(mesh, sharp_path):
    if sharp_path is None or not sharp_path.exists():
        raise RuntimeError('Quad Engine did not produce its sharp-edge sidecar')

    lines = [
        line.strip()
        for line in sharp_path.read_text(encoding='ascii').splitlines()
        if line.strip()
    ]
    if not lines:
        raise RuntimeError('Quad Engine produced an empty sharp-edge sidecar')
    expected = int(lines[0])
    if len(lines) - 1 != expected:
        raise RuntimeError(
            'Quad Engine sharp-edge sidecar has an invalid record count'
        )

    mesh_edges = {
        tuple(sorted(edge.vertices)): edge
        for edge in mesh.edges
    }
    marked = []
    for line in lines[1:]:
        fields = line.split(',')
        if len(fields) != 2:
            raise RuntimeError('Invalid quad sharp-edge record')
        key = tuple(sorted((int(fields[0]), int(fields[1]))))
        edge = mesh_edges.get(key)
        if edge is None:
            raise RuntimeError(
                'Imported OBJ topology does not match its sharp-edge sidecar'
            )
        if hasattr(edge, 'use_edge_sharp'):
            edge.use_edge_sharp = True
        marked.append(edge.index)

    sharp_attribute = mesh.attributes.get('sharp_edge')
    if sharp_attribute is None:
        sharp_attribute = mesh.attributes.new(
            name='sharp_edge', type='BOOLEAN', domain='EDGE'
        )
    if (
        sharp_attribute.domain != 'EDGE'
        or sharp_attribute.data_type != 'BOOLEAN'
    ):
        raise RuntimeError('Result mesh has an incompatible sharp_edge attribute')
    for edge_index in marked:
        sharp_attribute.data[edge_index].value = True
    return len(marked)


def _apply_qem_sharp_edges(mesh, sharp_path):
    if sharp_path is None or not sharp_path.exists():
        return 0
    lines = [
        line.strip()
        for line in sharp_path.read_text(encoding='ascii').splitlines()
        if line.strip()
    ]
    if not lines:
        return 0
    expected = int(lines[0])
    if len(lines) - 1 != expected:
        raise RuntimeError('QEM sharp-edge sidecar has an invalid record count')
    keys = set()
    for line in lines[1:]:
        fields = line.split(',')
        if len(fields) != 3:
            raise RuntimeError('Invalid QEM sharp-edge record')
        face_index = int(fields[1])
        edge_index = int(fields[2])
        if face_index < 0 or face_index >= len(mesh.polygons):
            continue
        vertices = list(mesh.polygons[face_index].vertices)
        if len(vertices) != 3:
            continue
        keys.add(tuple(sorted((
            vertices[edge_index % 3],
            vertices[(edge_index + 1) % 3],
        ))))
    marked = []
    for edge in mesh.edges:
        if tuple(sorted(edge.vertices)) not in keys:
            continue
        if hasattr(edge, 'use_edge_sharp'):
            edge.use_edge_sharp = True
        marked.append(edge.index)
    attribute = mesh.attributes.get('sharp_edge')
    if attribute is None:
        attribute = mesh.attributes.new(
            name='sharp_edge', type='BOOLEAN', domain='EDGE'
        )
    for edge_index in marked:
        attribute.data[edge_index].value = True
    return len(marked)


def _remove_new_unused_materials(existing_materials):
    """Delete material datablocks created only by the OBJ import."""
    for material in list(bpy.data.materials):
        if material not in existing_materials and material.users == 0:
            bpy.data.materials.remove(material)


def _copy_source_materials(result, source_name):
    """Replace imported material slots with the source object's existing materials."""
    source = bpy.data.objects.get(source_name)
    source_materials = []
    if source is not None and source.type == 'MESH':
        source_materials = [slot.material for slot in source.material_slots]

    result.data.materials.clear()
    for material in source_materials:
        if material is not None:
            result.data.materials.append(material)

    # Retopology cannot preserve the source face-to-material mapping. Keep all
    # generated faces on the first copied slot instead of retaining OBJ imports.
    for polygon in result.data.polygons:
        polygon.material_index = 0


def _numbered_result_name(source_name, operation):
    """Return a stable operation name without stacking repeated suffixes."""
    suffix_pattern = re.compile(
        r'_(?:Retopology|Decimated)(?:_\d{2})?$',
        re.IGNORECASE,
    )
    base_name = source_name
    while suffix_pattern.search(base_name):
        base_name = suffix_pattern.sub('', base_name)

    plain_name = f'{base_name}_{operation}'
    used_names = {obj.name for obj in bpy.data.objects}
    used_names.update(mesh.name for mesh in bpy.data.meshes)
    if plain_name not in used_names:
        return plain_name

    index = 2
    while f'{plain_name}_{index:02d}' in used_names:
        index += 1
    return f'{plain_name}_{index:02d}'


def _import_result(context, job):
    before = set(bpy.data.objects)
    existing_materials = set(bpy.data.materials)
    bpy.ops.wm.obj_import(filepath=str(job.result_path))
    imported = [obj for obj in bpy.data.objects if obj not in before]
    mesh_objects = [obj for obj in imported if obj.type == 'MESH']
    if not mesh_objects:
        raise RuntimeError('Blender imported no mesh from the engine output')

    result = mesh_objects[0]
    result.data.transform(result.matrix_world)
    result_name = _numbered_result_name(job.source_name, 'Retopology')
    result.name = result_name
    result.data.name = result_name
    _copy_source_materials(result, job.source_name)
    _remove_new_unused_materials(existing_materials)
    for polygon in result.data.polygons:
        polygon.use_smooth = True
    if job.use_sharp_boundaries:
        sharp_count = _apply_result_sharp_edges(
            result.data, job.sharp_result_path
        )
    else:
        _clear_sharp_edges(result.data)
        sharp_count = 0
    result.matrix_world = Matrix(job.source_matrix)
    result['modus_target_triangles'] = job.target_triangles
    result['modus_engine_target_triangles'] = max(
        100,
        job.target_triangles * int(job.proxy_multiplier)
        // (2 ** len(job.symmetry_axes)),
    )
    result['modus_symmetry_axes'] = job.symmetry_axes
    result['modus_smoothing'] = 'V3'
    result['modus_smoothing_iterations'] = job.smoothing_iterations
    result['modus_sharp_boundaries'] = job.use_sharp_boundaries
    result['modus_remesh_mode'] = 'COMPACT_DUAL'
    result['modus_compact_detail_weight'] = job.compact_detail_weight
    result['modus_qem_proxy_multiplier'] = int(job.proxy_multiplier)
    result['modus_sharp_edge_count'] = sharp_count
    context.view_layer.objects.active = result
    result.select_set(True)
    return result


def _import_qem_debug_result(context, job):
    if job.qem_result_path is None:
        return None
    before = set(bpy.data.objects)
    existing_materials = set(bpy.data.materials)
    bpy.ops.wm.obj_import(filepath=str(job.qem_result_path))
    imported = [obj for obj in bpy.data.objects if obj not in before]
    mesh_objects = [obj for obj in imported if obj.type == 'MESH']
    if not mesh_objects:
        raise RuntimeError('Blender imported no mesh from the QEM debug output')

    result = mesh_objects[0]
    result.data.transform(result.matrix_world)
    result.name = f'{job.source_name}_QEM_Debug'
    result.data.name = f'{job.source_name}_QEM_Debug'
    _copy_source_materials(result, job.source_name)
    _remove_new_unused_materials(existing_materials)
    result.matrix_world = Matrix(job.source_matrix)
    result['modus_debug_stage'] = 'post_qem'
    result['modus_engine_target_triangles'] = max(
        100,
        job.target_triangles * int(job.proxy_multiplier)
        // (2 ** len(job.symmetry_axes)),
    )
    result['modus_symmetry_axes'] = job.symmetry_axes
    result['modus_remesh_mode'] = 'COMPACT_DUAL'
    result['modus_compact_detail_weight'] = job.compact_detail_weight
    return result


def _import_decimate_result(context, job):
    before = set(bpy.data.objects)
    existing_materials = set(bpy.data.materials)
    bpy.ops.wm.obj_import(filepath=str(job.result_path))
    imported = [obj for obj in bpy.data.objects if obj not in before]
    meshes = [obj for obj in imported if obj.type == 'MESH']
    if not meshes:
        raise RuntimeError('Blender imported no Compact QEM mesh')
    result = meshes[0]
    result.data.transform(result.matrix_world)
    result_name = _numbered_result_name(job.source_name, 'Decimated')
    result.name = result_name
    result.data.name = result_name
    _copy_source_materials(result, job.source_name)
    _remove_new_unused_materials(existing_materials)
    result.matrix_world = Matrix.Identity(4)
    for polygon in result.data.polygons:
        polygon.use_smooth = True
    sharp_count = (
        _apply_qem_sharp_edges(result.data, job.sharp_result_path)
        if job.use_sharp_boundaries else 0
    )
    if job.symmetry_axes:
        blender_axes = _blender_symmetry_axes(job.symmetry_axes)
        context.view_layer.objects.active = result
        result.select_set(True)
        mirror = result.modifiers.new(name='Symmetry', type='MIRROR')
        mirror.use_axis[0] = 'X' in blender_axes
        mirror.use_axis[1] = 'Y' in blender_axes
        mirror.use_axis[2] = 'Z' in blender_axes
        mirror.use_clip = True
        mirror.use_mirror_merge = True
        mirror.merge_threshold = 1.0e-5
        bpy.ops.object.modifier_apply(modifier=mirror.name)
        sharp_attribute = result.data.attributes.get('sharp_edge')
        for edge in result.data.edges:
            vertices = [result.data.vertices[index].co for index in edge.vertices]
            is_center = any(
                axis_name in blender_axes
                and abs(vertices[0][axis_index]) <= 1.0e-5
                and abs(vertices[1][axis_index]) <= 1.0e-5
                for axis_index, axis_name in enumerate('XYZ')
            )
            if not is_center:
                continue
            if hasattr(edge, 'use_edge_sharp'):
                edge.use_edge_sharp = False
            if sharp_attribute is not None:
                sharp_attribute.data[edge.index].value = False
    result.matrix_world = Matrix(job.source_matrix)
    result['modus_decimate_target_triangles'] = job.target_triangles
    result['modus_decimate_keep_sharp'] = job.use_sharp_boundaries
    result['modus_sharp_edge_count'] = sharp_count
    context.view_layer.objects.active = result
    result.select_set(True)
    return result


def _clean_successful_job(job):
    try:
        parent = Path(bpy.app.tempdir).resolve()
        path = job.job_dir.resolve()
        if path.parent == parent and path.name.startswith('modus_retopology_'):
            shutil.rmtree(path)
    except OSError:
        pass


class _MODUS_GenerateRetopologyBase:
    bl_idname = 'modus.generate_retopology'
    bl_label = 'Generate Retopology'
    bl_description = 'Run Modus Quad Engine without blocking Blender'
    bl_options = {'REGISTER', 'UNDO'}

    _timer = None
    quality_preset = 'FAST'

    @classmethod
    def poll(cls, context):
        return (
            _active_job is None
            and context.mode == 'OBJECT'
            and context.active_object is not None
            and context.active_object.type == 'MESH'
        )

    def invoke(self, context, _event):
        global _active_job, _last_status

        settings = context.scene.modus_retopology
        engine_dir = _engine_directory()
        required = [
            engine_dir / 'Modus Quad Engine.exe',
            engine_dir / 'config' / 'prep_config' / 'basic_setup_Organic.txt',
            engine_dir / 'config' / 'main_config' / 'flow.txt',
        ]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            self.report({'ERROR'}, f'Missing retopology engine: {missing[0]}')
            return {'CANCELLED'}

        source = context.active_object
        job_dir = Path(tempfile.mkdtemp(
            prefix='modus_retopology_',
            dir=bpy.app.tempdir,
        ))
        input_path = job_dir / f'input_{uuid.uuid4().hex}.obj'
        sharp_path = job_dir / f'input_{uuid.uuid4().hex}.sharp'
        job = _RetopologyJob(
            source.name,
            tuple(tuple(row) for row in source.matrix_world),
            job_dir,
            input_path,
            settings.target_triangles,
            _engine_symmetry_axes(settings),
            settings.enable_smoothing,
            settings.smoothing_iterations,
            settings.use_sharp_boundaries,
            settings.use_multiresolution,
            sharp_path,
            '4.0' if self.quality_preset == 'SLOW' else '0.0',
            settings.debug_import_qem,
            proxy_multiplier='2' if self.quality_preset == 'SLOW' else '1',
        )
        _active_job = job
        _last_status = f'Preparing {self.quality_preset.title()} Retopology'
        _tag_view3d_redraw()

        try:
            exported_mesh = _export_input(
                context, source, input_path, settings.use_pre_smooth,
                settings.pre_smooth_iterations,
            )
            if settings.use_sharp_boundaries:
                _export_sharp_boundaries(exported_mesh, sharp_path)
        except Exception as exc:
            _active_job = None
            _last_status = f'Export failed: {exc}'
            self.report({'ERROR'}, _last_status)
            _tag_view3d_redraw()
            return {'CANCELLED'}

        thread = threading.Thread(
            target=_engine_worker,
            args=(job,),
            name='ModusRetopologyWorker',
            daemon=True,
        )
        thread.start()
        self._timer = context.window_manager.event_timer_add(
            0.2,
            window=context.window,
        )
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        global _active_job, _last_status

        job = _active_job
        if job is None:
            return self._finish_timer(context, {'CANCELLED'})

        if event.type == 'ESC':
            _terminate_job(job)
            return {'RUNNING_MODAL'}

        if event.type != 'TIMER':
            return {'PASS_THROUGH'}

        job.spinner_index = (job.spinner_index + 1) % len(_SPINNER)
        _tag_view3d_redraw()
        if not job.done:
            return {'PASS_THROUGH'}

        result = {'FINISHED'}
        if job.cancelled:
            _last_status = 'Cancelled'
            self.report({'WARNING'}, 'Retopology cancelled')
            result = {'CANCELLED'}
        elif job.error:
            _last_status = f'Failed — log: {job.job_dir / "pipeline.log"}'
            self.report({'ERROR'}, job.error)
            result = {'CANCELLED'}
        else:
            try:
                imported = _import_result(context, job)
                qem_debug = _import_qem_debug_result(context, job)
                context.view_layer.objects.active = imported
                imported.select_set(True)
                if qem_debug is not None:
                    qem_debug.select_set(False)
                _last_status = f'Created {imported.name}'
                if job.use_multiresolution:
                    source_obj = bpy.data.objects.get(job.source_name)
                    if source_obj is not None:
                        multires = imported.modifiers.new(
                            name='Multires', type='MULTIRES'
                        )
                        bpy.ops.object.multires_subdivide(
                            modifier='Multires'
                        )
                        bpy.ops.object.multires_subdivide(
                            modifier='Multires'
                        )
                        multires.levels = 2
                        multires.render_levels = 2
                        bpy.ops.object.shade_smooth()
                        shrinkwrap = imported.modifiers.new(
                            name='Shrinkwrap', type='SHRINKWRAP'
                        )
                        shrinkwrap.target = source_obj
                        shrinkwrap.wrap_method = 'NEAREST_SURFACEPOINT'
                        bpy.ops.object.modifier_apply(
                            modifier='Shrinkwrap'
                        )
                source_obj = bpy.data.objects.get(job.source_name)
                if source_obj is None:
                    raise RuntimeError(
                        'Original source mesh was renamed or removed during processing'
                    )
                _move_to_original_meshes_collection(context, source_obj)
                for stage, milliseconds in job.timings.items():
                    self.report(
                        {'INFO'},
                        f'Modus Quad Engine — {stage}: '
                        f'{milliseconds / 1000.0:.3f} s',
                    )
                _clean_successful_job(job)
                self.report({'INFO'}, _last_status)
            except Exception as exc:
                _last_status = f'Import failed: {exc}'
                self.report({'ERROR'}, _last_status)
                result = {'CANCELLED'}

        _active_job = None
        _tag_view3d_redraw()
        return self._finish_timer(context, result)

    def cancel(self, context):
        if _active_job is not None:
            _terminate_job(_active_job)
        self._finish_timer(context, {'CANCELLED'})

    def _finish_timer(self, context, result):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        return result


class MODUS_OT_generate_retopology(
        _MODUS_GenerateRetopologyBase, bpy.types.Operator):
    bl_idname = 'modus.generate_retopology'
    bl_label = 'Fast Retopology'
    bl_description = 'Use a 1x QEM proxy for the fastest retopology'
    bl_options = {'REGISTER', 'UNDO'}
    quality_preset = 'FAST'


class MODUS_OT_generate_retopology_slow(
        _MODUS_GenerateRetopologyBase, bpy.types.Operator):
    bl_idname = 'modus.generate_retopology_slow'
    bl_label = 'Slow Retopology'
    bl_description = 'Use a 2× QEM proxy with maximum detail preservation'
    quality_preset = 'SLOW'


class MODUS_OT_cancel_retopology(bpy.types.Operator):
    bl_idname = 'modus.cancel_retopology'
    bl_label = 'Cancel Active Operation'
    bl_description = 'Stop the active Modus Quad Engine process'

    @classmethod
    def poll(cls, _context):
        return _active_job is not None

    def execute(self, _context):
        _terminate_job(_active_job)
        return {'FINISHED'}


class MODUS_OT_quick_decimate(bpy.types.Operator):
    bl_idname = 'modus.quick_decimate'
    bl_label = 'Quick Decimate'
    bl_description = 'Reduce the selected mesh with external Compact QEM'
    bl_options = {'REGISTER', 'UNDO'}

    _timer = None

    @classmethod
    def poll(cls, context):
        return (
            _active_job is None
            and context.mode == 'OBJECT'
            and context.active_object is not None
            and context.active_object.type == 'MESH'
        )

    def invoke(self, context, _event):
        global _active_job, _last_status
        settings = context.scene.modus_retopology
        source = context.active_object
        engine = _engine_directory() / 'Modus Quad Engine.exe'
        if not engine.exists():
            self.report({'ERROR'}, f'Missing retopology engine: {engine}')
            return {'CANCELLED'}
        job_dir = Path(tempfile.mkdtemp(
            prefix='modus_retopology_', dir=bpy.app.tempdir
        ))
        input_path = job_dir / f'decimate_{uuid.uuid4().hex}.obj'
        sharp_path = job_dir / f'decimate_{uuid.uuid4().hex}.sharp'
        job = _RetopologyJob(
            source.name,
            tuple(tuple(row) for row in source.matrix_world),
            job_dir,
            input_path,
            settings.target_triangles,
            _engine_symmetry_axes(settings),
            False,
            1,
            settings.use_sharp_boundaries,
            False,
            sharp_path,
            '4.0',
            False,
            True,
        )
        _active_job = job
        _last_status = 'Preparing Quick Decimate'
        try:
            exported_mesh = _export_input(
                context, source, input_path, settings.use_pre_smooth,
                settings.pre_smooth_iterations,
            )
            if settings.use_sharp_boundaries:
                _export_sharp_boundaries(exported_mesh, sharp_path)
        except Exception as exc:
            _active_job = None
            _last_status = f'Export failed: {exc}'
            self.report({'ERROR'}, _last_status)
            return {'CANCELLED'}
        threading.Thread(
            target=_engine_worker,
            args=(job,),
            name='ModusQuickDecimateWorker',
            daemon=True,
        ).start()
        self._timer = context.window_manager.event_timer_add(
            0.2, window=context.window
        )
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        global _active_job, _last_status
        job = _active_job
        if job is None:
            return self._finish(context, {'CANCELLED'})
        if event.type == 'ESC':
            _terminate_job(job)
            return {'RUNNING_MODAL'}
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        job.spinner_index = (job.spinner_index + 1) % len(_SPINNER)
        _tag_view3d_redraw()
        if not job.done:
            return {'PASS_THROUGH'}
        result = {'FINISHED'}
        if job.cancelled:
            _last_status = 'Quick Decimate cancelled'
            result = {'CANCELLED'}
        elif job.error:
            _last_status = f'Quick Decimate failed — {job.error}'
            self.report({'ERROR'}, job.error)
            result = {'CANCELLED'}
        else:
            try:
                imported = _import_decimate_result(context, job)
                source = bpy.data.objects.get(job.source_name)
                if source is None:
                    raise RuntimeError('Source mesh was renamed or removed')
                _move_to_original_meshes_collection(context, source)
                context.view_layer.objects.active = imported
                _last_status = f'Created {imported.name}'
                for stage, milliseconds in job.timings.items():
                    self.report(
                        {'INFO'},
                        f'Modus Quick Decimate — {stage}: '
                        f'{milliseconds / 1000.0:.3f} s',
                    )
                _clean_successful_job(job)
                self.report({'INFO'}, _last_status)
            except Exception as exc:
                _last_status = f'Quick Decimate import failed: {exc}'
                self.report({'ERROR'}, _last_status)
                result = {'CANCELLED'}
        _active_job = None
        _tag_view3d_redraw()
        return self._finish(context, result)

    def cancel(self, context):
        if _active_job is not None:
            _terminate_job(_active_job)
        return self._finish(context, {'CANCELLED'})

    def _finish(self, context, result):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        return result


class VIEW3D_PT_modus_retopology(bpy.types.Panel):
    bl_label = 'Modus Quad Engine'
    bl_idname = 'VIEW3D_PT_modus_retopology'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Modus'
    bl_order = 30
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def draw(self, context):
        layout = self.layout
        settings = context.scene.modus_retopology
        layout.prop(settings, 'target_triangles')
        row = layout.row(align=True)
        row.label(text='Symmetry')
        row.prop(settings, 'symmetry_x', toggle=True)
        row.prop(settings, 'symmetry_y', toggle=True)
        row.prop(settings, 'symmetry_z', toggle=True)
        layout.prop(settings, 'use_sharp_boundaries')
        layout.prop(settings, 'use_multiresolution')
        pre_smooth_row = layout.row(align=True)
        pre_smooth_row.prop(settings, 'use_pre_smooth')
        iterations = pre_smooth_row.row(align=True)
        iterations.enabled = settings.use_pre_smooth
        iterations.prop(settings, 'pre_smooth_iterations', text='')

        if _active_job is not None:
            row = layout.row()
            row.label(
                text=f'{_SPINNER[_active_job.spinner_index]} {_active_job.status}',
                icon='TIME',
            )
            layout.operator('modus.cancel_retopology', icon='CANCEL')
        else:
            row = layout.row()
            row.enabled = (
                context.active_object is not None
                and context.active_object.type == 'MESH'
            )
            row.operator(
                'modus.generate_retopology',
                text='Fast Retopology',
                icon='MOD_REMESH',
            )
            row = layout.row()
            row.enabled = (
                context.active_object is not None
                and context.active_object.type == 'MESH'
            )
            row.operator(
                'modus.generate_retopology_slow',
                text='Slow Retopology',
                icon='MOD_REMESH',
            )
            row = layout.row()
            row.enabled = (
                context.active_object is not None
                and context.active_object.type == 'MESH'
            )
            row.operator('modus.quick_decimate', icon='MOD_DECIM')
            layout.label(text=_last_status, icon='INFO')

        debug_box = layout.box()
        debug_row = debug_box.row()
        debug_row.prop(
            settings,
            'show_debug',
            text='DEBUG',
            icon='TRIA_DOWN' if settings.show_debug else 'TRIA_RIGHT',
            emboss=False,
        )
        if settings.show_debug:
            debug_box.prop(settings, 'debug_import_qem', toggle=True)
            debug_box.prop(settings, 'enable_smoothing', toggle=True)
            smoothing_column = debug_box.column()
            smoothing_column.enabled = settings.enable_smoothing
            smoothing_column.prop(settings, 'smoothing_iterations')


_CLASSES = (
    MODUS_RetopologySettings,
    MODUS_OT_generate_retopology,
    MODUS_OT_generate_retopology_slow,
    MODUS_OT_cancel_retopology,
    MODUS_OT_quick_decimate,
    VIEW3D_PT_modus_retopology,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.modus_retopology = PointerProperty(
        type=MODUS_RetopologySettings
    )


def unregister():
    global _active_job
    if _active_job is not None:
        _terminate_job(_active_job)
        _active_job = None
    if hasattr(bpy.types.Scene, 'modus_retopology'):
        del bpy.types.Scene.modus_retopology
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)