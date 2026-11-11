"""Blender-only color studies from existing STL exports; no CAD exports are written.

Blender --background --python render_color_studies.py -- --draft
Blender --background --python render_color_studies.py
"""
import argparse
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "docs" / "color-studies"
P = json.loads((ROOT / "cad" / "parameters.json").read_text())
P.update(json.loads((HERE / "parameters.json").read_text()))
ANGLE = math.radians(P["face_angle_degrees"])
FACE_Z = (P["face_center_height"] + P["disk_height"]) / 1000
PALETTES = [
    {"slug": "01-clay-ink", "name": "Clay / Ink", "subtitle": "Warm terracotta, apricot rim, deep ink base",
     "body": "BB6046", "bezel": "E4AC82", "base": "29393B", "floor": "CFC5B8"},
    {"slug": "02-petrol-mint", "name": "Petrol / Mint", "subtitle": "Deep teal, sea-glass rim, pale mineral base",
     "body": "235A61", "bezel": "98C7BB", "base": "D7D3C5", "floor": "BFC9C6"},
    {"slug": "03-sage-cream", "name": "Sage / Cream", "subtitle": "Soft sage, warm ivory rim, forest base",
     "body": "8E9C7D", "bezel": "E7DFC4", "base": "37463D", "floor": "C9CCBD"},
    {"slug": "04-violet-hour", "name": "Violet Hour", "subtitle": "Dusty violet, lilac rim, midnight base",
     "body": "61557F", "bezel": "C6B6DE", "base": "292638", "floor": "C4BFCE"},
    {"slug": "05-porcelain", "name": "Porcelain", "subtitle": "Chalk body, champagne rim, blue-grey base",
     "body": "E4DFD3", "bezel": "C4B494", "base": "74868B", "floor": "D0CDC4"},
    {"slug": "06-acid-graphite", "name": "Acid / Graphite", "subtitle": "Electric citron, graphite rim and base",
     "body": "D6E73B", "bezel": "30383D", "base": "242D33", "floor": "C5C7BD"},
]
EDGY_PALETTES = [
    {"slug": "01-hazard", "name": "Hazard", "subtitle": "Graphite body / acid rim / signal-orange base",
     "body": "262B2F", "bezel": "E7F51B", "base": "F15020", "floor": "30343B"},
    {"slug": "02-ultraviolet", "name": "Ultraviolet", "subtitle": "Electric purple / laser-lime rim / near-black base",
     "body": "5620CC", "bezel": "BAFF20", "base": "171322", "floor": "30343B"},
    {"slug": "03-hotwire", "name": "Hotwire", "subtitle": "Hot-pink body / black rim / violet base",
     "body": "F33A80", "bezel": "171A24", "base": "5A29C9", "floor": "30343B"},
    {"slug": "04-voltage", "name": "Voltage", "subtitle": "Cobalt body / electric-cyan rim / ink base",
     "body": "163D97", "bezel": "16DAE4", "base": "171D2C", "floor": "30343B"},
    {"slug": "05-monolith", "name": "Monolith", "subtitle": "Black body / satin-metal rim / charcoal base",
     "body": "171A20", "bezel": "9CA6AD", "base": "23272D", "floor": "30343B", "rim_metallic": .75},
    {"slug": "06-rally", "name": "Rally", "subtitle": "Racing-red body / chalk rim / black base",
     "body": "EA311E", "bezel": "F3F0E8", "base": "1A2025", "floor": "30343B"},
]


def linear(hex_color):
    rgb = [int(hex_color[i:i+2], 16) / 255 for i in (0, 2, 4)]
    return tuple(c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in rgb)


