OCR-Testkorpus fuer DCardLabs
==============================

In diesen Ordner gehoeren Kartenbilder, deren korrekter Name du bereits
kennst (z.B. deine bisherigen 9 Beispielkarten). Sie dienen als
Sicherheitsnetz: jede kuenftige Aenderung an der OCR-Logik kann damit
sofort gegen bekannte, bereits gut funktionierende Faelle geprueft
werden.

VORGEHEN
--------
1. Bilddatei hier ablegen (z.B. karte_ronaldo.jpg) - am besten genau der
   Bildausschnitt, den ocr_name() normalerweise erhaelt.
2. In manifest.csv eine Zeile ergaenzen:
       karte_ronaldo.jpg,RONALDO
3. Test ausfuehren:
       tools\run_ocr_regression.bat
   Zeigt fuer jede Karte, ob der erwartete Name weiterhin erkannt wird.

Je mehr unterschiedliche Karten hier liegen (verschiedene Vorlagen,
Hersteller, Namensschild-Positionen), desto aussagekraeftiger wird der
Test - und desto sicherer koennen spaeter neue Regionsdefinitionen fuer
neue Kartentypen ergaenzt werden, ohne bestehende Erkennung zu
gefaehrden.
