"""Dotty sliced puck: parallel faces, desk slice and integrated rear foot.

Dimensions are mm. In local coordinates the body/bezel joint is z=0, USB is
+Y, and the PCB underside is z=-display_stack_allowance. The local cylinder
is tilted and sliced at the desk plane; the internal floor stays horizontal.
Hardware envelopes are prototype allowances, not a measured assembly.
"""
import argparse
import json
import math
from pathlib import Path

import cadquery as cq

HERE = Path(__file__).resolve().parent
P = json.loads((HERE / "parameters.json").read_text())
OUT = HERE / "exports"


def configure(parameters=None, output=None):
    """Apply a separate variant's JSON overrides without changing the default."""
    global P, OUT
    P = json.loads((HERE / "parameters.json").read_text())
    if parameters is not None:
        P.update(json.loads(Path(parameters).read_text()))
    OUT = Path(output).resolve() if output is not None else HERE / "exports"


def ring(outer, inner, height, z=0):
    return cq.Workplane("XY", origin=(0, 0, z)).circle(outer / 2).circle(inner / 2).extrude(height)


def orient(shape):
    """Move face-local geometry into its assembled desk coordinates."""
    return (shape.rotate((0, 0, 0), (1, 0, 0), P["face_angle_degrees"])
            .translate((0, 0, P["face_center_height"])))


