FROM eclipse-temurin:25-jdk AS java25
FROM eclipse-temurin:17-jre AS java17
FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    bubblewrap ca-certificates curl git gosu libatomic1 libcurl4 libssl3 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=java25 /opt/java/openjdk /opt/java/openjdk
COPY --from=java17 /opt/java/openjdk /app/.local-tools/jdk17
ENV JAVA_HOME=/opt/java/openjdk PATH=/opt/java/openjdk/bin:$PATH \
    BETTERHOSTER_HOST=0.0.0.0 BETTERHOSTER_IN_DOCKER=1 \
    BETTERHOSTER_EDITOR_PORT_MIN=8100 BETTERHOSTER_EDITOR_PORT_MAX=8119 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN mkdir -p /app/.local-editor /app/servers \
    && curl -fsSL --retry 3 'https://github.com/gitpod-io/openvscode-server/releases/download/openvscode-server-v1.109.5/openvscode-server-v1.109.5-linux-x64.tar.gz' \
       | tar -xz -C /app/.local-editor \
    && useradd --create-home --uid 1000 --shell /bin/bash betterhoster \
    && chown -R betterhoster:betterhoster /app
COPY --chown=betterhoster:betterhoster *.py *.html host-status.css host-status.js schema.sql ./
COPY docker-entrypoint.sh /usr/local/bin/betterhoster-entrypoint
RUN chmod +x /usr/local/bin/betterhoster-entrypoint
EXPOSE 8000 8100-8119 25565-25575/tcp 19132-19142/udp
ENTRYPOINT ["/usr/local/bin/betterhoster-entrypoint"]
