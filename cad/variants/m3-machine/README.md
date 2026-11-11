# Dotty for M3 machine screws

Print the **matching body and bezel** from this version, using **three M3 x 8 mm screws**. The original version uses M2 screws and has a different screw circle. The M3 body is **58 mm across**, allowing adequate material around the larger screws and head recesses.

| Feature | M3 version |
| --- | --- |
| Base grip-hole diameter | 2.8 mm |
| Base hole depth | 7.2 mm |
| Entry chamfer depth | 0.4 mm |
| Bezel clearance-hole diameter | 3.4 mm |
| Head recess | 6.2 mm diameter x 1.1 mm deep |
| Screw circle diameter | 50 mm |
| Screw length | 8 mm under the head |

These slightly undersized **grip holes let the machine screws form threads in the plastic**; they are not loose clearance holes, and no nuts or inserts are required for this version. Start straight and hand-tighten gently. Do not force a binding screw or use a powered driver. Heads taller than the 1.1 mm recess will stand above the bezel. Longer screws may bottom out before the bezel clamps.

**Print [fit-test.stl](exports/fit-test.stl) first.** It has labelled 2.6, 2.7, 2.8 and 2.9 mm pilot holes with the same inclination, depth and entry chamfer as the enclosure. Printed hole size varies with filament and printer; confirm that the supplied 2.8 mm size grips your screw without excessive force. For a tight print, manually clear the hole to 2.8 mm; for a loose print, reduce `screw_pilot_diameter` in this variant's JSON and regenerate.

The body retains the display seat, parallel back, USB opening and integrated 8 mm rear foot. Print the body on its flat base/heel and the bezel face down, with supports under the remaining back overhang. The main project's electronics and firmware instructions still apply. [PRINTING.txt](PRINTING.txt) contains the print and assembly notes.

Rebuild this variant without replacing the M2 files:

```sh
.venv/bin/python cad/enclosure.py --parameters cad/variants/m3-machine/parameters.json --output cad/variants/m3-machine/exports
.venv/bin/python scripts/verify_cad.py --parameters cad/variants/m3-machine/parameters.json --output cad/variants/m3-machine/exports
.venv/bin/python scripts/check_balance.py --parameters cad/variants/m3-machine/parameters.json --output cad/variants/m3-machine/exports
.venv/bin/python cad/variants/m3-machine/make_fit_test.py
```

The pilot diameter is a starting point informed by the [Bambu Lab / MakerWorld machine-screw hole guide](https://forum.bambulab.com/t/how-to-design-screw-holes-for-3d-printing/217352), which discusses using machine screws directly in printed plastic for light-duty connections that are not frequently disassembled. This variant has not yet been physically tested. The fit coupon is the appropriate check for your printer and screws.
