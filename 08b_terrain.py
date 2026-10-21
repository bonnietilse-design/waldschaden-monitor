r"""
08b_terrain.py - do elevation and aspect play a role? (step 8b)
===============================================================

Input: the Copernicus DEM GLO-30 tiles covering NRW (GeoTIFF, 30 m), unzipped
into DEM_DIR (any file names; every *.tif in the folder is used).

1. Mosaic the tiles, project to EPSG:25832 (30 m)       RESULT_GDB\DEM_<AREA_TAG>
2. Derive classes from the DEM (30 m):
      elevation band (100 m steps)                       RESULT_GDB\Hoehenstufe_<AREA_TAG>
      aspect: 0 = flat (slope < FLAT_SLOPE_DEG),         RESULT_GDB\Exposition_<AREA_TAG>
              1 N, 2 NE, 3 E, 4 SE, 5 S, 6 SW, 7 W, 8 NW
3. Combine (10 m, snapped to the species raster) of
      elevation band x aspect x Schad_agg first release x Schad_agg latest release
      x species
4. Per elevation band and per aspect class:
      con_ha     assessed conifer forest (latest release)
      loss_pct   dead or cleared since 2017, % of con_ha
      new_pct    NEW damage first release -> latest release, % of the forest that
                 was NOT yet damaged in the first release (fair comparison:
                 areas with little forest left can't lose much more)
      spruce_ok  healthy spruce left (ha)
   Shares, not hectares: spruce is more common higher up, so hectares alone
   would just show where the spruce is.

Outputs:
    reports\08b_elevation_<TAG>.csv, 08b_aspect_<TAG>.csv, 08b_elev_x_aspect_<TAG>.csv
    reports\08b_elevation_<TAG>.png, 08b_aspect_<TAG>.png  (charts for the StoryMap)

Note: GLO-30 is a surface model (it includes the tree canopy). For 100 m
elevation bands and 30 m aspect that makes no practical difference.

Needs: step 6 (rasters Schad_*), Spatial Analyst.
Runtime (measured): test district 1.9 min incl. DEM preparation; NRW longer (DEM prepared once, then reused).
"""

import arcpy
import csv
import glob
import os
import time
from collections import defaultdict

import config as cfg

# =============================================================================
# SETTINGS
# =============================================================================

DEM_DIR = os.path.join(cfg.DATA_DIR, "rasters_COP30")   # unzipped Copernicus DEM (OpenTopography)
BAND_M = 100              # elevation band width (m)
FLAT_SLOPE_DEG = 3        # flatter than this: aspect is meaningless -> "flat"
MIN_BAND_HA = 500         # bands/classes with less conifer forest are marked unreliable
REBUILD_DEM = False       # True: rebuild DEM and class rasters even if they exist

HA = 10_000.0
CELL = 10
ASPECTS = {0: "flat", 1: "N", 2: "NE", 3: "E", 4: "SE", 5: "S", 6: "SW", 7: "W", 8: "NW"}


def out(name):
    return os.path.join(cfg.RESULT_GDB, f"{name}_{cfg.AREA_TAG}")


# ---- 1. DEM ------------------------------------------------------------------

def prepare_dem(extent):
    dem = out("DEM")
    if arcpy.Exists(dem) and not REBUILD_DEM:
        print(f"  {os.path.basename(dem)} exists - reused")
        return dem
    tiles = sorted(glob.glob(os.path.join(DEM_DIR, "**", "*.tif"), recursive=True))
    if not tiles:
        raise SystemExit(f"No .tif files in {DEM_DIR} - download the Copernicus GLO-30 tiles first")
    print(f"  {len(tiles)} DEM tiles found")
    t0 = time.time()
    mosaic = os.path.join(cfg.RESULT_GDB, "DEM_mosaic_tmp")
    if arcpy.Exists(mosaic):
        arcpy.management.Delete(mosaic)
    # coordinate system of the tiles; Copernicus GLO-30 is WGS 84 (EPSG:4326) - some
    # downloads don't carry it, so fall back to 4326
    sr = arcpy.Describe(tiles[0]).spatialReference
    if sr is None or sr.name in ("", "Unknown") or not sr.factoryCode:
        print("  tiles have no coordinate system - assuming WGS 84 (EPSG:4326)")
        sr = arcpy.SpatialReference(4326)
    arcpy.management.MosaicToNewRaster(tiles, cfg.RESULT_GDB, "DEM_mosaic_tmp", sr,
                                       "32_BIT_FLOAT", None, 1, "MEAN")
    arcpy.management.DefineProjection(mosaic, sr)
    proj = os.path.join(cfg.RESULT_GDB, "DEM_proj_tmp")
    arcpy.management.ProjectRaster(mosaic, proj, arcpy.SpatialReference(cfg.TARGET_WKID),
                                   "BILINEAR", 30)
    # clip to the study area (+ 1 km, so slopes at the edge are computed correctly)
    rect = f"{extent.XMin - 1000} {extent.YMin - 1000} {extent.XMax + 1000} {extent.YMax + 1000}"
    arcpy.management.Clip(proj, rect, dem, "", "", "NONE", "NO_MAINTAIN_EXTENT")
    for x in (mosaic, proj):
        arcpy.management.Delete(x)
    print(f"  -> {os.path.basename(dem)} ({time.time() - t0:.0f} s)")
    return dem


