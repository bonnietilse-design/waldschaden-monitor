"""
config.py - shared settings for all Waldschaden-Monitor scripts.

Change the study area, paths or definitions HERE, not in the scripts.
"""

import os

# =============================================================================
# PATHS
# =============================================================================

DATA_DIR = r"D:\PORTFOLIO\DATA"
RAW_GDB = os.path.join(DATA_DIR, "Waldschaden_NRW_Rohdaten.gdb")      # NRW-wide downloads
INPUT_GDB = os.path.join(DATA_DIR, "Waldschaden_NRW_Input.gdb")       # boundaries
RESULT_GDB = os.path.join(DATA_DIR, "Waldschaden_NRW_Ergebnisse.gdb")  # everything we compute
REPORT_DIR = os.path.join(DATA_DIR, "reports")                         # CSV reports

# =============================================================================
# STUDY AREA
# =============================================================================

# The analysis covers ALL of NRW. While developing a script, run it on one test
# Kreis first (seconds instead of hours), then switch to "nrw".
#   RUN_MODE = "test"  -> only the Kreis TEST_KREIS_KN
#   RUN_MODE = "nrw"   -> all 53 Kreise and kreisfreie Staedte
RUN_MODE = "test"
TEST_KREIS_KN = "05962"   # Maerkischer Kreis: much conifer forest, 15 Gemeinden

# used by the scripts: None = all of NRW
AREA_KREIS_KN = TEST_KREIS_KN if RUN_MODE == "test" else None
# suffix of every output layer, so test and NRW results never overwrite each other
AREA_TAG = f"KN{TEST_KREIS_KN}" if RUN_MODE == "test" else "NRW"

KREISE = os.path.join(INPUT_GDB, "Kreise")
GEMEINDEN = os.path.join(INPUT_GDB, "Gemeinden")
NRW_GRENZE = os.path.join(INPUT_GDB, "NRW_Grenze")
NAME_FIELD = "GN"   # name in the DVG boundary layers
KEY_FIELD = "KN"    # official key (Kreis 5 digits, Gemeinde 8 digits)

# =============================================================================
# DATA
# =============================================================================

# vitality releases used in the analysis, oldest first: label -> layer in RAW_GDB
RELEASES = {
    "2023_09": "VA_2023_09",
    "2024_09": "VA_2024_09",
    "2025_08": "VA_2025_08",
}

SPECIES_RASTER = os.path.join(RAW_GDB, "Baumarten_NRW")

# raster value -> species group. DERIVED FROM THE DATA (03b_species_check, 19.10.2026):
# value 1 lies 100 % inside the conifer mask, 95 % on cleared areas -> calamity area,
# values 2-10 = the provider's 9 classes in the order of the dataset description.
# TO CONFIRM by eye against the Geoportal.NRW legend before publishing.
SPECIES_LEGEND = {
    1: "Kalamitaetsflaeche",
    2: "Eiche", 3: "Buche", 4: "Buntlaubholz", 5: "Weichlaubholz", 6: "Pappel",
    7: "Kiefer", 8: "Laerche", 9: "Fichte", 10: "Douglasie",
}
CALAMITY_VALUE = 1
CONIFERS = ("Kiefer", "Laerche", "Fichte", "Douglasie")

# =============================================================================
# DEFINITIONS (from the provider's legend)
# =============================================================================

# Checked with 03_inspect (19.10.2026): Schad_agg is cumulative ("ever damaged"),
# Schadstufe is the current state - 23,000 ha with Schad_agg 2/3 show Schadstufe 0/1
# again (regrowth on clearings). Schad_agg 1 == Schadstufe 1 exactly, so one field
# serves for everything. Klasse is NOT used (up to 1/3 of the area cloud-masked).
DAMAGE_FIELD = "Schad_agg"     # aggregated, consistent over time
DAMAGE_CODES = (2, 3)          # 2 = standing dead wood, 3 = cleared
DEAD_CODE, CLEARED_CODE = 2, 3

EARLY_FIELD = "Schad_agg"
EARLY_CODES = (1,)             # suspicion to first visible symptoms

NOT_ASSESSED_CODES = (-1, -3)  # cloud mask / too narrow - excluded, but reported

# fields kept from the vitality layers (everything else is dropped in step 4)
KEEP_FIELDS = ("Schad_agg", "Datum_T1", "Datum_T2")

# area fields added to every polygon in step 4 (all text, keys with leading zero)
GEM_KEY, GEM_NAME = "GEM_KN", "GEM_GN"        # Gemeinde, 8 digits
KREIS_KEY, KREIS_NAME = "KREIS_KN", "KREIS_GN"  # Kreis, 5 digits

LABELS = {
    "Klasse": {-3: "NoData (zu schmal)", -1: "maskiert (Wolken)", 0: "keine Veraenderung",
               1: "Klasse 1", 2: "Klasse 2", 3: "Klasse 3 (Verdacht/erste Symptome)",
               4: "Klasse 4 (sichtbarer Schaden)", 5: "Klasse 5", 6: "Klasse 6",
               7: "Klasse 7 (Totholz/Kahlflaeche)"},
    "Schadstufe": {-3: "NoData (zu schmal)", -1: "maskiert (Wolken)",
                   0: "keine bis sehr geringe Verluste", 1: "Verdacht bis erste Symptome",
                   2: "stehendes Totholz", 3: "geraeumte Kahlflaeche"},
    "Schad_agg": {-3: "NoData (zu schmal)", -1: "maskiert (Wolken)",
                  0: "keine bis sehr geringe Verluste", 1: "Verdacht bis erste Symptome",
                  2: "stehendes Totholz (aggregiert)", 3: "geraeumte Kahlflaeche (aggregiert)"},
}

TARGET_WKID = 25832
