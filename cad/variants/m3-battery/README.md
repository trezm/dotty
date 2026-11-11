# M3 puck with a compact battery base

This version adds a horizontal well for the user's **34 × 25 × 5 mm** cell
under the original sloped puck. The cell is **34 mm across and 25 mm front
to back**, turned 90° from the first battery version and shifted **6.9 mm
rearward**. Its front edge moves back **11.4 mm**. The lower wall follows the
original tilted cylinder, preserving the clean front curve. Only a short wall at the rear edge of the
battery base rises to meet the puck's inclined back. The upper rear is not
cut off or filled down to the desk.

The display remains **58 mm across, at 30°**, with the face center **4 mm
higher** than the original M3 case. The minimum well is **35 × 26 × 6 mm**,
with **0.5 mm allowance around the cell** and a **1.2 mm bottom skin**. An
opening above the well connects it to the electronics cavity, without a low
bridging roof. The surrounding internal floor is **1.6 mm** to provide
clearance for turning the cell during insertion; the floor directly under
the cell remains **1.2 mm**. A rigid-cell lift/tilt/turn removal path is checked
in CAD with the boards and bezel removed. The PCB mounting tabs and their
connections to the case rim are protected from the wider access cut.
Install the battery before the boards, and confirm the real lead exit,
retention and insertion path during the physical fit check.

Use three M3 × 8 mm screws and the matching bezel. See
[PRINTING.txt](PRINTING.txt) for print orientation and local support checks.
The original sloping rear still needs removable support during printing;
inspect the PCB seats and USB roof as well. Physical fit and print strength
have not been tested. CAD checks verify that the original front curve and
upper sloped rear are unchanged, in addition to battery/electronics clearance and
mesh validity.

Rebuild and validate from the project root:

```sh
.venv/bin/python cad/variants/m3-battery/build.py
```

The command exports STLs, editable STEP solids, an assembled STEP, validation
results, a CAD-derived cutaway, and `dotty-m3-battery-print-kit.zip`. It leaves
the original M2 and M3 files intact.
