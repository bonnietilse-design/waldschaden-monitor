"""
01_import_vitalitaet.py
=======================

Imports the OpenGeodata.NRW downloads "Vitalitaetsabnahme Nadelwald" (shapefiles)
and the tree species classification (TIFF) into ONE file geodatabase and gives
every layer a name that shows its time span at a glance.

    vitalitaetsabnahme_nadelwald_201706-202306_...  ->  VA_2023_06
    vitalitaetsabnahme_nadelwald_201706-202309_...  ->  VA_2023_09
    vitalitaetsabnahme_nadelwald_201706-202409_...  ->  VA_2024_09
    vitalitaetsabnahme_nadelwald_201706-202508_...  ->  VA_2025_08
    vitalitaetsabnahme_nadelwald_202003-202304_...  ->  VA_Basis2020_03_2023_04
    baumartenklassifikation_nrw_EPSG25832_TIFF      ->  Baumarten_NRW

"VA" = Vitalitaetsabnahme. Layers without "Basis" compare against June 2017
(the common baseline); the 2020 product has a different baseline and is
marked as such. Each layer also gets an alias such as
"Vitalitaetsabnahme Nadelwald 06/2017 - 06/2023".

Optional extras (switch on/off below):
  - coded value domains, so Klasse, Schadstufe and Schad_agg show their
    meaning ("Stehendes Totholz") instead of numbers
  - time fields (Basis, Stand, Zeitraum) for a time slider later on
  - a CSV report with feature counts and field names

Run in the Python window of ArcGIS Pro or as a standalone script with the
ArcGIS Pro Python environment. Nothing in DATA_DIR is changed.
"""

import arcpy
import csv
import os
import re
import time

# =============================================================================
# SETTINGS
# =============================================================================

# Folder with the unzipped downloads
DATA_DIR = r"D:\PORTFOLIO\DATA"

# Target geodatabase (created if missing). On the external drive, because the
# NRW-wide data is several GB.
OUT_GDB = r"D:\PORTFOLIO\DATA\Waldschaden_NRW_Rohdaten.gdb"

OVERWRITE = False          # True = replace layers that already exist in the gdb
IMPORT_TREE_SPECIES = True # also copy the Baumarten TIFF into the gdb
ADD_DOMAINS = True         # readable labels for Klasse / Schadstufe / Schad_agg
ADD_TIME_FIELDS = False    # Basis/Stand/Zeitraum per feature (slow: millions of rows)

EXPECTED_WKID = 25832      # ETRS89 / UTM 32N

# Common baseline of the time series (June 2017)
MAIN_BASELINE = ("2017", "06")

# =============================================================================
# DOMAINS (from the provider's legend, 2025-10-06_Lieferung_Info.txt)
# =============================================================================

NODATA = {
    -3: "NoData (zu geringe Breite, nicht ausgewertet)",
    -1: "Datenmaskierung (Bewoelkung, Qualitaetsfilter)",
}

DOMAINS = {
    "Klasse": {
        **NODATA,
        0: "Keine negative Veraenderung der Vitalitaet",
        1: "Klasse 1 (kein bis geringer Vitalitaetsverlust)",
        2: "Klasse 2",
        3: "Klasse 3 (Verdacht bis erste sichtbare Symptome)",
        4: "Klasse 4 (sichtbarer Kaefer-/Trocknisschaden)",
        5: "Klasse 5",
        6: "Klasse 6",
        7: "Klasse 7 (z. B. Totholz, Kahlflaeche)",
    },
    "Schadstufe": {
        **NODATA,
        0: "Keine bis sehr geringe Vitalitaetsverluste",
        1: "Verdachtsmoment bis erste sichtbare Symptome",
        2: "Stehendes Totholz",
        3: "Geraeumte Kahlflaeche",
    },
    "Schad_agg": {
        **NODATA,
        0: "Keine bis sehr geringe Vitalitaetsverluste",
        1: "Verdachtsmoment bis erste sichtbare Symptome",
        2: "Stehendes Totholz (aggregiert)",
        3: "Geraeumte Kahlflaeche (aggregiert)",
    },
}

# arcpy field type -> domain field type
DOMAIN_TYPES = {"SmallInteger": "SHORT", "Integer": "LONG"}

PATTERN = re.compile(
    r"vitalitaetsabnahme_nadelwald_(\d{4})(\d{2})-(\d{4})(\d{2})", re.IGNORECASE
)


# =============================================================================
# HELPERS
# =============================================================================

def find_files(folder, *extensions):
    """All files with one of the given extensions in folder (also in sub-folders)."""
    hits = []
    for root, _, files in os.walk(folder):
        hits += [os.path.join(root, f) for f in files
                 if f.lower().endswith(extensions)]
    return sorted(hits)


