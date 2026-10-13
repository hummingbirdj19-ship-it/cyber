# Run the coc CLI inside the sidecar image, on the current folder (mounted at /work).
# python -P: import coc from the image (/app), never from a coc/ folder in the current directory.
$Image = if ($env:COC_IMAGE) { $env:COC_IMAGE } else { "ghcr.io/hummingbirdj19-ship-it/coc-sidecar:latest" }
docker run --rm --add-host "host.docker.internal:host-gateway" -v "${PWD}:/work" -w /work $Image python -P -m coc @args
exit $LASTEXITCODE
