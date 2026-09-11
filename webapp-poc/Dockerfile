# Build context must be the REPO ROOT (not this folder), because this PoC
# reuses scanner/ and integrations/ unchanged from the desktop app:
#
#   docker build -f webapp-poc/Dockerfile \
#     --build-arg GIT_COMMIT=$(git rev-parse --short HEAD) \
#     -t dcardslab-webapp-poc .
#   docker run -d --name dcardslab-webapp-poc -p 8000:8000 \
#     -e ANTHROPIC_API_KEY=dein-key \
#     dcardslab-webapp-poc
#
# Runs as its own container, separate from ebay-oauth-server - different
# dependencies (OpenCV, Anthropic, FastAPI), no shared code beyond this
# repo checkout.
FROM python:3.11-slim

# GIT_COMMIT baut den aktuell ausgecheckten Commit-Kurzhash in das Image ein
# (siehe --build-arg oben) - ohne das Argument bleibt es leer und main.py's
# GET /api/version zeigt "unbekannt". Damit laesst sich per einfachem
# HTTP-Request pruefen, ob ein erwarteter PR/Commit wirklich im laufenden
# Container steckt, ohne Shell-Zugriff auf den Deployment-Host zu brauchen -
# haeufige Verwirrung bisher: ein Rebuild ohne vorheriges "git pull" auf dem
# Deployment-Host baut unbemerkt den alten Code erneut.
ARG GIT_COMMIT=""
ENV GIT_COMMIT=$GIT_COMMIT

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

# Zeitstempel des Builds - braucht anders als GIT_COMMIT oben KEIN
# --build-arg (funktioniert also automatisch, egal ob per docker build,
# docker compose oder einer NAS-Docker-App mit eigenem "Neu bauen"-Knopf
# gestartet wird). Liegt bewusst NACH dem COPY webapp-poc/ oben: Docker
# laesst RUN date nur dann neu laufen, wenn sich am kopierten Code wirklich
# etwas geaendert hat - zeigt also "wann wurde dieser Code zuletzt
# tatsaechlich neu gebaut", nicht nur "wann lief der letzte Build-Befehl"
# (der bei unveraendertem Code aus dem Cache bedient wird und stehenbleibt).
RUN date -u +"%Y-%m-%d %H:%M UTC" > webapp-poc/BUILD_TIME

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "webapp-poc"]
