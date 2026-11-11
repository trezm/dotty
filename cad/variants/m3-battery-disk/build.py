"""Original sliced puck on a separate circular base for a 40 x 30 x 8 mm cell."""
import json
import math
from pathlib import Path
import sys
import zipfile
import cadquery as cq
import trimesh

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "cad"))
import enclosure

OUT = HERE / "exports"
DOCS = ROOT / "docs" / "battery-disk"
NAME = "dotty-m3-battery-disk"
APPEARANCE = json.loads((HERE / "appearance.json").read_text())


def part_color(name):
    material = APPEARANCE["materials"][APPEARANCE["parts"][name]]
    # Blender's material RGB is linear; preserve the same swatches in STEP.
    return cq.Color(*material["rgb"], 1.0, False)


def volume(shape):
    return shape.val().Volume()


def passage(x, y, z, height):
    return (cq.Workplane("XY").box(8, 5, height, centered=(True, True, False))
            .edges("|Z").fillet(1).translate((x, y, z)))


def countersink(part, centers, thickness, mouth, shaft, angle):
    depth = (mouth - shaft) / (2 * math.tan(math.radians(angle / 2)))
    assert 0 < depth < thickness, "Countersink must retain a straight hole land"
    holes = (cq.Workplane("XY", origin=(0, 0, -.01)).pushPoints(centers)
             .circle(shaft / 2).extrude(thickness + .02))
    part = part.cut(holes)
    for x, y in centers:
        cutter = cq.Solid.makeCone(shaft / 2, mouth / 2, depth,
                                  cq.Vector(x, y, thickness - depth))
        part = part.cut(cq.Workplane("XY").newObject([cutter]))
    return part, depth


def countersunk_bezel(p):
    centers = [(p["screw_circle_diameter"] / 2 * math.cos(math.radians(a)),
                p["screw_circle_diameter"] / 2 * math.sin(math.radians(a)))
               for a in (-90, 30, 150)]
    thickness = p["bezel_thickness"]
    bezel = enclosure.ring(p["outer_diameter"], p["view_diameter"], thickness)
    bezel = bezel.edges(">Z").chamfer(.4)
    bezel, depth = countersink(bezel, centers, thickness, p["bezel_countersink_diameter"],
                              p["screw_clearance_diameter"], p["bezel_countersink_angle"])
    # Flat heads are measured over the head, unlike the prior pan/socket heads.
    tangent = math.tan(math.radians(p["bezel_countersink_angle"] / 2))
    head_top = thickness - (p["bezel_countersink_diameter"] - p["bezel_nominal_head_diameter"]) / (2 * tangent)
    tip = head_top - p["screw_length"]
    shafts = (cq.Workplane("XY", origin=(0, 0, tip)).pushPoints(centers)
              .circle(p["screw_shaft_diameter"] / 2).extrude(p["screw_length"]))
    heads = cq.Workplane("XY")
    head_h = (p["bezel_nominal_head_diameter"] - p["screw_shaft_diameter"]) / (2 * tangent)
    for x, y in centers:
        head = cq.Solid.makeCone(p["screw_shaft_diameter"] / 2,
                                p["bezel_nominal_head_diameter"] / 2, head_h,
                                cq.Vector(x, y, head_top - head_h))
        heads = heads.union(cq.Workplane("XY").newObject([head]))
    # The coupon has the same thickness and face-down print orientation.
    coupon = cq.Workplane("XY").box(46, 14, thickness, centered=(True, True, False)).edges("|Z").chamfer(.5)
    notch = cq.Workplane("XY").box(3, 4, thickness + 1, centered=(True, True, False)).translate((-23, 0, -.1))
    coupon = coupon.cut(notch)
    for x, mouth in zip((-15, -5, 5, 15), (6.0, 6.2, 6.4, 6.8)):
        coupon, _ = countersink(coupon, [(x, 0)], thickness, mouth, p["screw_clearance_diameter"], p["bezel_countersink_angle"])
    return bezel, coupon, {"centers": centers, "depth": depth, "head_top": head_top,
                           "tip": tip, "heads": heads, "screws": shafts.union(heads)}


