# Supabase Setup für DCardsLab WebApp

Einmalige manuelle Einrichtung (kostenloser Free-Tier reicht zum Start:
500 MB DB, 1 GB Storage).

1. Projekt auf https://supabase.com anlegen.
2. Im SQL Editor den Inhalt von `schema.sql` ausführen (legt `scan_batches`
   und `cards` an).
3. Unter Storage einen neuen Bucket `card-images` anlegen, **"Public
   bucket" AUSGESCHALTET lassen** (privat – das Backend erzeugt bei
   Bedarf signierte, zeitlich begrenzte URLs statt dauerhaft offener
   Links).
4. Unter Project Settings → API: `Project URL` und `service_role`
   Secret Key kopieren (NICHT den `anon`-Key – der Service-Role-Key hat
   vollen Server-Zugriff und gehört nur ins Backend-Environment, niemals
   in Frontend-Code).
5. Als Env-Variablen beim Deployment des `webapp-poc`-Containers setzen:
   `SUPABASE_URL` = Project URL, `SUPABASE_SERVICE_KEY` = service_role-Key.

Bekannte Free-Tier-Einschränkung: Projekte pausieren nach 1 Woche ohne
API-Zugriff (im Supabase-Dashboard mit einem Klick reaktivierbar).
