#!/usr/bin/env bash
# Run the coc CLI inside the sidecar image, on the current folder (mounted at /work).
# python -P: import coc from the image (/app), never from a coc/ folder in the current directory.
# --user keeps files it creates (config.toml, keys) owned by you, not root (RT-38, RT-47).
set -e
IMAGE="${COC_IMAGE:-chain-of-custody-sidecar:latest}"
exec docker run --rm --add-host host.docker.internal:host-gateway --user "$(id -u):$(id -g)" \
  -v "$(pwd)":/work -w /work "$IMAGE" python -P -m coc "$@"
