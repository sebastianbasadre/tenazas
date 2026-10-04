# Instrumentation image only; never tag or replace the production image.
FROM tenazas-mcp-review:d5d1a45
USER root
RUN apt-get update && apt-get install -y --no-install-recommends strace \
    && rm -rf /var/lib/apt/lists/*
USER 1026:1026
