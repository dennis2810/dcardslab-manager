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

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "webapp-poc"]
