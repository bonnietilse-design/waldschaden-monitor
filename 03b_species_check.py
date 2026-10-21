"""
03b_species_check.py - which species raster values are conifers / spruce?

Overlays the species raster with the vitality polygons of the test Kreis
(output of step 4) and prints, for each Schad_agg class, how many hectares
fall on each raster value.

How to read it:
  - Schad_agg 0 (healthy conifer): the values with most area here are the
    conifer classes; in the Sauerland the biggest one is almost certainly spruce.
  - Schad_agg 3 (cleared): shows what the raster says on clearings - tells us
    whether the raster was made before or after the spruce died.

Needs: RUN_MODE = "test" and step 4 already run. Uses Spatial Analyst.
Runtime: about a minute.
"""

import arcpy
import os
from collections import defaultdict

import config as cfg

arcpy.CheckOutExtension("Spatial")
arcpy.env.overwriteOutput = True

latest = list(cfg.RELEASES.values())[-1]
zones = os.path.join(cfg.RESULT_GDB, f"{latest}_{cfg.AREA_TAG}")
raster = os.path.join(cfg.RESULT_GDB, f"Baumarten_{cfg.AREA_TAG}")
out = r"memory\species_by_damage"

# Schad_agg is stored as a decimal number, but TabulateArea needs an integer
# zone field -> work on a copy in memory with an integer field "zone"
tmp = r"memory\zones"
arcpy.conversion.ExportFeatures(zones, tmp)
arcpy.management.AddField(tmp, "zone", "SHORT")
arcpy.management.CalculateField(tmp, "zone", f"int(!{cfg.DAMAGE_FIELD}!)", "PYTHON3")

arcpy.env.snapRaster = raster
arcpy.sa.TabulateArea(tmp, "zone", raster, "Value", out, 10)

value_fields = [f.name for f in arcpy.ListFields(out) if f.name.upper().startswith("VALUE_")]
table = defaultdict(dict)
with arcpy.da.SearchCursor(out, ["zone"] + value_fields) as cur:
    for code, *areas in cur:
        for fld, a in zip(value_fields, areas):
            table[int(code)][int(fld.split("_")[1])] = a / 10_000

values = sorted({v for row in table.values() for v in row})
print(f"Hectares per Schad_agg class (rows) and species raster value (columns), {latest}\n")
print("Schad_agg".ljust(40) + "".join(f"{v:>8}" for v in values))
for code in sorted(table):
    name = f"{code:>3} {cfg.LABELS[cfg.DAMAGE_FIELD].get(code, '?')}"
    print(name[:39].ljust(40) + "".join(f"{table[code].get(v, 0):>8,.0f}" for v in values))

# raster values in the whole Kreis, also outside the conifer mask
if not arcpy.Raster(raster).hasRAT:
    arcpy.management.BuildRasterAttributeTable(raster)
counts = {}
with arcpy.da.SearchCursor(raster, ["Value", "Count"]) as cur:
    for v, c in cur:
        counts[int(v)] = c * 100 / 10_000   # 10 m cells = 0.01 ha
print("\nWhole Kreis".ljust(40) + "".join(f"{counts.get(v, 0):>8,.0f}" for v in values))
inside = {v: sum(table[c].get(v, 0) for c in table) for v in values}
print("% inside conifer mask".ljust(40) +
      "".join(f"{100 * inside[v] / counts[v] if counts.get(v) else 0:>8.0f}" for v in values))