# ---- 2. classes ----------------------------------------------------------------

def terrain_classes(dem):
    band_ras, asp_ras = out("Hoehenstufe"), out("Exposition")
    if arcpy.Exists(band_ras) and arcpy.Exists(asp_ras) and not REBUILD_DEM:
        print("  elevation band and aspect rasters exist - reused")
        return band_ras, asp_ras
    t0 = time.time()
    d = arcpy.Raster(dem)
    # elevation band: 0 = 0-100 m, 1 = 100-200 m, ...
    arcpy.sa.Int(d / BAND_M).save(band_ras)
    slope = arcpy.sa.Slope(d, "DEGREE")
    asp = arcpy.sa.Aspect(d)
    sector = arcpy.sa.Int(arcpy.sa.Mod(asp + 22.5, 360) / 45) + 1      # 1 = N ... 8 = NW
    arcpy.sa.Con((slope < FLAT_SLOPE_DEG) | (asp < 0), 0, sector).save(asp_ras)
    for r in (band_ras, asp_ras):
        arcpy.management.BuildRasterAttributeTable(r, "Overwrite")
    print(f"  -> {os.path.basename(band_ras)}, {os.path.basename(asp_ras)} ({time.time() - t0:.0f} s)")
    return band_ras, asp_ras


# ---- 3. combine ------------------------------------------------------------------

def combine(rasters):
    comb = os.path.join(cfg.RESULT_GDB, f"Combine_tmp_{cfg.AREA_TAG}")
    arcpy.sa.Combine([arcpy.Raster(r) for r in rasters]).save(comb)
    if not arcpy.Raster(comb).hasRAT:
        arcpy.management.BuildRasterAttributeTable(comb)
    fields = [f.name for f in arcpy.ListFields(comb)
              if f.type != "OID" and f.name.upper() not in ("VALUE", "COUNT")][:len(rasters)]
    res = {}
    with arcpy.da.SearchCursor(comb, ["Count"] + fields) as cur:
        for count, *vals in cur:
            res[tuple(int(v) for v in vals)] = count * CELL * CELL / HA
    arcpy.management.Delete(comb)
    return res


# ---- 4. indicators ----------------------------------------------------------------

def summarise(table, key_index):
    """{class: indicators}; key_index 0 = elevation band, 1 = aspect, (0, 1) = both."""
    dmg, na = set(cfg.DAMAGE_CODES), set(cfg.NOT_ASSESSED_CODES)
    spruce = [v for v, n in cfg.SPECIES_LEGEND.items() if n == "Fichte"][0]
    acc = defaultdict(lambda: defaultdict(float))
    for (band, asp, first, last, sp), ha in table.items():
        if last in na:
            continue
        key = (band, asp) if key_index == (0, 1) else (band, asp)[key_index]
        a = acc[key]
        a["con_ha"] += ha
        if last in dmg:
            a["dmg_ha"] += ha
        if first not in dmg and first not in na:          # still standing in the first release
            a["base_ha"] += ha
            if last in dmg:
                a["new_ha"] += ha
        if sp == spruce and last == 0:
            a["spruce_ok"] += ha
    for a in acc.values():
        a["loss_pct"] = 100 * a["dmg_ha"] / a["con_ha"] if a["con_ha"] else None
        a["new_pct"] = 100 * a["new_ha"] / a["base_ha"] if a["base_ha"] else None
    return acc


def band_label(b):
    return f"{b * BAND_M}-{(b + 1) * BAND_M} m"


def write_csv(path, rows, header):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(header)
        w.writerows(rows)


def print_table(title, items, label):
    print(f"\n=== {title} ===")
    print(f"  {'class':<12}{'conifer ha':>12}{'loss since 2017':>18}{'new damage':>13}"
          f"{'healthy spruce ha':>19}")
    for k, a in items:
        flag = "  (few data)" if a["con_ha"] < MIN_BAND_HA else ""
        print(f"  {label(k):<12}{a['con_ha']:>12,.0f}{a['loss_pct'] or 0:>17.1f}%"
              f"{a['new_pct'] or 0:>12.2f}%{a['spruce_ok']:>19,.0f}{flag}")


