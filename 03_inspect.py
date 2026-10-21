"""
03_inspect.py - check the data before the analysis (step 3)
==========================================================

Answers, for the whole of NRW:
  1. How many polygons and hectares per class (Klasse, Schadstufe, Schad_agg)
     in each release?
  2. How do Schadstufe and Schad_agg relate (cross table, latest release)?
  3. Which image dates (Datum_T1 / Datum_T2) are in the data?
  4. Which values does the species raster have, and how many hectares each?
  5. Do the boundaries look right, and what is the key of the study area?

Nothing is changed in the data, with one exception: if the species raster has
no attribute table, one is built (needed to read its values).

Output: reports/03_inspect_classes.csv, reports/03_inspect_species.csv
and a summary in the console. Runtime NRW-wide: roughly 5-15 minutes.
"""

import arcpy
import csv
import os
import time

import config as cfg

HA = 10_000.0


def stats(fc, case_fields):
    """Count and area (ha) per value combination, using the fast Statistics tool."""
    out = r"memory\stats_tmp"
    if arcpy.Exists(out):
        arcpy.management.Delete(out)
    arcpy.analysis.Statistics(fc, out, [["Shape_Area", "SUM"]], case_fields)
    rows = []
    with arcpy.da.SearchCursor(out, case_fields + ["FREQUENCY", "SUM_Shape_Area"]) as cur:
        for r in cur:
            rows.append((r[:-2], r[-2], (r[-1] or 0) / HA))
    arcpy.management.Delete(out)
    return sorted(rows)


def field_names(fc):
    return {f.name.lower(): f for f in arcpy.ListFields(fc)}


def check_releases(writer):
    print("\n=== 1. Classes per release (NRW-wide) ===")
    for label, layer in cfg.RELEASES.items():
        fc = os.path.join(cfg.RAW_GDB, layer)
        if not arcpy.Exists(fc):
            print(f"\n{layer}: NOT FOUND in {cfg.RAW_GDB}")
            continue
        fields = field_names(fc)
        print(f"\n{layer}  ({int(arcpy.management.GetCount(fc)[0]):,} polygons)")
        for fld in ("Schad_agg", "Schadstufe", "Klasse"):
            if fld.lower() not in fields:
                print(f"  {fld}: field missing")
                continue
            t0 = time.time()
            rows = stats(fc, [fields[fld.lower()].name])
            total = sum(ha for _, _, ha in rows)
            print(f"  {fld}  ({time.time() - t0:.0f} s)")
            for (val,), n, ha in rows:
                name = cfg.LABELS.get(fld, {}).get(val, "?")
                print(f"    {val:>3}  {name:<38} {n:>10,} polygons {ha:>12,.0f} ha"
                      f"  {100 * ha / total:5.1f} %")
                writer.writerow([layer, fld, val, name, n, round(ha, 1)])

        # summary in the terms of the analysis
        rows = stats(fc, [fields[cfg.DAMAGE_FIELD.lower()].name])
        dmg = sum(ha for (v,), _, ha in rows if v in cfg.DAMAGE_CODES)
        na = sum(ha for (v,), _, ha in rows if v in cfg.NOT_ASSESSED_CODES)
        tot = sum(ha for _, _, ha in rows)
        print(f"  -> damaged ({cfg.DAMAGE_FIELD} {cfg.DAMAGE_CODES}): {dmg:,.0f} ha"
              f" | not assessed: {na:,.0f} ha ({100 * na / tot:.1f} %) | total {tot:,.0f} ha")


def check_crosstab(writer):
    latest = list(cfg.RELEASES.values())[-1]
    fc = os.path.join(cfg.RAW_GDB, latest)
    if not arcpy.Exists(fc):
        return
    print(f"\n=== 2. Schadstufe x Schad_agg ({latest}, ha) ===")
    f = field_names(fc)
    rows = stats(fc, [f["schadstufe"].name, f["schad_agg"].name])
    for (stufe, agg), n, ha in rows:
        print(f"  Schadstufe {stufe:>3} / Schad_agg {agg:>3}: {ha:>12,.0f} ha  ({n:,} polygons)")
        writer.writerow([latest, "Schadstufe|Schad_agg", f"{stufe}|{agg}", "", n, round(ha, 1)])


