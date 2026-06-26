#!/usr/bin/env bash
# Mantiene vivo un túnel SSM saliente desde el contenedor de Railway al
# NLB privado del cluster ClickHouse mx-central-1. La app se conecta a
# https://localhost:8443 (cert del NLB → verify=false). Si la sesión SSM
# se cae, este loop la vuelve a levantar.
#
# Requiere en el entorno (Railway env vars):
#   AWS_ACCESS_KEY_ID
#   AWS_SECRET_ACCESS_KEY
#   AWS_DEFAULT_REGION=mx-central-1
set -u

NLB="cortex-clickhouse-prod-nlb-1c811a5fd4264cda.elb.mx-central-1.amazonaws.com"

while true; do
  WORKER=$(aws ec2 describe-instances --region mx-central-1 \
    --filters "Name=tag:Name,Values=cortex-peerdb-prod-mx-worker" "Name=instance-state-name,Values=running" \
    --query "Reservations[0].Instances[0].InstanceId" --output text 2>/dev/null)

  if [ -z "$WORKER" ] || [ "$WORKER" = "None" ]; then
    echo "[tunnel] no encontré worker; reintento en 10s…" >&2
    sleep 10
    continue
  fi

  echo "[tunnel] worker=$WORKER → abriendo port-forward localhost:8443"
  aws ssm start-session --region mx-central-1 --target "$WORKER" \
    --document-name AWS-StartPortForwardingSessionToRemoteHost \
    --parameters "{\"host\":[\"$NLB\"],\"portNumber\":[\"8443\"],\"localPortNumber\":[\"8443\"]}"

  echo "[tunnel] sesión cerrada, reintento en 5s…" >&2
  sleep 5
done
