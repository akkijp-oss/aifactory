#!/bin/bash
# Public entry point. Keep execution at EOF so a truncated download cannot install.
aifactory_install() {
  set +x
  set -euo pipefail
  umask 077
  : "${AIFACTORY_URL:?Set AIFACTORY_URL to the worker HTTPS endpoint}"
  : "${AIFACTORY_WORKER:?Set AIFACTORY_WORKER to an enrolled worker ID}"
  : "${AIFACTORY_TOKEN:?Set AIFACTORY_TOKEN to the dedicated worker token}"
  local system ref staging
  system=$(uname -s)
  case "$system" in
    Linux) [[ $EUID == 0 ]] || { echo 'Run bash as root (sudo env AIFACTORY_... bash).' >&2; return 1; } ;;
    Darwin) [[ $EUID != 0 && $(uname -m) == arm64 ]] || { echo 'Run as the logged-in Apple Silicon Mac user, without sudo.' >&2; return 1; } ;;
    *) echo 'Supported: systemd Linux or Apple Silicon macOS. Windows: workers/install.ps1.' >&2; return 1 ;;
  esac
  if ! command -v python3 >/dev/null; then
    [[ ${AIFACTORY_CHECK_ONLY:-0} != 1 ]] || { echo "CHECK_ONLY requires Python 3 already installed" >&2; return 1; }
    if [[ "$system" == Linux ]] && command -v apt-get >/dev/null; then
      env -u AIFACTORY_TOKEN apt-get update
      env -u AIFACTORY_TOKEN apt-get install -y python3 ca-certificates curl
    else
      echo 'Python 3 is required. On macOS install Homebrew and its python package first.' >&2; return 1
    fi
  fi
  ref=${AIFACTORY_REF:-main}
  [[ "$ref" =~ ^[a-zA-Z0-9][a-zA-Z0-9._/-]*$ && "$ref" != *..* ]] || { echo 'Invalid AIFACTORY_REF' >&2; return 1; }
  staging=$(mktemp -d "${TMPDIR:-/tmp}/aifactory-install.XXXXXXXX")
  trap "rm -rf -- $(printf '%q' "$staging")" EXIT
  env -u AIFACTORY_TOKEN curl --proto '=https' --tlsv1.2 -fsSL --retry 3 "https://codeload.github.com/akkijp-oss/aifactory/tar.gz/$ref" -o "$staging/source.tgz"
  tar -xzf "$staging/source.tgz" -C "$staging" --strip-components=1
  python3 "$staging/workers/bootstrap/install.py"
}
aifactory_install