def check_dates():
    print("\n=== 3. Image dates ===")
    for layer in cfg.RELEASES.values():
        fc = os.path.join(cfg.RAW_GDB, layer)
        if not arcpy.Exists(fc):
            continue
        f = field_names(fc)
        for fld in ("datum_t1", "datum_t2"):
            if fld not in f:
                print(f"  {layer}.{fld}: missing")
                continue
            rows = stats(fc, [f[fld].name])
            values = ", ".join(f"{v[0]} ({ha:,.0f} ha)" for v, _, ha in rows[:8])
            more = f" ... +{len(rows) - 8} more" if len(rows) > 8 else ""
            print(f"  {layer}.{f[fld].name} [{f[fld].type}]: {values}{more}")


def check_species(writer):
    print("\n=== 4. Species raster ===")
    if not arcpy.Exists(cfg.SPECIES_RASTER):
        print(f"  NOT FOUND: {cfg.SPECIES_RASTER}")
        return
    r = arcpy.Raster(cfg.SPECIES_RASTER)
    cell_ha = r.meanCellWidth * r.meanCellHeight / HA
    print(f"  cell size {r.meanCellWidth} m | type {r.pixelType} | NoData {r.noDataValue}"
          f" | {r.width:,} x {r.height:,} cells | {r.spatialReference.name}")
    if not r.hasRAT:
        print("  no attribute table - building one (a few minutes) ...")
        arcpy.management.BuildRasterAttributeTable(cfg.SPECIES_RASTER)
    fields = [f.name for f in arcpy.ListFields(cfg.SPECIES_RASTER)]
    print(f"  attribute table fields: {fields}")
    extra = [f for f in fields if f.upper() not in ("OBJECTID", "VALUE", "COUNT")]
    with arcpy.da.SearchCursor(cfg.SPECIES_RASTER, ["Value", "Count"] + extra) as cur:
        for row in cur:
            val, cnt = row[0], row[1]
            name = cfg.SPECIES_LEGEND.get(val, " / ".join(str(x) for x in row[2:]) or "?")
            print(f"    value {val:>3}: {cnt * cell_ha:>12,.0f} ha   {name}")
            writer.writerow([val, name, cnt, round(cnt * cell_ha, 1)])
    if not cfg.SPECIES_LEGEND:
        print("  -> fill SPECIES_LEGEND in config.py (value -> species),"
              " using the legend on Geoportal.NRW")


def check_boundaries():
    print("\n=== 5. Boundaries ===")
    for fc, expected in ((cfg.KREISE, 53), (cfg.GEMEINDEN, 396)):
        n = int(arcpy.management.GetCount(fc)[0])
        print(f"  {os.path.basename(fc)}: {n} ({'OK' if n == expected else f'expected {expected}'})")
    with arcpy.da.SearchCursor(cfg.KREISE, [cfg.KEY_FIELD, cfg.NAME_FIELD]) as cur:
        kreise = {str(k): n for k, n in cur}
    print(f"  run mode: {cfg.RUN_MODE} "
          f"({'all of NRW' if cfg.RUN_MODE == 'nrw' else 'test Kreis only'})")
    match = {k: n for k, n in kreise.items() if k.startswith(cfg.TEST_KREIS_KN)}
    if match:
        k, n = next(iter(match.items()))
        with arcpy.da.SearchCursor(cfg.GEMEINDEN, [cfg.KEY_FIELD]) as cur:
            n_gem = sum(1 for (g,) in cur if str(g).startswith(cfg.TEST_KREIS_KN))
        print(f"  test Kreis: {n} (KN {k}) with {n_gem} Gemeinden")
    else:
        print(f"  TEST_KREIS_KN {cfg.TEST_KREIS_KN} not found. Kreis keys, e.g.:")
        for k, n in list(sorted(kreise.items()))[:5]:
            print(f"    {k}  {n}")


def main():
    os.makedirs(cfg.REPORT_DIR, exist_ok=True)
    t0 = time.time()
    with open(os.path.join(cfg.REPORT_DIR, "03_inspect_classes.csv"), "w",
              newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Layer", "Feld", "Wert", "Bedeutung", "Polygone", "Hektar"])
        check_releases(w)
        check_crosstab(w)
    check_dates()
    with open(os.path.join(cfg.REPORT_DIR, "03_inspect_species.csv"), "w",
              newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Wert", "Baumart", "Zellen", "Hektar"])
        check_species(w)
    check_boundaries()
    print(f"\nDone in {(time.time() - t0) / 60:.1f} min. Reports in {cfg.REPORT_DIR}")


if __name__ == "__main__":
    main()
