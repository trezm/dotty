"""Check printable meshes, sliced base, inclined joint, and hardware allowances."""
import argparse
import json
import math
import sys
from pathlib import Path

import trimesh

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cad"))
import enclosure

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--parameters", type=Path, help="Variant JSON parameter overrides")
parser.add_argument("--output", type=Path, help="Variant export directory")
enclosure.configure(**vars(parser.parse_args()))
P = enclosure.P
OUT = enclosure.OUT
parts, meta = enclosure.build()
checks = {}
for name in parts:
    mesh = trimesh.load_mesh(OUT / f"{name}.stl")
    assert mesh.is_watertight, f"{name}: open mesh"
    assert mesh.is_winding_consistent and mesh.volume > 0, f"{name}: inverted faces"
    assert len(mesh.split()) == 1, f"{name}: disconnected pieces"
    assert abs(mesh.bounds[0, 2]) < 0.001, f"{name}: not on print bed"
    checks[name] = {"watertight": True, "connected_components": 1,
                    "triangles": len(mesh.faces), "bed_aligned": True}

body = parts["body"]
lid = meta["bezel_assembly"]

def local(shape):
    return (shape.translate((0, 0, -P["face_center_height"]))
            .rotate((0, 0, 0), (1, 0, 0), -P["face_angle_degrees"]))

overlap = lid.intersect(body).val().Volume()
assert overlap < 0.001, f"Bezel intrudes into body by {overlap} mm³"
hardware_checks = {}
for name, volume in meta["hardware_envelopes"].items():
    collision = volume.intersect(body).val().Volume()
    assert collision < 0.001, f"Body intrudes into assumed {name}: {collision} mm³"
    lid_collision = volume.intersect(lid).val().Volume()
    assert lid_collision < 0.001, f"Bezel intrudes into assumed {name}: {lid_collision} mm³"
    bb = volume.val().BoundingBox()
    assert bb.zmin > P["floor_thickness"], f"{name} reaches the floor"
    local_bb = local(volume).val().BoundingBox()
    back_clearance = local_bb.zmin - meta["back_inner_local_z"]
    assert back_clearance > 0, f"Parallel back wall truncates {name}"
    hardware_checks[name] = {"collision_mm3": round(collision, 6),
                             "minimum_floor_clearance_mm": round(bb.zmin - P["floor_thickness"], 3),
                             "minimum_back_wall_clearance_mm": round(back_clearance, 3)}
    screw_collision = volume.intersect(meta["screw_envelope"]).val().Volume()
    assert screw_collision < 0.001, f"Screws intersect {name}"
assert meta["screw_envelope"].val().BoundingBox().zmin > 0, "Screw protrudes through base"
# A finite, horizontal contact face must exist, rather than point contact.
flat_faces = [f for f in body.faces().vals() if f.geomType() == "PLANE"
              and abs(f.Center().z) < 0.001 and abs(f.normalAt().z) > 0.999]
assert flat_faces and sum(f.Area() for f in flat_faces) > 700, "No broad flat desk base"
normal = meta["display_normal"]
rear_faces = []
parallel_errors = []
for face in body.faces().vals():
    if face.geomType() != "PLANE": continue
    c = face.Center()
    plane_z = c.y * normal[1] + (c.z - P["face_center_height"]) * normal[2]
    if abs(plane_z - meta["back_local_z"]) > 0.001: continue
    n = face.normalAt()
    alignment = abs(n.y * normal[1] + n.z * normal[2])
    if alignment < 0.999: continue
    rear_faces.append(face)
    parallel_errors.append(math.degrees(math.acos(min(1, alignment))))
assert rear_faces and sum(f.Area() for f in rear_faces) > 700, "Missing back face parallel to display"
assert max(parallel_errors) < 0.001, "Back cut is not parallel to the display"
assert local(meta["body_without_rear_foot"]).val().BoundingBox().zmin > meta["back_local_z"] - 0.05, "Main back wasn't fully sliced"
contact_rear_y = max(f.BoundingBox().ymax for f in flat_faces)
assert abs(contact_rear_y - meta["rear_foot_end_y"]) < 0.05, "Foot does not reach the desk at its rear"
assert abs(contact_rear_y - meta["original_rear_contact_y"] - P["rear_foot_extension"]) < 0.05, "Foot extension is incorrect"
assert body.val().Volume() > meta["body_without_rear_foot"].val().Volume(), "Foot did not add material"
checks["assembly"] = {"body_bezel_collision_mm3": round(overlap, 6),
                       "face_angle_above_table_degrees": meta["face_angle"],
                       "front_top_height_mm": round(meta["front_top_height"], 2),
                       "rear_top_height_mm": round(meta["rear_top_height"], 2),
                       "flat_base_contact_area_mm2": round(sum(f.Area() for f in flat_faces), 1),
                       "rear_contact_extension_mm": round(contact_rear_y - meta["original_rear_contact_y"], 3),
                       "rear_contact_y_mm": round(contact_rear_y, 3),
                       "back_parallel_error_degrees": round(max(parallel_errors), 6),
                       "body_depth_normal_to_display_mm": P["back_depth_below_face"],
                       "rear_flat_wall_area_mm2": round(sum(f.Area() for f in rear_faces), 1),
                       "assumed_hardware_clearance": hardware_checks,
                       "separate_stand": False}
(OUT / "mesh-checks.json").write_text(json.dumps(checks, indent=2) + "\n")
print(json.dumps(checks, indent=2))
