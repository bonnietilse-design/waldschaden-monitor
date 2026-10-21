r"""
04_clip.py - cut the data to the study area (step 4)
====================================================

For every release in config.RELEASES:
  1. intersect the vitality polygons with the Gemeinden of the study area
     (test: the Gemeinden of one Kreis, nrw: all 396)
  2. give every polygon its Gemeinde AND Kreis (GEM_KN, GEM_GN, KREIS_KN, KREIS_GN),
     so step 5 can summarise at both levels without another intersect
  3. keep only the fields we need (config.KEEP_FIELDS + the four area fields)
  4. write  RESULT_GDB\VA_<release>_<AREA_TAG>   e.g. VA_2025_08_KN05962

In test mode the species raster is also clipped to the test Kreis
(RESULT_GDB\Baumarten_<AREA_TAG>); in nrw mode the original raster is used.

Checks printed at the end:
  - hectares per Schad_agg class and release
  - total area per release: must be the same, because every release uses
    the same conifer mask (NRW-wide: 340,904 ha)

Runtime (measured): test Kreis ~10 min (each release is scanned NRW-wide),
all of NRW 2.5 h (17.3 million output polygons for three releases).
"""

import arcpy
import os
import time

import config as cfg

HA = 10_000.0


def key_where(fc, prefix):
    """WHERE clause for 'key starts with prefix' - works for text and numeric keys."""
    ftype = [f.type for f in arcpy.ListFields(fc, cfg.KEY_FIELD)][0]
    if ftype == "String":
        return f"{cfg.KEY_FIELD} LIKE '{prefix}%'"
    digits = 8 if fc == cfg.GEMEINDEN else 5
    lo = int(prefix.ljust(digits, "0"))
    hi = int(prefix.ljust(digits, "9"))
    return f"{cfg.KEY_FIELD} >= {lo} AND {cfg.KEY_FIELD} <= {hi}"


def area_layers():
    """Gemeinden layer of the study area + lookup Kreis key -> Kreis name."""
    where = key_where(cfg.GEMEINDEN, cfg.AREA_KREIS_KN) if cfg.AREA_KREIS_KN else None
    gem = arcpy.management.MakeFeatureLayer(cfg.GEMEINDEN, "gem_area", where)[0]
    n = int(arcpy.management.GetCount(gem)[0])
    if n == 0:
        raise SystemExit(f"No Gemeinde found for {where} - check TEST_KREIS_KN in config.py")

    with arcpy.da.SearchCursor(cfg.KREISE, [cfg.KEY_FIELD, cfg.NAME_FIELD]) as cur:
        # Kreis key may be stored with 5 or 8 digits ("05962" / "05962000")
        kreis_names = {}
        for k, name in cur:
            s = str(k).strip()
            s = s.zfill(5) if len(s) <= 5 else s.zfill(8)
            kreis_names[s[:5]] = name
    label = kreis_names.get(cfg.AREA_KREIS_KN, "") if cfg.AREA_KREIS_KN else "all of NRW"
    print(f"Study area ({cfg.RUN_MODE}): {label} - {n} Gemeinden")
    return gem, kreis_names


def add_area_fields(fc, kreis_names):
    """Copy the Gemeinde key/name into GEM_* and derive KREIS_* from the key."""
    for name, length in ((cfg.GEM_KEY, 8), (cfg.GEM_NAME, 80),
                         (cfg.KREIS_KEY, 5), (cfg.KREIS_NAME, 80)):
        arcpy.management.AddField(fc, name, "TEXT", field_length=length)
    fields = [cfg.KEY_FIELD, cfg.NAME_FIELD,
              cfg.GEM_KEY, cfg.GEM_NAME, cfg.KREIS_KEY, cfg.KREIS_NAME]
    with arcpy.da.UpdateCursor(fc, fields) as cur:
        for kn, gn, *_ in cur:
            gem_kn = str(kn).zfill(8)
            kreis_kn = gem_kn[:5]
            cur.updateRow([kn, gn, gem_kn, gn, kreis_kn, kreis_names.get(kreis_kn, "")])


