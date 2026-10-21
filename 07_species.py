r"""
07_species.py - which tree species, and how much living spruce is left? (step 7)
================================================================================

Combines three 10 m rasters (snapped to the species raster):
    Gemeinde (from the boundaries)  x  Schad_agg of the latest release (step 6)
    x  species raster (legend in config.SPECIES_LEGEND)

Answers:
  1. Species x damage class (latest release), whole study area
     -> which species show early symptoms / dead wood, and how often?
  2. Living spruce left: Fichte pixels in the whole forest, and inside the
     conifer mask without damage
  3. Per Gemeinde: spruce area, healthy spruce, early symptoms in spruce / pine,
     calamity area (raster value 1)

Note: the species raster was made AFTER most of the spruce died - cleared
areas are their own class (value 1), so it can't tell what grew there before.

Needs: step 6 (raster RESULT_GDB\Schad_<latest>_<AREA_TAG>). Spatial Analyst.
Outputs:
    reports\07_species_<AREA_TAG>.csv           species x class, whole area
    reports\07_species_gemeinden_<AREA_TAG>.csv per Gemeinde
    new fields in RESULT_GDB\Stats_Gemeinden_<AREA_TAG> (from step 5)
Runtime (measured): all of NRW 4 min.
"""

import arcpy
import csv
import os
import time
from collections import defaultdict

import config as cfg

HA = 10_000.0
CELL = 10
CELL_HA = CELL * CELL / HA


def gemeinde_raster():
    """Gemeinden of the study area -> raster of OBJECTID; returns raster + {oid: (kn, name)}."""
    where = None
    if cfg.AREA_KREIS_KN:
        ftype = [f.type for f in arcpy.ListFields(cfg.GEMEINDEN, cfg.KEY_FIELD)][0]
        where = (f"{cfg.KEY_FIELD} LIKE '{cfg.AREA_KREIS_KN}%'" if ftype == "String"
                 else f"{cfg.KEY_FIELD} >= {int(cfg.AREA_KREIS_KN + '000')} AND "
                      f"{cfg.KEY_FIELD} <= {int(cfg.AREA_KREIS_KN + '999')}")
    lyr = arcpy.management.MakeFeatureLayer(cfg.GEMEINDEN, "gem_species", where)[0]
    oid = arcpy.Describe(lyr).OIDFieldName
    lookup = {}
    with arcpy.da.SearchCursor(lyr, [oid, cfg.KEY_FIELD, cfg.NAME_FIELD]) as cur:
        for o, kn, gn in cur:
            lookup[o] = (str(kn).zfill(8), gn)
    out = os.path.join(cfg.RESULT_GDB, f"Gemeinden_ras_{cfg.AREA_TAG}")
    arcpy.conversion.PolygonToRaster(lyr, oid, out, "CELL_CENTER", "", CELL)
    arcpy.management.Delete(lyr)
    return out, lookup


def combine(rasters):
    """{(v1, v2, ...): ha} from arcpy.sa.Combine (input order kept)."""
    # Combine returns a temporary raster: save it, so its table can be read
    comb = os.path.join(cfg.RESULT_GDB, f"Combine_tmp_{cfg.AREA_TAG}")
    arcpy.sa.Combine([arcpy.Raster(r) for r in rasters]).save(comb)
    if not arcpy.Raster(comb).hasRAT:
        arcpy.management.BuildRasterAttributeTable(comb)
    fields = [f.name for f in arcpy.ListFields(comb)
              if f.type != "OID" and f.name.upper() not in ("VALUE", "COUNT")][:len(rasters)]
    res = {}
    with arcpy.da.SearchCursor(comb, ["Count"] + fields) as cur:
        for count, *vals in cur:
            res[tuple(int(v) for v in vals)] = count * CELL_HA
    arcpy.management.Delete(comb)
    return res