def charts(elev, aspect, labels):
    """Two simple bar charts for the StoryMap (matplotlib ships with ArcGIS Pro)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  matplotlib not available - no charts")
        return
    copper, grey, ink = "#c0702a", "#9aa39b", "#2b2b2b"

    def two_panels(keys, data, xlabels, title, fname):
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.6), dpi=200)
        for ax, field, sub in ((a1, "loss_pct", "Dead or cleared since 2017 (%)"),
                               (a2, "new_pct", f"New damage {labels[0]} → {labels[1]} (%)")):
            vals = [data[k][field] or 0 for k in keys]
            cols = [copper if data[k]["con_ha"] >= MIN_BAND_HA else grey for k in keys]
            ax.bar(range(len(keys)), vals, color=cols, width=0.72)
            ax.set_xticks(range(len(keys)))
            ax.set_xticklabels(xlabels, rotation=45 if len(keys) > 6 else 0, ha="right" if len(keys) > 6 else "center",
                               fontsize=9, color=ink)
            ax.set_title(sub, fontsize=11, color=ink, loc="left")
            for s in ("top", "right"):
                ax.spines[s].set_visible(False)
            ax.tick_params(colors=ink, labelsize=9)
            ax.grid(axis="y", color="#e3e3e3", linewidth=0.8)
            ax.set_axisbelow(True)
        fig.suptitle(title, fontsize=13, color=ink, x=0.01, ha="left", fontweight="bold")
        fig.text(0.01, 0.005, f"Grey: less than {MIN_BAND_HA} ha of conifer forest. "
                 "Data: Wald und Holz NRW, Copernicus DEM GLO-30; own analysis.", fontsize=8, color="#666")
        fig.tight_layout(rect=(0, 0.03, 1, 0.94))
        path = os.path.join(cfg.REPORT_DIR, fname)
        fig.savefig(path, facecolor="white")
        plt.close(fig)
        print(f"  chart: {path}")

    bands = sorted(elev)
    two_panels(bands, elev, [band_label(b) for b in bands],
               "Conifer forest loss by elevation", f"08b_elevation_{cfg.AREA_TAG}.png")
    asp = [k for k in range(1, 9) if k in aspect] + ([0] if 0 in aspect else [])
    two_panels(asp, aspect, [ASPECTS[k] for k in asp],
               "Conifer forest loss by aspect", f"08b_aspect_{cfg.AREA_TAG}.png")


def main():
    arcpy.CheckOutExtension("Spatial")
    arcpy.env.overwriteOutput = True
    arcpy.env.parallelProcessingFactor = "100%"
    os.makedirs(cfg.REPORT_DIR, exist_ok=True)
    t0 = time.time()

    labels = list(cfg.RELEASES)
    first = out(f"Schad_{labels[0]}")
    last = out(f"Schad_{labels[-1]}")
    for r in (first, last):
        if not arcpy.Exists(r):
            raise SystemExit(f"{r} not found - run 06_change.py first (same RUN_MODE)")
    extent = arcpy.Describe(last).extent

    print("1. Elevation model")
    dem = prepare_dem(extent)
    print("2. Elevation bands and aspect")
    band_ras, asp_ras = terrain_classes(dem)

    print("3. Combine (10 m)")
    arcpy.env.snapRaster = cfg.SPECIES_RASTER
    arcpy.env.cellSize = CELL
    arcpy.env.extent = extent
    t1 = time.time()
    table = combine([band_ras, asp_ras, first, last, cfg.SPECIES_RASTER])
    print(f"  {len(table):,} combinations ({time.time() - t1:.0f} s)")

    elev = summarise(table, 0)
    aspect = summarise(table, 1)
    both = summarise(table, (0, 1))

    print_table("Elevation", sorted(elev.items()), band_label)
    print_table("Aspect", [(k, aspect[k]) for k in list(range(1, 9)) + [0] if k in aspect],
                lambda k: ASPECTS[k])

    head = ["Klasse", "Nadelwald_ha", "Verlust_seit_2017_%", f"neuer_Schaden_{labels[0]}_{labels[-1]}_%",
            "Fichte_gesund_ha", "wenig_Daten"]
    row = lambda lab, a: [lab, round(a["con_ha"], 1), round(a["loss_pct"] or 0, 2),
                          round(a["new_pct"] or 0, 3), round(a["spruce_ok"], 1),
                          int(a["con_ha"] < MIN_BAND_HA)]
    write_csv(os.path.join(cfg.REPORT_DIR, f"08b_elevation_{cfg.AREA_TAG}.csv"),
              [row(band_label(k), a) for k, a in sorted(elev.items())], head)
    write_csv(os.path.join(cfg.REPORT_DIR, f"08b_aspect_{cfg.AREA_TAG}.csv"),
              [row(ASPECTS[k], aspect[k]) for k in list(range(1, 9)) + [0] if k in aspect], head)
    write_csv(os.path.join(cfg.REPORT_DIR, f"08b_elev_x_aspect_{cfg.AREA_TAG}.csv"),
              [row(f"{band_label(b)} {ASPECTS[s]}", a) for (b, s), a in sorted(both.items())], head)

    print("\n4. Charts")
    charts(elev, aspect, (labels[0].replace("_", "/"), labels[-1].replace("_", "/")))
    print(f"\nReports in {cfg.REPORT_DIR}\nDone in {(time.time() - t0) / 60:.1f} min.")


if __name__ == "__main__":
    main()