def check_shapefile(shp):
    """Return a problem description, or None if the shapefile looks complete
    and ArcGIS can read it."""
    base = os.path.splitext(shp)[0]
    missing = [ext for ext in (".shx", ".dbf")
               if not os.path.exists(base + ext) and not os.path.exists(base + ext.upper())]
    if missing:
        return f"missing {', '.join(missing)} (incomplete unzip?)"
    try:
        desc = arcpy.Describe(shp)
        sr = getattr(desc, "spatialReference", None)
    except Exception as e:  # noqa: BLE001 - any read error means: skip
        return f"cannot be read by ArcGIS ({e})"
    if sr is None:
        return "cannot be read by ArcGIS (damaged or incomplete files - unzip again)"
    if not os.path.exists(base + ".prj"):
        print("  WARNING: no .prj file - coordinate system unknown")
    elif sr.factoryCode != EXPECTED_WKID:
        print(f"  WARNING: coordinate system is {sr.name} ({sr.factoryCode}),"
              f" expected EPSG:{EXPECTED_WKID}")
    return None


def layer_name(by, bm, ey, em):
    """Short, sortable name; the baseline appears only if it is not June 2017."""
    if (by, bm) == MAIN_BASELINE:
        return f"VA_{ey}_{em}"
    return f"VA_Basis{by}_{bm}_{ey}_{em}"


def layer_alias(by, bm, ey, em):
    return f"Vitalitaetsabnahme Nadelwald {bm}/{by} - {em}/{ey}"


def ensure_gdb(path):
    if not arcpy.Exists(path):
        arcpy.management.CreateFileGDB(os.path.dirname(path), os.path.basename(path))
        print(f"Created: {path}")


def field_map(fc):
    """{lowercase name: field} for case-insensitive look-ups."""
    return {f.name.lower(): f for f in arcpy.ListFields(fc)}


def ensure_domain(gdb, base_name, field_type, values, existing):
    """Create a coded value domain once per field type and return its name."""
    name = f"dom_{base_name}_{field_type}"
    if name not in existing:
        arcpy.management.CreateDomain(gdb, name, f"Legende {base_name}",
                                      field_type, "CODED")
        for code, label in sorted(values.items()):
            arcpy.management.AddCodedValueToDomain(gdb, name, code, label)
        existing.add(name)
        print(f"    domain {name} created")
    return name


def add_domains(gdb, fc, existing_domains):
    fields = field_map(fc)
    for base_name, values in DOMAINS.items():
        fld = fields.get(base_name.lower())
        if fld is None:
            print(f"    field {base_name} not found - no domain")
            continue
        ftype = DOMAIN_TYPES.get(fld.type)
        if ftype is None:
            print(f"    field {fld.name} is {fld.type} - no domain (needs integer)")
            continue
        dom = ensure_domain(gdb, base_name, ftype, values, existing_domains)
        arcpy.management.AssignDomainToField(fc, fld.name, dom)


def add_time_fields(fc, by, bm, ey, em):
    """Baseline and end date (1st of the month) plus a readable period."""
    arcpy.management.AddFields(fc, [
        ["Basis", "DATE", "Basis (Vergleichszeitpunkt)"],
        ["Stand", "DATE", "Stand (Aufnahmezeitpunkt)"],
        ["Zeitraum", "TEXT", "Zeitraum", 20],
    ])
    code = "import datetime"
    arcpy.management.CalculateField(
        fc, "Basis", f"datetime.datetime({int(by)}, {int(bm)}, 1)", "PYTHON3", code)
    arcpy.management.CalculateField(
        fc, "Stand", f"datetime.datetime({int(ey)}, {int(em)}, 1)", "PYTHON3", code)
    arcpy.management.CalculateField(
        fc, "Zeitraum", f"'{by}-{bm} bis {ey}-{em}'", "PYTHON3")


# =============================================================================
# MAIN
# =============================================================================