def build():
    enclosure.configure(HERE / "parameters.json")
    p = enclosure.P
    original, reference = enclosure.build()
    bezel_local, coupon, bezel_mounts = countersunk_bezel(p)
    height, cy, joint = p["disk_height"], p["disk_center_y"], p["cover_joint_z"]
    # Use the actual pre-heel sliced puck; the larger disk supplies the footprint.
    original_puck = reference["body_without_rear_foot"]
    puck = original_puck
    sliced_envelope = enclosure.orient(
        cq.Workplane("XY", origin=(0, 0, -90)).circle(p["outer_diameter"] / 2).extrude(90))
    sliced_envelope = sliced_envelope.cut(
        cq.Workplane("XY").box(250, 250, 150, centered=(True, True, False)).translate((0, 0, -150)))
    sliced_envelope = sliced_envelope.cut(enclosure.orient(
        cq.Workplane("XY").box(250, 250, 150, centered=(True, True, False))
        .translate((0, 0, -p["back_depth_below_face"] - 150))))
    # Offset the front mount to clear the original inclined bezel screw.
    bolts = [(-23.8, 6), (23.8, 6), (8, -15)]
    bosses = cq.Workplane("XY").pushPoints(bolts).circle(3.2).extrude(5.4)
    puck = puck.union(bosses.intersect(sliced_envelope))
    pilots = cq.Workplane("XY", origin=(0, 0, -.1)).pushPoints(bolts).circle(1.4).extrude(4.5)
    puck = puck.cut(pilots)
    ports = [(-14, 5), (14, 5)]
    for x, y in ports:
        puck = puck.cut(passage(x, y, -.1, 3.3))
    disk_blank = cq.Workplane("XY").center(0, cy).circle(p["disk_diameter"] / 2).extrude(height)
    disk_blank = disk_blank.edges(">Z").chamfer(.5).edges("<Z").chamfer(.4)
    margin = p["battery_clearance_each_side"]
    dims = [p[k] + 2 * margin for k in ("battery_width", "battery_length", "battery_thickness")]
    floor = p["battery_bottom_skin"]
    pocket = (cq.Workplane("XY").box(*dims, centered=(True, True, False))
              .translate((0, p["battery_center_y"], floor)))
    cell = (cq.Workplane("XY").box(p["battery_width"], p["battery_length"], p["battery_thickness"],
                                   centered=(True, True, False))
            .translate((0, p["battery_center_y"], floor + margin)))
    disk = disk_blank.cut(pocket)
    channel_z = floor + dims[2]
    channels = []
    for x, y in ports:
        start = (math.copysign(18.9, x), p["battery_center_y"] + 14.5)
        distance = math.hypot(start[0] - x, start[1] - y)
        angle = math.degrees(math.atan2(start[1] - y, start[0] - x))
        channel = (cq.Workplane("XY").slot2D(distance + p["wire_channel_width"], p["wire_channel_width"], angle)
                   .extrude(p["wire_channel_height"])
                   .translate(((start[0] + x) / 2, (start[1] + y) / 2, channel_z)))
        channels.append(channel)
        disk = disk.cut(channel).cut(passage(x, y, channel_z - .05, height - channel_z + .15))
    below = cq.Workplane("XY").box(200, 200, joint, centered=(True, True, False))
    cover, cup = disk.intersect(below), disk.cut(below)
    lip_h = p["registration_lip_height"]
    lip = (cq.Workplane("XY", origin=(0, cy, joint)).circle(31.5).circle(30.5).extrude(lip_h))
    cover = cover.union(lip)
    tolerance = p["registration_radial_clearance"]
    groove = (cq.Workplane("XY", origin=(0, cy, joint - .01))
              .circle(31.5 + tolerance).circle(30.5 - tolerance).extrude(lip_h + .16))
    cup = cup.cut(groove)
    clear = cq.Workplane("XY").pushPoints(bolts).circle(1.7).extrude(height + .1)
    cup, cover = cup.cut(clear), cover.cut(clear)
    for x, y in bolts:
        cover = cover.cut(cq.Workplane("XY").newObject([
            cq.Solid.makeCone(3.3, 1.7, 1.6, cq.Vector(x, y, 0))]))
    screws = cq.Workplane("XY").pushPoints(bolts).circle(1.5).extrude(16)
    puck_a = puck.translate((0, 0, height))
    bezel_a = enclosure.orient(bezel_local).translate((0, 0, height))
    hardware_a = {k: v.translate((0, 0, height)) for k, v in reference["hardware_envelopes"].items()}
    # Cup top-face down avoids bridging a 40 mm closed battery roof.
    cup_print = cup.rotate((0, 0, 0), (1, 0, 0), 180).translate((0, 0, height))
    face_down = lambda shape: shape.rotate((0, 0, 0), (1, 0, 0), 180).translate((0, 0, p["bezel_thickness"]))
    parts = {"puck-body": puck, "base-disk": cup_print, "battery-cover": cover,
             "bezel": face_down(bezel_local), "fit-ring": original["fit-ring"],
             "countersink-fit": face_down(coupon)}
    meta = {"puck": puck_a, "puck_local": puck, "original_puck": original_puck,
            "sliced_envelope": sliced_envelope,
            "bezel": bezel_a, "cup": cup, "cover": cover, "blank": disk_blank,
            "cell": cell, "pocket": pocket, "dimensions": dims, "bolts": bolts,
            "screws": screws, "channels": channels, "ports": ports,
            "hardware": hardware_a, "reference": reference,
            "bezel_local": bezel_local, "bezel_mounts": bezel_mounts,
            "bezel_screws": enclosure.orient(bezel_mounts["screws"]).translate((0, 0, height))}
    return p, parts, meta


