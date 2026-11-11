#!/usr/bin/env bash
# Keep tunnel authentication user-owned; license acceptance requires an explicit opt-in.
set -euo pipefail

usage() {
    cat <<'HELP'
Usage: crucible-tunnel --name NAME [OPTIONS]

Start a foreground authenticated VS Code tunnel from inside a workspace.

Options:
  -n, --name NAME                  3-32 lowercase letters, digits, and hyphens
      --accept-server-license-terms
                                  Explicitly accept the VS Code Server license
  -h, --help                      Show this help without signing in
HELP
}

usage_error() { printf 'Error: %s\n' "$*" >&2; usage >&2; exit 2; }

name=
arguments=()
while (($#)); do
    case $1 in
        -n|--name)
            [[ $# -ge 2 && -n $2 && $2 != -* ]] || usage_error "$1 requires a name"
            name=$2; shift 2 ;;
        --name=*) name=${1#*=}; shift ;;
        --accept-server-license-terms) arguments+=(--accept-server-license-terms); shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage_error "Unknown argument: $1" ;;
    esac
done
[[ $name =~ ^[a-z][a-z0-9-]{1,30}[a-z0-9]$ ]] ||
    usage_error "--name must contain 3-32 lowercase letters, digits, and hyphens"
if ! command -v code >/dev/null; then
    printf 'Error: VS Code CLI is missing. Use the Crucible development image.\n' >&2
    exit 1
fi
printf '%s\n' \
    "Starting a foreground, authenticated VS Code tunnel." \
    "Complete sign-in and review the server license unless you explicitly accepted it." \
    "This requires customer-approved outbound access to Microsoft Dev Tunnels." \
    "Anonymous access is not enabled."
exec code tunnel --name "$name" "${arguments[@]}"
