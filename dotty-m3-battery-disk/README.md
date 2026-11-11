# Dotty: sliced puck on a separate battery disk

This variant restores the original **58 mm, 30° sliced-puck profile** above a
separate **66 mm diameter × 12.2 mm high circular base**. The disk houses the
larger battery; it replaces the integrated heel and enlarged battery foot.
The puck's face center is 21 mm above the disk, as in the original M3 shell.
The overall assembled envelope is approximately **66 × 66 × 49.41 mm**.

The **40 × 30 × 8 mm** cell sits in a **40.5 × 30.5 × 8.5 mm pocket**.
That is **0.5 mm total clearance per dimension, or 0.25 mm on each side**,
including above and below the nominal cell. This is a close printed fit;
the actual pouch must fit without compression. The cell runs 40 mm across and
30 mm front to back, with its center 10 mm behind the puck center.

The disk has a **removable underside**, a **1.2 mm battery floor**, and a
registration lip with 0.15 mm radial mating clearance. Internal roof channels
run from either rear corner of the battery to two 8 × 5 mm passages into the
puck. The channels are 3.2 mm wide and 1.3 mm high, leaving 1.2 mm of roof.
They provide lead routing without enlarging the specified battery envelope.
Check the actual lead and connector through the passages before assembly.

## Print and assemble

Print `puck-body.stl`, `base-disk.stl`, `battery-cover.stl` and `bezel.stl`.
The bezel now has **90° countersinks** for flat-head screws. Its mounting
positions and mating face still match the original M3 body. The fit ring and
screw coupons are optional fit checks. All STLs are already on the bed in their
intended orientation. The disk cup is **top-face down**, so the battery cavity
prints upward and has no wide roof to bridge. See `PRINTING.txt`.

Use **three M3 × 16 mm, 90° countersunk/flat-head screws** from the underside
through the cover and disk into the puck. These three screws clamp the whole
stack; removing them releases both the bottom cover and the puck. The disk
has 3.4 mm clearance holes, the cover accepts heads up to 6.6 mm across, and
the puck has 2.8 mm blind grip holes. The screws engage about 3.8 mm into the
puck. Use **three M3 × 8 mm, 90° countersunk screws** for the bezel. The 8 mm
length includes the flat head. Do not substitute longer
stack screws: the blind holes and nearby electronics limit their length.

The bezel's seats taper from **6.2 mm at the face to a 3.4 mm through-hole**
over **1.4 mm**, leaving **0.8 mm of straight hole** in the 2.2 mm bezel.
The design assumes a **6.0 mm head**, which seats about 0.1 mm below the face.
Head sizes vary between fastener specifications; this is a design assumption,
not a measurement of your screws. [Bossard's DIN 7991 dimensions](https://www.tme.eu/Document/8d07529a1413d6e53f44b64cc7bb02f5/BN616-IT.pdf)
list 6 mm for M3; some ISO 10642 listings use a larger theoretical diameter.
Print `countersink-fit.stl` first in the same orientation and settings as the
bezel. With its notched edge on the left and the wide openings facing you,
its seats are **6.0, 6.2, 6.4 and 6.8 mm**, left to right. Check the 6.2 mm seat
with a screw and a straightedge. If another seat fits better, set
`bezel_countersink_diameter` in `parameters.json` before rebuilding.

Dry-fit the stack and gently pre-thread the puck's plastic grip holes before
installing the battery. With power disconnected, route the corner lead through
the disk and puck, seat the cell through the open underside, and reconnect the
electronics. Keep the lead clear of seams and fasteners. Fit the cover and
hand-tighten without squeezing the pouch. Confirm the power switch and USB
connector remain accessible with the real hardware. Thin rubber feet on the
flat disk underside are optional.

CAD verification checks the full battery clearance envelope, straight bottom
loading, hardware and fastener clearance, wire passages, floor/roof thickness,
screw support material, screw-seat taper, retained bezel interface, and valid single-solid,
watertight, bed-aligned exports. Electronics keep-outs are prototype envelopes;
printed fit, screw strength, wire routing and support removal still require a
physical check. The render's screen and battery are illustrative.

Rebuild from the project root:

```sh
.venv/bin/python cad/variants/m3-battery-disk/build.py
/Applications/Blender.app/Contents/MacOS/Blender --background --python cad/variants/m3-battery-disk/render.py
```

The kit is `dotty-m3-battery-disk-print-kit.zip`. Editable individual STEP parts,
an assembled STEP, print instructions and `validation.json` are included.
Earlier small-battery and integrated large-battery variants remain separate.

## Colors

The assembled `assembly.step` and individual STEP parts carry the render's
colors: **orange puck body and bezel**, **slate base disk and battery cover**.
`appearance.json` defines the shared CAD/render palette. Colors do not change
the geometry or fit. Print those two groups in the corresponding filament;
they are separate parts, so a single-extruder printer can make them in
separate batches. STL files contain geometry only and cannot retain colors.