def verify(p, parts, m):
    assert m["dimensions"] == [40.5, 30.5, 8.5]
    assembly = m["puck"].union(m["cup"]).union(m["cover"])
    pair_overlaps = {}
    for a, b in (("puck", "cup"), ("puck", "cover"), ("cup", "cover"), ("puck", "bezel")):
        pair_overlaps[a + "/" + b] = volume(m[a].intersect(m[b]))
        assert pair_overlaps[a + "/" + b] < 1e-5, (a, b, pair_overlaps)
    collisions = {}
    for name, shape in {**m["hardware"], "battery_clearance": m["pocket"], "bezel": m["bezel"]}.items():
        collisions[name] = volume(shape.intersect(assembly))
        assert collisions[name] < 1e-5, (name, collisions[name])
    assert volume(m["screws"].intersect(m["pocket"])) < 1e-5
    for name, shape in m["hardware"].items():
        assert volume(m["screws"].intersect(shape)) < 1e-5, ("mount screw", name)
    assert volume(m["bezel_screws"].intersect(m["screws"])) < 1e-5
    assert volume(m["cup"].union(m["cover"]).cut(m["blank"])) < 1e-5
    floor = (cq.Workplane("XY").box(m["dimensions"][0], m["dimensions"][1], p["battery_bottom_skin"],
                                    centered=(True, True, False)).translate((0, p["battery_center_y"], 0)))
    assert volume(floor.cut(m["cover"])) < 1e-5, "Battery floor breached"
    grip = (cq.Workplane("XY", origin=(0, 0, 1)).pushPoints(m["bolts"])
            .circle(2.6).circle(1.4).extrude(3.3))
    assert volume(grip.cut(m["puck_local"])) < 1e-5, "Missing thread walls"
    caps = (cq.Workplane("XY", origin=(0, 0, 4.4)).pushPoints(m["bolts"])
            .circle(1.4).extrude(1))
    assert volume(caps.cut(m["puck_local"])) < 1e-5, "Blind grip roof breached"
    shoulders = (cq.Workplane("XY", origin=(0, 0, 1.6)).pushPoints(m["bolts"])
                 .circle(3.25).circle(1.7).extrude(.8))
    assert volume(shoulders.cut(m["cover"])) < 1e-5, "Missing screw-head shoulder"
    # Changes to the upper body are internal: added boss and drilled holes only.
    outside = volume(m["puck_local"].cut(m["sliced_envelope"]))
    assert outside < 1e-5, "Mounts change the sliced-puck silhouette"
    lost = m["original_puck"].cut(m["puck_local"])
    seats = m["bezel_mounts"]
    keepout = (cq.Workplane("XY", origin=(0, 0, -.1)).pushPoints(seats["centers"])
               .circle(p["bezel_countersink_diameter"] / 2 + .2).extrude(p["bezel_thickness"] + .2))
    old = m["reference"]["bezel_local"].cut(keepout)
    new = m["bezel_local"].cut(keepout)
    interface_change = volume(old.cut(new)) + volume(new.cut(old))
    assert interface_change < 1e-5, "Bezel changed outside screw seats"
    assert p["bezel_thickness"] - seats["depth"] >= .5 - 1e-6
    assert volume(seats["heads"].intersect(m["bezel_local"])) < 1e-5, "Head fouls countersink"
    for x, y in seats["centers"]:
        for fraction in (.15, .5, .85):
            z = p["bezel_thickness"] - seats["depth"] + seats["depth"] * fraction
            radius = p["screw_clearance_diameter"] / 2 + (p["bezel_countersink_diameter"] - p["screw_clearance_diameter"]) / 2 * fraction
            for side in (-1, 1):
                assert not m["bezel_local"].val().isInside(cq.Vector(x + side * (radius - .05), y, z)), "Taper obstructed"
                assert m["bezel_local"].val().isInside(cq.Vector(x + side * (radius + .05), y, z)), "Taper has missing wall"
    for hardware in m["hardware"].values():
        assert volume(hardware.intersect(m["bezel_screws"])) < 1e-5, "Bezel screw hits electronics"
    envelope = m["pocket"]
    lower = m["cup"].union(m["puck"])
    removal = max(volume(envelope.translate((0, 0, -.25 * i)).intersect(lower)) for i in range(81))
    assert removal < 1e-5, ("Battery cannot load from below", removal)
    for x, y in m["ports"]:
        route = passage(x, y, 9.65, p["disk_height"] + 3.1 - 9.65)
        assert volume(route.intersect(lower)) < 1e-5, "Wire passage blocked"
    # A wire groove still leaves at least 1.2 mm of plastic under the top surface.
    roof = p["disk_height"] - p["battery_bottom_skin"] - m["dimensions"][2] - p["wire_channel_height"]
    assert roof >= 1.2 - 1e-6
    bb = cq.Compound.makeCompound([m[k].val() for k in ("puck", "cup", "cover", "bezel")]).BoundingBox()
    return {"battery_mm": [40, 30, 8], "pocket_mm": m["dimensions"],
            "total_clearance_each_axis_mm": .5, "clearance_per_side_mm": .25,
            "base_disk_mm": [p["disk_diameter"], p["disk_height"]],
            "assembly_size_mm": [round(bb.xlen, 3), round(bb.ylen, 3), round(bb.zlen, 3)],
            "upper_puck_face_center_above_disk_mm": p["face_center_height"],
            "upper_puck_uses_original_sliced_profile": True, "separate_rear_heel": False,
            "material_outside_original_sliced_profile_mm3": round(outside, 6),
            "bezel_change_outside_screw_seats_mm3": round(interface_change, 6),
            "bezel_fasteners": {"type": "M3 x 8 countersunk (overall length)",
                                "included_angle_degrees": p["bezel_countersink_angle"],
                                "mouth_diameter_mm": p["bezel_countersink_diameter"],
                                "through_diameter_mm": p["screw_clearance_diameter"],
                                "taper_depth_mm": round(seats["depth"], 3),
                                "remaining_straight_land_mm": round(p["bezel_thickness"] - seats["depth"], 3),
                                "assumed_screw_head_diameter_mm": p["bezel_nominal_head_diameter"],
                                "assumed_head_below_face_mm": round(p["bezel_thickness"] - seats["head_top"], 3),
                                "screw_tip_face_local_z_mm": round(seats["tip"], 3),
                                "coupon_mouth_diameters_mm": [6.0, 6.2, 6.4, 6.8]},
            "component_overlaps_mm3": pair_overlaps, "hardware_collisions_mm3": collisions,
            "bottom_loading_clearance_collision_mm3": round(removal, 6), "bottom_loading_samples": 81,
            "battery_floor_mm": p["battery_bottom_skin"], "minimum_roof_above_wire_channels_mm": round(roof, 3),
            "wire_channels_mm": [p["wire_channel_width"], p["wire_channel_height"]],
            "wire_passages_mm": [8, 5], "registration_clearance_radial_mm": p["registration_radial_clearance"],
            "stack_fasteners": "3 x M3 x 16 mm 90-degree countersunk", "stack_hole_centers_xy_mm": m["bolts"],
            "original_upper_body_removed_volume_mm3": round(volume(lost), 3),
            "hardware_envelopes_are_assumed": True, "physical_fit_and_slicer_review_required": True}


