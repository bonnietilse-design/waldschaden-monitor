r"""
05_stats.py - damage statistics per Gemeinde and Kreis (step 5)
===============================================================

Reads the clipped layers from step 4 (RESULT_GDB\VA_<release>_<AREA_TAG>) and
computes, per Gemeinde and per Kreis and for every release:

    con_ha      conifer area (provider's mask, incl. not assessed)
    ass_ha      assessed area (without cloud mask / too narrow)
    dmg_ha      damaged area: Schad_agg 2 + 3 (dead wood + cleared, cumulative)
    dmg_pct     dmg_ha as % of ass_ha
    early_ha    early symptoms: Schad_agg 1
    new_ha      new damage since the previous release (dmg_ha - previous dmg_ha)

Outputs (all names end in _<AREA_TAG>):
    RESULT_GDB\Stats_Gemeinden_*   Gemeinde polygons with the numbers (for maps)
    RESULT_GDB\Stats_Kreise_*      Kreis polygons with the numbers
    reports\05_stats_*.csv         long table: one row per area and release (for charts)

Runtime (measured): test Kreis 0.4 min, all of NRW 1.2 min.
"""

import arcpy
import csv
import os
import time
from collections import defaultdict

import config as cfg

HA = 10_000.0


def area_key(kn, digits):
    """Normalise a DVG key: Kreis = first 5 digits, Gemeinde = 8 digits.

    DVG layers may store the Kreis key with 5 digits ("05962") or padded to
    8 ("05962000"), as text or as a number (leading zero lost)."""
    s = str(kn).strip()
    if s.endswith(".0"):
        s = s[:-2]
    if s.isdigit():
        s = s.zfill(5) if len(s) <= 5 else s.zfill(8)
    return s[:digits]


def collect(fc):
    """{gem_kn: {code: ha}} for one release."""
    out = r"memory\stats_tmp"
    if arcpy.Exists(out):
        arcpy.management.Delete(out)
    arcpy.analysis.Statistics(fc, out, [["Shape_Area", "SUM"]],
                              [cfg.GEM_KEY, cfg.DAMAGE_FIELD])
    res = defaultdict(dict)
    with arcpy.da.SearchCursor(out, [cfg.GEM_KEY, cfg.DAMAGE_FIELD, "SUM_Shape_Area"]) as cur:
        for kn, code, area in cur:
            res[kn][int(code)] = (area or 0) / HA
    arcpy.management.Delete(out)
    return res


def indicators(codes):
    """Turn {code: ha} into the indicators of one area and release."""
    con = sum(codes.values())
    na = sum(codes.get(c, 0) for c in cfg.NOT_ASSESSED_CODES)
    ass = con - na
    dmg = sum(codes.get(c, 0) for c in cfg.DAMAGE_CODES)
    early = sum(codes.get(c, 0) for c in cfg.EARLY_CODES)
    return {"con_ha": con, "ass_ha": ass, "na_ha": na, "dmg_ha": dmg,
            "dmg_pct": 100 * dmg / ass if ass else None, "early_ha": early}


def build(per_release, level):
    """{area_key: {release: indicators}} for level 'gem' or 'kreis'."""
    table = defaultdict(dict)
    for label, gem_data in per_release.items():
        sums = defaultdict(lambda: defaultdict(float))
        for gem_kn, codes in gem_data.items():
            key = gem_kn if level == "gem" else gem_kn[:5]
            for c, ha in codes.items():
                sums[key][c] += ha
        for key, codes in sums.items():
            table[key][label] = indicators(codes)
    # new damage since the previous release
    labels = list(per_release)
    for key, rel in table.items():
        for prev, cur in zip(labels, labels[1:]):
            if prev in rel and cur in rel:
                rel[cur]["new_ha"] = rel[cur]["dmg_ha"] - rel[prev]["dmg_ha"]
    return table


def write_fc(src, where, table, out_name, digits):
    """Copy the boundary polygons and add one set of fields per release."""
    out = os.path.join(cfg.RESULT_GDB, f"{out_name}_{cfg.AREA_TAG}")
    lyr = arcpy.management.MakeFeatureLayer(src, "tmp_area", where)[0]
    arcpy.conversion.ExportFeatures(lyr, out)
    arcpy.management.Delete(lyr)

    labels = list(cfg.RELEASES)
    latest = labels[-1]
    arcpy.management.AddField(out, "con_ha", "DOUBLE", field_alias="Nadelwald (ha)")
    names = ["con_ha"]
    spec = []                       # (field name, indicator, release)
    for lab in labels:
        for ind, alias in (("ass_ha", "bewertet (ha)"), ("dmg_ha", "Schaden (ha)"),
                           ("dmg_pct", "Schaden (%)"), ("early_ha", "erste Symptome (ha)"),
                           ("new_ha", "neuer Schaden (ha)")):
            if ind == "new_ha" and lab == labels[0]:
                continue
            fname = f"{ind}_{lab}"
            arcpy.management.AddField(out, fname, "DOUBLE",
                                      field_alias=f"{alias} {lab.replace('_', '/')}")
            names.append(fname)
            spec.append((fname, ind, lab))

    matched = 0
    sample = None
    with arcpy.da.UpdateCursor(out, [cfg.KEY_FIELD] + names) as cur:
        for row in cur:
            sample = sample or row[0]
            rel = table.get(area_key(row[0], digits), {})
            matched += bool(rel)
            if not rel:
                cur.updateRow([row[0]] + [None] * len(names))   # no conifer forest
                continue
            vals = [rel[latest]["con_ha"] if latest in rel else None]
            for _, ind, lab in spec:
                v = rel.get(lab, {}).get(ind)
                vals.append(round(v, 2) if v is not None else None)
            cur.updateRow([row[0]] + vals)
    n = int(arcpy.management.GetCount(out)[0])
    print(f"  {os.path.basename(out)}: {n} areas, {matched} with numbers")
    if matched == 0:
        print(f"  WARNING: no area matched - key in layer e.g. {sample!r}, "
              f"in table e.g. {list(table)[:3]}")
    return out


