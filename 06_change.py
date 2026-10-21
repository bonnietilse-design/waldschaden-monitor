r"""
06_change.py - when did the damage appear, and do early symptoms predict it? (step 6)
=====================================================================================

The polygons of the releases don't line up (each release is cut differently),
so the comparison is done on a common 10 m grid (snapped to the species raster):

  1. each release -> raster of Schad_agg       RESULT_GDB\Schad_<release>_<AREA_TAG>
  2. Combine of all releases -> one table with the area of every
     combination (e.g. "1 in 2023, 3 in 2025")
  3. Schadensjahr raster                        RESULT_GDB\Schadensjahr_<AREA_TAG>
        0 = not damaged (latest release)
        1 = damaged already in the first release (by 2023/09)
        2 = new damage by the 2nd release (2024/09)
        3 = new damage by the 3rd release (2025/08)
      NoData = outside the conifer mask or not assessed in the latest release
  4. Early warning test: of the area with early symptoms (Schad_agg 1) in the
     first release, how much was dead/cleared later - compared with areas that
     looked healthy (Schad_agg 0)?

Report: reports\06_change_<AREA_TAG>.csv
Runtime (measured): all of NRW 9.4 min, 2.8 min when the Schad_* rasters are reused.
Uses Spatial Analyst.
"""

import arcpy
import csv
import os
import time
from collections import defaultdict

import config as cfg

HA = 10_000.0
CELL = 10
REUSE_RASTERS = True   # skip step 1 if the Schad_* rasters already exist (False = rebuild)


def to_raster(label, layer):
    """Polygon layer of one release -> integer raster of Schad_agg."""
    fc = os.path.join(cfg.RESULT_GDB, f"{layer}_{cfg.AREA_TAG}")
    out = os.path.join(cfg.RESULT_GDB, f"Schad_{label}_{cfg.AREA_TAG}")
    if not arcpy.Exists(fc):
        raise SystemExit(f"{fc} not found - run 04_clip.py first (same RUN_MODE)")
    if REUSE_RASTERS and arcpy.Exists(out):
        print(f"  {os.path.basename(out)} exists - reused")
        return out
    t0 = time.time()
    tmp = r"memory\ras_tmp"
    arcpy.conversion.PolygonToRaster(fc, cfg.DAMAGE_FIELD, tmp, "CELL_CENTER", "", CELL)
    arcpy.sa.Int(arcpy.Raster(tmp)).save(out)
    arcpy.management.Delete(tmp)
    print(f"  {os.path.basename(fc)} -> {os.path.basename(out)} ({time.time() - t0:.0f} s)")
    return out


def combine(rasters):
    """{(code_release1, code_release2, ...): ha} from arcpy.sa.Combine."""
    t0 = time.time()
    # Combine returns a temporary raster: save it, so its table can be read
    comb = os.path.join(cfg.RESULT_GDB, f"Combine_tmp_{cfg.AREA_TAG}")
    arcpy.sa.Combine([arcpy.Raster(r) for r in rasters]).save(comb)
    if not arcpy.Raster(comb).hasRAT:
        arcpy.management.BuildRasterAttributeTable(comb)
    # Combine table: OID, Value, Count, then one field per input, in input order
    value_fields = [f.name for f in arcpy.ListFields(comb)
                    if f.type != "OID" and f.name.upper() not in ("VALUE", "COUNT")]
    value_fields = value_fields[:len(rasters)]
    table = {}
    with arcpy.da.SearchCursor(comb, ["Count"] + value_fields) as cur:
        for count, *codes in cur:
            table[tuple(int(c) for c in codes)] = count * CELL * CELL / HA
    arcpy.management.Delete(comb)
    print(f"  Combine: {len(table)} combinations ({time.time() - t0:.0f} s)")
    return table


def damage_year(rasters, labels):
    """Raster: number of the first release in which the cell counts as damaged."""
    rs = [arcpy.Raster(r) for r in rasters]

    def in_codes(r, codes):
        """1 where the cell has one of the codes, else 0 (InList would give NoData)."""
        out = r == codes[0]
        for c in codes[1:]:
            out = out | (r == c)
        return out

    dmg = [in_codes(r, cfg.DAMAGE_CODES) for r in rs]
    year = arcpy.sa.Con(dmg[-1] == 1, len(rs), 0)       # damaged at the end -> last release
    for i in range(len(rs) - 2, -1, -1):                 # walk back: earliest wins
        year = arcpy.sa.Con(dmg[i] == 1, i + 1, year)
    not_assessed = in_codes(rs[-1], cfg.NOT_ASSESSED_CODES)
    year = arcpy.sa.SetNull(not_assessed == 1, year)

    out = os.path.join(cfg.RESULT_GDB, f"Schadensjahr_{cfg.AREA_TAG}")
    year.save(out)
    arcpy.management.BuildRasterAttributeTable(out, "Overwrite")
    arcpy.management.AddField(out, "Klasse", "TEXT", field_length=60)
    names = {0: f"nicht geschaedigt ({labels[-1].replace('_', '/')})",
             1: f"geschaedigt bis {labels[0].replace('_', '/')}"}
    for i, lab in enumerate(labels[1:], start=2):
        names[i] = f"neu bis {lab.replace('_', '/')}"
    counts = {}
    with arcpy.da.UpdateCursor(out, ["Value", "Klasse", "Count"]) as cur:
        for v, _, c in cur:
            cur.updateRow([v, names.get(int(v), "?"), c])
            counts[int(v)] = c * CELL * CELL / HA
    print(f"  -> {os.path.basename(out)}: "
          + ", ".join(f"class {k} {v:,.0f} ha" for k, v in sorted(counts.items())))
    if not counts:
        print("  WARNING: Schadensjahr raster is EMPTY")
    return names


