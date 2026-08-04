# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import bpy

from ..core import redraw, settings

_IMAGE_PREFIX = "Modus UV Grid"
_GROUP_NAME = "Modus UV Preview"
_MATERIAL_NAME = "Modus UV Preview"
_NODE_MARKER = "modus_uv_preview"
_TEMP_SLOT_MARKER = "modus_uv_preview_slot"
_viewport_states = {}


def _preferences(context=None):
    return settings.get_preferences(context)


def _image_size(prefs):
    return int(getattr(prefs, "uv_grid_resolution", "4096"))


def ensure_uv_image(context=None):
    prefs = _preferences(context)
    size = _image_size(prefs)
    style = getattr(prefs, "uv_grid_style", "UV_GRID")
    expected_name = f"{_IMAGE_PREFIX} {size} {style}"

    # Exactly one Modus test image is retained. Changing style or
    # resolution replaces the previous generated image.
    for image in list(bpy.data.images):
        if image.name.startswith(_IMAGE_PREFIX) and image.name != expected_name:
            bpy.data.images.remove(image)

    image = bpy.data.images.get(expected_name)
    if image is None:
        generated_type = "COLOR_GRID" if style == "COLOR_GRID" else "UV_GRID"
        # Blender 5.2 no longer accepts generated_type in Images.new().
        # Create the image first, then configure the generated pattern.
        image = bpy.data.images.new(
            expected_name,
            width=size,
            height=size,
            alpha=False,
        )
        image.generated_type = generated_type
    return image


def _ensure_group(image):
    group = bpy.data.node_groups.get(_GROUP_NAME)
    if group is None:
        group = bpy.data.node_groups.new(_GROUP_NAME, "ShaderNodeTree")
        try:
            group.interface.new_socket(name="Shader", in_out="INPUT", socket_type="NodeSocketShader")
            group.interface.new_socket(name="Shader", in_out="OUTPUT", socket_type="NodeSocketShader")
        except AttributeError:
            group.inputs.new("NodeSocketShader", "Shader")
            group.outputs.new("NodeSocketShader", "Shader")
        input_node = group.nodes.new("NodeGroupInput")
        output_node = group.nodes.new("NodeGroupOutput")
        texture = group.nodes.new("ShaderNodeTexImage")
        emission = group.nodes.new("ShaderNodeEmission")
        texture.name = "Modus UV Image"
        input_node.location = (-500, 100)
        texture.location = (-500, -100)
        emission.location = (-200, -100)
        output_node.location = (100, 0)
        group.links.new(texture.outputs["Color"], emission.inputs["Color"])
        group.links.new(emission.outputs["Emission"], output_node.inputs["Shader"])
    texture = group.nodes.get("Modus UV Image")
    if texture is not None:
        texture.image = image
    return group


def _preview_material(image):
    material = bpy.data.materials.get(_MATERIAL_NAME)
    if material is None:
        material = bpy.data.materials.new(_MATERIAL_NAME)
        material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    texture = tree.nodes.new("ShaderNodeTexImage")
    emission = tree.nodes.new("ShaderNodeEmission")
    texture.image = image
    tree.links.new(texture.outputs["Color"], emission.inputs["Color"])
    tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return material


def _material_enabled(material):
    return bool(material and material.use_nodes and any(node.get(_NODE_MARKER) for node in material.node_tree.nodes))


def _enable_material(material, group):
    if material is None:
        return
    material.use_nodes = True
    tree = material.node_tree
    for output in [node for node in tree.nodes if node.bl_idname == "ShaderNodeOutputMaterial"]:
        surface = output.inputs.get("Surface")
        if surface is None or any(link.from_node.get(_NODE_MARKER) for link in surface.links):
            continue
        original = surface.links[0].from_socket if surface.links else None
        if surface.links:
            tree.links.remove(surface.links[0])
        preview = tree.nodes.new("ShaderNodeGroup")
        preview.node_tree = group
        preview.label = "Modus UV Preview"
        preview.name = f"Modus UV Preview — {output.name}"
        preview[_NODE_MARKER] = True
        preview["output_node"] = output.name
        preview.location = (output.location.x - 220, output.location.y)
        if original is not None:
            tree.links.new(original, preview.inputs["Shader"])
        tree.links.new(preview.outputs["Shader"], surface)


