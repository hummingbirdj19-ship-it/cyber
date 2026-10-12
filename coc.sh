#!/usr/bin/env bash
set -e
IMAGE="${COC_IMAGE:-chain-of-custody-sidecar:latest}"
docker run --rm -v "$(pwd)":/work -w /work "$IMAGE" python -m coc "$@"
