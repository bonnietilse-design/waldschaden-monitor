r"""
09b_publish.py - publish the lean layers to ArcGIS Online (step 9b)
===================================================================

Uploads Waldschaden_NRW_Publish.gdb (from 09a_prepare_publish.py) and publishes
it as ONE hosted feature layer with three sublayers:
    Gemeinden_Waldschaden, Kreise_Waldschaden, Hotspots_Hex

Uses the ArcGIS Online account you are signed in with in ArcGIS Pro
(GIS("pro")) - no password in the code.

Safe by default:
  - stops if an item with the same title already exists (OVERWRITE = False)
  - shares nothing publicly (SHARE_PUBLIC = False); switch it on when the
    StoryMap is ready, a public StoryMap needs public layers

Cost: storage only, ~1-2 credits per month for the ~4 MB of this data.
Close ArcGIS Pro's map on the publish gdb before running (lock files).
"""

import os
import time
import zipfile

from arcgis.gis import GIS

import config as cfg

# =============================================================================
# SETTINGS
# =============================================================================

PUBLISH_GDB = os.path.join(cfg.DATA_DIR, "Waldschaden_NRW_Publish.gdb")
TITLE = "Waldschaden NRW 2023-2025"
FOLDER = "Waldschaden-Monitor"
OVERWRITE = False      # True: delete MY existing items with this title first
SHARE_PUBLIC = False   # True: share the layer with everyone

TAGS = ["Waldschaden", "Borkenkäfer", "Fichte", "NRW", "Fernerkundung", "Hotspot",
        "Python", "ArcPy"]
SNIPPET = ("Vitalitätsabnahme im Nadelwald NRW (Stand 2023/09, 2024/09, 2025/08) "
           "je Gemeinde und Kreis, mit Hotspot-Analyse auf 1-km²-Hexagonen.")
DESCRIPTION = (
    "<p>Ausgewertet mit Python/ArcPy im Projekt <i>Vibe coding in ArcGIS: an NRW "
    "case study</i>.</p>"
    "<ul><li><b>Gemeinden_Waldschaden / Kreise_Waldschaden</b>: Nadelwaldfläche, "
    "geschädigte Fläche (stehendes Totholz + geräumte Kahlfläche) in ha und %, "
    "erste Symptome, neuer Schaden je Stand; Fichtenbestand aus der "
    "Baumartenklassifikation NRW.</li>"
    "<li><b>Hotspots_Hex</b>: Optimized Hot Spot Analysis (Getis-Ord Gi*) für "
    "Schadensanteil, neuen Schaden seit 2023 und erste Symptome "
    "(+3 = Hotspot 99 % … −3 = Coldspot 99 %).</li></ul>"
    "<p>Gemeinden mit weniger als 100 ha Nadelwald sind markiert "
    "(<code>wenig_nadelwald = 1</code>): dort sind Prozentwerte nicht aussagekräftig.</p>")
ACCESS_INFO = ("Datengrundlage: Landesbetrieb Wald und Holz NRW / OpenGeodata.NRW "
               "(Vitalitätsabnahme Nadelwald, Baumartenklassifikation NRW); "
               "Verwaltungsgrenzen: Geobasis NRW (DVG2). Lizenz: dl-de/zero-2.0.")
LICENSE = "Datenlizenz Deutschland – Zero – Version 2.0 (dl-de/zero-2.0)"


# =============================================================================
# MAIN
# =============================================================================

def zip_gdb(gdb):
    """Zip the gdb folder (without lock files) next to it."""
    zip_path = gdb[:-4] + ".zip"
    base = os.path.basename(gdb)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in os.listdir(gdb):
            if f.lower().endswith(".lock"):
                continue
            z.write(os.path.join(gdb, f), os.path.join(base, f))
    print(f"  zipped: {zip_path} ({os.path.getsize(zip_path) / 1024 / 1024:.1f} MB)")
    return zip_path


def existing_items(gis, me):
    q = f'title:"{TITLE}" AND owner:{me}'
    return [i for i in gis.content.search(q, max_items=50) if i.title == TITLE]


def upload(gis, zip_path, props):
    """Add the zip as a File Geodatabase item (works with old and new arcgis API)."""
    try:                                    # arcgis >= 2.3
        folders = gis.content.folders
        folder = folders.get(FOLDER) or folders.create(FOLDER)
        job = folder.add(item_properties=props, file=zip_path)
        return job.result() if hasattr(job, "result") else job
    except AttributeError:                  # older arcgis versions
        if FOLDER not in [f["title"] for f in gis.users.me.folders]:
            gis.content.create_folder(FOLDER)
        return gis.content.add(props, data=zip_path, folder=FOLDER)


def share_public(item):
    try:
        item.sharing.sharing_level = "EVERYONE"
    except AttributeError:
        item.share(everyone=True)


def main():
    if not os.path.isdir(PUBLISH_GDB):
        raise SystemExit(f"{PUBLISH_GDB} not found - run 09a_prepare_publish.py first")

    gis = GIS("pro")
    me = gis.users.me
    print(f"Signed in as {me.username} ({gis.properties.portalName})")

    old = existing_items(gis, me.username)
    if old:
        print(f"Existing items titled '{TITLE}': " +
              ", ".join(f"{i.type} ({i.id})" for i in old))
        if not OVERWRITE:
            raise SystemExit("Stopped. Set OVERWRITE = True to replace them.")
        for i in old:
            print(f"  deleting {i.type} {i.id}")
            i.delete()

    t0 = time.time()
    zip_path = zip_gdb(PUBLISH_GDB)
    props = {"title": TITLE, "type": "File Geodatabase", "tags": ",".join(TAGS),
             "snippet": SNIPPET}
    gdb_item = upload(gis, zip_path, props)
    print(f"  uploaded: {gdb_item.id}")

    layer = gdb_item.publish()
    layer.update(item_properties={"description": DESCRIPTION, "snippet": SNIPPET,
                                  "tags": ",".join(TAGS), "accessInformation": ACCESS_INFO,
                                  "licenseInfo": LICENSE})
    print(f"  published: {layer.title} ({layer.type})")
    for lyr in layer.layers:
        print(f"    - {lyr.properties.name}: {lyr.query(return_count_only=True)} features")

    if SHARE_PUBLIC:
        share_public(layer)
        print("  shared with everyone")
    else:
        print("  NOT shared publicly yet (SHARE_PUBLIC = False)")

    print(f"\nItem page: {layer.homepage}")
    print(f"Done in {(time.time() - t0) / 60:.1f} min.")


if __name__ == "__main__":
    main()
