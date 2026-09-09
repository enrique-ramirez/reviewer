#!/usr/bin/env bash
# Refreshes the review kit in this repository from the ai-engineering checkout.
#
# Set AI_ENGINEERING if that checkout is not a sibling of this one. A kit file you
# edited locally is moved to `<name>.local.bak` and reported rather than dropped.
set -euo pipefail
cd "$(dirname "$0")"
exec "${AI_ENGINEERING:-../ai-engineering}/bin/install.sh" . --mode copy --force
