"""Build the original sloped M3 puck above a compact battery base.

Coordinates below are assembled desk coordinates unless explicitly face-local.
This separate variant leaves the M2 and original M3 print exports untouched.
"""
import json
import math
import sys
import zipfile
from pathlib import Path

import cadquery as cq
import trimesh

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "cad"))
import enclosure

OUT = HERE / "exports"
DOCS = ROOT / "docs" / "battery-study"


def below_plane(intercept, slope):
    return (cq.Workplane("YZ")
            .polyline([(-100, -100), (100, -100),
                       (100, intercept + 100 * slope),
                       (-100, intercept - 100 * slope)])
            .close().extrude(100, both=True))


def build(parameters=None):
    enclosure.configure(parameters or HERE / "parameters.json")
    p = enclosure.P
    parts, meta = enclosure.build()
    angle = math.radians(p["face_angle_degrees"])
    radius = p["outer_diameter"] / 2
    depth = p["back_depth_below_face"]
    height = p["face_center_height"]
    original_body = parts["body"]
    # Only the battery base's rear edge rises to meet the inclined back.
    # Do not project the whole puck down to the desk or trim its silhouette.
    base_rear_y = p["battery_center_y"] + p["battery_pod_length"] / 2
    rear_riser = (cq.Workplane("XY")
                  .box(p["battery_pod_width"], p["battery_rear_riser_depth"], 80,
                       centered=(True, True, False))
                  .translate((0, base_rear_y - p["battery_rear_riser_depth"] / 2, 0))
                  .edges("|Z").chamfer(0.6)
                  .intersect(below_plane(height - depth / math.cos(angle),
                                         math.tan(angle))))
    lower = (cq.Workplane("XY").box(250, 250, 150, centered=(True, True, False))
             .translate((0, 0, -150)))
    rear_cut = enclosure.orient(
        cq.Workplane("XY").box(250, 250, 150, centered=(True, True, False))
        .translate((0, 0, -depth - 150)))
    original_cylinder = enclosure.orient(
        cq.Workplane("XY", origin=(0, 0, -90)).circle(radius).extrude(90))
    original_blank = original_cylinder.cut(lower).cut(rear_cut)
    # Add material outside the existing case envelope only, preserving the
    # display cavity, inclined screw holes, seats and electronics relief.
    pod = (cq.Workplane("XY")
           .box(p["battery_pod_width"], p["battery_pod_length"],
                p["battery_pod_height"], centered=(True, True, False))
           .translate((0, p["battery_center_y"], 0))
           .edges("|Z").chamfer(0.6))
    margin = p["battery_clearance"]
    pocket_dimensions = [p[key] + 2 * margin for key in
                         ("battery_width", "battery_length", "battery_thickness")]
    pocket = (cq.Workplane("XY")
              .box(*pocket_dimensions, centered=(True, True, False))
              .translate((0, p["battery_center_y"], p["battery_bottom_skin"])))
    battery = (cq.Workplane("XY")
               .box(p["battery_width"], p["battery_length"], p["battery_thickness"],
                    centered=(True, True, False))
               .translate((0, p["battery_center_y"], p["battery_bottom_skin"] + margin)))
    # Open the well into the electronics cavity. Straight walls let the cell
    # lift out before being tilted through the circular opening, with the
    # boards removed. This also avoids a low horizontal roof over the cell.
    access = (cq.Workplane("XY")
              .box(pocket_dimensions[0], pocket_dimensions[1],
                   p["battery_access_top"] - p["battery_bottom_skin"],
                   centered=(True, True, False))
              .translate((0, p["battery_center_y"], p["battery_bottom_skin"])))
    well = pocket.union(access)
    body = (original_body.union(rear_riser)
            .union(pod.cut(original_blank.union(meta["rear_foot"])))
            .cut(well))
    # Preserve the mounting tabs and their connection to the surrounding rim
    # when the wider access well overlaps their lower corners.
    seat_mask = None
    for seat_angle in (0, 143, 217):
        rib = enclosure.orient(
            cq.Workplane("XY").box(15, 5.2, 4.4, centered=(False, True, False))
            .translate((17.5, 0, -9.0))
            .rotate((0, 0, 0), (0, 0, 1), seat_angle))
        seat_mask = rib if seat_mask is None else seat_mask.union(rib)
    protected_seats = original_body.intersect(seat_mask)
    body = body.union(protected_seats)
    # Follow the original tilted cylinder at the nose and sides. A rectangular
    # battery pod must never square off or protrude through that clean curve.
    body = body.intersect(original_cylinder)
    usb = enclosure.orient(
        cq.Workplane("XY")
        .box(p["usb_width"], p["outer_diameter"], p["usb_height"],
             centered=(True, False, True))
        .translate((0, 16, -p["usb_depth_below_face"])))
    body = body.cut(usb)
    parts["body"] = body
    meta.update(battery=battery, battery_pocket=pocket, battery_well=well,
                original_blank=original_blank, original_body=original_body,
                original_cylinder=original_cylinder, protected_seats=protected_seats,
                rear_riser=rear_riser, base_rear_y=base_rear_y,
                pod=pod, usb_cutter=usb, pocket_dimensions=pocket_dimensions)
    return p, parts, meta


