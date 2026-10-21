"""
00a_download_vitalitaet.py - download OpenGeodata.NRW zips safely

For each file:
  1. download to <name>.zip.part (an interrupted download is resumed)
  2. compare the size with the size the server announces
  3. test every file in the zip (CRC)
  4. only then rename to .zip and unzip into a folder of the same name

Runs with the ArcGIS Pro Python environment (needs 'requests', which is included).
"""

import os
import zipfile

import requests

# ---- settings ---------------------------------------------------------------
BASE_URL = "https://www.opengeodata.nrw.de/produkte/umwelt_klima/wald_forst/fernerkundung/"
FILES = [
    "vitalitaetsabnahme_nadelwald_201706-202306_EPSG25832_Shape.zip",
    # add the others here if you want to re-download them too:
    # "vitalitaetsabnahme_nadelwald_201706-202309_EPSG25832_Shape.zip",
    # "vitalitaetsabnahme_nadelwald_201706-202409_EPSG25832_Shape.zip",
    # "vitalitaetsabnahme_nadelwald_201706-202508_EPSG25832_Shape.zip",
]
OUT_DIR = r"D:\PORTFOLIO\DATA"
UNZIP = True
CHUNK = 1024 * 1024  # 1 MB
# ------------------------------------------------------------------------------


def download(url, target):
    part = target + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    head = requests.head(url, allow_redirects=True, timeout=60)
    head.raise_for_status()
    total = int(head.headers.get("Content-Length", 0))

    if total and have == total:
        print("   already fully downloaded")
    else:
        headers = {"Range": f"bytes={have}-"} if have else {}
        with requests.get(url, headers=headers, stream=True, timeout=60) as r:
            r.raise_for_status()
            if have and r.status_code != 206:      # server ignored the resume
                have = 0
            mode = "ab" if have else "wb"
            done = have
            with open(part, mode) as f:
                for chunk in r.iter_content(CHUNK):
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        print(f"\r   {done / total:6.1%}  {done / 1e6:,.0f} / {total / 1e6:,.0f} MB",
                              end="", flush=True)
        print()

    size = os.path.getsize(part)
    if total and size != total:
        raise RuntimeError(f"size {size:,} bytes, server says {total:,} - run again to resume")

    with zipfile.ZipFile(part) as z:
        bad = z.testzip()
    if bad:
        os.remove(part)
        raise RuntimeError(f"damaged file inside the zip: {bad} - deleted, run again")

    if os.path.exists(target):
        os.remove(target)
    os.rename(part, target)
    print(f"   OK: {size / 1e6:,.1f} MB, zip test passed")


def unzip(zip_path):
    folder = zip_path[:-4]
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(folder)
    print(f"   unzipped to {folder}")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for name in FILES:
        print(f"\n{name}")
        target = os.path.join(OUT_DIR, name)
        try:
            download(BASE_URL + name, target)
            if UNZIP:
                unzip(target)
        except (requests.RequestException, RuntimeError, zipfile.BadZipFile) as e:
            print(f"   FAILED: {e}")


if __name__ == "__main__":
    main()
