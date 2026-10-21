r"""
09a_prepare_publish.py - lean layers for ArcGIS Online (step 9a, nothing is uploaded)
=====================================================================================

ArcGIS Online charges hosted FEATURE layers by size every month (about
2.4 credits per 10 MB), so only small, map-ready layers go online as features.
This script copies them into one "publish" geodatabase, keeps only the fields
the maps need, and prints the size, so you can check the credit cost BEFORE
anything is uploaded (step 9b).

Layers in  DATA_DIR\Waldschaden_NRW_Publish.gdb:
    Gemeinden_Waldschaden   396 Gemeinden: damage per release, spruce fields,
                            flag "wenig_nadelwald" (< 100 ha conifer: grey on maps)
    Kreise_Waldschaden      53 Kreise, same numbers
    Hotspots_Hex            1 km hexagons (>= 5 ha conifer) with the values
                            AND the three hotspot results in one layer:
                            gi_dmg, gi_new, gi_early  (+3 hot 99 % ... -3 cold 99 %)

Big data (vitality polygons, Schadensjahr raster) are NOT copied: they go
online as tile layers, which are charged per GB instead of per 10 MB.

Runtime: a few minutes.
"""

import arcpy
import os

import config as cfg

PUBLISH_GDB = os.path.join(cfg.DATA_DIR, "Waldschaden_NRW_Publish.gdb")
TAG = "NRW"                 # always publish the NRW results
MIN_CONIFER_HA = 100        # below this, percentages are not meaningful

GI_LABELS = {3: "Hotspot (99 %)", 2: "Hotspot (95 %)", 1: "Hotspot (90 %)",
             0: "nicht signifikant", -1: "Coldspot (90 %)", -2: "Coldspot (95 %)",
             -3: "Coldspot (99 %)"}


def src(name):
    path = os.path.join(cfg.RESULT_GDB, f"{name}_{TAG}")
    if not arcpy.Exists(path):
        raise SystemExit(f"{path} not found - run the steps for RUN_MODE = 'nrw' first")
    return path


def keep_only(fc, keep):
    existing = [f.name for f in arcpy.ListFields(fc)]
    keep = [k for k in keep if k in existing]
    arcpy.management.DeleteField(fc, keep, "KEEP_FIELDS")
    return keep


def stats_layer(source, out_name, extra_keep):
    out = os.path.join(PUBLISH_GDB, out_name)
    arcpy.conversion.ExportFeatures(source, out)
    fields = [f.name for f in arcpy.ListFields(out)]
    keep = [cfg.KEY_FIELD, cfg.NAME_FIELD, "con_ha"] + extra_keep + \
           [f for f in fields if f.startswith(("dmg_ha_", "dmg_pct_", "new_ha_", "early_ha_"))]
    keep_only(out, keep)
    # flag areas with too little conifer forest for a meaningful percentage
    arcpy.management.AddField(out, "wenig_nadelwald", "SHORT",
                              field_alias=f"weniger als {MIN_CONIFER_HA} ha Nadelwald")
    with arcpy.da.UpdateCursor(out, ["con_ha", "wenig_nadelwald"]) as cur:
        for con, _ in cur:
            cur.updateRow([con, 1 if (con or 0) < MIN_CONIFER_HA else 0])
    print(f"  {out_name}: {int(arcpy.management.GetCount(out)[0])} features")
    return out


def gi_lookup(hs_fc):
    """{hexagon OID: Gi_Bin} from an Optimized Hot Spot output (SOURCE_ID = input OID)."""
    fields = [f.name for f in arcpy.ListFields(hs_fc)]
    if "SOURCE_ID" not in fields:
        raise SystemExit(f"{hs_fc} has no SOURCE_ID field - fields are {fields}")
    with arcpy.da.SearchCursor(hs_fc, ["SOURCE_ID", "Gi_Bin"]) as cur:
        return {sid: b for sid, b in cur}


def hotspot_layer():
    hexes = src("Hex")
    out = os.path.join(PUBLISH_GDB, "Hotspots_Hex")
    lookups = {name: gi_lookup(src(f"HS_{field}"))
               for name, field in (("gi_dmg", "dmg_pct"), ("gi_new", "new_ha"),
                                   ("gi_early", "early_pct"))}

    # only hexagons that took part in the hotspot test (>= 5 ha conifer)
    lyr = arcpy.management.MakeFeatureLayer(hexes, "hex_pub", "ass_ha >= 5")[0]
    arcpy.conversion.ExportFeatures(lyr, out)
    arcpy.management.Delete(lyr)

    # remember the original OID before it changes in the copy
    oid_src = arcpy.Describe(hexes).OIDFieldName
    with arcpy.da.SearchCursor(hexes, [oid_src, "GRID_ID"], "ass_ha >= 5") as cur:
        oid_by_grid = {g: o for o, g in cur}

    for name, alias in (("gi_dmg", "Hotspot Schadensanteil"),
                        ("gi_new", "Hotspot neuer Schaden seit 2023"),
                        ("gi_early", "Hotspot erste Symptome")):
        arcpy.management.AddField(out, name, "SHORT", field_alias=alias)
        arcpy.management.AddField(out, name + "_txt", "TEXT", field_length=20,
                                  field_alias=alias + " (Text)")
    names = list(lookups)
    missing = 0
    with arcpy.da.UpdateCursor(out, ["GRID_ID"] + names + [n + "_txt" for n in names]) as cur:
        for row in cur:
            o = oid_by_grid.get(row[0])
            bins = [lookups[n].get(o) for n in names]
            missing += any(b is None for b in bins)
            cur.updateRow([row[0]] + bins + [GI_LABELS.get(b, "") for b in bins])
    keep_only(out, ["GRID_ID", "ass_ha", "dmg_ha", "dmg_pct", "new_ha", "early_pct"]
              + names + [n + "_txt" for n in names])
    n = int(arcpy.management.GetCount(out)[0])
    print(f"  Hotspots_Hex: {n:,} hexagons" +
          (f"  (WARNING: {missing} without a hotspot value)" if missing else ""))
    return out


def folder_mb(path):
    total = 0
    for root, _, files in os.walk(path):
        total += sum(os.path.getsize(os.path.join(root, f)) for f in files)
    return total / 1024 / 1024


def main():
    arcpy.env.overwriteOutput = True
    if arcpy.Exists(PUBLISH_GDB):
        arcpy.management.Delete(PUBLISH_GDB)
    arcpy.management.CreateFileGDB(os.path.dirname(PUBLISH_GDB), os.path.basename(PUBLISH_GDB))
    print(f"Publish geodatabase: {PUBLISH_GDB}\n")

    stats_layer(src("Stats_Gemeinden"), "Gemeinden_Waldschaden",
                ["fi_ha", "fi_ok_ha", "fi_early_ha", "ki_early_ha", "kal_ha"])
    stats_layer(src("Stats_Kreise"), "Kreise_Waldschaden", [])
    hotspot_layer()

    arcpy.management.Compact(PUBLISH_GDB)
    mb = folder_mb(PUBLISH_GDB)
    # hosted layers are usually somewhat larger than the file gdb -> range
    lo, hi = mb * 2.4 / 10, mb * 2 * 2.4 / 10
    print(f"\nSize of the publish gdb: {mb:.1f} MB")
    print(f"Estimated credit cost as hosted feature layers: "
          f"~{lo:.1f}-{hi:.1f} credits per month")
    print("Check the layers in ArcGIS Pro, then run 09b_publish.py.")


if __name__ == "__main__":
    main()
