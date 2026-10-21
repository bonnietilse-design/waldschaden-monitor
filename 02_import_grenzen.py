"""
02_import_grenzen.py
====================

Imports the NRW administrative boundaries (DVG, OpenGeodata.NRW) into the
input geodatabase:

    *gem*  ->  Gemeinden     (expected: 396)
    *krs*  ->  Kreise        (expected: 53 Kreise and kreisfreie Staedte)
    *bld*  ->  NRW_Grenze    (expected: 1, also used for the cover image)

The layers are found by the abbreviation in the shapefile name, so the script
works for dvg1 (exact) and dvg2 (generalised) alike. It prints the fields of
each layer, so we can see which one holds the name and the official key
(AGS / GN / KN ...), and writes a short CSV report.
"""

import arcpy
import csv
import os
import re

# =============================================================================
# SETTINGS
# =============================================================================

DVG_DIR = r"D:\PORTFOLIO\DATA\dvg2_EPSG25832_Shape"          # unzipped download
OUT_GDB = r"D:\PORTFOLIO\DATA\Waldschaden_NRW_Input.gdb"
OVERWRITE = True

# abbreviation in the file name -> (layer name in the gdb, expected count)
LAYERS = {
    "gem": ("Gemeinden", 396),
    "krs": ("Kreise", 53),
    "bld": ("NRW_Grenze", 1),
}
EXPECTED_WKID = 25832


# =============================================================================
# MAIN
# =============================================================================

def find_shapefiles(folder):
    hits = []
    for root, _, files in os.walk(folder):
        hits += [os.path.join(root, f) for f in files if f.lower().endswith(".shp")]
    return sorted(hits)


def main():
    arcpy.env.overwriteOutput = OVERWRITE
    if not arcpy.Exists(OUT_GDB):
        arcpy.management.CreateFileGDB(os.path.dirname(OUT_GDB), os.path.basename(OUT_GDB))
        print(f"Created: {OUT_GDB}")

    shps = find_shapefiles(DVG_DIR)
    print(f"Shapefiles in {DVG_DIR}:")
    for s in shps:
        print(f"  {os.path.basename(s)}")

    report = []
    for key, (name, expected) in LAYERS.items():
        # 1st choice: exactly dvgNkey_nw.shp (e.g. dvg2gem_nw.shp)
        matches = [s for s in shps
                   if re.fullmatch(rf"dvg\d{key}_nw\.shp", os.path.basename(s).lower())]
        if not matches:
            matches = [s for s in shps if key in os.path.basename(s).lower()]
        print(f"\n{name}  (candidates: {[os.path.basename(m) for m in matches]})")
        # still ambiguous: take the file with the expected number of features
        if len(matches) > 1:
            by_count = [m for m in matches
                        if int(arcpy.management.GetCount(m)[0]) == expected]
            if len(by_count) == 1:
                print(f"  several files - using {os.path.basename(by_count[0])} "
                      f"({expected} features)")
                matches = by_count
        if len(matches) != 1:
            print(f"  SKIPPED: expected 1 file, found {len(matches)}: "
                  f"{[os.path.basename(m) for m in matches]}")
            report.append([name, "", "", f"{len(matches)} files found", ""])
            continue

        src = matches[0]
        sr = arcpy.Describe(src).spatialReference
        if sr is None or sr.factoryCode != EXPECTED_WKID:
            print(f"  WARNING: coordinate system {getattr(sr, 'name', 'unknown')}")

        out_fc = os.path.join(OUT_GDB, name)
        arcpy.conversion.ExportFeatures(src, out_fc)
        count = int(arcpy.management.GetCount(out_fc)[0])
        check = "OK" if count == expected else f"expected {expected}"
        fields = [f.name for f in arcpy.ListFields(out_fc)
                  if f.type not in ("OID", "Geometry")
                  and f.name not in ("Shape_Length", "Shape_Area")]
        print(f"  {os.path.basename(src)} -> {name}: {count} features ({check})")
        print(f"  fields: {', '.join(fields)}")

        # first two rows, so we can see which field holds names and keys
        with arcpy.da.SearchCursor(out_fc, fields) as cur:
            for i, row in enumerate(cur):
                if i == 2:
                    break
                print("   ", dict(zip(fields, row)))

        report.append([name, os.path.basename(src), count, check, "; ".join(fields)])

    csv_path = os.path.join(os.path.dirname(OUT_GDB), "import_report_grenzen.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Layer", "Quelle", "Features", "Check", "Felder"])
        w.writerows(report)
    print(f"\nReport: {csv_path}\nGeodatabase: {OUT_GDB}")


if __name__ == "__main__":
    main()