def main():
    arcpy.CheckOutExtension("Spatial")
    arcpy.env.overwriteOutput = True
    arcpy.env.snapRaster = cfg.SPECIES_RASTER
    arcpy.env.cellSize = CELL
    arcpy.env.parallelProcessingFactor = "100%"
    os.makedirs(cfg.REPORT_DIR, exist_ok=True)
    t0 = time.time()

    latest = list(cfg.RELEASES)[-1]
    schad = os.path.join(cfg.RESULT_GDB, f"Schad_{latest}_{cfg.AREA_TAG}")
    if not arcpy.Exists(schad):
        raise SystemExit(f"{schad} not found - run 06_change.py first (same RUN_MODE)")
    legend = cfg.SPECIES_LEGEND
    spruce = [v for v, n in legend.items() if n == "Fichte"][0]
    pine = [v for v, n in legend.items() if n == "Kiefer"][0]
    dmg, early = set(cfg.DAMAGE_CODES), set(cfg.EARLY_CODES)
    na = set(cfg.NOT_ASSESSED_CODES)

    print("1. Gemeinden -> raster")
    gem_ras, gem_lookup = gemeinde_raster()
    arcpy.env.extent = arcpy.Describe(gem_ras).extent

    print("2. Combine Gemeinde x Schad_agg x species (conifer mask only)")
    in_mask = combine([gem_ras, schad, cfg.SPECIES_RASTER])
    print("3. Combine Gemeinde x species (whole forest)")
    whole = combine([gem_ras, cfg.SPECIES_RASTER])
    print(f"   ({time.time() - t0:.0f} s)")

    # ---- 1. species x damage class, whole study area --------------------------
    sp_cls = defaultdict(lambda: defaultdict(float))
    for (g, c, s), ha in in_mask.items():
        sp_cls[s][c] += ha
    codes = sorted({c for d in sp_cls.values() for c in d})
    names = cfg.LABELS[cfg.DAMAGE_FIELD]

    print(f"\n=== Species x {cfg.DAMAGE_FIELD} ({latest}), inside the conifer mask, ha ===")
    print(f"  {'species':<20}" + "".join(f"{c:>9}" for c in codes) +
          f"{'early %':>9}{'dmg %':>8}")
    rows = []
    for s in sorted(sp_cls):
        d = sp_cls[s]
        ass = sum(v for c, v in d.items() if c not in na)
        e = sum(v for c, v in d.items() if c in early)
        x = sum(v for c, v in d.items() if c in dmg)
        e_pct = 100 * e / ass if ass else 0
        x_pct = 100 * x / ass if ass else 0
        print(f"  {legend.get(s, s):<20}" + "".join(f"{d.get(c, 0):>9,.0f}" for c in codes) +
              f"{e_pct:>9.1f}{x_pct:>8.1f}")
        rows.append([s, legend.get(s, "?")] + [round(d.get(c, 0), 1) for c in codes] +
                    [round(ass, 1), round(e_pct, 2), round(x_pct, 2)])
    print("  early % / dmg % = share of the assessed area of that species")

    path = os.path.join(cfg.REPORT_DIR, f"07_species_{cfg.AREA_TAG}.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Wert", "Baumart"] + [f"{c} {names.get(c, '')}" for c in codes] +
                   ["bewertet_ha", "erste_Symptome_%", "Schaden_%"])
        w.writerows(rows)

    # ---- 2. living spruce left -------------------------------------------------
    spruce_all = sum(ha for (g, s), ha in whole.items() if s == spruce)
    spruce_mask = sum(ha for (g, c, s), ha in in_mask.items() if s == spruce)
    spruce_ok = sum(ha for (g, c, s), ha in in_mask.items() if s == spruce and c == 0)
    spruce_early = sum(ha for (g, c, s), ha in in_mask.items() if s == spruce and c in early)
    calamity = sum(ha for (g, s), ha in whole.items() if s == cfg.CALAMITY_VALUE)
    print("\n=== Spruce ===")
    print(f"  Fichte pixels, whole forest:           {spruce_all:>10,.0f} ha")
    print(f"    of which inside the conifer mask:    {spruce_mask:>10,.0f} ha")
    print(f"    healthy (Schad_agg 0):               {spruce_ok:>10,.0f} ha")
    print(f"    with early symptoms (Schad_agg 1):   {spruce_early:>10,.0f} ha"
          f"  ({100 * spruce_early / spruce_mask if spruce_mask else 0:.1f} % of mask spruce)")
    print(f"  Calamity area (raster value {cfg.CALAMITY_VALUE}):        {calamity:>10,.0f} ha")

    # ---- 3. per Gemeinde ---------------------------------------------------------
    per = defaultdict(lambda: defaultdict(float))
    for (g, s), ha in whole.items():
        if s == spruce:
            per[g]["fi_ha"] += ha
        if s == cfg.CALAMITY_VALUE:
            per[g]["kal_ha"] += ha
    for (g, c, s), ha in in_mask.items():
        if s == spruce and c == 0:
            per[g]["fi_ok_ha"] += ha
        if s == spruce and c in early:
            per[g]["fi_early_ha"] += ha
        if s == pine and c in early:
            per[g]["ki_early_ha"] += ha
        if s == pine and c not in na:
            per[g]["ki_ass_ha"] += ha
    fields = [("fi_ha", "Fichte gesamt (ha)"), ("fi_ok_ha", "Fichte gesund (ha)"),
              ("fi_early_ha", "Fichte erste Symptome (ha)"),
              ("ki_early_ha", "Kiefer erste Symptome (ha)"),
              ("kal_ha", "Kalamitaetsflaeche Baumartenkarte (ha)")]

    path2 = os.path.join(cfg.REPORT_DIR, f"07_species_gemeinden_{cfg.AREA_TAG}.csv")
    by_kn = {}
    with open(path2, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["KN", "Gemeinde"] + [k for k, _ in fields] + ["ki_early_pct"])
        for g, vals in sorted(per.items(), key=lambda x: gem_lookup.get(x[0], ("", ""))[0]):
            kn, name = gem_lookup.get(g, ("?", "?"))
            ki_pct = 100 * vals["ki_early_ha"] / vals["ki_ass_ha"] if vals["ki_ass_ha"] else 0
            w.writerow([kn, name] + [round(vals[k], 1) for k, _ in fields] + [round(ki_pct, 2)])
            by_kn[kn] = vals

    stats_fc = os.path.join(cfg.RESULT_GDB, f"Stats_Gemeinden_{cfg.AREA_TAG}")
    if arcpy.Exists(stats_fc):
        existing = [f.name for f in arcpy.ListFields(stats_fc)]
        for name, alias in fields:
            if name not in existing:
                arcpy.management.AddField(stats_fc, name, "DOUBLE", field_alias=alias)
        with arcpy.da.UpdateCursor(stats_fc, [cfg.KEY_FIELD] + [k for k, _ in fields]) as cur:
            for row in cur:
                vals = by_kn.get(str(row[0]).zfill(8))
                cur.updateRow([row[0]] + [round(vals[k], 1) if vals else None
                                          for k, _ in fields])
        print(f"\n  fields added to {os.path.basename(stats_fc)}")
    else:
        print(f"\n  {os.path.basename(stats_fc)} not found - run 05_stats.py to get the map layer")

    print(f"\n=== Gemeinden with the most healthy spruce left ===")
    print(f"  {'Gemeinde':<28}{'Fichte ha':>11}{'healthy':>10}{'early':>8}{'calamity':>10}")
    top = sorted(per.items(), key=lambda x: -x[1]["fi_ok_ha"])[:15]
    for g, v in top:
        print(f"  {gem_lookup.get(g, ('', '?'))[1][:27]:<28}{v['fi_ha']:>11,.0f}"
              f"{v['fi_ok_ha']:>10,.0f}{v['fi_early_ha']:>8,.0f}{v['kal_ha']:>10,.0f}")

    print(f"\nReports: {path}\n         {path2}\nDone in {(time.time() - t0) / 60:.1f} min.")


if __name__ == "__main__":
    main()