def verify(p, parts, meta):
    body = parts["body"]
    assert body.val().isValid() and len(body.solids().vals()) == 1
    collisions = {}
    for name, shape in {**meta["hardware_envelopes"],
                        "bezel": meta["bezel_assembly"],
                        "battery_clearance": meta["battery_pocket"],
                        "usb_path": meta["usb_cutter"]}.items():
        collisions[name] = shape.intersect(body).val().Volume()
        assert collisions[name] < 1e-5, (name, collisions[name])
    for name, shape in {**meta["hardware_envelopes"],
                        "screw_tips": meta["screw_envelope"]}.items():
        overlap = shape.intersect(meta["battery_pocket"]).val().Volume()
        assert overlap < 1e-5, ("battery", name, overlap)
    # A closed bottom exists under the entire rectangular clearance envelope.
    floor = (cq.Workplane("XY")
             .box(meta["pocket_dimensions"][0], meta["pocket_dimensions"][1],
                  p["battery_bottom_skin"], centered=(True, True, False))
             .translate((0, p["battery_center_y"], 0)))
    assert floor.cut(body).val().Volume() < 1e-5, "Battery floor is breached"
    # The front part of the well shares a substantial opening with the main
    # electronics cavity. Battery installation precedes fitting the boards.
    connection = (cq.Workplane("XY").box(24, 12, 2)
                  .translate((0, 0, 7.2)))
    assert connection.intersect(body).val().Volume() < 1e-5, "Well is sealed"
    rear_faces = [face for face in body.faces().vals()
                  if face.geomType() == "PLANE"
                  and abs(face.Center().y - meta["base_rear_y"]) < 1e-5
                  and face.normalAt().y > 0.999]
    assert rear_faces and sum(face.Area() for face in rear_faces) > 200
    assert min(face.BoundingBox().zmin for face in rear_faces) < 1e-5
    # The original puck behind the small base is retained exactly; no tall
    # rear block, clipping plane, or material beneath the upper overhang.
    rear_region = (cq.Workplane("XY").box(200, 200, 150, centered=(True, False, False))
                   .translate((0, meta["base_rear_y"] + 0.01, 0)))
    original_rear = meta["original_body"].intersect(rear_region)
    revised_rear = body.intersect(rear_region)
    rear_change = (original_rear.cut(revised_rear).val().Volume()
                   + revised_rear.cut(original_rear).val().Volume())
    assert rear_change < 1e-5, ("Original sloped puck changed", rear_change)
    assert meta["rear_riser"].val().BoundingBox().zmax < 14, "Rear riser became a tall block"
    front_y = p["battery_center_y"] - p["battery_pod_length"] / 2
    front_region = (cq.Workplane("XY").box(200, 200, 150, centered=(True, False, False))
                    .translate((0, front_y - 200 - 0.01, 0)))
    original_front = meta["original_body"].intersect(front_region)
    revised_front = body.intersect(front_region)
    front_change = (original_front.cut(revised_front).val().Volume()
                    + revised_front.cut(original_front).val().Volume())
    assert front_change < 1e-5, ("Original curved front changed", front_change)
    outside_curve = body.cut(meta["original_cylinder"]).val().Volume()
    assert outside_curve < 1e-5, "Battery base protrudes through original curve"
    seat_loss = meta["protected_seats"].cut(body).val().Volume()
    assert seat_loss < 1e-5, "Battery access removed a PCB mounting tab"
    # Check a rigid-cell removal path with the boards and bezel removed:
    # lift flat, tilt parallel to the display, center and turn below the tabs,
    # then lift out with the narrow side across the mounting-tab opening.
    # Installation follows the reverse order; this does not test lead routing.
    cell = meta["battery"]
    cell_z = p["battery_bottom_skin"] + p["battery_clearance"] + p["battery_thickness"] / 2
    pickup = p["battery_removal_lift_z"]
    lift = pickup - cell_z
    poses = [cell.translate((0, 0, lift * index / 8)) for index in range(9)]
    angle = math.radians(p["face_angle_degrees"])
    flat = cell.translate((0, 0, lift))
    pivot = (0, p["battery_center_y"], pickup)
    axis_end = (1, p["battery_center_y"], pickup)
    poses += [flat.rotate(pivot, axis_end, p["face_angle_degrees"] * index / 8)
              for index in range(1, 9)]
    tilted = poses[-1]
    local_y = (math.cos(angle) * p["battery_center_y"]
               + math.sin(angle) * (pickup - p["face_center_height"]))
    poses += [tilted.translate((0, -math.cos(angle) * local_y * index / 6,
                               -math.sin(angle) * local_y * index / 6))
              for index in range(1, 7)]
    centered = poses[-1]
    local_z = (-math.sin(angle) * p["battery_center_y"]
               + math.cos(angle) * (pickup - p["face_center_height"]))
    raise_n = p["battery_turn_local_z"] - local_z
    poses += [centered.translate((0, -math.sin(angle) * raise_n * index / 4,
                                  math.cos(angle) * raise_n * index / 4))
              for index in range(1, 5)]
    turn_base = poses[-1]
    center = (0, p["battery_center_y"] - math.cos(angle) * local_y - math.sin(angle) * raise_n,
              pickup - math.sin(angle) * local_y + math.cos(angle) * raise_n)
    normal_end = (0, center[1] - math.sin(angle), center[2] + math.cos(angle))
    poses += [turn_base.rotate(center, normal_end, 90 * index / 18)
              for index in range(1, 19)]
    turned = poses[-1]
    poses += [turned.translate((0, -math.sin(angle) * index * 4,
                               math.cos(angle) * index * 4)) for index in range(1, 11)]
    path_overlap = max(pose.intersect(body).val().Volume() for pose in poses)
    assert path_overlap < 1e-5, ("Battery removal path blocked", path_overlap)
    assembly = cq.Compound.makeCompound([body.val(), meta["bezel_assembly"].val()])
    bb = assembly.BoundingBox()
    return {"valid_single_body": True,
            "assembly_size_mm": [round(bb.xlen, 3), round(bb.ylen, 3), round(bb.zlen, 3)],
            "face_angle_degrees": p["face_angle_degrees"],
            "battery_base_rear_y_mm": meta["base_rear_y"],
            "battery_base_rear_riser_height_mm": round(meta["rear_riser"].val().BoundingBox().zmax, 3),
            "battery_base_rear_riser_area_mm2": round(sum(f.Area() for f in rear_faces), 3),
            "original_sloped_puck_rear_change_mm3": round(rear_change, 6),
            "original_sloped_puck_preserved": True,
            "original_curved_front_change_mm3": round(front_change, 6),
            "battery_base_outside_original_curve_mm3": round(outside_curve, 6),
            "pcb_seat_material_lost_mm3": round(seat_loss, 6),
            "rear_exterior_overhang_requires_support_review": True,
            "battery_mm": [p["battery_width"], p["battery_length"], p["battery_thickness"]],
            "battery_minimum_clearance_pocket_mm": meta["pocket_dimensions"],
            "bottom_skin_mm": p["battery_bottom_skin"],
            "main_cavity_floor_mm": p["floor_thickness"],
            "battery_center_rearward_shift_from_previous_mm": p["battery_center_y"] - 0.6,
            "battery_well_open_to_main_cavity": True,
            "rigid_battery_removal_path_with_boards_removed_mm3": round(path_overlap, 6),
            "body_collision_mm3": {key: round(value, 6) for key, value in collisions.items()},
            "hardware_geometry_is_assumed": True,
            "physical_fit_and_slicer_support_review_required": True}


