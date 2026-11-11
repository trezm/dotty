"""Render the actual battery-variant STLs with illustrative glass and cell.

Run with Blender in background mode, after build.py. Render-only illustrative
parts do not change any exported print geometry.
"""
import json
import math
from pathlib import Path

import bpy
from mathutils import Vector

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "docs" / "battery-study"
P = json.loads((ROOT / "cad/parameters.json").read_text())
P.update(json.loads((HERE / "parameters.json").read_text()))
MM = 0.001
ANGLE = math.radians(P["face_angle_degrees"])
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)


def material(name, rgb, roughness=0.4, metallic=0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    shader = mat.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (*rgb, 1)
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Metallic"].default_value = metallic
    return mat


orange = material("Orange shell", (0.95, 0.19, 0.018), 0.37)
glass_mat = material("Illustrative blank glass", (0.008, 0.013, 0.018), 0.22)
cell_mat = material("Illustrative battery envelope", (0.58, 0.61, 0.65), 0.42, 0.6)
floor_mat = material("Studio", (0.71, 0.74, 0.78), 0.8)


def imported(name):
    bpy.ops.wm.stl_import(filepath=str(HERE / "exports" / f"{name}.stl"))
    obj = bpy.context.object
    obj.name = name
    obj.scale = (MM, MM, MM)
    obj.data.materials.append(orange)
    return obj


body = imported("body")
bezel = imported("bezel")
for vertex in bezel.data.vertices:
    vertex.co.y *= -1
    vertex.co.z = P["bezel_thickness"] - vertex.co.z
bezel.rotation_euler.x = ANGLE
bezel.location.z = P["face_center_height"] * MM

bpy.ops.mesh.primitive_cylinder_add(vertices=180, radius=21.3 * MM, depth=0.3 * MM)
glass = bpy.context.object
glass.name = "Illustrative cover glass"
glass.rotation_euler.x = ANGLE
glass.location = (0, 0.35 * math.sin(ANGLE) * MM,
                  (P["face_center_height"] - 0.35 * math.cos(ANGLE)) * MM)
glass.data.materials.append(glass_mat)

bpy.ops.mesh.primitive_cube_add(size=1)
cell = bpy.context.object
cell.name = "Illustrative 34 x 25 x 5 mm battery block"
cell.dimensions = (P["battery_width"] * MM, P["battery_length"] * MM,
                   P["battery_thickness"] * MM)
cell.location = (0, P["battery_center_y"] * MM,
                 (P["battery_bottom_skin"] + P["battery_clearance"]
                  + P["battery_thickness"] / 2) * MM)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
cell.data.materials.append(cell_mat)
bevel = cell.modifiers.new("Pouch edge illustration", "BEVEL")
bevel.width = 0.6 * MM
bevel.segments = 3
cell.hide_render = True

bpy.ops.mesh.primitive_plane_add(size=2)
floor = bpy.context.object
floor.location.z = -0.0001
floor.data.materials.append(floor_mat)


def point_at(obj, target):
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()


for name, location, energy, size in (
        ("Softbox", (0.06, -0.10, 0.16), 0.55, 0.14),
        ("Rim", (-0.08, 0.09, 0.13), 0.4, 0.12),
        ("Fill", (-0.12, -0.06, 0.07), 0.2, 0.1)):
    bpy.ops.object.light_add(type="AREA", location=location)
    light = bpy.context.object
    light.name = name
    light.data.energy = energy
    light.data.shape = "DISK"
    light.data.size = size
    point_at(light, (0, 0.006, 0.02))

bpy.ops.object.camera_add(location=(0.095, -0.135, 0.085))
camera = bpy.context.object
camera.data.type = "ORTHO"
camera.data.ortho_scale = 0.095
camera.data.clip_start = 0.001
point_at(camera, (0, 0.006, 0.019))
scene = bpy.context.scene
scene.camera = camera
scene.render.engine = "CYCLES"
scene.cycles.samples = 32
scene.cycles.use_denoising = True
scene.world.color = (0.4, 0.4, 0.4)
scene.view_settings.view_transform = "AgX"
scene.render.resolution_x = 1400
scene.render.resolution_y = 1050
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
OUT.mkdir(exist_ok=True, parents=True)
scene.render.filepath = str(OUT / "crosswise-front-render.png")
bpy.ops.render.render(write_still=True)

bezel.hide_render = True
glass.hide_render = True
cell.hide_render = False
camera.location = (0.045, -0.075, 0.17)
camera.data.ortho_scale = 0.088
point_at(camera, (0, 0.005, 0.012))
scene.render.filepath = str(OUT / "crosswise-open-render.png")
bpy.ops.render.render(write_still=True)
