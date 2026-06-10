#!/usr/bin/env bash
set -euo pipefail

profile="${1:-}"
case "$profile" in
  public)
    prompt="Pass the public_repo classic token on stdin."
    volume_name="studyos-agent-gateway_gh-public-auth"
    auth_dir="/auth/gh-public"
    ;;
  studyos-org)
    prompt="Pass the Tue-StudyOS fine-grained token on stdin."
    volume_name="studyos-agent-gateway_gh-studyos-org-auth"
    auth_dir="/auth/gh-studyos-org"
    ;;
  *)
    echo "Usage: $0 public|studyos-org" >&2
    exit 2
    ;;
esac

JETSON_HOST="${JETSON_HOST:-sebastian@jetson-thor.mvl1.uni-tuebingen.de}"
JETSON_SSH_KEY="${JETSON_SSH_KEY:-$HOME/.ssh/id_ed25519_studyos_jetson}"
CONTAINER_NAME="${CONTAINER_NAME:-studyos-agent-gateway}"
IMAGE_TAG="${IMAGE_TAG:-}"

token_file="$(mktemp)"
trap 'rm -f "$token_file"' EXIT
cat >"$token_file"

if [[ ! -s "$token_file" ]]; then
  echo "$prompt" >&2
  exit 2
fi

ssh_cmd=(ssh -i "$JETSON_SSH_KEY" -o BatchMode=yes)

if [[ -z "$IMAGE_TAG" ]]; then
  IMAGE_TAG="$("${ssh_cmd[@]}" "$JETSON_HOST" \
    "docker inspect '$CONTAINER_NAME' --format '{{.Config.Image}}'")"
fi

"${ssh_cmd[@]}" "$JETSON_HOST" \
  "docker volume create '$volume_name' >/dev/null && \
docker run --rm -i \
  --network host \
  -e GH_CONFIG_DIR='$auth_dir' \
  -e STUDYOS_GH_PROFILE='$profile' \
  -v '$volume_name:$auth_dir' \
  '$IMAGE_TAG' \
  sh -euc 'token=\$(cat); \
    user=\$(GH_TOKEN=\"\$token\" gh api user --jq .login); \
    check_file=\$(mktemp); \
    case \"\$STUDYOS_GH_PROFILE\" in \
      public) \
        GH_TOKEN=\"\$token\" gh api repos/MertOkur/StudyOS_Agent --jq .full_name \
          >\"\$check_file\" ;; \
      studyos-org) \
        GH_TOKEN=\"\$token\" gh api orgs/Tue-StudyOS --jq .login >\"\$check_file\"; \
        GH_TOKEN=\"\$token\" gh api \"orgs/Tue-StudyOS/repos?per_page=1\" \
          --jq \".[0].full_name // \\\"no-repos\\\"\" >>\"\$check_file\" ;; \
    esac; \
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
    printf \"user=%s\\n\" \"\$user\"; \
    cat \"\$check_file\"'" <"$token_file"