def build():
    p = P
    od = p["outer_diameter"]
    bore = p["hardware_diameter"] + 2 * p["radial_clearance"]
    lid_h = p["bezel_thickness"]
    angle = math.radians(p["face_angle_degrees"])
    assert 0 < p["face_angle_degrees"] < 60
    assert 32.9 < p["view_diameter"] < p["hardware_diameter"] < bore
    assert p["seat_inner_diameter"] < p["hardware_diameter"]
    assert p["rear_wall_thickness"] >= 1.6
    assert p["back_depth_below_face"] > p["display_stack_allowance"] + p["xiao_depth_below_pcb"] + p["rear_wall_thickness"]
    assert (p["screw_circle_diameter"] + p["screw_head_diameter"]
            < od - 1.0), "Leave material outside the screw counterbores"
    bolt_r = p["screw_circle_diameter"] / 2
    bolts = [(bolt_r * math.cos(math.radians(a)), bolt_r * math.sin(math.radians(a)))
             for a in (-90, 30, 150)]
    # Start with a long cylinder normal to the display. Trim at a horizontal
    # plane rather than lifting a complete puck onto a wedge-shaped stand.
    lower_half = cq.Workplane("XY").box(250, 250, 150, centered=(True, True, False)).translate((0, 0, -150))
    above_floor = cq.Workplane("XY").box(250, 250, 150, centered=(True, True, False)).translate((0, 0, p["floor_thickness"]))
    # The back slice is in the SAME local XY plane as the display, offset only
    # along its normal. It must not be a vertical cut in desk coordinates.
    rear_cut_local = (cq.Workplane("XY").box(250, 250, 150, centered=(True, True, False))
                      .translate((0, 0, -p["back_depth_below_face"] - 150)))
    rear_cut = orient(rear_cut_local)
    rear_wall_zone = orient(rear_cut_local.translate((0, 0, p["rear_wall_thickness"])))
    outer = cq.Workplane("XY", origin=(0, 0, -90)).circle(od / 2).extrude(90)
    outer = outer.edges(">Z").chamfer(0.4)
    body = orient(outer).cut(lower_half).cut(rear_cut)
    cavity = cq.Workplane("XY", origin=(0, 0, -90)).circle(bore / 2).extrude(91)
    # Stop the cavity short of the new rear face to keep a closed, printable
    # wall; simply chopping the original shell would expose its interior.
    cavity = orient(cavity).intersect(above_floor).cut(rear_wall_zone)
    body = body.cut(cavity)

    # Three tabs contact bare PCB around the published mounting-hole regions,
    # with USB rotated to +Y. The wedges have printable sloped undersides.
    seat_z = -p["display_stack_allowance"]
    tab = (cq.Workplane("XZ")
           .polyline([(bore / 2 + 0.1, seat_z - p["seat_thickness"]),
                      (bore / 2 + 0.8, seat_z - p["seat_thickness"]),
                      (bore / 2 + 0.8, seat_z),
                      (p["seat_inner_diameter"] / 2, seat_z)])
           .close().extrude(2.4, both=True))
    for a in (0, 143, 217):
        body = body.union(orient(tab.rotate((0, 0, 0), (0, 0, 1), a)).cut(lower_half).cut(rear_cut))
    # Restrict the cable cutter to the rear wall; it must not cut into the flat
    # internal floor as a center-to-edge cutter would on this sloped enclosure.
    # Keep the USB opening on the cylindrical edge and leave material between
    # the opening and the parallel back face.
    usb = (cq.Workplane("XY").box(p["usb_width"], od, p["usb_height"],
                                  centered=(True, False, True))
           .translate((0, 16, -p["usb_depth_below_face"])))
    body = body.cut(orient(usb))
    pilots = (cq.Workplane("XY", origin=(0, 0, -7.2)).pushPoints(bolts)
              .circle(p["screw_pilot_diameter"] / 2).extrude(7.3))
    body = body.cut(orient(pilots))
    lead_in = p.get("screw_lead_in_depth", 0)
    if lead_in > 0:
        for x, y in bolts:
            cone = cq.Solid.makeCone(p["screw_pilot_diameter"] / 2,
                                    p["screw_clearance_diameter"] / 2,
                                    lead_in, cq.Vector(x, y, -lead_in))
            body = body.cut(orient(cq.Workplane("XY").newObject([cone])))

    bezel = ring(od, p["view_diameter"], lid_h)
    bezel = bezel.edges(">Z").chamfer(0.4)
    bezel = bezel.cut(cq.Workplane("XY").pushPoints(bolts)
                      .circle(p["screw_clearance_diameter"] / 2).extrude(lid_h + 1))
    head_recess = p.get("screw_head_recess_depth", 1.1)
    assert 0 < head_recess < lid_h
    bezel = bezel.cut(cq.Workplane("XY", origin=(0, 0, lid_h - head_recess))
                      .pushPoints(bolts).circle(p["screw_head_diameter"] / 2).extrude(head_recess + 0.1))
    bezel_print = bezel.rotate((0, 0, 0), (1, 0, 0), 180).translate((0, 0, lid_h))

    fit_ring = ring(od, bore, 3.2)
    test_tab = (cq.Workplane("XY").box(bore / 2 + 0.8 - p["seat_inner_diameter"] / 2,
                                      4.8, 1.0, centered=(False, True, False))
                .translate((p["seat_inner_diameter"] / 2, 0, 0)))
    for a in (0, 143, 217):
        fit_ring = fit_ring.union(test_tab.rotate((0, 0, 0), (0, 0, 1), a))
    fit_ring = fit_ring.cut(cq.Workplane("XY").box(8, 15, 5,
                          centered=(True, False, False)).translate((0, 20, 0)))

    # Keep-out volumes verify that slimming the enclosure does not slice into
    # the assumed display, underside components, XIAO, or screw tips.
    pcb = (cq.Workplane("XY", origin=(0, 0, seat_z))
           .circle(p["hardware_diameter"] / 2).extrude(p["display_stack_allowance"] - 0.2))
    underside = (cq.Workplane("XY", origin=(0, 0, seat_z - p["underside_component_allowance"]))
                 .circle(17.5).extrude(p["underside_component_allowance"]))
    # Underside tabs occupy only the PCB mounting areas outside r=18 mm.
    xiao = (cq.Workplane("XY").box(p["xiao_envelope_width"], p["xiao_envelope_length"],
                                    p["xiao_depth_below_pcb"], centered=(True, True, False))
            .translate((0, p["xiao_center_y"], seat_z - p["xiao_depth_below_pcb"])))
    # A small rectangular relief clears the XIAO/header envelope's rear corners
    # where they extend beyond the circular display cavity.
    xiao_relief = (cq.Workplane("XY")
                   .box(p["xiao_envelope_width"] + 0.4, p["xiao_envelope_length"] + 0.4,
                        p["xiao_depth_below_pcb"] + 0.3, centered=(True, True, False))
                   .translate((0, p["xiao_center_y"], seat_z - p["xiao_depth_below_pcb"] - 0.2)))
    body = body.cut(orient(xiao_relief))
    body_without_rear_foot = body
    # A low, broad heel fills part of the air gap beneath the parallel back.
    # Its base is coplanar with the desk slice and it overlaps the existing
    # floor/back wall, so it prints as one body with no separate attachment.
    original_rear_contact_y = (p["back_depth_below_face"]
                               - p["face_center_height"] * math.cos(angle)) / math.sin(angle)
    foot_start_y = original_rear_contact_y - p["rear_foot_base_overlap"]
    foot_end_y = original_rear_contact_y + p["rear_foot_extension"]
    foot_back_plane_z = p["rear_foot_extension"] * math.tan(angle)
    foot_height = foot_back_plane_z + p["rear_foot_wall_overlap"]
    assert p["rear_foot_extension"] > 0 and 20 <= p["rear_foot_width"] < od
    assert 0 < p["rear_foot_wall_overlap"] < p["floor_thickness"]
    foot = (cq.Workplane("YZ")
            .polyline([(foot_start_y, 0), (foot_end_y, 0),
                       (foot_end_y, foot_height), (foot_start_y, p["rear_foot_wall_overlap"])])
            .close().extrude(p["rear_foot_width"] / 2, both=True))
    foot = foot.edges("|Z").chamfer(p["rear_foot_corner_chamfer"])
    body = body.union(foot)
    screw_length = p.get("screw_length", 8.0)
    screw_tips = (cq.Workplane("XY", origin=(0, 0, lid_h - head_recess - screw_length))
                  .pushPoints(bolts).circle(p.get("screw_shaft_diameter", 2.0) / 2).extrude(screw_length))
    return {"body": body, "bezel": bezel_print, "fit-ring": fit_ring}, {
        "bezel_assembly": orient(bezel), "bezel_local": bezel,
        "face_angle": p["face_angle_degrees"], "face_center_height": p["face_center_height"],
        "front_top_height": p["face_center_height"] + lid_h * math.cos(angle) - od / 2 * math.sin(angle),
        "rear_top_height": p["face_center_height"] + lid_h * math.cos(angle) + od / 2 * math.sin(angle),
        "back_local_z": -p["back_depth_below_face"],
        "back_inner_local_z": -p["back_depth_below_face"] + p["rear_wall_thickness"],
        "display_normal": (0, -math.sin(angle), math.cos(angle)),
        "body_without_rear_foot": body_without_rear_foot,
        "rear_foot": foot, "original_rear_contact_y": original_rear_contact_y,
        "rear_foot_end_y": foot_end_y,
        "rear_foot_visible_height": foot_back_plane_z,
        "hardware_envelopes": {"display_stack": orient(pcb),
                               "underside_components": orient(underside), "xiao_headers": orient(xiao)},
        "screw_envelope": orient(screw_tips),
    }