def matte(name, hex_color, roughness=.43, texture=True, metallic=0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    shader = nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (*linear(hex_color), 1)
    mat.diffuse_color = (*linear(hex_color), 1)
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Metallic"].default_value = metallic
    shader.inputs["Specular IOR Level"].default_value = .3
    if texture:
        coord = nodes.new("ShaderNodeTexCoord")
        grain = nodes.new("ShaderNodeTexNoise")
        grain.inputs["Scale"].default_value = 42000
        grain.inputs["Detail"].default_value = 2
        links.new(coord.outputs["Object"], grain.inputs["Vector"])
        bump = nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = .18
        bump.inputs["Distance"].default_value = .000008
        links.new(grain.outputs["Fac"], bump.inputs["Height"])
        links.new(bump.outputs["Normal"], shader.inputs["Normal"])
    return mat


def finish_mesh(obj, bevel=.000055):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    for face in obj.data.polygons:
        face.use_smooth = True
    obj.data.set_sharp_from_angle(angle=math.radians(32))
    if bevel:
        mod = obj.modifiers.new("Tiny edge highlights (render only)", "BEVEL")
        mod.width, mod.segments, mod.limit_method = bevel, 3, "ANGLE"
        mod.angle_limit = math.radians(35)
    weighted = obj.modifiers.new("Weighted surface normals", "WEIGHTED_NORMAL")
    weighted.keep_sharp = True
    obj.select_set(False)


def part(name, material):
    bpy.ops.wm.stl_import(filepath=str(HERE / "exports" / (name + ".stl")))
    obj = bpy.context.object
    obj.name = name
    # Convert exported millimetres to Blender metres in the mesh itself.
    for v in obj.data.vertices:
        v.co *= .001
    obj.data.materials.append(material)
    # The CAD already has edge chamfers. Extra bevels on triangulated bores
    # create shading scratches; retain the exported geometry here.
    finish_mesh(obj, bevel=0)
    return obj


def face_location(x, y, z):
    return (x, y * math.cos(ANGLE) - z * math.sin(ANGLE),
            FACE_Z + y * math.sin(ANGLE) + z * math.cos(ANGLE))


def aim(obj, point):
    obj.rotation_euler = (Vector(point) - obj.location).to_track_quat("-Z", "Y").to_euler()


def area(name, location, target, power, size, color=(1, 1, 1), size_y=None):
    bpy.ops.object.light_add(type="AREA", location=location)
    obj = bpy.context.object
    obj.name = name
    obj.data.energy, obj.data.color, obj.data.size = power, color, size
    obj.data.shape = "RECTANGLE" if size_y else "DISK"
    if size_y:
        obj.data.size_y = size_y
    aim(obj, target)


def display():
    # Exact existing firmware layout, with illustrative example counts.
    texture = bpy.data.images.load(str(ROOT / "docs/ui/ring-dashboard-example-battery-layout.png"), check_existing=True)
    texture.pack()
    mat = matte("Illustrative dashboard under satin glass", "080C10", .23, texture=False)
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    shader = nodes.get("Principled BSDF")
    uv, tex = nodes.new("ShaderNodeTexCoord"), nodes.new("ShaderNodeTexImage")
    tex.image = texture
    tex.extension = "EXTEND"
    links.new(uv.outputs["UV"], tex.inputs["Vector"])
    links.new(tex.outputs["Color"], shader.inputs["Base Color"])
    links.new(tex.outputs["Color"], shader.inputs["Emission Color"])
    shader.inputs["Emission Strength"].default_value = .3
    shader.inputs["Specular IOR Level"].default_value = .18
    radius, steps = .02125, 192
    vertices = [(0, 0, 0)] + [(radius * math.cos(2 * math.pi * i / steps), radius * math.sin(2 * math.pi * i / steps), 0) for i in range(steps)]
    faces = [(0, 1+i, 1+(i+1) % steps) for i in range(steps)]
    mesh = bpy.data.meshes.new("Round display face")
    mesh.from_pydata(vertices, [], faces)
    mesh.uv_layers.new(name="Dashboard")
    for poly in mesh.polygons:
        for loop in poly.loop_indices:
            v = mesh.vertices[mesh.loops[loop].vertex_index].co
            # Fit the complete UI within the bezel's visible aperture.
            mesh.uv_layers.active.data[loop].uv = (.5 + v.x / .0366, .5 + v.y / .0366)
    obj = bpy.data.objects.new("Screen (illustrative UI, not live status)", mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(mat)
    obj.rotation_euler.x = ANGLE
    obj.location = face_location(0, 0, -.00005)


def fasteners():
    mat = matte("Satin graphite fasteners (illustrative)", "292D30", .3, texture=False, metallic=.8)
    for angle in (-90, 30, 150):
        x = .025 * math.cos(math.radians(angle))
        y = .025 * math.sin(math.radians(angle))
        # Illustrative 90-degree head seated against the actual CAD taper.
        head_radius = P["bezel_nominal_head_diameter"] / 2000
        shaft_radius = P["screw_shaft_diameter"] / 2000
        slope = math.tan(math.radians(P["bezel_countersink_angle"] / 2))
        head_height = (head_radius - shaft_radius) / slope
        head_top = P["bezel_thickness"] / 1000 - (P["bezel_countersink_diameter"] / 2000 - head_radius) / slope
        bpy.ops.mesh.primitive_cone_add(vertices=96, radius1=shaft_radius, radius2=head_radius, depth=head_height)
        head = bpy.context.object
        head.name = "Illustrative M3 90-degree countersunk head"
        bpy.ops.mesh.primitive_cylinder_add(vertices=6, radius=.001155, depth=.0009, location=(0, 0, head_height / 2 - .00035))
        cutter = bpy.context.object
        mod = head.modifiers.new("Hex drive", "BOOLEAN")
        mod.object = cutter
        bpy.context.view_layer.objects.active = head
        bpy.ops.object.modifier_apply(modifier=mod.name)
        bpy.data.objects.remove(cutter, do_unlink=True)
        head.data.materials.clear()
        head.data.materials.append(mat)
        for poly in head.data.polygons:
            poly.material_index = 0
        finish_mesh(head, .00004)
        head.location = face_location(x, y, head_top - head_height / 2)
        head.rotation_euler.x = ANGLE


def make_scene(palette, draft, edgy=False):
    scene = bpy.data.scenes.new(palette["name"].replace(" / ", " + "))
    bpy.context.window.scene = scene
    body_mat = matte(palette["name"] + " | body", palette["body"], .36 if edgy else .43)
    rim_mat = matte(palette["name"] + " | bezel", palette["bezel"], .27 if edgy else .38,
                    metallic=palette.get("rim_metallic", 0))
    base_mat = matte(palette["name"] + " | base", palette["base"], .49)
    body = part("puck-body", body_mat)
    body.location.z = P["disk_height"] / 1000
    cup = part("base-disk", base_mat)
    for v in cup.data.vertices:
        v.co.y *= -1
        v.co.z = P["disk_height"] / 1000 - v.co.z
    part("battery-cover", base_mat)
    rim = part("bezel", rim_mat)
    for v in rim.data.vertices:
        v.co.y *= -1
        v.co.z = P["bezel_thickness"] / 1000 - v.co.z
    rim.rotation_euler.x = ANGLE
    rim.location.z = FACE_Z
    display()
    fasteners()
    bpy.ops.mesh.primitive_plane_add(size=200)
    floor = bpy.context.object
    floor.name = "Seamless studio surface"
    floor.location.z = -.00006
    floor.data.materials.append(matte("Studio | " + palette["name"], palette["floor"], .7, texture=False))
    focus = (0, .003, .022)
    if edgy:
        area("Harder side key", (-.085, -.095, .15), focus, .48, .075, (1, .98, .95), .13)
        area("Cool edge fill", (.12, -.015, .08), focus, .12, .035, (.84, .92, 1), .12)
        area("Bright rear strip", (.015, .10, .13), focus, .7, .095, (1, 1, 1), .025)
        area("Frontal bounce", (-.01, -.15, .035), focus, .025, .08)
    else:
        area("Large warm key", (-.085, -.09, .16), focus, .36, .125, (1, .94, .86), .1)
        area("Cool side fill", (.105, -.02, .085), focus, .15, .105, (.86, .94, 1), .13)
        area("Back rim strip", (.02, .12, .14), focus, .48, .095, (1, 1, 1), .045)
        area("Soft frontal bounce", (-.01, -.15, .045), focus, .035, .09, (1, .97, .94))
    bpy.ops.object.camera_add(location=(.112, -.16, .125) if edgy else (.103, -.152, .142))
    camera = bpy.context.object
    camera.name = "Hero camera"
    camera.data.type = "PERSP"
    camera.data.lens, camera.data.sensor_width = 88, 36
    camera.data.clip_start, camera.data.clip_end = .001, 1000
    aim(camera, focus)
    scene.camera = camera
    scene.world = bpy.data.worlds.new("Studio environment | " + palette["name"])
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (.5, .55, .6, 1)
    scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = .06 if edgy else .12
    scene.render.engine = "CYCLES"
    scene.cycles.device = "GPU"
    scene.cycles.samples = 32 if draft else 144
    scene.cycles.use_denoising = True
    scene.cycles.adaptive_threshold = .015
    scene.cycles.max_bounces = 8
    scene.render.resolution_x, scene.render.resolution_y = (960, 880) if draft else (1920, 1760)
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = -.25 if edgy else -.55
    scene.render.filepath = str(OUT / (palette["slug"] + ("-draft" if draft else "") + ".png"))
    scene["Palette"] = json.dumps(palette)
    scene["Geometry source"] = str(HERE / "exports")
    scene["Notes"] = "Existing CAD meshes. Illustrative UI/fasteners; micro-bevels and finishes are render only."
    # Open the saved file in the hero camera, with material colors available.
    for area_ui in bpy.context.screen.areas:
        if area_ui.type == "VIEW_3D":
            area_ui.spaces.active.region_3d.view_perspective = "CAMERA"
            area_ui.spaces.active.shading.type = "MATERIAL"
            area_ui.spaces.active.shading.use_scene_world = True
            area_ui.spaces.active.shading.use_scene_lights = True
    return scene


def main():
    global OUT
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft", action="store_true")
    parser.add_argument("--edgy", action="store_true")
    args = parser.parse_args(argv)
    if args.edgy:
        OUT = ROOT / "docs" / "color-studies-edgy"
    OUT.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.compute_device_type = "METAL"
    prefs.get_devices()
    for device in prefs.devices:
        device.use = device.type == "METAL"
    empty = bpy.context.scene
    palettes = EDGY_PALETTES if args.edgy else PALETTES
    if args.draft:
        palettes = palettes[:1]
    scenes = [make_scene(p, args.draft, args.edgy) for p in palettes]
    bpy.data.scenes.remove(empty)
    if not args.draft:
        bpy.context.window.scene = scenes[0]
        bpy.context.preferences.filepaths.save_version = 0
        blend_name = "dotty-edgy-color-studies.blend" if args.edgy else "dotty-color-studies.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(OUT / blend_name))
        (OUT / "palettes.json").write_text(json.dumps(palettes, indent=2) + "\n")
    for scene in scenes:
        bpy.context.window.scene = scene
        bpy.ops.render.render(write_still=True, scene=scene.name)
        print("FINISHED_COLOR_STUDY", scene.render.filepath, flush=True)


if __name__ == "__main__":
    main()