def clip_release(layer, gem, kreis_names):
    src = os.path.join(cfg.RAW_GDB, layer)
    out = os.path.join(cfg.RESULT_GDB, f"{layer}_{cfg.AREA_TAG}")
    if not arcpy.Exists(src):
        print(f"  {layer}: NOT FOUND - skipped")
        return None

    t0 = time.time()
    # work in a temporary layer, so a crash never leaves a half result
    tmp = out + "_tmp"
    if arcpy.Exists(tmp):
        arcpy.management.Delete(tmp)
    arcpy.analysis.PairwiseIntersect([src, gem], tmp, "ALL")
    add_area_fields(tmp, kreis_names)

    # drop everything we don't need (much smaller and faster later on)
    keep = list(cfg.KEEP_FIELDS) + [cfg.GEM_KEY, cfg.GEM_NAME, cfg.KREIS_KEY, cfg.KREIS_NAME]
    existing = {f.name.lower(): f.name for f in arcpy.ListFields(tmp)}
    missing = [k for k in keep if k.lower() not in existing]
    if missing:
        print(f"  WARNING: fields not found and skipped: {missing}")
    keep = [existing[k.lower()] for k in keep if k.lower() in existing]
    arcpy.management.DeleteField(tmp, keep, "KEEP_FIELDS")

    if arcpy.Exists(out):
        arcpy.management.Delete(out)
    arcpy.management.Rename(tmp, out)
    try:
        arcpy.AlterAliasName(out, f"Vitalitaetsabnahme {layer[3:]} ({cfg.AREA_TAG})")
    except Exception:
        pass
    n = int(arcpy.management.GetCount(out)[0])
    print(f"  {layer} -> {os.path.basename(out)}: {n:,} polygons ({time.time() - t0:.0f} s)")
    return out


def clip_species():
    out = os.path.join(cfg.RESULT_GDB, f"Baumarten_{cfg.AREA_TAG}")
    if cfg.RUN_MODE != "test":
        print("  nrw mode: species raster used as is (already NRW-wide)")
        return
    t0 = time.time()
    where = key_where(cfg.KREISE, cfg.AREA_KREIS_KN)
    kreis = arcpy.management.MakeFeatureLayer(cfg.KREISE, "kreis_area", where)[0]
    e = arcpy.Describe(kreis).extent
    rect = f"{e.XMin} {e.YMin} {e.XMax} {e.YMax}"
    arcpy.management.Clip(cfg.SPECIES_RASTER, rect, out, kreis, "", "ClippingGeometry",
                          "NO_MAINTAIN_EXTENT")
    print(f"  Baumarten_NRW -> {os.path.basename(out)} ({time.time() - t0:.0f} s)")


def area_by_class(fc):
    out = r"memory\area_tmp"
    if arcpy.Exists(out):
        arcpy.management.Delete(out)
    arcpy.analysis.Statistics(fc, out, [["Shape_Area", "SUM"]], [cfg.DAMAGE_FIELD])
    with arcpy.da.SearchCursor(out, [cfg.DAMAGE_FIELD, "SUM_Shape_Area"]) as cur:
        res = {int(v): a / HA for v, a in cur}
    arcpy.management.Delete(out)
    return res


def check(outputs):
    print(f"\n=== Check: hectares per {cfg.DAMAGE_FIELD} class ===")
    tables = {lab: area_by_class(fc) for lab, fc in outputs.items()}
    codes = sorted({c for t in tables.values() for c in t})
    print("  code  " + "".join(f"{lab:>12}" for lab in tables))
    for c in codes:
        name = cfg.LABELS[cfg.DAMAGE_FIELD].get(c, "?")
        print(f"  {c:>4}  " + "".join(f"{t.get(c, 0):>12,.0f}" for t in tables.values())
              + f"   {name}")
    totals = {lab: sum(t.values()) for lab, t in tables.items()}
    print("  total " + "".join(f"{v:>12,.0f}" for v in totals.values()))
    damaged = {lab: sum(t.get(c, 0) for c in cfg.DAMAGE_CODES) for lab, t in tables.items()}
    print("  dmg.  " + "".join(f"{v:>12,.0f}" for v in damaged.values())
          + f"   codes {cfg.DAMAGE_CODES}")

    spread = max(totals.values()) - min(totals.values())
    ok = spread < 0.001 * max(totals.values())
    print(f"\n  same total area in all releases: {'OK' if ok else 'NO'}"
          f" (difference {spread:,.1f} ha)")


def main():
    arcpy.env.overwriteOutput = True
    arcpy.env.parallelProcessingFactor = "100%"
    if not arcpy.Exists(cfg.RESULT_GDB):
        arcpy.management.CreateFileGDB(os.path.dirname(cfg.RESULT_GDB),
                                       os.path.basename(cfg.RESULT_GDB))
        print(f"Created: {cfg.RESULT_GDB}")

    t0 = time.time()
    gem, kreis_names = area_layers()
    print("\nVitality releases:")
    outputs = {}
    for label, layer in cfg.RELEASES.items():
        out = clip_release(layer, gem, kreis_names)
        if out:
            outputs[label] = out
    print("\nSpecies raster:")
    clip_species()
    if outputs:
        check(outputs)
    print(f"\nDone in {(time.time() - t0) / 60:.1f} min. Results in {cfg.RESULT_GDB}")


if __name__ == "__main__":
    main()
