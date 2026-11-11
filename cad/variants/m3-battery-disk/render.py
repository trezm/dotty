"""Render the actual exported CAD; screen and battery envelope are illustrative."""
import json
import math
from pathlib import Path
import bpy
from mathutils import Vector

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "docs" / "battery-disk"
P = json.loads((ROOT / "cad" / "parameters.json").read_text())
P.update(json.loads((HERE / "parameters.json").read_text()))
APPEARANCE = json.loads((HERE / "appearance.json").read_text())
MM = .001
ANGLE = math.radians(P["face_angle_degrees"])
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)


def material(name, color, roughness=.4, metal=0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    shader = mat.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (*color, 1)
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Metallic"].default_value = metal
    return mat


orange_spec = APPEARANCE["materials"]["orange"]
slate_spec = APPEARANCE["materials"]["slate"]
orange = material(orange_spec["name"], orange_spec["rgb"])
slate = material(slate_spec["name"], slate_spec["rgb"], .5)
glass_mat = material("Illustrative glass", (.008, .013, .018), .22)
cell_mat = material("40 x 30 x 8 envelope", (.58, .61, .65), .42, .6)
floor_mat = material("Studio floor", (.71, .74, .78), .8)


def imported(name, mat):
    bpy.ops.wm.stl_import(filepath=str(HERE / "exports" / (name + ".stl")))
    obj = bpy.context.object
    obj.name = name
    obj.scale = (MM, MM, MM)
    obj.data.materials.append(mat)
    return obj


body = imported("puck-body", orange)
body.location.z = P["disk_height"] * MM
cup = imported("base-disk", slate)
for v in cup.data.vertices:
    v.co.y *= -1
    v.co.z = P["disk_height"] - v.co.z
cover = imported("battery-cover", slate)
bezel = imported("bezel", orange)
for v in bezel.data.vertices:
    v.co.y *= -1
    v.co.z = P["bezel_thickness"] - v.co.z
bezel.rotation_euler.x = ANGLE
face_height = P["face_center_height"] + P["disk_height"]
bezel.location.z = face_height * MM
bpy.ops.mesh.primitive_cylinder_add(vertices=160, radius=21.3 * MM, depth=.3 * MM)
glass = bpy.context.object
glass.rotation_euler.x = ANGLE
glass.location = (0, .35 * math.sin(ANGLE) * MM,
                  (face_height - .35 * math.cos(ANGLE)) * MM)
glass.data.materials.append(glass_mat)
bpy.ops.mesh.primitive_cube_add(size=1)
cell = bpy.context.object
cell.dimensions = tuple(P[key] * MM for key in ("battery_width", "battery_length", "battery_thickness"))
cell.location = (0, P["battery_center_y"] * MM,
                 (P["battery_bottom_skin"] + P["battery_clearance_each_side"] + P["battery_thickness"] / 2) * MM)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
cell.data.materials.append(cell_mat)
bevel = cell.modifiers.new("Illustrative pouch edge", "BEVEL")
bevel.width, bevel.segments = .5 * MM, 3
cell.hide_render = True
bpy.ops.mesh.primitive_plane_add(size=2)
floor = bpy.context.object
floor.location.z = -.0001
floor.data.materials.append(floor_mat)


def point_at(obj, target):
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()


for location, energy, size in (((.06, -.1, .16), .55, .14),
                               ((-.08, .09, .13), .4, .12), ((-.12, -.06, .07), .2, .1)):
    bpy.ops.object.light_add(type="AREA", location=location)
    light = bpy.context.object
    light.data.energy, light.data.size, light.data.shape = energy, size, "DISK"
    point_at(light, (0, .006, .025))
bpy.ops.object.camera_add(location=(.095, -.135, .09))
camera = bpy.context.object
camera.data.type, camera.data.ortho_scale, camera.data.clip_start = "ORTHO", .105, .001
point_at(camera, (0, .006, .025))
scene = bpy.context.scene
scene.camera, scene.render.engine = camera, "CYCLES"
scene.cycles.samples, scene.cycles.use_denoising = 32, True
scene.world.color = (.4, .4, .4)
scene.view_settings.view_transform = "AgX"
scene.render.resolution_x, scene.render.resolution_y = 1400, 1050
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
OUT.mkdir(parents=True, exist_ok=True)


def render(name):
    scene.render.filepath = str(OUT / name)
    bpy.ops.render.render(write_still=True)


render("front-render.png")
camera.location = (.13, 0, .06)
camera.data.ortho_scale = .10
point_at(camera, (0, .005, .025))
render("side-render.png")
# Pull the cover and cell downward to show the straight loading route.
bezel.hide_render = glass.hide_render = True
cell.hide_render = False
cover.location.z = -.024
cell.location.z -= .016
floor.location.z = -.025
camera.location = (.10, -.14, .09)
camera.data.ortho_scale = .13
point_at(camera, (0, .008, .01))
render("bottom-loading-render.png")