def preview(p, parts, meta):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path as PlotPath

    def section(ax, shape, color, edge, zorder=2, plane=None, axes=("y", "z")):
        plane = plane or cq.Plane(origin=(0.1, 0, 0), xDir=(0, 1, 0), normal=(1, 0, 0))
        for face in cq.Workplane(plane).add(shape.val()).section().faces().vals():
            vertices, codes = [], []
            for wire in [face.outerWire(), *face.innerWires()]:
                points, _ = wire.sample(0.04)
                xy = [[getattr(v, axes[0]), getattr(v, axes[1])] for v in points]
                if len(xy) < 3:
                    continue
                vertices += xy + [xy[0]]
                codes += [PlotPath.MOVETO] + [PlotPath.LINETO] * (len(xy) - 1) + [PlotPath.CLOSEPOLY]
            ax.add_patch(PathPatch(PlotPath(vertices, codes), facecolor=color,
                                   edgecolor=edge, lw=1.3, zorder=zorder))

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    fig, (ax, plan) = plt.subplots(1, 2, figsize=(15, 8), facecolor="#f7f8fa",
                                  gridspec_kw={"width_ratios": (1.35, 1)})
    fig.subplots_adjust(left=0.045, right=0.97, bottom=0.2, top=0.76, wspace=0.12)
    ax.set_aspect("equal"); ax.axis("off")
    ax.set_xlim(-30, 48); ax.set_ylim(-5, 46)
    ax.plot([-28, 44], [0, 0], color="#9aa7b7", lw=1)
    section(ax, parts["body"], "#cfd6df", "#6a7789")
    section(ax, meta["bezel_assembly"], "#cfd6df", "#6a7789")
    for name, colors in {
            "display_stack": ("#388b7c", "#1e5f54"),
            "underside_components": ("#b1d1c5", "#579b84"),
            "xiao_headers": ("#537db6", "#325b8c")}.items():
        section(ax, meta["hardware_envelopes"][name], *colors, zorder=4)
    section(ax, meta["screw_envelope"], "#97a1af", "#536074", 5)
    section(ax, meta["battery"], "#eea12a", "#b97011", 6)
    ax.text(p["battery_center_y"], 4.2, "25 mm deep",
            ha="center", va="center", fontsize=10, color="#623b0d", zorder=10)
    ax.set_title("Side cutaway", loc="left", fontsize=15, fontweight="bold", color="#253348")
    ax.annotate("Original\nsloped back",
                xy=(30, 17), xytext=(33, 26),
                color="#334155", fontsize=10, va="center",
                arrowprops={"arrowstyle": "-", "color": "#334155"})
    ax.annotate("1.2 mm bottom skin", xy=(5, 0.6), xytext=(-15, -3.7),
                color="#475569", fontsize=11,
                arrowprops={"arrowstyle": "-", "color": "#475569"})
    plan.set_aspect("equal"); plan.axis("off")
    plan.set_xlim(-32, 32); plan.set_ylim(-25, 31)
    plan.set_title("Base plan · USB / rear at top", loc="left", fontsize=15,
                   fontweight="bold", color="#253348")
    plane = cq.Plane(origin=(0, 0, 1.8), xDir=(1, 0, 0), normal=(0, 0, 1))
    section(plan, parts["body"], "#cfd6df", "#6a7789", plane=plane, axes=("x", "y"))
    section(plan, meta["battery"], "#eea12a", "#b97011", 6, plane=plane, axes=("x", "y"))
    plan.text(0, p["battery_center_y"], "350 mAh\n34 × 25 mm", ha="center", va="center",
              fontsize=11, color="#623b0d", zorder=10)
    plan.annotate("", xy=(-17, -8), xytext=(17, -8),
                  arrowprops={"arrowstyle": "<->", "color": "#a35a06"})
    plan.text(0, -11, "34 mm across", ha="center", color="#a35a06", fontsize=10)
    plan.text(0, -23, "Original curved nose", ha="center", color="#475569", fontsize=10)
    fig.text(0.045, 0.94, "Battery turned across the puck", fontsize=25,
             fontweight="bold", color="#1d293a")
    fig.text(0.045, 0.88, "34 mm across × 25 mm front to back · shifted rearward 6.9 mm · original front curve retained",
             fontsize=13, color="#586579")
    fig.text(0.045, 0.13, "The battery's front edge moves back 11.4 mm, leaving the curved nose clear.",
             fontsize=12, color="#253348", fontweight="bold")
    fig.text(0.045, 0.085, "35 × 26 × 6 mm pocket · 1.2 mm battery floor · original PCB seats and USB path preserved",
             fontsize=11, color="#697386")
    fig.text(0.045, 0.04, "CAD geometry only. Confirm the real cell, leads, retention and print supports during physical fit checks.",
             fontsize=10, color="#697386")
    DOCS.mkdir(exist_ok=True, parents=True)
    fig.savefig(DOCS / "compact-base.png", dpi=170, facecolor=fig.get_facecolor())
    fig.savefig(DOCS / "compact-base.svg", facecolor=fig.get_facecolor())
    fig.savefig(DOCS / "crosswise-base.png", dpi=170, facecolor=fig.get_facecolor())
    fig.savefig(DOCS / "crosswise-base.svg", facecolor=fig.get_facecolor())
    plt.close(fig)