def main():
    arcpy.env.overwriteOutput = OVERWRITE
    ensure_gdb(OUT_GDB)
    existing_domains = {d.name for d in arcpy.da.ListDomains(OUT_GDB)}
    report = []

    # ---- vitality layers ----------------------------------------------------
    folders = sorted(d for d in os.listdir(DATA_DIR)
                     if PATTERN.search(d) and os.path.isdir(os.path.join(DATA_DIR, d)))
    if not folders:
        raise SystemExit(f"No 'vitalitaetsabnahme_nadelwald_*' folders in {DATA_DIR}")

    for folder in folders:
        by, bm, ey, em = PATTERN.search(folder).groups()
        name = layer_name(by, bm, ey, em)
        out_fc = os.path.join(OUT_GDB, name)
        print(f"\n{folder}\n  -> {name}")

        shps = find_files(os.path.join(DATA_DIR, folder), ".shp")
        if len(shps) != 1:
            print(f"  SKIPPED: expected 1 shapefile, found {len(shps)}: {shps}")
            report.append([name, folder, "", "", f"{len(shps)} shapefiles found", ""])
            continue
        shp = shps[0]

        problem = check_shapefile(shp)
        if problem:
            print(f"  SKIPPED: {problem}")
            report.append([name, folder, f"{by}-{bm}", f"{ey}-{em}",
                           f"SKIPPED: {problem}", "", ""])
            continue

        newly_imported = False
        if arcpy.Exists(out_fc) and not OVERWRITE:
            print("  exists - not imported again (set OVERWRITE = True to replace)")
            status = "already in gdb"
        else:
            # write under a temporary name first; only a complete copy gets the
            # real name, so an interrupted run never leaves a half-finished layer
            tmp_fc = out_fc + "_tmp"
            t0 = time.time()
            try:
                if arcpy.Exists(tmp_fc):
                    arcpy.management.Delete(tmp_fc)        # leftover from a broken run
                print("  copying into the gdb (can take 10-30 min for NRW) ...")
                arcpy.conversion.ExportFeatures(shp, tmp_fc)
                if arcpy.Exists(out_fc):
                    arcpy.management.Delete(out_fc)
                arcpy.management.Rename(tmp_fc, out_fc)
            except arcpy.ExecuteError:
                msg = arcpy.GetMessages(2).strip().splitlines()[-1]
                print(f"  FAILED: {msg}")
                if arcpy.Exists(tmp_fc):
                    arcpy.management.Delete(tmp_fc)
                report.append([name, folder, f"{by}-{bm}", f"{ey}-{em}",
                               f"FAILED: {msg}", "", ""])
                continue
            newly_imported = True
            status = f"imported in {(time.time() - t0) / 60:.1f} min"
            print(f"  {status}")

        # alias and domains: quick, and also applied to layers imported earlier
        try:
            arcpy.AlterAliasName(out_fc, layer_alias(by, bm, ey, em))
            if ADD_DOMAINS:
                add_domains(OUT_GDB, out_fc, existing_domains)
            if ADD_TIME_FIELDS and newly_imported:
                print("  adding time fields (this can take a while) ...")
                add_time_fields(out_fc, by, bm, ey, em)
        except (arcpy.ExecuteError, RuntimeError) as e:
            print(f"  WARNING: alias/domains not set: {e}")

        count = int(arcpy.management.GetCount(out_fc)[0])
        fields = [f.name for f in arcpy.ListFields(out_fc)
                  if f.type not in ("OID", "Geometry")]
        print(f"  {count:,} features | fields: {', '.join(fields)}")
        report.append([name, folder, f"{by}-{bm}", f"{ey}-{em}", status, count,
                       "; ".join(fields)])

    # ---- tree species raster ------------------------------------------------
    if IMPORT_TREE_SPECIES:
        tif_folders = [d for d in os.listdir(DATA_DIR)
                       if d.lower().startswith("baumartenklassifikation")]
        tifs = [t for d in tif_folders
                for t in find_files(os.path.join(DATA_DIR, d), ".tif", ".tiff")]
        out_ras = os.path.join(OUT_GDB, "Baumarten_NRW")
        if len(tifs) != 1:
            print(f"\nBaumarten: expected 1 TIFF, found {len(tifs)} - skipped")
            for t in tifs:
                print(f"  {t}")
        elif arcpy.Exists(out_ras) and not OVERWRITE:
            print("\nBaumarten_NRW exists - skipped")
        else:
            print(f"\n{os.path.basename(tifs[0])}\n  -> Baumarten_NRW")
            arcpy.management.CopyRaster(tifs[0], out_ras)
            print("  copied (check the class legend in the metadata on Geoportal.NRW)")
        if arcpy.Exists(out_ras):
            report.append(["Baumarten_NRW", ", ".join(tif_folders), "", "",
                           "raster", "", ""])

    # ---- report -------------------------------------------------------------
    csv_path = os.path.join(os.path.dirname(OUT_GDB), "import_report_vitalitaet.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Layer", "Quelle", "Basis", "Stand", "Status", "Features", "Felder"])
        w.writerows(report)
    print(f"\nReport: {csv_path}\nGeodatabase: {OUT_GDB}")


if __name__ == "__main__":
    main()
