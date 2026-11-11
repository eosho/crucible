#!/usr/bin/env bash
# Install reviewed public tool archives without accepting archive-defined links.
set -euo pipefail
trap 'printf "Error: Tool installation failed at line %s.\n" "$LINENO" >&2' ERR

usage() {
    cat <<'HELP'
Usage: install-tools.sh [OPTIONS]

Install checksum-pinned tools for the native Linux architecture.

Options:
  --manifest PATH    JSON lockfile (default: tools.lock.json beside this script)
  --prefix PATH      Installation root (default: /opt/crucible)
  --skip-installed   Keep tools already available on PATH; do not upgrade them
  -h, --help         Show this help
HELP
}

fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
usage_error() { printf 'Error: %s\n' "$*" >&2; usage >&2; exit 2; }

manifest="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/tools.lock.json"
prefix=/opt/crucible
skip_installed=false
while (($#)); do
    case $1 in
        --manifest|--prefix)
            [[ $# -ge 2 && -n $2 && $2 != -* ]] || usage_error "$1 requires a path"
            case $1 in
                --manifest) manifest=$2 ;;
                --prefix) prefix=$2 ;;
            esac
            shift 2 ;;
        -h|--help) usage; exit 0 ;;
        --skip-installed) skip_installed=true; shift ;;
        *) usage_error "Unknown argument: $1" ;;
    esac
done

for tool in jq curl sha256sum tar find mktemp; do
    command -v "$tool" >/dev/null || fail "Required tool is missing: $tool"
done
case $(uname -m) in
    x86_64) architecture=amd64 ;;
    aarch64) architecture=arm64 ;;
    *) fail "Unsupported image architecture: $(uname -m)" ;;
esac

temporary=$(mktemp -d)
trap 'find "$temporary" -depth -delete' EXIT
jq -er --arg arch "$architecture" '
    to_entries[] |
    [.key, .value.version, .value[$arch].url, .value[$arch].sha256] | @tsv
' "$manifest" > "$temporary/tools"
mkdir -p -- "$prefix/bin" "$prefix/tools"
prefix=$(cd -- "$prefix" && pwd -P)
export PATH="$prefix/bin:$PATH"

while IFS=$'\t' read -r name version url checksum; do
    [[ $name =~ ^[a-z][a-z0-9-]*$ && -n $version ]] || fail "Invalid tool name or version"
    [[ $checksum =~ ^[a-f0-9]{64}$ ]] || fail "$name: invalid SHA-256 pin"
    if [[ $skip_installed == true ]] && command -v "$name" >/dev/null; then
        printf 'Keeping installed tool: %s\n' "$name"
        continue
    fi
    destination="$prefix/tools/$name"
    [[ ! -e $destination && ! -L $destination ]] || fail "$name: installation directory already exists"
    [[ ! -e $prefix/bin/$name && ! -L $prefix/bin/$name ]] || fail "$name: executable already exists"
    printf 'Installing %s %s (%s)\n' "$name" "$version" "$architecture"

    archive="$temporary/archive.tar.gz"
    curl --fail --silent --show-error --location \
        --proto '=https' --proto-redir '=https' \
        --connect-timeout 30 --max-time 120 --output "$archive" "$url"
    printf '%s  %s\n' "$checksum" "$archive" | sha256sum --check --status ||
        fail "$name: downloaded archive failed SHA-256 verification"

    tar --list --gzip --file "$archive" --quoting-style=escape > "$temporary/members"
    while IFS= read -r member; do
        [[ -n $member && $member != /* && $member != *\\* ]] ||
            fail "$name: unsafe archive path"
        case "/$member/" in
            *"/../"*) fail "$name: unsafe archive path" ;;
        esac
    done < "$temporary/members"
    tar --list --verbose --gzip --file "$archive" > "$temporary/entries"
    while IFS= read -r entry; do
        case ${entry:0:1} in
            -|d) ;;
            *) fail "$name: archive must contain only regular files and directories" ;;
        esac
    done < "$temporary/entries"

    mkdir -- "$destination"
    tar --extract --gzip --file "$archive" --directory "$destination" \
        --no-same-owner --no-same-permissions
    find "$destination" -type f -name "$name" -print0 > "$temporary/executables"
    mapfile -d '' -t executables < "$temporary/executables"
    [[ ${#executables[@]} -eq 1 ]] || fail "$name: expected exactly one executable in archive"
    chmod 0755 -- "${executables[0]}"
    ln -s -- "${executables[0]}" "$prefix/bin/$name"
done < "$temporary/tools"
