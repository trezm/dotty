"""Small angled grip-hole coupon using the body's hole depth and lead-in."""
from pathlib import Path

import cadquery as cq

HERE = Path(__file__).resolve().parent
OUT = HERE / "exports"
OUT.mkdir(parents=True, exist_ok=True)

def tilt(shape):
    return shape.rotate((0, 0, 0), (1, 0, 0), 30).translate((0, 0, 11))

stock = cq.Workplane("XY").box(44, 12, 40, centered=(True, True, False)).translate((0, 0, -40))
below = cq.Workplane("XY").box(100, 100, 60, centered=(True, True, False)).translate((0, 0, -60))
coupon = tilt(stock).cut(below)
for x, diameter in zip((-15, -5, 5, 15), (2.6, 2.7, 2.8, 2.9)):
    hole = cq.Workplane("XY", origin=(x, 1.5, -7.2)).circle(diameter / 2).extrude(7.3)
    cone = cq.Solid.makeCone(diameter / 2, 1.7, 0.4, cq.Vector(x, 1.5, -0.4))
    coupon = coupon.cut(tilt(hole)).cut(tilt(cq.Workplane("XY").newObject([cone])))
    label = cq.Workplane("XY", origin=(x, -3.5, 0)).text(f"{diameter:.1f}", 2.8, 0.35)
    coupon = coupon.union(tilt(label))
assert coupon.val().isValid() and len(coupon.solids().vals()) == 1
cq.exporters.export(coupon, str(OUT / "fit-test.stl"), tolerance=0.05, angularTolerance=0.08)
cq.exporters.export(coupon, str(OUT / "fit-test.step"))
print("Exported M3 fit test: 2.6 / 2.7 / 2.8 / 2.9 mm holes, 30-degree face.")
