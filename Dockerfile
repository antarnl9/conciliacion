FROM python:3.11-slim

# Dependencias de sistema:
#   - awscli + session-manager-plugin → para abrir túnel SSM al cluster
#     ClickHouse privado en mx-central-1 (el cluster no es accesible por
#     internet; Railway no permite VPC peering con AWS).
#   - netcat-openbsd → el entrypoint hace nc -z para esperar a que el
#     túnel esté arriba antes de arrancar uvicorn.
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl unzip ca-certificates netcat-openbsd \
 && curl -s "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip \
 && unzip -q /tmp/awscliv2.zip -d /tmp \
 && /tmp/aws/install \
 && curl -s "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb" -o /tmp/smp.deb \
 && dpkg -i /tmp/smp.deb \
 && rm -rf /tmp/aws* /tmp/smp.deb /tmp/awscliv2.zip \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Scripts ejecutables del contenedor
RUN install -m 0755 start-tunnel.sh /usr/local/bin/start-tunnel.sh \
 && install -m 0755 entrypoint.sh /usr/local/bin/entrypoint.sh

# Credenciales de ClickHouse vía variables de entorno (CH_HOST, CH_USER, CH_PASSWORD, ...)
# Credenciales AWS para el túnel SSM (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION).
# DuckDB persiste en /app/data — montar un volumen ahí en el host (Railway/Render/Fly).
ENV PORT=8770
EXPOSE 8770
CMD ["/usr/local/bin/entrypoint.sh"]
