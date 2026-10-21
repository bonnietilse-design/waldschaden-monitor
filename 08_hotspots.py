r"""
08_hotspots.py - where are the statistically significant hotspots? (step 8)
===========================================================================

1. 1 km2 hexagons over the study area          RESULT_GDB\Hex_<AREA_TAG>
2. per hexagon (Combine of a hexagon raster with the Schad_* rasters of step 6):
      ass_ha     assessed conifer area
      dmg_ha     damaged (dead wood + cleared) by the latest release
      dmg_pct    dmg_ha as % of ass_ha
      new_ha     NEW damage since the first release (Schadensjahr >= 2)
      early_pct  early symptoms in the latest release, % of ass_ha
3. Optimized Hot Spot Analysis (Getis-Ord Gi*) for dmg_pct, new_ha and
   early_pct, only on hexagons with at least MIN_HA of conifer forest
                                               RESULT_GDB\HS_<field>_<AREA_TAG>
   Gi_Bin: +3 / +2 / +1 = hot spot with 99 / 95 / 90 % confidence,
           -3 / -2 / -1 = cold spot, 0 = not significant

Why Optimized and not Emerging Hot Spot: Emerging needs >= 10 time steps,
we have 3 releases.

Needs: step 6 (rasters Schad_*). Spatial Analyst.
Runtime (measured): test Kreis 4.7 min (most of it the first hotspot run).
"""

import arcpy
import csv
import os
import time

import config as cfg

HA = 10_000.0
CELL = 10
MIN_HA = 5          # hexagons with less conifer forest are left out of the hotspot test
HEX_SIZE = "1 SquareKilometers"
FIELDS = ("dmg_pct", "new_ha", "early_pct")


def make_hexagons():
    out = os.path.join(cfg.RESULT_GDB, f"Hex_{cfg.AREA_TAG}")
    if cfg.AREA_KREIS_KN:
        ftype = [f.type for f in arcpy.ListFields(cfg.KREISE, cfg.KEY_FIELD)][0]
        where = (f"{cfg.KEY_FIELD} LIKE '{cfg.AREA_KREIS_KN}%'" if ftype == "String"
                 else f"{cfg.KEY_FIELD} = {int(cfg.AREA_KREIS_KN)}")
        area = arcpy.management.MakeFeatureLayer(cfg.KREISE, "hex_area", where)[0]
    else:
        area = arcpy.management.MakeFeatureLayer(cfg.NRW_GRENZE, "hex_area")[0]
    desc = arcpy.Describe(area)
    tmp = r"memory\hex_all"
    arcpy.management.GenerateTessellation(tmp, desc.extent, "HEXAGON", HEX_SIZE,
                                          desc.spatialReference)
    lyr = arcpy.management.MakeFeatureLayer(tmp, "hex_lyr")[0]
    arcpy.management.SelectLayerByLocation(lyr, "INTERSECT", area)
    arcpy.conversion.ExportFeatures(lyr, out)
    for x in (lyr, tmp, area):
        arcpy.management.Delete(x)
    print(f"  {os.path.basename(out)}: {int(arcpy.management.GetCount(out)[0]):,} hexagons")
    return out


def per_hexagon(hexes, rasters):
    """{hex OID: {(code_rel1, code_rel2, ...): ha}} via Combine of hexagon raster + releases.

    Same technique as steps 6 and 7 (Combine), so no dependency on the
    Schadensjahr raster."""
    oid = arcpy.Describe(hexes).OIDFieldName
    hex_ras = os.path.join(cfg.RESULT_GDB, f"Hex_ras_{cfg.AREA_TAG}")
    arcpy.conversion.PolygonToRaster(hexes, oid, hex_ras, "CELL_CENTER", "", CELL)
    comb = os.path.join(cfg.RESULT_GDB, f"Combine_tmp_{cfg.AREA_TAG}")
    arcpy.sa.Combine([arcpy.Raster(r) for r in [hex_ras] + rasters]).save(comb)
    if not arcpy.Raster(comb).hasRAT:
        arcpy.management.BuildRasterAttributeTable(comb)
    vfields = [f.name for f in arcpy.ListFields(comb)
               if f.type != "OID" and f.name.upper() not in ("VALUE", "COUNT")]
    vfields = vfields[:1 + len(rasters)]
    res = {}
    with arcpy.da.SearchCursor(comb, ["Count"] + vfields) as cur:
        for count, h, *codes in cur:
            res.setdefault(int(h), {})[tuple(int(c) for c in codes)] = count * CELL * CELL / HA
    for x in (comb, hex_ras):
        arcpy.management.Delete(x)
    total = sum(sum(d.values()) for d in res.values())
    print(f"   {len(res):,} hexagons with conifer forest, {total:,.0f} ha in total")
    return res


