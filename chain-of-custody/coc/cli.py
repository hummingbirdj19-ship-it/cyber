"""Command line interface for Chain of Custody (coc)."""
import argparse
from pathlib import Path
import sys

from coc.check import run_check
from coc.init import run_init


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments and dispatch to coc subcommands."""
    parser = argparse.ArgumentParser(prog="coc", description="Chain of Custody CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # coc init
    init_parser = subparsers.add_parser("init", help="initialize profile and node keys")
    init_parser.add_argument("--node-id", default=None, help="node identifier")
    init_parser.add_argument("--spool", default=None, help="spool file path")
    init_parser.add_argument("--witness-url", default=None, help="witness service URL")
    init_parser.add_argument("--force", action="store_true", help="force regeneration (fails if sidecar DB exists)")
    init_parser.add_argument("--config", type=Path, default=Path("config.toml"), help="profile configuration path")

    # coc check
    check_parser = subparsers.add_parser("check", help="check profile, paths, spool, and witness readiness")
    check_parser.add_argument("--config", type=Path, default=Path("config.toml"), help="profile configuration path")

    args = parser.parse_args(argv)

    if args.command == "init":
        return run_init(
            node_id=args.node_id,
            spool_path=args.spool,
            witness_url=args.witness_url,
            force=args.force,
            config_path=args.config,
        )
    elif args.command == "check":
        return run_check(config_path=args.config)

    return 2


if __name__ == "__main__":
    sys.exit(main())
