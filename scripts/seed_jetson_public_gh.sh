#!/usr/bin/env bash
set -euo pipefail

JETSON_HOST="${JETSON_HOST:-sebastian@jetson-thor.mvl1.uni-tuebingen.de}"
JETSON_SSH_KEY="${JETSON_SSH_KEY:-$HOME/.ssh/id_ed25519_studyos_jetson}"
CONTAINER_NAME="${CONTAINER_NAME:-studyos-agent-gateway}"
IMAGE_TAG="${IMAGE_TAG:-}"

token_file="$(mktemp)"
trap 'rm -f "$token_file"' EXIT
cat >"$token_file"

if [[ ! -s "$token_file" ]]; then
  echo "Pass the public_repo classic token on stdin." >&2
  exit 2
fi

ssh_cmd=(ssh -i "$JETSON_SSH_KEY" -o BatchMode=yes)

if [[ -z "$IMAGE_TAG" ]]; then
  IMAGE_TAG="$("${ssh_cmd[@]}" "$JETSON_HOST" \
    "docker inspect '$CONTAINER_NAME' --format '{{.Config.Image}}'")"
fi

"${ssh_cmd[@]}" "$JETSON_HOST" \
  "docker volume create studyos-agent-gateway_gh-public-auth >/dev/null && \
docker run --rm -i \
  --network host \
  -e GH_CONFIG_DIR=/auth/gh-public \
  -v studyos-agent-gateway_gh-public-auth:/auth/gh-public \
  '$IMAGE_TAG' \
  sh -euc 'token=\$(cat); \
    user=\$(GH_TOKEN=\"\$token\" gh api user --jq .login); \
    umask 077; mkdir -p \"\$GH_CONFIG_DIR\"; \
    { printf \"github.com:\\n\"; \
      printf \"    git_protocol: https\\n\"; \
      printf \"    users:\\n\"; \
      printf \"        %s:\\n\" \"\$user\"; \
      printf \"            oauth_token: %s\\n\" \"\$token\"; \
      printf \"    user: %s\\n\" \"\$user\"; \
      printf \"    oauth_token: %s\\n\" \"\$token\"; \
    } >\"\$GH_CONFIG_DIR/hosts.yml\"; \
    chmod -R go-rwx \"\$GH_CONFIG_DIR\"; \
    gh api user --jq .login; \
    gh api repos/MertOkur/StudyOS_Agent --jq .full_name'" <"$token_file"
