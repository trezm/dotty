"""Estimate tipping resistance from the actual CAD support polygon.

This is a quasi-static estimate, not a physical test. Masses are assumptions;
print infill is treated as a uniform effective density. Cable loads, slipping,
rubber compliance and impact are excluded. Presses point into the display.
"""
import argparse
import itertools
import json
import math
from pathlib import Path
import sys

import numpy as np
from scipy.spatial import ConvexHull

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cad"))
import enclosure

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--parameters", type=Path, help="Variant JSON parameter overrides")
parser.add_argument("--output", type=Path, help="Variant export directory")
enclosure.configure(**vars(parser.parse_args()))
P = enclosure.P
PARTS, META = enclosure.build()
ANGLE = math.radians(P["face_angle_degrees"])
BASELINE = META["body_without_rear_foot"]
NOMINAL = {"body": 16.0, "bezel": 2.5, "display": 14.0, "xiao": 4.0, "screws": 0.6}
MASS_BOUNDS = {"body": [10, 22], "bezel": [2, 3], "display": [10, 25], "xiao": [2, 6]}


def support(shape):
    faces = [f for f in shape.faces().vals() if f.geomType() == "PLANE"
             and abs(f.Center().z) < 0.001 and abs(f.normalAt().z) > 0.999]
    points = []
    for face in faces:
        vertices, _ = face.tessellate(0.05)
        points.extend((v.x, v.y) for v in vertices)
    points = np.unique(np.asarray(points), axis=0)
    hull = ConvexHull(points)
    return hull, points, sum(f.Area() for f in faces)


def evaluate(shape, input_masses):
    hull, points, area = support(shape)
    masses = dict(input_masses)
    # Account for the heel's added mass at the same effective print density.
    masses["body"] *= shape.val().Volume() / BASELINE.val().Volume()
    centers = {
        "body": shape.val().Center().toTuple(),
        "bezel": META["bezel_assembly"].val().Center().toTuple(),
        "display": META["hardware_envelopes"]["display_stack"].val().Center().toTuple(),
        "xiao": META["hardware_envelopes"]["xiao_headers"].val().Center().toTuple(),
        "screws": (0, 0.85, P["face_center_height"] - 1.7 * math.cos(ANGLE)),
    }
    total = sum(masses.values())
    cg = sum(masses[k] * np.asarray(centers[k]) for k in masses) / total
    normals, offsets = hull.equations[:, :2], hull.equations[:, 2]
    margins = -(normals @ cg[:2] + offsets)
    assert margins.min() > 0, "Estimated centre of mass is outside the support polygon"

    def press_threshold(x_local, y_local):
        z_local = P["bezel_thickness"]
        y = y_local * math.cos(ANGLE) - z_local * math.sin(ANGLE)
        z = P["face_center_height"] + y_local * math.sin(ANGLE) + z_local * math.cos(ANGLE)
        # Resultant ground-force point must remain inside every hull edge.
        denominator = (math.cos(ANGLE) * (normals @ [x_local, y] + offsets)
                       + math.sin(ANGLE) * z * normals[:, 1])
        active = denominator > 1e-9
        if not active.any(): return None
        return float((total * 0.00980665 * margins[active] / denominator[active]).min())

    edge_forces = [press_threshold(16.2 * math.sin(t), 16.2 * math.cos(t))
                   for t in np.linspace(0, 2 * math.pi, 361)]
    worst = min(f for f in edge_forces if f is not None)
    def rounded_force(value):
        return None if value is None else round(value, 3)
    return {
        "estimated_mass_g": round(total, 2),
        "estimated_center_of_mass_mm": np.round(cg, 3).tolist(),
        "rear_balance_margin_mm": round(float(points[:, 1].max() - cg[1]), 3),
        "minimum_support_margin_mm": round(float(margins.min()), 3),
        "contact_depth_mm": round(float(np.ptp(points[:, 1])), 3),
        "contact_area_mm2": round(area, 1),
        "estimated_tip_force_N": {
            "screen_center_normal_press": rounded_force(press_threshold(0, 0)),
            "upper_active_edge_normal_press": rounded_force(press_threshold(0, 16.2)),
            "worst_active_edge_normal_press": round(worst, 3),
        },
    }


before = evaluate(BASELINE, NOMINAL)
after = evaluate(PARTS["body"], NOMINAL)
assert abs(after["contact_depth_mm"] - before["contact_depth_mm"] - P["rear_foot_extension"]) < 0.05
improvement = (after["estimated_tip_force_N"]["worst_active_edge_normal_press"]
               / before["estimated_tip_force_N"]["worst_active_edge_normal_press"])
assert improvement > 2, "Foot should materially improve modeled tipping resistance"
ranges = []
for values in itertools.product(*MASS_BOUNDS.values()):
    masses = dict(zip(MASS_BOUNDS, values))
    masses["screws"] = NOMINAL["screws"]
    ranges.append(evaluate(PARTS["body"], masses))
assert min(r["rear_balance_margin_mm"] for r in ranges) > 5
result = {
    "status": "estimate_not_physical_test",
    "assumptions": {
        "nominal_masses_g_without_foot": NOMINAL,
        "mass_bounds_g_without_foot": MASS_BOUNDS,
        "print_mass_distribution": "uniform effective density within CAD plastic",
        "null_tip_force": "No tipping mode for this exact force direction in the ideal model; sliding and material limits still apply",
        "excluded": ["USB cable loads", "sliding", "rubber compliance", "dynamic impact"],
    },
    "without_foot": before,
    "with_foot": after,
    "estimated_worst_edge_tipping_force_improvement_ratio": round(improvement, 2),
    "mass_range_sensitivity": {
        "rear_balance_margin_mm": [min(r["rear_balance_margin_mm"] for r in ranges),
                                    max(r["rear_balance_margin_mm"] for r in ranges)],
        "minimum_support_margin_mm": [min(r["minimum_support_margin_mm"] for r in ranges),
                                       max(r["minimum_support_margin_mm"] for r in ranges)],
    },
}
(enclosure.OUT / "balance-checks.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
