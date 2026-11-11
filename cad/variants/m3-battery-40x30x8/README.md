# Dotty M3 case for a 40 × 30 × 8 mm battery

This separate variant fits a **40 × 30 × 8 mm** cell in a **41 × 31 × 9 mm**
clearance pocket, with **0.5 mm allowance on each side**. The cell runs 40 mm
across and 30 mm front to back. Its top-corner lead is treated as lying within
the supplied dimensions; the well opens into the electronics cavity for routing.
No extra wire-exit enlargement is added to the cell dimensions.

The face remains **58 mm at 30°** and uses the existing M3 bezel. The assembled
case is **3 mm taller** than the 34 × 25 × 5 mm battery version. Its curved
front and upper inclined back are preserved; only the low battery base changes.
The assembled envelope is approximately **58 × 62.13 × 44.21 mm**. A CAD
cutaway is included in the kit as `cutaway.png`.

## Bottom loading

The larger cell cannot use the small version's checked top-loading route past
the PCB supports. This variant has a **removable bottom plate**, with a
**1.2 mm floor beneath the cell** and a 2.4 mm perimeter. Removing four screws
lets the full clearance envelope pass straight through the underside; there is
no need to bend the battery or pass it through the display opening.

- Keep the existing **three M3 × 8 mm screws** for the bezel.
- Add **four M3 × 8 mm, 90° countersunk/flat-head screws** for the bottom.
- Bottom-head recesses are 6.6 mm across; clearance holes are 3.4 mm and
  plastic grip holes are 2.8 mm. Socket/pan heads do not sit flush underneath.
- The existing M3 screw-fit coupon is included. Hand-tighten gently.

Use `body.stl`, `battery-cover.stl`, and the matching or existing `bezel.stl`.
All supplied STLs are already oriented on the print bed. See `PRINTING.txt`.
Keep the earlier battery version's body/cover separate: its body is one piece.

During assembly, disconnect the battery, feed its corner lead into the main
cavity, and seat the cell through the bottom. Leave the lead clear of the seam
and screw bosses. Connect and test the electronics before fitting the cover;
verify connector access and retention with the actual hardware. The cover must
close without compressing the pouch. The screws are outside the battery pocket.

CAD checks cover solid and mesh validity, battery/board/screw/USB clearances,
the closed floor, retained PCB seats, original front/upper-back geometry, bezel
interchangeability, and straight bottom loading of the full 41 × 31 × 9 mm
clearance envelope. Electronics envelopes remain prototype assumptions.
Physical battery fit, print strength, wire routing and slicer supports still
need checking on the printed part.

Rebuild from the project root:

```sh
.venv/bin/python cad/variants/m3-battery-40x30x8/build.py
```

The exports and `dotty-m3-battery-40x30x8-print-kit.zip` are separate from all
earlier M2, M3 and smaller-battery exports. Editable STEP parts and an assembled
STEP are included.
