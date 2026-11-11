"""Measure current M3 enclosure battery clearances and draw a CAD-derived cutaway.

Creates study artifacts only. Does not change enclosure parameters or print exports.
Hardware solids are the existing conservative keep-out assumptions, not a measured assembly.
"""
from pathlib import Path
import json
import math
import sys

import cadquery as cq
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, PathPatch, Rectangle
from matplotlib.path import Path as PlotPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cad"))
import enclosure

OUT = ROOT / "docs" / "battery-study"
OUT.mkdir(parents=True, exist_ok=True)
enclosure.configure(ROOT / "cad/variants/m3-machine/parameters.json")
P = enclosure.P
parts, meta = enclosure.build()
angle = math.radians(P["face_angle_degrees"])


def yz(y, z):
    return (y * math.cos(angle) - z * math.sin(angle),
            P["face_center_height"] + y * math.sin(angle) + z * math.cos(angle))


occupied = parts["body"].union(meta["screw_envelope"])
for shape in meta["hardware_envelopes"].values():
    occupied = occupied.union(shape)
back = -P["back_depth_below_face"] + P["rear_wall_thickness"]
roof = -P["display_stack_allowance"]
bore = P["hardware_diameter"] + 2 * P["radial_clearance"]
inner = enclosure.orient(cq.Workplane("XY", origin=(0, 0, back)).circle(bore / 2).extrude(roof - back))
inner = inner.intersect(cq.Workplane("XY").box(200, 200, 200, centered=(True, True, False))
                        .translate((0, 0, P["floor_thickness"])))
free = inner.cut(occupied)

dimensions = (30, 10, 5)
center = (0, -7.4, -10.9)
margin = 0.5
battery = enclosure.orient(cq.Workplane("XY").box(*dimensions).translate(center))
envelope = enclosure.orient(cq.Workplane("XY").box(*(d + 2 * margin for d in dimensions)).translate(center))
collision = envelope.intersect(occupied).val().Volume()
outside = envelope.cut(inner).val().Volume()
assert collision < 1e-5 and outside < 1e-5, "Test block does not fit with the stated clearance"

gap = (-P["display_stack_allowance"] - P["xiao_depth_below_pcb"]) - back
report = {"variant": "m3-machine", "hardware_envelopes_are_assumptions": True,
          "inner_bore_diameter_mm": bore, "residual_space_below_pcb_cm3": free.val().Volume() / 1000,
          "minimum_xiao_to_parallel_back_mm": gap,
          "test_block_mm": dimensions, "test_block_center_face_local_mm": center,
          "test_block_clearance_each_side_mm": margin,
          "expanded_block_collision_mm3": collision, "expanded_block_outside_cavity_mm3": outside,
          "test_block_is_not_a_selected_battery": True,
          "runtime_assumptions": {"hours": 6, "usable_capacity_fraction": 0.8,
                                  "current_is_battery_side_average_mA": True},
          "six_hour_capacity_mAh": {str(current): current * 6 / 0.8 for current in (70, 100, 150)}}
(OUT / "measurements.json").write_text(json.dumps(report, indent=2) + "\n")


def face_path(face, axes):
    vertices, codes = [], []
    for wire in [face.outerWire(), *face.innerWires()]:
        points, _ = wire.sample(0.04)
        xy = [[getattr(p, axes[0]), getattr(p, axes[1])] for p in points]
        if len(xy) < 3:
            continue
        vertices += xy + [xy[0]]
        codes += [PlotPath.MOVETO] + [PlotPath.LINETO] * (len(xy) - 1) + [PlotPath.CLOSEPOLY]
    return PlotPath(vertices, codes)


def section(ax, shape, color, edge, *, plane=None, axes=("y", "z"), hatch=None, alpha=1, zorder=2):
    # A slight offset avoids an OpenCascade coincident-face seam at exactly x=0.
    plane = plane or cq.Plane(origin=(0.1, 0, 0), xDir=(0, 1, 0), normal=(1, 0, 0))
    cross = cq.Workplane(plane).add(shape.val()).section()
    for face in cross.faces().vals():
        ax.add_patch(PathPatch(face_path(face, axes), facecolor=color, edgecolor=edge,
                               linewidth=1.2, hatch=hatch, alpha=alpha, zorder=zorder))


plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11, "svg.fonttype": "none"})
fig = plt.figure(figsize=(15.5, 8.1), facecolor="#f7f8fa")
gs = fig.add_gridspec(1, 2, left=0.045, right=0.97, bottom=0.2, top=0.79, width_ratios=(1.62, 1), wspace=0.12)
side, plan = fig.add_subplot(gs[0]), fig.add_subplot(gs[1])
for ax in (side, plan):
    ax.set_facecolor("#f7f8fa")
    ax.set_aspect("equal")
    ax.axis("off")

shell, shell_edge = "#cfd6df", "#6a7789"
pcb, header, amber, orange = "#388b7c", "#537db6", "#fff0cb", "#eea12a"
section(side, free, amber, "#d5b66c", hatch="///", zorder=1)
section(side, parts["body"], shell, shell_edge)
section(side, meta["bezel_assembly"], shell, shell_edge)
section(side, meta["hardware_envelopes"]["display_stack"], pcb, "#1e5f54", zorder=4)
section(side, meta["hardware_envelopes"]["underside_components"], "#b1d1c5", "#579b84", zorder=3)
section(side, meta["hardware_envelopes"]["xiao_headers"], header, "#325b8c", zorder=5)
section(side, meta["screw_envelope"], "#97a1af", "#536074", zorder=6)
section(side, battery, orange, "#b97011", zorder=7)
side.plot([-31, 44], [0, 0], color="#a1a8b2", lw=1, zorder=0)
side.text(-28, -2.3, "Desk", color="#6a7789", fontsize=10)
side.set_xlim(-31, 46)
side.set_ylim(-3, 43)
side.set_title("Side cutaway", loc="left", fontsize=15, fontweight="bold", color="#253348", pad=15)


