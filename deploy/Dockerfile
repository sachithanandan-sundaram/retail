# Retail LLM service for the Axelera Metis host.
#
# Follows Axelera's own pattern: DO NOT bake the ~12 GB SDK into the image.
# The SDK's axelera-env is bind-mounted from the host at run time (see
# docker-compose.yml) and this image only adds the app + the system tools
# axllm device discovery shells out to.
FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv \
        pciutils kmod \
        ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
# device discovery runs lspci / lsmod from /usr/sbin
ENV PATH="/usr/sbin:${PATH}"

WORKDIR /app
COPY backend /app/backend
COPY frontend/dist /app/frontend/dist
COPY retail.db /app/retail.db

# App deps are installed INTO the bind-mounted SDK venv at container start
# (entrypoint), because the process needs axelera.llm + these importable at
# once and the SDK venv isn't present at build time.
COPY deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENV RETAIL_LLM_BACKEND=axelera \
    VOYAGER_SDK_ROOT=/voyager-sdk \
    AXELERA_DEVICE=0 \
    RETAIL_DB_PATH=/app/retail.db \
    RETAIL_NOW=2026-08-27T20:00:00

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