def main():
    arcpy.CheckOutExtension("Spatial")
    arcpy.env.overwriteOutput = True
    arcpy.env.snapRaster = cfg.SPECIES_RASTER
    arcpy.env.cellSize = CELL
    arcpy.env.parallelProcessingFactor = "100%"
    os.makedirs(cfg.REPORT_DIR, exist_ok=True)
    t0 = time.time()

    labels = list(cfg.RELEASES)
    first = os.path.join(cfg.RESULT_GDB, f"{cfg.RELEASES[labels[0]]}_{cfg.AREA_TAG}")
    if arcpy.Exists(first):
        arcpy.env.extent = arcpy.Describe(first).extent

    print("1. Releases -> rasters")
    rasters = [to_raster(lab, layer) for lab, layer in cfg.RELEASES.items()]

    print("\n2. Combine")
    table = combine(rasters)

    print("\n3. Schadensjahr raster")
    names = damage_year(rasters, labels)

    # ---- summaries from the Combine table ------------------------------------
    dmg = set(cfg.DAMAGE_CODES)
    na = set(cfg.NOT_ASSESSED_CODES)

    def first_damaged(codes):
        if codes[-1] in na:
            return None
        for i, c in enumerate(codes):
            if c in dmg:
                return i + 1
        return 0

    by_year = defaultdict(float)
    for codes, ha in table.items():
        y = first_damaged(codes)
        if y is not None:
            by_year[y] += ha
    total = sum(by_year.values())

    print("\n=== When did the damage appear? ===")
    for y in sorted(by_year):
        print(f"  {names[y]:<36}{by_year[y]:>12,.0f} ha {100 * by_year[y] / total:6.1f} %")

    # early warning: start state in the first release -> damaged in a later one?
    print(f"\n=== Early warning: state in {labels[0]} -> damaged later? ===")
    warn_rows = []
    for start, start_name in ((1, "erste Symptome"), (0, "gesund")):
        base = {k: v for k, v in table.items() if k[0] == start}
        base_ha = sum(base.values())
        line = f"  {start_name:<16}{base_ha:>10,.0f} ha"
        row = [start_name, round(base_ha, 1)]
        for i, lab in enumerate(labels[1:], start=1):
            later = sum(v for k, v in base.items() if k[i] in dmg)
            pct = 100 * later / base_ha if base_ha else 0
            line += f"  | damaged by {lab}: {later:>8,.0f} ha ({pct:4.1f} %)"
            row += [round(later, 1), round(pct, 2)]
        print(line)
        warn_rows.append(row)
    if warn_rows[1][-1]:
        ratio = warn_rows[0][-1] / warn_rows[1][-1]
        print(f"  -> areas with early symptoms were {ratio:.1f}x as likely to be "
              f"dead/cleared by {labels[-1]} as healthy-looking areas")

    path = os.path.join(cfg.REPORT_DIR, f"06_change_{cfg.AREA_TAG}.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Schadensjahr", "Klasse", "Hektar", "Anteil_%"])
        for y in sorted(by_year):
            w.writerow([y, names[y], round(by_year[y], 1), round(100 * by_year[y] / total, 2)])
        w.writerow([])
        head = ["Start " + labels[0], "Hektar"]
        for lab in labels[1:]:
            head += [f"geschaedigt bis {lab} (ha)", f"geschaedigt bis {lab} (%)"]
        w.writerow(head)
        w.writerows(warn_rows)
        w.writerow([])
        w.writerow(["Kombination " + " | ".join(labels), "Hektar"])
        for codes, ha in sorted(table.items(), key=lambda x: -x[1]):
            w.writerow([" | ".join(str(c) for c in codes), round(ha, 1)])
    print(f"\nReport: {path}\nDone in {(time.time() - t0) / 60:.1f} min.")


if __name__ == "__main__":
    main()
