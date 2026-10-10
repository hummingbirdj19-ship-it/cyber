"""Verifier CLI entry point: performs integrity checks and prints verdict JSON."""
import argparse
import json
import sys
import tomllib
from pathlib import Path

from verifier.checks import verify_offline


def main(argv: list[str] | None = None) -> int:
    """Run verifier CLI, print results as JSON, and exit with status code 0, 1, or 2."""
    parser = argparse.ArgumentParser(prog="python -m verifier", description="Verify log integrity.")
    parser.add_argument("--config", type=Path, default=Path("config.toml"), help="path to profile config file")
    parser.add_argument("--witness", type=str, default=None, help="witness service URL (for online checks)")
    args = parser.parse_args(argv)
    if args.witness:
        # Fail closed until D8: never print "clean" for checks that did not run.
        print("error: --witness checks (TRUNCATION, FORK, SILENCE) are not implemented yet (D8)", file=sys.stderr)
        return 2

    try:
        if not args.config.exists():
            print(f"error: config file not found: {args.config}", file=sys.stderr)
            return 2

        with open(args.config, "rb") as f:
            config = tomllib.load(f)

        db_path = Path(config["paths"]["db_path"])
        pub_path = Path(config["paths"]["public_key_path"])

        result = verify_offline(db_path, pub_path)
    except Exception as exc:
        print(f"error: verification failed: {exc}", file=sys.stderr)
        return 2

    # Print JSON output to stdout exactly as specified in CONTRACT.md section 6
    print(json.dumps(result, indent=2 if sys.stdout.isatty() else None))
    return 0 if result["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
