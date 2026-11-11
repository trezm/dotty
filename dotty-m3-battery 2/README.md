# M3 puck with a compact battery base

This version adds a horizontal well for the user's **34 × 25 × 5 mm** cell
under the original sloped puck. Only a short wall at the rear edge of the
battery base rises to meet the puck's inclined back. The upper rear is not
cut off or filled down to the desk.

The display remains **58 mm across, at 30°**, with the face center **4 mm
higher** than the original M3 case. The minimum well is **26 × 35 × 6 mm**,
with **0.5 mm allowance around the cell** and a **1.2 mm bottom skin**. An
opening above the well connects it to the electronics cavity, without a low
bridging roof. A rigid-cell lift/tilt removal path is checked in CAD with the
boards and bezel removed.
Install the battery before the boards, and confirm the real lead exit,
retention and insertion path during the physical fit check.

Use three M3 × 8 mm screws and the matching bezel. See
[PRINTING.txt](PRINTING.txt) for print orientation and local support checks.
The original sloping rear still needs removable support during printing;
inspect the PCB seats and USB roof as well. Physical fit and print strength
have not been tested. CAD checks verify that the original puck behind the
battery base is unchanged, in addition to battery/electronics clearance and
mesh validity.

Rebuild and validate from the project root:

```sh
.venv/bin/python cad/variants/m3-battery/build.py
```

The command exports STLs, editable STEP solids, an assembled STEP, validation
results, a CAD-derived cutaway, and `dotty-m3-battery-print-kit.zip`. It leaves
the original M2 and M3 files intact.