def callout(text, point, label, color="#253348", ha="left"):
    side.annotate(text, xy=point, xytext=label, color=color, fontsize=10.5, ha=ha,
                  va="center", linespacing=1.4,
                  arrowprops={"arrowstyle": "-", "color": color, "lw": 1,
                              "connectionstyle": "angle,angleA=0,angleB=70,rad=4"},
                  bbox={"boxstyle": "round,pad=0.35", "fc": "#f7f8fa", "ec": "none"}, zorder=12)


callout("Display / PCB\n4.8 mm stack allowance", yz(-10, -2), (-28, 31), "#1e5f54")
callout("XIAO + standard headers\n18 × 22 × 14 mm keep-out", yz(11, -10), (7, 39), "#325b8c")
callout("30 × 10 × 5 mm test block\n0.5 mm clearance each side", yz(-7.4, -10.9), (-29, 17), "#a35a06")
a, b = yz(11, -18.8), yz(11, back)
side.annotate("", xy=a, xytext=b, arrowprops={"arrowstyle": "<->", "color": "#b14045", "lw": 1.4}, zorder=14)
callout(f"Only {gap:.1f} mm\nbehind the XIAO stack", ((a[0]+b[0])/2, (a[1]+b[1])/2), (28, 5), "#b14045")

top, bottom = yz(33, 0), yz(33, -P["back_depth_below_face"])
side.annotate("", xy=top, xytext=bottom, arrowprops={"arrowstyle": "<->", "color": "#6a7789", "lw": 1})
mid = ((top[0]+bottom[0])/2, (top[1]+bottom[1])/2)
side.text(mid[0]+1.3, mid[1]+0.8, "22 mm body depth", rotation=-60, ha="center", color="#5b687a", fontsize=10)

# Plan section at the test block center; component footprint is projected for context.
local_free = (free.translate((0, 0, -P["face_center_height"]))
              .rotate((0, 0, 0), (1, 0, 0), -P["face_angle_degrees"]))
section(plan, local_free, amber, "#d5b66c", axes=("x", "y"),
        plane=cq.Plane(origin=(0, 0, center[2]), xDir=(1, 0, 0), normal=(0, 0, 1)), hatch="///", zorder=1)
plan.add_patch(Circle((0, 0), bore/2, fill=False, edgecolor=shell_edge, lw=2))
plan.add_patch(Rectangle((-9, P["xiao_center_y"]-11), 18, 22, facecolor=header,
                         edgecolor="#325b8c", lw=1.2, zorder=3))
plan.text(0, 9.65, "XIAO +\nheaders", color="white", ha="center", va="center", fontsize=11, linespacing=1.4)
plan.add_patch(Rectangle((-15.5, center[1]-5.5), 31, 11, facecolor="none",
                         edgecolor="#b97011", lw=1, ls="--", zorder=4))
plan.add_patch(Rectangle((-15, center[1]-5), 30, 10, facecolor=orange,
                         edgecolor="#b97011", lw=1.4, zorder=5))
plan.text(0, center[1], "TEST VOLUME", color="#623b0d", ha="center", va="center", fontsize=10, fontweight="bold")
plan.annotate("", xy=(-22, 25), xytext=(22, 25), arrowprops={"arrowstyle": "<->", "color": shell_edge})
plan.text(0, 27, "44 mm internal bore", ha="center", color="#5b687a", fontsize=11)
plan.annotate("", xy=(-15, -16), xytext=(15, -16), arrowprops={"arrowstyle": "<->", "color": "#a35a06"})
plan.text(0, -24.5, "30 mm wide × 10 mm deep", ha="center", color="#a35a06", fontsize=10)
plan.text(0, -29, "Under the PCB • front at bottom", ha="center", color="#6a7789", fontsize=10)
plan.set_xlim(-26, 26)
plan.set_ylim(-32, 31)
plan.set_title("Plan of the remaining pocket", loc="left", fontsize=15, fontweight="bold", color="#253348", pad=15)

fig.text(0.045, 0.94, "Battery space in the current M3 puck", fontsize=25, fontweight="bold", color="#1d293a")
fig.text(0.045, 0.885, "CAD-derived geometry · 58 mm outer body · no dedicated battery bay", fontsize=13, color="#586579")
fig.text(0.045, 0.145, f"{report['residual_space_below_pcb_cm3']:.1f} cm³ residual space in total — distributed around the headers and sloping floor", fontsize=14, color="#253348", fontweight="bold")
fig.text(0.045, 0.095, "Orange is a clearance-checked geometric example, not a selected battery or a maximum-size fit.", fontsize=11, color="#6b531f")
fig.text(0.045, 0.055, "Electronics use the existing conservative CAD envelopes; verify the actual stack, pouch, leads, protection board and mounting allowance.", fontsize=10, color="#697386")
fig.savefig(OUT / "cutaway.png", dpi=170, facecolor=fig.get_facecolor())
fig.savefig(OUT / "cutaway.svg", facecolor=fig.get_facecolor())
plt.close(fig)
print(json.dumps(report, indent=2))