def main():
    arcpy.CheckOutExtension("Spatial")
    arcpy.env.overwriteOutput = True
    arcpy.env.snapRaster = cfg.SPECIES_RASTER
    arcpy.env.cellSize = CELL
    arcpy.env.parallelProcessingFactor = "100%"
    os.makedirs(cfg.REPORT_DIR, exist_ok=True)
    t0 = time.time()

    rasters = [os.path.join(cfg.RESULT_GDB, f"Schad_{lab}_{cfg.AREA_TAG}")
               for lab in cfg.RELEASES]
    for r in rasters:
        if not arcpy.Exists(r):
            raise SystemExit(f"{r} not found - run 06_change.py first (same RUN_MODE)")
    dmg_codes, early_codes = set(cfg.DAMAGE_CODES), set(cfg.EARLY_CODES)
    na_codes = set(cfg.NOT_ASSESSED_CODES)

    print("1. Hexagons")
    hexes = make_hexagons()

    print("2. Damage per hexagon (Combine)")
    data = per_hexagon(hexes, rasters)
    print(f"   ({time.time() - t0:.0f} s)")

    new_fields = [("ass_ha", "Nadelwald bewertet (ha)"), ("dmg_ha", "Schaden (ha)"),
                  ("dmg_pct", "Schaden (%)"), ("new_ha", "neuer Schaden seit 2023 (ha)"),
                  ("early_pct", "erste Symptome (%)")]
    for name, alias in new_fields:
        arcpy.management.AddField(hexes, name, "DOUBLE", field_alias=alias)
    oid = arcpy.Describe(hexes).OIDFieldName
    with arcpy.da.UpdateCursor(hexes, [oid] + [n for n, _ in new_fields]) as cur:
        for row in cur:
            ass = dmg = new = early = 0.0
            for codes, ha in data.get(row[0], {}).items():
                if codes[-1] in na_codes:          # not assessed in the latest release
                    continue
                ass += ha
                if codes[-1] in dmg_codes:
                    dmg += ha
                    if codes[0] not in dmg_codes:  # not yet damaged in the first release
                        new += ha
                if codes[-1] in early_codes:
                    early += ha
            row[1:] = [round(ass, 2), round(dmg, 2),
                       round(100 * dmg / ass, 2) if ass else None, round(new, 2),
                       round(100 * early / ass, 2) if ass else None]
            cur.updateRow(row)

    print(f"\n3. Optimized Hot Spot Analysis (hexagons with >= {MIN_HA} ha conifer)")
    forest = arcpy.management.MakeFeatureLayer(hexes, "hex_forest", f"ass_ha >= {MIN_HA}")[0]
    n = int(arcpy.management.GetCount(forest)[0])
    print(f"   {n:,} hexagons in the test")
    if n < 30:
        raise SystemExit("Fewer than 30 hexagons - too few for a hotspot analysis")

    summary = []
    for field in FIELDS:
        out = os.path.join(cfg.RESULT_GDB, f"HS_{field}_{cfg.AREA_TAG}")
        t1 = time.time()
        arcpy.stats.OptimizedHotSpotAnalysis(forest, out, field)
        bins = {}
        with arcpy.da.SearchCursor(out, ["Gi_Bin", field]) as cur:
            for b, v in cur:
                c = bins.setdefault(b, [0, 0.0])
                c[0] += 1
                c[1] += v or 0
        print(f"\n   {field} -> {os.path.basename(out)} ({time.time() - t1:.0f} s)")
        for b in sorted(bins, reverse=True):
            label = {3: "hot 99%", 2: "hot 95%", 1: "hot 90%", 0: "n.s.",
                     -1: "cold 90%", -2: "cold 95%", -3: "cold 99%"}[b]
            cnt, tot = bins[b]
            print(f"     {label:<10}{cnt:>8,} hexagons   mean {field} {tot / cnt:8.2f}")
            summary.append([field, b, label, cnt, round(tot / cnt, 3)])

    path = os.path.join(cfg.REPORT_DIR, f"08_hotspots_{cfg.AREA_TAG}.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Feld", "Gi_Bin", "Klasse", "Hexagone", "Mittelwert"])
        w.writerows(summary)
    print(f"\nReport: {path}\nDone in {(time.time() - t0) / 60:.1f} min.")


if __name__ == "__main__":
    main()