def export():
    OUT.mkdir(parents=True, exist_ok=True)
    parts, meta = build()
    report = {}
    for name, part in parts.items():
        shape = part.val()
        assert shape.isValid() and len(part.solids().vals()) == 1, name
        cq.exporters.export(part, str(OUT / f"{name}.stl"), tolerance=0.05, angularTolerance=0.08)
        cq.exporters.export(part, str(OUT / f"{name}.step"))
        bb = shape.BoundingBox()
        report[name] = {"valid_solid": True, "solid_count": 1,
                        "size_mm": [round(bb.xlen, 3), round(bb.ylen, 3), round(bb.zlen, 3)],
                        "volume_mm3": round(shape.Volume(), 1)}
    assembly = cq.Assembly(name="Dotty-Parallel-Back-Puck")
    assembly.add(parts["body"], name="body")
    assembly.add(meta["bezel_assembly"], name="bezel")
    assembly.export(str(OUT / "assembly.step"))
    assembly_bb = cq.Compound.makeCompound([parts["body"].val(), meta["bezel_assembly"].val()]).BoundingBox()
    report["assembly"] = {"face_angle_above_table_degrees": meta["face_angle"],
                           "front_top_height_mm": round(meta["front_top_height"], 2),
                           "rear_top_height_mm": round(meta["rear_top_height"], 2),
                           "back_parallel_to_display": True,
                           "body_depth_normal_to_display_mm": P["back_depth_below_face"],
                           "total_depth_normal_to_display_mm": P["back_depth_below_face"] + P["bezel_thickness"],
                           "rear_wall_thickness_mm": P["rear_wall_thickness"],
                           "fasteners": {"shaft_diameter_mm": P.get("screw_shaft_diameter", 2.0),
                                         "screw_length_mm": P.get("screw_length", 8.0),
                                         "base_grip_hole_diameter_mm": P["screw_pilot_diameter"],
                                         "bezel_clearance_diameter_mm": P["screw_clearance_diameter"],
                                         "head_recess_diameter_mm": P["screw_head_diameter"]},
                           "integrated_rear_foot": {"extension_mm": P["rear_foot_extension"],
                                                    "width_mm": P["rear_foot_width"],
                                                    "visible_height_mm": round(meta["rear_foot_visible_height"], 2)},
                           "size_mm": [round(assembly_bb.xlen, 2), round(assembly_bb.ylen, 2), round(assembly_bb.zlen, 2)],
                           "separate_stand": False}
    (OUT / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parameters", type=Path, help="JSON parameter overrides for a variant")
    parser.add_argument("--output", type=Path, help="Separate export directory for this variant")
    configure(**vars(parser.parse_args()))
    export()
