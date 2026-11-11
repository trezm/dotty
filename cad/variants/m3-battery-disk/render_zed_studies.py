"""Zed-inspired Blender studies, using the existing countersunk CAD meshes.

Blender --background --python render_zed_studies.py [-- --draft]
"""
import argparse
import importlib.util
import json
import math
from pathlib import Path
import sys

import bpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "docs/color-studies-zed"
spec = importlib.util.spec_from_file_location("dotty_studio", HERE / "render_color_studies.py")
studio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(studio)
studio.OUT = OUT
PALETTES = [
    {"slug": "01-carbon", "name": "Carbon", "subtitle": "Near-black body / Zed-yellow rim / graphite base",
     "body": "101018", "bezel": "FFFF00", "base": "272833", "floor": "14141D"},
    {"slug": "02-dayglow", "name": "Dayglow", "subtitle": "Zed-yellow body / black rim / dark-gray base",
     "body": "FFFF00", "bezel": "101018", "base": "484848", "floor": "14141D"},
    {"slug": "03-titanium", "name": "Titanium", "subtitle": "Warm-gray body / graphite rim / neon-yellow base",
     "body": "E1E0DB", "bezel": "484848", "base": "FFFF00", "floor": "14141D"},
    {"slug": "04-amethyst", "name": "Amethyst", "subtitle": "Zed violet / neon-yellow rim / midnight base",
     "body": "9A7ABD", "bezel": "FFFF00", "base": "101018", "floor": "14141D"},
]


def sand_material(name):
    mat = studio.matte(name, "11111B", .48, texture=False)
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    shader = nodes.get("Principled BSDF")
    shader.inputs["Specular IOR Level"].default_value = .5
    position = nodes.new("ShaderNodeTexCoord")
    grain = nodes.new("ShaderNodeTexNoise")
    grain.inputs["Scale"].default_value = 7000
    grain.inputs["Detail"].default_value = 2
    grain.inputs["Roughness"].default_value = .8
    links.new(position.outputs["Object"], grain.inputs["Vector"])
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = .72
    bump.inputs["Distance"].default_value = .00010
    links.new(grain.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], shader.inputs["Normal"])
    rough = nodes.new("ShaderNodeValToRGB")
    rough.color_ramp.elements[0].position = .28
    rough.color_ramp.elements[0].color = (.10, .10, .10, 1)
    rough.color_ramp.elements[1].position = .76
    rough.color_ramp.elements[1].color = (.63, .63, .63, 1)
    links.new(grain.outputs["Fac"], rough.inputs["Fac"])
    links.new(rough.outputs["Color"], shader.inputs["Roughness"])
    return mat


def sand_dunes(scene):
    floor = next(o for o in scene.objects if o.name.startswith("Seamless studio"))
    # Set dressing only: subtly rippled black sand, flat under the actual base.
    n, extent = 260, 1.2
    vertices, faces = [], []
    for j in range(n + 1):
        y = extent * (j / n - .5)
        for i in range(n + 1):
            x = extent * (i / n - .5)
            radius = math.hypot(x, y - .005)
            blend = min(1, max(0, (radius - .0336) / .018))
            blend = blend * blend * (3 - 2 * blend)
            height = .0009 * math.sin(145 * y + 1.2 * math.sin(31 * x)) + .00045 * math.sin(62 * x + 53 * y)
            vertices.append((x, y, -.00006 + blend * height))
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i
            faces.append((a, a+1, a+n+2, a+n+1))
    mesh = bpy.data.meshes.new("Black sand dunes (set dressing)")
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.append(sand_material("Zed-inspired volcanic sand | " + scene.name))
    for poly in mesh.polygons:
        poly.use_smooth = True
    floor.data = mesh
    floor.location.z = 0
    floor.name = "Black-sand studio setting"


def zed_screen(scene):
    image = bpy.data.images.load(str(OUT / "zed-dashboard-concept.png"), check_existing=True)
    image.pack()
    screen = next(o for o in scene.objects if o.name.startswith("Screen (illustrative"))
    for node in screen.data.materials[0].node_tree.nodes:
        if node.type == "TEX_IMAGE":
            node.image = image


def make_scene(palette, draft):
    scene = studio.make_scene(palette, draft, edgy=True)
    sand_dunes(scene)
    zed_screen(scene)
    if palette["name"] == "Titanium":
        body = next(o for o in scene.objects if o.name.startswith("puck-body"))
        shader = body.data.materials[0].node_tree.nodes.get("Principled BSDF")
        shader.inputs["Metallic"].default_value = .22
        shader.inputs["Roughness"].default_value = .31
    scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (.26, .28, .4, 1)
    scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = .065
    scene.view_settings.exposure = -.45
    key = next(o for o in scene.objects if o.name.startswith("Harder side key"))
    key.data.energy = .32
    key.data.color = (1, 1, 1)
    key.data.size = .10
    fill = next(o for o in scene.objects if o.name.startswith("Cool edge fill"))
    fill.data.energy = .17
    rim = next(o for o in scene.objects if o.name.startswith("Bright rear strip"))
    rim.data.energy = .8
    scene["Theme reference"] = "https://zed-web-site.vercel.app"
    scene["Concept note"] = "Site-inspired palette and black-sand staging. Dashboard recoloring is illustrative only. No firmware or CAD updates."
    scene.cycles.samples = 32 if draft else 192
    return scene


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft", action="store_true")
    args = parser.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.compute_device_type = "METAL"
    prefs.get_devices()
    for device in prefs.devices:
        device.use = device.type == "METAL"
    empty = bpy.context.scene
    palettes = PALETTES[:1] if args.draft else PALETTES
    scenes = [make_scene(p, args.draft) for p in palettes]
    bpy.data.scenes.remove(empty)
    if not args.draft:
        bpy.context.window.scene = scenes[0]
        bpy.context.preferences.filepaths.save_version = 0
        bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "dotty-zed-studies.blend"))
        (OUT / "palettes.json").write_text(json.dumps(PALETTES, indent=2) + "\n")
    for scene in scenes:
        bpy.context.window.scene = scene
        bpy.ops.render.render(write_still=True, scene=scene.name)
        print("FINISHED_ZED_STUDY", scene.render.filepath, flush=True)


if __name__ == "__main__":
    main()
