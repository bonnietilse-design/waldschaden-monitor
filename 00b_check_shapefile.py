"""
00b_check_shapefile.py - is a downloaded shapefile complete?

Checks, without ArcGIS:
  1. the zip archive (size, CRC test of every file)
  2. the unzipped .shp, .shx and .dbf: does each file have the size its own
     header says it should have, and do the record counts match?

A truncated download or a broken unzip shows up as "TOO SHORT" or as
different record counts.
"""

import os
import struct
import zipfile

# ---- settings ---------------------------------------------------------------
ZIP_FILE = r"D:\PORTFOLIO\DATA\vitalitaetsabnahme_nadelwald_201706-202306_EPSG25832_Shape.zip"
FOLDER = r"D:\PORTFOLIO\DATA\vitalitaetsabnahme_nadelwald_201706-202306_EPSG25832_Shape"
# ------------------------------------------------------------------------------


def mb(n):
    return f"{n / 1024 ** 2:,.1f} MB"


def check_zip(path):
    print(f"\n== ZIP: {path}")
    if not os.path.exists(path):
        print("   not found (zip already deleted?) - skipping zip test")
        return
    print(f"   size on disk: {mb(os.path.getsize(path))}  (OpenGeodata.NRW lists 238.4 MB)")
    try:
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                print(f"   {info.filename:<70} {mb(info.file_size):>12}")
            bad = z.testzip()
            print("   CRC test: OK" if bad is None else f"   CRC test: DAMAGED -> {bad}")
    except zipfile.BadZipFile as e:
        print(f"   NOT A VALID ZIP: {e}  -> download again")


def check_shapefile(folder):
    print(f"\n== Folder: {folder}")
    shps = [os.path.join(r, f) for r, _, fs in os.walk(folder) for f in fs
            if f.lower().endswith(".shp")]
    if len(shps) != 1:
        print(f"   expected 1 .shp, found {len(shps)}: {shps}")
        if not shps:
            return
    base = os.path.splitext(shps[0])[0]
    files = {ext: base + ext for ext in (".shp", ".shx", ".dbf", ".prj")}
    for ext, p in files.items():
        print(f"   {ext}: {mb(os.path.getsize(p)) if os.path.exists(p) else 'MISSING'}")

    n_shx = n_dbf = None

    # .shp / .shx: file length in the header is stored in 16-bit words (big endian)
    for ext in (".shp", ".shx"):
        p = files[ext]
        if not os.path.exists(p):
            continue
        with open(p, "rb") as f:
            head = f.read(100)
        expected = struct.unpack(">i", head[24:28])[0] * 2
        actual = os.path.getsize(p)
        state = "OK" if expected == actual else ("TOO SHORT" if actual < expected else "too long")
        print(f"   {ext} header says {mb(expected)}, file is {mb(actual)} -> {state}")
        if ext == ".shx":
            n_shx = (actual - 100) // 8

    # .dbf: record count, header length and record length (little endian)
    p = files[".dbf"]
    if os.path.exists(p):
        with open(p, "rb") as f:
            head = f.read(32)
        n_dbf, hlen, rlen = struct.unpack("<IHH", head[4:12])
        expected = hlen + n_dbf * rlen
        actual = os.path.getsize(p)
        state = "OK" if actual in (expected, expected + 1) else (
            "TOO SHORT" if actual < expected else "too long")
        print(f"   .dbf: {n_dbf:,} records x {rlen} bytes -> expected {mb(expected)},"
              f" file is {mb(actual)} -> {state}")

    if n_shx is not None and n_dbf is not None:
        same = "OK" if n_shx == n_dbf else "MISMATCH"
        print(f"   records: .shx {n_shx:,} | .dbf {n_dbf:,} -> {same}")


check_zip(ZIP_FILE)
check_shapefile(FOLDER)
