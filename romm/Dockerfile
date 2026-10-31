# RomM with the RomStop integration.
# Build from the repo root: docker build -f romm/Dockerfile -t romstop-romm romm
# It takes the official RomM image of the same version and swaps in the patched backend and a rebuilt frontend.
ARG ROMM_VERSION=5.4.1

FROM node:24-alpine AS source
ARG ROMM_VERSION
RUN apk add --no-cache git bash
WORKDIR /src
RUN git clone --depth 1 --branch "${ROMM_VERSION}" https://github.com/rommapp/romm.git romm
COPY overlay /romshelf/overlay
COPY romm.patch apply.sh ROMM_VERSION /romshelf/
RUN bash /romshelf/apply.sh /src/romm

FROM source AS frontend
WORKDIR /src/romm/frontend
RUN npm ci --ignore-scripts --no-audit --no-fund
# Typecheck too: vite build alone lets a missing import through to the browser.
RUN npm run typecheck && npm run build

FROM rommapp/romm:${ROMM_VERSION}
ARG WEBSERVER_FOLDER=/var/www/html
COPY --from=source /src/romm/backend/main.py /backend/main.py
COPY --from=source /src/romm/backend/endpoints/romshelf.py /backend/endpoints/romshelf.py
COPY --from=source /src/romm/backend/handler/romshelf /backend/handler/romshelf
# Copied over, not replacing the folder: the official image keeps emulators, platform icons and the resources link there too.
COPY --from=frontend /src/romm/frontend/dist ${WEBSERVER_FOLDER}
LABEL org.opencontainers.image.title="RomM + RomStop" \
      org.opencontainers.image.description="RomM with the RomStop romhack integration" \
      org.opencontainers.image.source="https://github.com/itsdinkd/RomStop"
