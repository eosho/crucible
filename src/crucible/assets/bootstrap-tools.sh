#!/usr/bin/env bash
# Add user-space tools without changing repository contents or replacing installed tools.
set -euo pipefail

usage() {
    printf 'Usage: bootstrap-tools.sh [--prefix PATH] [--help]\n'
}

prefix=/workspaces/.crucible
while (($#)); do
    case $1 in
        --prefix)
            [[ $# -ge 2 && -n $2 && $2 != -* ]] || { usage >&2; exit 2; }
            prefix=$2; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Error: Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

bundle=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
bash "$bundle/install-tools.sh" --manifest "$bundle/tools.lock.json" \
    --prefix "$prefix" --skip-installed
prefix=$(cd -- "$prefix" && pwd -P)
install -m 0755 "$bundle/connect-tunnel.sh" "$prefix/bin/crucible-tunnel"
printf 'export PATH=%q/bin:"$PATH"\n' "$prefix" > "$prefix/env.sh"
printf -v source_line '[ ! -f %q ] || . %q' "$prefix/env.sh" "$prefix/env.sh"
for profile in "$HOME/.profile" "$HOME/.bashrc"; do
    if [[ ! -f $profile ]] || ! grep -Fxq -- "$source_line" "$profile"; then
        printf '\n%s\n' "$source_line" >> "$profile"
    fi
done
printf 'Tools ready in %s/bin. Open a new shell or source %s/env.sh.\n' "$prefix" "$prefix"
