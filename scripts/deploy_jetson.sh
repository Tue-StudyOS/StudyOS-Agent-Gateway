#!/usr/bin/env bash
set -euo pipefail

JETSON_HOST="${JETSON_HOST:-sebastian@jetson-thor.mvl1.uni-tuebingen.de}"
JETSON_SSH_KEY="${JETSON_SSH_KEY:-$HOME/.ssh/id_ed25519_studyos_jetson}"
REMOTE_RUNTIME_DIR="${REMOTE_RUNTIME_DIR:-/home/sebastian/studyos-agent-gateway}"
REMOTE_BUILD_DIR="${REMOTE_BUILD_DIR:-/home/sebastian/studyos-agent-gateway-build}"
IMAGE_TAG="${IMAGE_TAG:-studyos-agent-gateway:jetson-$(date -u +%Y%m%d%H%M%S)}"
CONTAINER_NAME="${CONTAINER_NAME:-studyos-agent-gateway}"

ssh_cmd=(ssh -i "$JETSON_SSH_KEY" -o BatchMode=yes)
rsync_cmd=(rsync -az --delete -e "ssh -i $JETSON_SSH_KEY -o BatchMode=yes")

"${ssh_cmd[@]}" "$JETSON_HOST" "mkdir -p '$REMOTE_BUILD_DIR'"

"${rsync_cmd[@]}" \
  --exclude .git/ \
  --exclude .venv/ \
  --exclude .env \
  --exclude .secrets/ \
  --exclude .learnings/ \
  --exclude .journal/ \
  --exclude __pycache__/ \
  --exclude .pytest_cache/ \
  --exclude .ruff_cache/ \
  --exclude .pyright/ \
  ./ "$JETSON_HOST:$REMOTE_BUILD_DIR/"

"${ssh_cmd[@]}" "$JETSON_HOST" \
  "REMOTE_RUNTIME_DIR='$REMOTE_RUNTIME_DIR' REMOTE_BUILD_DIR='$REMOTE_BUILD_DIR' \
IMAGE_TAG='$IMAGE_TAG' CONTAINER_NAME='$CONTAINER_NAME' bash -s" <<'REMOTE'
set -euo pipefail

cd "$REMOTE_BUILD_DIR"
docker build --network host -f Dockerfile.agent -t "$IMAGE_TAG" .

test -f "$REMOTE_RUNTIME_DIR/.env"

docker volume create studyos-agent-gateway_codex-auth >/dev/null
docker volume create studyos-agent-gateway_gh-auth >/dev/null
docker volume create studyos-agent-gateway_gh-public-auth >/dev/null
docker volume create studyos-agent-gateway_agent-workspaces >/dev/null
docker volume create studyos-agent-gateway_artifacts >/dev/null
docker volume create studyos-agent-gateway_discord-attachments >/dev/null

copy_container_dir_to_volume() {
  local source_path="$1"
  local volume_name="$2"
  local tmp_dir
  tmp_dir="$(mktemp -d)"
  if docker inspect "$CONTAINER_NAME" >/dev/null 2>&1 \
    && docker cp "$CONTAINER_NAME:$source_path" "$tmp_dir/source" >/dev/null 2>&1; then
    docker run --rm \
      --network none \
      -v "$tmp_dir/source:/source:ro" \
      -v "$volume_name:/target" \
      "$IMAGE_TAG" \
      sh -lc 'mkdir -p /target && cp -a /source/. /target/'
  fi
  rm -rf "$tmp_dir"
}

copy_container_dir_to_volume /tmp/studyos-artifacts studyos-agent-gateway_artifacts
copy_container_dir_to_volume /tmp/studyos-discord-attachments \
  studyos-agent-gateway_discord-attachments

docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
docker run -d \
  --name "$CONTAINER_NAME" \
  --network host \
  --restart unless-stopped \
  --env-file "$REMOTE_RUNTIME_DIR/.env" \
  -e CODEX_HOME=/auth/codex \
  -e GH_CONFIG_DIR=/auth/gh \
  -e GH_PUBLIC_CONFIG_DIR=/auth/gh-public \
  -v studyos-agent-gateway_codex-auth:/auth/codex \
  -v studyos-agent-gateway_gh-auth:/auth/gh \
  -v studyos-agent-gateway_gh-public-auth:/auth/gh-public \
  -v studyos-agent-gateway_agent-workspaces:/workspaces \
  -v studyos-agent-gateway_artifacts:/tmp/studyos-artifacts \
  -v studyos-agent-gateway_discord-attachments:/tmp/studyos-discord-attachments \
  "$IMAGE_TAG"

healthy=false
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8080/health; then
    healthy=true
    break
  fi
  sleep 1
done
if [ "$healthy" != true ]; then
  docker logs --tail 80 "$CONTAINER_NAME"
  exit 1
fi
echo
docker ps --filter "name=$CONTAINER_NAME" --format "{{.Names}} {{.Image}} {{.Status}}"
REMOTE