def main():
    p, parts, meta = build()
    report = verify(p, parts, meta)
    OUT.mkdir(exist_ok=True, parents=True)
    for name, part in parts.items():
        assert part.val().isValid() and len(part.solids().vals()) == 1
        cq.exporters.export(part, str(OUT / f"{name}.stl"), tolerance=0.05, angularTolerance=0.08)
        cq.exporters.export(part, str(OUT / f"{name}.step"))
        mesh = trimesh.load_mesh(OUT / f"{name}.stl")
        assert mesh.is_watertight and mesh.is_winding_consistent and mesh.volume > 0, name
        assert len(mesh.split()) == 1 and abs(mesh.bounds[0, 2]) < 0.001, name
        report.setdefault("meshes", {})[name] = {
            "watertight": True, "connected_components": 1, "bed_aligned": True,
            "triangles": len(mesh.faces)}
    assembly = cq.Assembly(name="Dotty-M3-Battery-Compact-Base")
    assembly.add(parts["body"], name="body")
    assembly.add(meta["bezel_assembly"], name="bezel")
    assembly.export(str(OUT / "assembly.step"))
    (OUT / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    preview(p, parts, meta)
    kit = ROOT / "dotty-m3-battery-print-kit.zip"
    with zipfile.ZipFile(kit, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ("body.stl", "bezel.stl", "fit-ring.stl", "body.step", "assembly.step", "validation.json"):
            archive.write(OUT / name, "dotty-m3-battery/" + name)
        archive.write(HERE / "PRINTING.txt", "dotty-m3-battery/PRINTING.txt")
        archive.write(HERE / "README.md", "dotty-m3-battery/README.md")
        coupon = HERE.parent / "m3-machine" / "exports" / "fit-test.stl"
        archive.write(coupon, "dotty-m3-battery/fit-test.stl")
    print(json.dumps(report, indent=2))
    print(f"Print kit: {kit}")


if __name__ == "__main__":
    main()
