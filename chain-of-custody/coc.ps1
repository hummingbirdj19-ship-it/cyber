$Image = if ($env:COC_IMAGE) { $env:COC_IMAGE } else { "chain-of-custody-sidecar:latest" }
docker run --rm -v "${PWD}:/work" -w /work $Image python -m coc $args
