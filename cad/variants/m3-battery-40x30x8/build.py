"""40 x 30 x 8 mm battery variant, with a removable bottom and unchanged M3 bezel."""
import importlib.util
import json
import math
from pathlib import Path
import zipfile

import cadquery as cq
import trimesh

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = HERE / "exports"
DOCS = ROOT / "docs" / "battery-40x30x8"
NAME = "dotty-m3-battery-40x30x8"
spec = importlib.util.spec_from_file_location("dotty_battery_base", HERE.parent / "m3-battery" / "build.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


def build():
    p, original, meta = base.build(HERE / "parameters.json")
    joint = 2.4
    bolts = [(x, y) for x in (-24.0, 24.0) for y in (6.5, 20.5)]
    bosses = cq.Workplane("XY").pushPoints(bolts).circle(3.6).extrude(11.4)
    envelope = meta["original_cylinder"]
    whole = original["body"].union(bosses.intersect(envelope).cut(meta["battery_well"]))
    below = cq.Workplane("XY").box(200, 200, joint, centered=(True, True, False))
    cover = whole.intersect(below)
    upper = whole.cut(below)
    # Flat-head M3 x 8 screws enter from underneath; their heads stay above the desk.
    clear = cq.Workplane("XY").pushPoints(bolts).circle(1.7).extrude(joint + .1)
    cover = cover.cut(clear)
    for x, y in bolts:
        cone = cq.Solid.makeCone(3.3, 1.7, 1.6, cq.Vector(x, y, 0))
        cover = cover.cut(cq.Workplane("XY").newObject([cone]))
    pilots = (cq.Workplane("XY", origin=(0, 0, joint - .01))
              .pushPoints(bolts).circle(1.4).extrude(8.4 - joint + .01))
    upper = upper.cut(pilots)
    screws = cq.Workplane("XY").pushPoints(bolts).circle(1.5).extrude(8)
    meta.update(body_assembly=upper, cover=cover, combined=upper.union(cover),
                cover_joint=joint, cover_bolts=bolts, cover_screws=screws)
    parts = {"body": upper.translate((0, 0, -joint)), "battery-cover": cover,
             "bezel": original["bezel"], "fit-ring": original["fit-ring"]}
    return p, parts, meta


def volume(shape):
    return shape.val().Volume()


def verify(p, parts, meta):
    body, cover, combined = meta["body_assembly"], meta["cover"], meta["combined"]
    collisions = {}
    for name, shape in {**meta["hardware_envelopes"], "bezel": meta["bezel_assembly"],
                        "battery_clearance": meta["battery_pocket"], "usb": meta["usb_cutter"]}.items():
        collisions[name] = volume(shape.intersect(combined))
        assert collisions[name] < 1e-5, (name, collisions[name])
    for name, shape in {**meta["hardware_envelopes"], "bezel_screws": meta["screw_envelope"],
                        "cover_screws": meta["cover_screws"]}.items():
        overlap = volume(shape.intersect(meta["battery_pocket"]))
        assert overlap < 1e-5, ("battery clearance", name, overlap)
    assert volume(body.intersect(cover)) < 1e-5
    # Verify continuous plastic around the grip bores and above each countersink.
    grip_start = meta["cover_joint"] + .1
    grip_walls = (cq.Workplane("XY", origin=(0, 0, grip_start))
                  .pushPoints(meta["cover_bolts"]).circle(2.6).circle(1.4).extrude(8.3 - grip_start))
    assert volume(grip_walls.cut(body)) < 1e-5, "Insufficient plastic around bottom screws"
    shoulders = (cq.Workplane("XY", origin=(0, 0, 1.6)).pushPoints(meta["cover_bolts"])
                 .circle(3.25).circle(1.7).extrude(.8))
    assert volume(shoulders.cut(cover)) < 1e-5, "Countersink shoulder is breached"
    floor = (cq.Workplane("XY").box(41, 31, p["battery_bottom_skin"], centered=(True, True, False))
             .translate((0, p["battery_center_y"], 0)))
    assert volume(floor.cut(cover)) < 1e-5, "Battery floor is breached"
    assert volume(meta["protected_seats"].cut(combined)) < 1e-5, "PCB seats changed"
    assert volume(combined.cut(meta["original_cylinder"])) < 1e-5, "Curved silhouette changed"
    changes = {}
    for name, y in (("front", p["battery_center_y"] - p["battery_pod_length"] / 2 - .01),
                    ("upper_rear", meta["base_rear_y"] + .01)):
        region = cq.Workplane("XY").box(200, 200, 150, centered=(True, False, False))
        region = region.translate((0, y - 200 if name == "front" else y, 0))
        before, after = meta["original_body"].intersect(region), combined.intersect(region)
        changes[name] = volume(before.cut(after)) + volume(after.cut(before))
        assert changes[name] < 1e-5, (name, changes[name])
    # Load/remove straight through the bottom after removing the four-screw cover.
    # Test the full clearance envelope, not just the nominal battery pouch.
    cell_z = p["battery_bottom_skin"] + meta["pocket_dimensions"][2] / 2
    envelope = (cq.Workplane("XY").box(*meta["pocket_dimensions"])
                .translate((0, p["battery_center_y"], cell_z)))
    removal = max(volume(envelope.translate((0, 0, -.25 * i)).intersect(body)) for i in range(81))
    assert removal < 1e-5, ("Bottom loading blocked", removal)
    # The existing M3 bezel is geometrically interchangeable with this version.
    prior = cq.importers.importStep(str(HERE.parent / "m3-battery" / "exports" / "bezel.step"))
    bezel_change = volume(prior.cut(parts["bezel"])) + volume(parts["bezel"].cut(prior))
    assert bezel_change < 1e-5
    bb = cq.Compound.makeCompound([body.val(), cover.val(), meta["bezel_assembly"].val()]).BoundingBox()
    return {"battery_mm": [40, 30, 8], "minimum_pocket_mm": meta["pocket_dimensions"],
            "clearance_each_side_mm": p["battery_clearance"], "bottom_skin_mm": p["battery_bottom_skin"],
            "assembly_size_mm": [round(bb.xlen, 3), round(bb.ylen, 3), round(bb.zlen, 3)],
            "face_diameter_mm": p["outer_diameter"], "face_angle_degrees": p["face_angle_degrees"],
            "height_increase_over_34x25x5_variant_mm": p["face_center_height"] - 25,
            "bezel_unchanged_mm3": round(bezel_change, 6), "body_collision_mm3": collisions,
            "original_front_change_mm3": round(changes["front"], 6),
            "original_upper_rear_change_mm3": round(changes["upper_rear"], 6),
            "bottom_loading_clearance_envelope_collision_mm3": round(removal, 6),
            "bottom_loading_samples": 81, "battery_corner_lead_included_in_user_dimensions": True,
            "bottom_cover_joint_z_mm": meta["cover_joint"], "bottom_cover_fasteners": "4 x M3 x 8 countersunk",
            "bottom_cover_hole_centers_xy_mm": meta["cover_bolts"],
            "bottom_grip_radial_wall_checked_mm": 1.2, "countersink_shoulder_checked_mm": .8,
            "hardware_envelopes_are_assumed": True, "physical_fit_and_slicer_review_required": True}


def preview(p, parts, meta):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.path import Path as PlotPath
    from matplotlib.patches import PathPatch

    def section(ax, solid, color, edge, plane, axes):
        cut = cq.Workplane(plane).add(solid.val()).section()
        for face in cut.faces().vals():
            vertices, codes = [], []
            for wire in [face.outerWire(), *face.innerWires()]:
                points, _ = wire.sample(.04)
                xy = [[getattr(v, axes[0]), getattr(v, axes[1])] for v in points]
                if len(xy) < 3: continue
                vertices += xy + [xy[0]]
                codes += [PlotPath.MOVETO] + [PlotPath.LINETO] * (len(xy) - 1) + [PlotPath.CLOSEPOLY]
            ax.add_patch(PathPatch(PlotPath(vertices, codes), facecolor=color, edgecolor=edge, lw=1.1))

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    fig, (side, plan) = plt.subplots(1, 2, figsize=(14, 8), facecolor="#f7f8fa")
    fig.subplots_adjust(left=.04, right=.97, bottom=.2, top=.78, wspace=.12)
    for ax in (side, plan): ax.set_aspect("equal"); ax.axis("off")
    for ax, plane, axes in ((side, cq.Plane(origin=(.1, 0, 0), xDir=(0, 1, 0), normal=(1, 0, 0)), ("y", "z")),
                             (plan, cq.Plane(origin=(0, 0, 5.7), normal=(0, 0, 1)), ("x", "y"))):
        section(ax, meta["body_assembly"], "#cfd6df", "#607080", plane, axes)
        section(ax, meta["bezel_assembly"], "#cfd6df", "#607080", plane, axes)
        section(ax, meta["cover"], "#e8b28d", "#91522f", plane, axes)
        for shape in meta["hardware_envelopes"].values(): section(ax, shape, "#a6c7c0", "#447e72", plane, axes)
        section(ax, meta["battery"], "#eea12a", "#a9670d", plane, axes)
    side.set_xlim(-32, 46); side.set_ylim(-7, 48)
    side.plot([-30, 43], [0, 0], color="#9aa7b7", lw=1)
    side.annotate("Removable floor\n1.2 mm beneath cell", xy=(12, .6), xytext=(-22, -5),
                  color="#91522f", arrowprops={"arrowstyle": "-", "color": "#91522f"})
    side.set_title("Side cutaway · battery in orange", loc="left", fontweight="bold")
    plan.set_xlim(-32, 32); plan.set_ylim(-23, 40)
    plan.text(0, p["battery_center_y"], "40 × 30 × 8 mm", ha="center", color="#623b0d")
    plan.set_title("Plan through cell · USB / rear at top", loc="left", fontweight="bold")
    fig.text(.045, .94, "Larger battery, same curved puck", fontsize=25, fontweight="bold", color="#1d293a")
    fig.text(.045, .88, "41 × 31 × 9 mm pocket · 58 mm M3 face · body 3 mm taller · battery loads from below",
             fontsize=13, color="#586579")
    fig.text(.045, .11, "Remove the four-screw bottom plate to insert the cell straight in. Existing M3 bezel fits.",
             fontsize=12, color="#253348")
    fig.text(.045, .055, "CAD-derived cutaway. Electronics envelopes are assumed; physical fit and slicer supports still need checking.",
             fontsize=10, color="#697386")
    DOCS.mkdir(parents=True, exist_ok=True)
    fig.savefig(DOCS / "cutaway.png", dpi=160, facecolor=fig.get_facecolor())
    fig.savefig(DOCS / "cutaway.svg", facecolor=fig.get_facecolor())
    plt.close(fig)


def main():
    p, parts, meta = build()
    report = verify(p, parts, meta)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, part in parts.items():
        assert part.val().isValid() and len(part.solids().vals()) == 1, name
        cq.exporters.export(part, str(OUT / (name + ".stl")), tolerance=.05, angularTolerance=.08)
        cq.exporters.export(part, str(OUT / (name + ".step")))
        mesh = trimesh.load_mesh(OUT / (name + ".stl"))
        assert mesh.is_watertight and mesh.is_winding_consistent and mesh.volume > 0, name
        assert len(mesh.split()) == 1 and abs(mesh.bounds[0, 2]) < .001, name
        report.setdefault("meshes", {})[name] = {"watertight": True, "connected": True,
                                                    "bed_aligned": True, "triangles": len(mesh.faces)}
    assembly = cq.Assembly(name=NAME)
    assembly.add(meta["body_assembly"], name="body")
    assembly.add(meta["cover"], name="battery-cover")
    assembly.add(meta["bezel_assembly"], name="bezel")
    assembly.export(str(OUT / "assembly.step"))
    (OUT / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    preview(p, parts, meta)
    kit = ROOT / (NAME + "-print-kit.zip")
    with zipfile.ZipFile(kit, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ("body.stl", "battery-cover.stl", "bezel.stl", "fit-ring.stl", "body.step",
                     "battery-cover.step", "assembly.step", "validation.json"):
            archive.write(OUT / name, NAME + "/" + name)
        for name in ("README.md", "PRINTING.txt"):
            archive.write(HERE / name, NAME + "/" + name)
        archive.write(DOCS / "cutaway.png", NAME + "/cutaway.png")
        archive.write(HERE.parent / "m3-machine" / "exports" / "fit-test.stl", NAME + "/fit-test.stl")
    print(json.dumps(report, indent=2))
    print("Print kit:", kit)


if __name__ == "__main__": main()
