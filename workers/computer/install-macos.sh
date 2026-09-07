#!/bin/bash
# Run inside the dedicated macOS guest, as its logged-in automation user.
set -euo pipefail
source_dir="${1:?directory containing aifactory-computer and desktop-native}"
dest="$HOME/.local/lib/aifactory-computer"
mkdir -p "$dest"
install -m 755 "$source_dir/aifactory-computer" "$source_dir/desktop-native" "$dest/"
echo 'Installed. Grant Screen Recording and Accessibility to the responsible app (often tart-guest-agent) if requested.'
echo 'Run a screenshot and an input test before saving this guest as the base image.'
