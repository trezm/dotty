"""Run with Blender: blender -b --python scripts/render_preview.py

Renders the actual exported STL geometry, with illustrative display/electronics.
Uses Blender's Python; Pillow is only required for scripts/make_display_preview.py.
"""
import json
import math
from pathlib import Path
import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
P = json.loads((ROOT / "cad/parameters.json").read_text())
MM = 0.001
angle = math.radians(P["face_angle_degrees"])
center_z = P["face_center_height"]
height = 0  # Body/bezel joint is local z=0 in the sliced design.

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)


def material(name, color, roughness=0.4, metallic=0):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat


shell_mat = material("Warm porcelain PLA", (0.68, 0.71, 0.68), 0.36)
glass_mat = material("Black cover glass", (0.006, 0.012, 0.014), 0.17)
metal_mat = material("Screw heads", (0.12, 0.15, 0.16), 0.26, 0.8)
pcb_mat = material("Illustrative PCB", (0.025, 0.19, 0.12), 0.6)


def puck_transform(obj, local_z=0):
    obj.rotation_euler.x = angle
    # Object's pre-existing geometry is centered around local z=0.
    obj.location = (0, -local_z * math.sin(angle) * MM,
                    (center_z + local_z * math.cos(angle)) * MM)


def import_part(name, mat, lift=0):
    bpy.ops.wm.stl_import(filepath=str(ROOT / "cad/exports" / f"{name}.stl"))
    obj = bpy.context.object
    obj.name = name
    obj.scale = (MM, MM, MM)
    obj.data.materials.append(mat)
    if name == "bezel":
        # STL was flipped face-down for the printer; undo that here.
        for v in obj.data.vertices:
            v.co.y *= -1
            v.co.z = P["bezel_thickness"] - v.co.z
        puck_transform(obj, lift)
    return obj


def disk(name, radius, depth, z, mat):
    bpy.ops.mesh.primitive_cylinder_add(vertices=160, radius=radius * MM,
                                       depth=depth * MM)
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(mat)
    puck_transform(obj, z)
    return obj


body = import_part("body", shell_mat)
bezel = import_part("bezel", shell_mat)
glass = disk("Illustrative display glass", 21.45, 0.5, height - 0.45, glass_mat)
pcb = disk("Illustrative display PCB", 21.45, 1, -P["display_stack_allowance"] + 0.5, pcb_mat)

# A circular, texture-mapped active display: real hardware's active area is 32.4 mm.
verts = [(0, 0, 0)]
for i in range(161):
    t = i * math.tau / 160
    verts.append((16.2 * MM * math.cos(t), 16.2 * MM * math.sin(t), 0))
faces = [(0, i + 1, i + 2) for i in range(160)]
mesh = bpy.data.meshes.new("LCD surface")
mesh.from_pydata(verts, [], faces)
mesh.uv_layers.new()
for polygon in mesh.polygons:
    for loop_id in polygon.loop_indices:
        v = mesh.vertices[mesh.loops[loop_id].vertex_index].co
        mesh.uv_layers.active.data[loop_id].uv = (v.x / (32.4 * MM) + 0.5,
                                                v.y / (32.4 * MM) + 0.5)
lcd = bpy.data.objects.new("Illustrative hello-world UI", mesh)
bpy.context.collection.objects.link(lcd)
puck_transform(lcd, height - 0.19)
lcd_mat = bpy.data.materials.new("LCD pixels")
lcd_mat.use_nodes = True
nodes = lcd_mat.node_tree.nodes
nodes.clear()
output = nodes.new("ShaderNodeOutputMaterial")
emission = nodes.new("ShaderNodeEmission")
texture = nodes.new("ShaderNodeTexImage")
texture.image = bpy.data.images.load(str(ROOT / "docs/display-texture.png"))
lcd_mat.node_tree.links.new(texture.outputs["Color"], emission.inputs["Color"])
lcd_mat.node_tree.links.new(emission.outputs[0], output.inputs["Surface"])
lcd.data.materials.append(lcd_mat)

# Three illustrative screw heads at the actual screw coordinates.
screws = []
for a in (-90, 30, 150):
    a = math.radians(a)
    obj = disk("M2 screw", 1.8, 0.8, height + P["bezel_thickness"] - 0.7, metal_mat)
    local_x = P["screw_circle_diameter"] / 2 * math.cos(a) * MM
    local_y = P["screw_circle_diameter"] / 2 * math.sin(a) * MM
    obj.location += Vector((local_x, local_y * math.cos(angle), local_y * math.sin(angle)))
    screws.append(obj)

bpy.ops.mesh.primitive_plane_add(size=2000 * MM)
floor = bpy.context.object
floor.name = "Studio floor"
floor.location.z = -0.0001
floor.data.materials.append(material("Studio", (0.14, 0.17, 0.19), 0.75))


def point_at(obj, target):
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()


def area(name, location, energy, size):
    bpy.ops.object.light_add(type="AREA", location=location)
    obj = bpy.context.object
    obj.name = name
    obj.data.energy = energy
    obj.data.shape = "DISK"
    obj.data.size = size
    point_at(obj, (0, 0, 0.025))


area("Large softbox", (0.07, -0.09, 0.18), 0.5, 0.16)
area("Rear rim light", (-0.08, 0.08, 0.14), 0.4, 0.12)
area("Fill", (-0.12, -0.04, 0.07), 0.15, 0.1)
bpy.ops.object.camera_add(location=(0.105, -0.15, 0.1))
camera = bpy.context.object
camera.data.type = "ORTHO"
camera.data.ortho_scale = 0.115
camera.data.clip_start = 0.001
point_at(camera, (0, 0.009, 0.018))
scene = bpy.context.scene
scene.camera = camera
scene.render.engine = "CYCLES"
scene.cycles.samples = 40
scene.cycles.use_denoising = True
scene.world.color = (0.18, 0.18, 0.18)
scene.render.resolution_x = 1400
scene.render.resolution_y = 1100
scene.render.resolution_percentage = 100
scene.view_settings.view_transform = "AgX"
scene.render.image_settings.file_format = "PNG"
scene.render.filepath = str(ROOT / "docs/enclosure-preview.png")
bpy.ops.wm.save_as_mainfile(filepath=str(ROOT / "cad/dotty-preview.blend"))
bpy.ops.render.render(write_still=True)

# Low rear view shows the back plane parallel to the display.
camera.location = (0.105, 0.15, 0.055)
point_at(camera, (0, 0.007, 0.018))
scene.render.filepath = str(ROOT / "docs/rear-preview.png")
bpy.ops.render.render(write_still=True)

# Side profile makes the parallel faces and the horizontal desk slice clear.
camera.location = (0.17, 0.006, 0.018)
camera.data.ortho_scale = 0.095
point_at(camera, (0, 0.006, 0.018))
scene.render.filepath = str(ROOT / "docs/side-preview.png")
bpy.ops.render.render(write_still=True)

camera.location = (0.105, -0.15, 0.1)
# Same actual parts, spaced along the display normal for assembly context.
for obj, distance in [(pcb, 12), (glass, 12), (lcd, 12), (bezel, 23)]:
    obj.location += Vector((0, -math.sin(angle) * distance * MM,
                           math.cos(angle) * distance * MM))
for obj in screws:
    obj.location += Vector((0, -math.sin(angle) * 28 * MM, math.cos(angle) * 28 * MM))
camera.data.ortho_scale = 0.135
point_at(camera, (0, 0.002, 0.031))
scene.render.filepath = str(ROOT / "docs/exploded-preview.png")
bpy.ops.render.render(write_still=True)
