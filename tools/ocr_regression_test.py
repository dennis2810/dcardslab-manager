"""
OCR-Regressionstest fuer DCardLabs.

Prueft ocr_name() aus app/dcardlabs_manager.py gegen einen selbst
gepflegten Bestand bekannter Kartenbilder (tests/ocr_corpus/) und meldet,
ob die Erkennung noch die erwarteten Namen liefert. Damit laesst sich vor
UND nach jeder Codeaenderung an der OCR-Logik pruefen, ob bisher gut
erkannte Karten weiterhin korrekt erkannt werden.

VERWENDUNG
----------
1. Kartenbilder (Vorderseite, wie sie ocr_name() normalerweise erhaelt)
   nach tests/ocr_corpus/ legen.
2. In tests/ocr_corpus/manifest.csv pro Karte eine Zeile eintragen:
       dateiname,erwarteter_name
3. Ausfuehren:
       python tools/ocr_regression_test.py
   (oder per Doppelklick: tools/run_ocr_regression.bat)

Exit-Code 0 = alle Karten korrekt erkannt, 1 = mindestens eine Abweichung.
"""

import csv
import sys
from pathlib import Path

import cv2

TOOLS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIR.parent
CORPUS_DIR = PROJECT_ROOT / "tests" / "ocr_corpus"
MANIFEST = CORPUS_DIR / "manifest.csv"

sys.path.insert(0, str(PROJECT_ROOT / "app"))
sys.path.insert(0, str(PROJECT_ROOT / "scanner"))
sys.path.insert(0, str(PROJECT_ROOT / "integrations"))

import dcardlabs_manager as dcl  # noqa: E402


def normalize(text):
    return " ".join((text or "").upper().split())


def load_manifest():
    rows = []
    if not MANIFEST.exists():
        print(f"Manifest nicht gefunden: {MANIFEST}")
        return rows
    with open(MANIFEST, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(
            (line for line in f if not line.startswith("#"))
        ):
            if row.get("dateiname") and row.get("erwarteter_name"):
                rows.append(row)
    return rows


def main():
    ok, status = dcl.ocr_setup_status()
    if not ok:
        print(f"OCR nicht einsatzbereit: {status}")
        return 1

    rows = load_manifest()
    if not rows:
        print(
            "Kein Testkorpus gefunden. Bitte Kartenbilder + Eintraege in\n"
            f"{MANIFEST} anlegen (siehe Kommentar am Dateianfang)."
        )
        return 1

    passed, failed = 0, 0
    print(f"{'Datei':<30} {'Erwartet':<25} {'Erkannt':<25} {'Konf.':>6}  Status")
    print("-" * 100)

    for row in rows:
        img_path = CORPUS_DIR / row["dateiname"]
        expected = row["erwarteter_name"]
        if not img_path.exists():
            print(f"{row['dateiname']:<30} FEHLT (Bilddatei nicht gefunden)")
            failed += 1
            continue

        card = cv2.imread(str(img_path))
        if card is None:
            print(f"{row['dateiname']:<30} FEHLER (Bild nicht lesbar)")
            failed += 1
            continue

        name, ocr_status, conf, _raw = dcl.ocr_name(card)
        match = normalize(name) == normalize(expected)
        passed += match
        failed += not match

        mark = "OK" if match else "ABWEICHUNG"
        print(
            f"{row['dateiname']:<30} {expected:<25} {name:<25} "
            f"{conf:>5.1f}%  {mark} ({ocr_status})"
        )

    total = passed + failed
    print("-" * 100)
    print(f"Ergebnis: {passed}/{total} korrekt erkannt.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