def write_csv(tables, names):
    path = os.path.join(cfg.REPORT_DIR, f"05_stats_{cfg.AREA_TAG}.csv")
    cols = ["con_ha", "ass_ha", "na_ha", "dmg_ha", "dmg_pct", "early_ha", "new_ha"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Ebene", "KN", "Name", "Stand"] + cols)
        for level, table in tables.items():
            for key in sorted(table):
                for lab, ind in table[key].items():
                    w.writerow([level, key, names[level].get(key, ""), lab] +
                               [round(ind[c], 2) if ind.get(c) is not None else "" for c in cols])
    print(f"  {path}")


def main():
    arcpy.env.overwriteOutput = True
    t0 = time.time()
    os.makedirs(cfg.REPORT_DIR, exist_ok=True)

    per_release = {}
    for label, layer in cfg.RELEASES.items():
        fc = os.path.join(cfg.RESULT_GDB, f"{layer}_{cfg.AREA_TAG}")
        if not arcpy.Exists(fc):
            raise SystemExit(f"{fc} not found - run 04_clip.py first (same RUN_MODE)")
        if cfg.GEM_KEY not in [f.name for f in arcpy.ListFields(fc)]:
            raise SystemExit(f"{fc} has no {cfg.GEM_KEY} - re-run the new 04_clip.py")
        per_release[label] = collect(fc)
        print(f"  read {os.path.basename(fc)}")

    gem = build(per_release, "gem")
    kreis = build(per_release, "kreis")

    names = {"gem": {}, "kreis": {}}
    with arcpy.da.SearchCursor(cfg.GEMEINDEN, [cfg.KEY_FIELD, cfg.NAME_FIELD]) as cur:
        names["gem"] = {area_key(k, 8): n for k, n in cur}
    with arcpy.da.SearchCursor(cfg.KREISE, [cfg.KEY_FIELD, cfg.NAME_FIELD]) as cur:
        names["kreis"] = {area_key(k, 5): n for k, n in cur}

    print("\nOutputs:")
    where = None
    if cfg.AREA_KREIS_KN:
        ftype = [f.type for f in arcpy.ListFields(cfg.GEMEINDEN, cfg.KEY_FIELD)][0]
        where = (f"{cfg.KEY_FIELD} LIKE '{cfg.AREA_KREIS_KN}%'" if ftype == "String"
                 else f"{cfg.KEY_FIELD} >= {int(cfg.AREA_KREIS_KN + '000')} AND "
                      f"{cfg.KEY_FIELD} <= {int(cfg.AREA_KREIS_KN + '999')}")
    write_fc(cfg.GEMEINDEN, where, gem, "Stats_Gemeinden", 8)
    where_k = None
    if cfg.AREA_KREIS_KN:
        ftype = [f.type for f in arcpy.ListFields(cfg.KREISE, cfg.KEY_FIELD)][0]
        where_k = (f"{cfg.KEY_FIELD} LIKE '{cfg.AREA_KREIS_KN}%'" if ftype == "String"
                   else f"{cfg.KEY_FIELD} = {int(cfg.AREA_KREIS_KN)}")
    write_fc(cfg.KREISE, where_k, kreis, "Stats_Kreise", 5)
    write_csv({"Gemeinde": gem, "Kreis": kreis},
              {"Gemeinde": names["gem"], "Kreis": names["kreis"]})

    # console summary, latest release
    latest = list(cfg.RELEASES)[-1]
    print(f"\n=== Gemeinden by damage share ({latest}) ===")
    print(f"  {'Gemeinde':<28}{'conifer ha':>11}{'damaged ha':>12}{'%':>7}"
          f"{'new ha':>9}{'early ha':>10}")
    rows = [(names["gem"].get(k, k), r[latest]) for k, r in gem.items() if latest in r]
    for name, ind in sorted(rows, key=lambda x: -(x[1]["dmg_pct"] or 0)):
        print(f"  {name[:27]:<28}{ind['con_ha']:>11,.0f}{ind['dmg_ha']:>12,.0f}"
              f"{ind['dmg_pct'] or 0:>7.1f}{ind.get('new_ha', 0):>9,.0f}{ind['early_ha']:>10,.0f}")
    tot = sum(i["dmg_ha"] for _, i in rows)
    print(f"  {'sum':<28}{sum(i['con_ha'] for _, i in rows):>11,.0f}{tot:>12,.0f}")
    print(f"\nDone in {(time.time() - t0) / 60:.1f} min.")


if __name__ == "__main__":
    main()