def preview(p, parts, m):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.path import Path as PlotPath
    from matplotlib.patches import PathPatch
    def section(ax, shape, color, edge):
        plane = cq.Plane(origin=(.1, 0, 0), xDir=(0, 1, 0), normal=(1, 0, 0))
        for face in cq.Workplane(plane).add(shape.val()).section().faces().vals():
            vertices, codes = [], []
            for wire in [face.outerWire(), *face.innerWires()]:
                points, _ = wire.sample(.04)
                xy = [[v.y, v.z] for v in points]
                if len(xy) < 3: continue
                vertices += xy + [xy[0]]
                codes += [PlotPath.MOVETO] + [PlotPath.LINETO] * (len(xy) - 1) + [PlotPath.CLOSEPOLY]
            ax.add_patch(PathPatch(PlotPath(vertices, codes), facecolor=color, edgecolor=edge, lw=1.2))
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    fig, ax = plt.subplots(figsize=(12, 8), facecolor="#f7f8fa")
    fig.subplots_adjust(left=.07, right=.95, top=.77, bottom=.2)
    ax.set_aspect("equal"); ax.axis("off"); ax.set_xlim(-34, 44); ax.set_ylim(-5, 55)
    section(ax, m["cup"], "#a8b1bd", "#526171")
    section(ax, m["cover"], "#ced4dc", "#526171")
    for key in ("puck", "bezel"): section(ax, m[key], "#efad83", "#9b5c36")
    for shape in m["hardware"].values(): section(ax, shape, "#a6c7c0", "#447e72")
    section(ax, m["cell"], "#efbf44", "#a67a1d")
    ax.plot([-31, 42], [0, 0], color="#9aa7b7", lw=1)
    ax.annotate("Original sliced puck", xy=(-18, 20), xytext=(-31, 31), arrowprops={"arrowstyle": "-"})
    ax.annotate("Separate 66 × 12.2 mm disk", xy=(36, 5), xytext=(19, -4), ha="center", arrowprops={"arrowstyle": "-"})
    ax.text(p["battery_center_y"], 5.3, "40 × 30 × 8 mm cell", ha="center", fontsize=10, color="#735419")
    fig.text(.07, .94, "Puck in sand, battery in the base", fontsize=25, fontweight="bold", color="#1d293a")
    fig.text(.07, .88, "40.5 × 30.5 × 8.5 mm pocket · 0.5 mm total clearance · separate circular battery disk",
             fontsize=12, color="#586579")
    fig.text(.07, .11, "The underside opens for loading. Internal roof channels route the corner lead into the puck.", color="#253348")
    fig.text(.07, .055, "CAD cutaway; prototype electronics envelopes. Print-fit and wire routing require a physical check.", fontsize=10, color="#697386")
    DOCS.mkdir(parents=True, exist_ok=True)
    fig.savefig(DOCS / "cutaway.png", dpi=160, facecolor=fig.get_facecolor())
    fig.savefig(DOCS / "cutaway.svg", facecolor=fig.get_facecolor())
    plt.close(fig)
    # Actual CAD section through the front screw, in face-local coordinates.
    fig, ax = plt.subplots(figsize=(10, 5), facecolor="#f4f2ed")
    plane = cq.Plane(origin=(0, -p["screw_circle_diameter"] / 2, 0),
                     xDir=(1, 0, 0), normal=(0, -1, 0))
    for shape, color, edge in ((m["bezel_local"], "#c8c9c5", "#52606a"),
                               (m["bezel_mounts"]["heads"], "#29343d", "#15202a")):
        for face in cq.Workplane(plane).add(shape.val()).section().faces().vals():
            vertices, codes = [], []
            for wire in [face.outerWire(), *face.innerWires()]:
                points, _ = wire.sample(.02)
                xy = [[v.x, v.z] for v in points]
                vertices += xy + [xy[0]]
                codes += [PlotPath.MOVETO] + [PlotPath.LINETO] * (len(xy) - 1) + [PlotPath.CLOSEPOLY]
            ax.add_patch(PathPatch(PlotPath(vertices, codes), facecolor=color, edgecolor=edge, lw=1.4))
    ax.set_aspect("equal"); ax.set_xlim(-5, 5); ax.set_ylim(-.6, 4.1); ax.axis("off")
    ax.annotate("90° tapered seat", xy=(2.6, 1.7), xytext=(3.3, 3.25),
                ha="center", arrowprops={"arrowstyle": "->"})
    ax.annotate("6 mm head: 0.1 mm below face", xy=(0, m["bezel_mounts"]["head_top"]),
                xytext=(-1.3, 3.45), ha="center", arrowprops={"arrowstyle": "->"})
    ax.annotate("3.4 mm through-hole", xy=(1.7, .3), xytext=(0, -.45),
                ha="center", arrowprops={"arrowstyle": "->"})
    fig.text(.08, .93, "A tapered seat for the flat-head screw", fontsize=20,
             fontweight="bold", color="#243137")
    fig.text(.08, .08, "Actual bezel CAD section · 2.2 mm bezel · 1.4 mm taper · 0.8 mm straight land",
             fontsize=11, color="#52606a")
    fig.savefig(DOCS / "countersink-section.png", dpi=180, facecolor=fig.get_facecolor())
    plt.close(fig)


