# NextPush in one container: the Python program plus the official SFTPGo (checksum verified at build time).
#   docker build -t nextpush .
#   docker run -d --name nextpush -p 8095:8095 -p 8096:8096 -v nextpush-data:/data -v /srv/media:/media nextpush
# Look for the one-time setup code with:  docker logs nextpush
FROM python:3.13-slim
ARG SFTPGO_VERSION=2.7.6
ARG SUM_X86_64=71f5c9ae3284fc20bb606b2cf396db49645f9bdf4dba770cd76d6fd4720d5cb4
ARG SUM_ARM64=0d5c54a266515ae86a6a534e76c94807453353a1913ba3a26e81b9c20e548937
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends curl xz-utils ca-certificates && rm -rf /var/lib/apt/lists/*
RUN set -eux; \
    case "${TARGETARCH:-amd64}" in amd64) A=x86_64; SUM="$SUM_X86_64";; arm64) A=arm64; SUM="$SUM_ARM64";; *) echo "unsupported arch"; exit 1;; esac; \
    curl -fL --proto '=https' --tlsv1.2 -o /tmp/sftpgo.tar.xz "https://github.com/drakkan/sftpgo/releases/download/v${SFTPGO_VERSION}/sftpgo_v${SFTPGO_VERSION}_linux_${A}.tar.xz"; \
    echo "${SUM}  /tmp/sftpgo.tar.xz" | sha256sum -c -; \
    mkdir -p /app/bin /tmp/x && tar -xJf /tmp/sftpgo.tar.xz -C /tmp/x; \
    cp /tmp/x/sftpgo /app/bin/sftpgo; cp -r /tmp/x/templates /app/bin/templates; cp /tmp/x/LICENSE /app/bin/SFTPGO-LICENSE.txt; \
    rm -rf /tmp/x /tmp/sftpgo.tar.xz
COPY nextpush /app/nextpush
COPY LICENSE THIRD-PARTY-NOTICES.md /app/
WORKDIR /app
ENV NEXTPUSH_DATA_DIR=/data PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8095 8096
ENTRYPOINT ["python", "-m", "nextpush"]
