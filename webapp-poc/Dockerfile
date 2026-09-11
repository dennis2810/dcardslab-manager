# Build context must be the REPO ROOT (not this folder), because this PoC
# reuses scanner/ and integrations/ unchanged from the desktop app:
#
#   docker build -f webapp-poc/Dockerfile -t dcardslab-webapp-poc .
#   docker run -d --name dcardslab-webapp-poc -p 8000:8000 \
#     -e ANTHROPIC_API_KEY=dein-key \
#     dcardslab-webapp-poc
#
# Runs as its own container, separate from ebay-oauth-server - different
# dependencies (OpenCV, Anthropic, FastAPI), no shared code beyond this
# repo checkout.

# Eigene Build-Stage nur zum Auslesen von Git-Metadaten (Commit-Kurzhash +
# letzte Commit-Betreffzeile, meist "Merge pull request #NN from ...") aus
# dem .git-Ordner im Build-Kontext - braucht dafuer git NUR waehrend des
# Builds, nicht im fertigen Image, und vor allem KEIN git auf dem
# Docker-Host selbst (der hat oft gar kein git installiert, siehe README.md)
# und KEIN --build-arg. Funktioniert damit automatisch bei jedem Build,
# egal ob per docker build, docker compose oder einer NAS-Docker-App mit
# eigenem "Neu bauen"-Knopf. Ergebnis landet in main.py's GET /api/version
# und den Einstellungen ("Version") - beantwortet "laeuft im Container
# wirklich der erwartete PR/Commit?" ohne Shell-Zugriff auf den
# Deployment-Host.
FROM python:3.11-slim AS git-info
WORKDIR /src
COPY .git .git
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && (git log -1 --pretty=%h > /GIT_COMMIT || echo unbekannt > /GIT_COMMIT) \
    && (git log -1 --pretty=%s > /LAST_COMMIT_SUBJECT || echo unbekannt > /LAST_COMMIT_SUBJECT)

FROM python:3.11-slim

WORKDIR /app
COPY webapp-poc/requirements.txt webapp-poc/requirements.txt
RUN pip install --no-cache-dir -r webapp-poc/requirements.txt

COPY scanner/ scanner/
COPY integrations/ integrations/
COPY webapp-poc/ webapp-poc/
# ebay_listing.py reads the eBay-provided CSV templates at runtime
# (Sport/Non-Sport required item specifics) - without this, every
# eBay-listing operation fails with FileNotFoundError inside the container.
COPY templates/ templates/

COPY --from=git-info /GIT_COMMIT webapp-poc/GIT_COMMIT
COPY --from=git-info /LAST_COMMIT_SUBJECT webapp-poc/LAST_COMMIT_SUBJECT

# Zeitstempel des Builds - liegt bewusst NACH dem COPY webapp-poc/ oben:
# Docker laesst RUN date nur dann neu laufen, wenn sich am kopierten Code
# wirklich etwas geaendert hat - zeigt also "wann wurde dieser Code zuletzt
# tatsaechlich neu gebaut", nicht nur "wann lief der letzte Build-Befehl"
# (der bei unveraendertem Code aus dem Cache bedient wird und stehenbleibt).
RUN date -u +"%Y-%m-%d %H:%M UTC" > webapp-poc/BUILD_TIME

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "webapp-poc"]