def main():
    p, parts, m = build()
    report = verify(p, parts, m)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, part in parts.items():
        assert part.val().isValid() and len(part.solids().vals()) == 1, name
        cq.exporters.export(part, str(OUT / (name + ".stl")), tolerance=.05, angularTolerance=.08)
        colored_part = cq.Assembly(name=name + "-model")
        colored_part.add(part, name=name, color=part_color(name))
        colored_part.export(str(OUT / (name + ".step")))
        mesh = trimesh.load_mesh(OUT / (name + ".stl"))
        assert mesh.is_watertight and mesh.is_winding_consistent and mesh.volume > 0, name
        assert len(mesh.split()) == 1 and abs(mesh.bounds[0, 2]) < .001, name
        report.setdefault("meshes", {})[name] = {"watertight": True, "connected": True,
                                                "bed_aligned": True, "triangles": len(mesh.faces)}
    assembly = cq.Assembly(name=NAME)
    for key, name in (("puck", "puck-body"), ("cup", "base-disk"), ("cover", "battery-cover"), ("bezel", "bezel")):
        assembly.add(m[key], name=name, color=part_color(name))
    assembly.export(str(OUT / "assembly.step"))
    report["part_colors_srgb"] = {
        name: list(part_color(name).toTuple()[:3]) for name in parts
    }
    (OUT / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    preview(p, parts, m)
    kit = ROOT / (NAME + "-print-kit.zip")
    with zipfile.ZipFile(kit, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in [*(part + suffix for part in parts for suffix in (".stl", ".step")),
                     "assembly.step", "validation.json"]:
            archive.write(OUT / name, NAME + "/" + name)
        for name in ("README.md", "PRINTING.txt", "parameters.json", "appearance.json"):
            archive.write(HERE / name, NAME + "/" + name)
        archive.write(DOCS / "cutaway.png", NAME + "/cutaway.png")
        archive.write(DOCS / "countersink-section.png", NAME + "/countersink-section.png")
        archive.write(HERE.parent / "m3-machine" / "exports" / "fit-test.stl", NAME + "/fit-test.stl")
    print(json.dumps(report, indent=2))
    print("Print kit:", kit)


if __name__ == "__main__": main()