def _disable_material(material):
    if not _material_enabled(material):
        return
    tree = material.node_tree
    for preview in [node for node in tree.nodes if node.get(_NODE_MARKER)]:
        output = tree.nodes.get(preview.get("output_node", ""))
        original = preview.inputs.get("Shader")
        source = original.links[0].from_socket if original and original.links else None
        if output is not None and output.inputs.get("Surface") is not None:
            surface = output.inputs["Surface"]
            for link in list(surface.links):
                if link.from_node is preview:
                    tree.links.remove(link)
            if source is not None:
                tree.links.new(source, surface)
        tree.nodes.remove(preview)


def selected_material_preview_enabled(context):
    objects = [obj for obj in context.selected_objects if obj and obj.type == "MESH"]
    if not objects:
        return False
    for obj in objects:
        for slot in obj.material_slots:
            material = slot.material
            if _material_enabled(material) or (material and material.get(_TEMP_SLOT_MARKER)):
                return True
    return False


class MODUS_OT_toggle_uv_material_preview(bpy.types.Operator):
    bl_idname = "modus.toggle_uv_material_preview"
    bl_label = "UV Material"
    bl_description = "Insert or remove the reversible Modus UV test shader"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and any(obj.type == "MESH" for obj in context.selected_objects)

    def execute(self, context):
        objects = [obj for obj in context.selected_objects if obj.type == "MESH"]
        disable = selected_material_preview_enabled(context)
        if disable:
            for obj in objects:
                for slot in obj.material_slots:
                    _disable_material(slot.material)
                # Remove only slots added by Modus; keep the material datablock.
                for index in reversed(range(len(obj.material_slots))):
                    material = obj.material_slots[index].material
                    if material and material.get(_TEMP_SLOT_MARKER):
                        obj.data.materials.pop(index=index)
        else:
            image = ensure_uv_image(context)
            group = _ensure_group(image)
            temp_material = _preview_material(image)
            temp_material[_TEMP_SLOT_MARKER] = True
            for obj in objects:
                materials = [slot.material for slot in obj.material_slots if slot.material]
                if not materials:
                    obj.data.materials.append(temp_material)
                    materials = [temp_material]
                for material in materials:
                    if material is temp_material:
                        continue
                    _enable_material(material, group)
        redraw.tag_view3d_redraw()
        return {"FINISHED"}


def _viewport_key(context):
    return context.area.as_pointer() if context.area else 0


def viewport_texture_enabled(context):
    if context.area is None or context.area.type != "VIEW_3D":
        return False
    shading = context.space_data.shading
    return shading.type == "SOLID" and getattr(shading, "color_type", None) == "TEXTURE"


class MODUS_OT_toggle_uv_texture_view(bpy.types.Operator):
    bl_idname = "modus.toggle_uv_texture_view"
    bl_label = "Texture View"
    bl_description = "Toggle Blender's Solid Texture shading for this viewport"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D" and context.mode in {"OBJECT", "SCULPT"}

    def execute(self, context):
        key = _viewport_key(context)
        shading = context.space_data.shading
        if viewport_texture_enabled(context):
            state = _viewport_states.pop(key, None)
            if state is not None:
                shading.type = state["type"]
                if hasattr(shading, "color_type"):
                    shading.color_type = state["color_type"]
            else:
                # Texture view may have been enabled through Blender's own UI.
                # In that case, leave Solid shading active and return its source to Material.
                shading.type = "SOLID"
                if hasattr(shading, "color_type"):
                    shading.color_type = "MATERIAL"
        else:
            _viewport_states[key] = {
                "type": shading.type,
                "color_type": getattr(shading, "color_type", "MATERIAL"),
            }
            shading.type = "SOLID"
            shading.color_type = "TEXTURE"
        context.area.tag_redraw()
        return {"FINISHED"}


def draw_material_preview(layout, context):
    layout.operator(
        MODUS_OT_toggle_uv_material_preview.bl_idname,
        text="UV Material",
        icon="TEXTURE",
        depress=selected_material_preview_enabled(context),
    )


def draw_viewport_control(layout, context):
    layout.operator(
        MODUS_OT_toggle_uv_texture_view.bl_idname,
        text="Texture View",
        icon="TEXTURE",
        depress=viewport_texture_enabled(context),
    )



_CLASSES = (
    MODUS_OT_toggle_uv_material_preview,
    MODUS_OT_toggle_uv_texture_view,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    _viewport_states.clear()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
