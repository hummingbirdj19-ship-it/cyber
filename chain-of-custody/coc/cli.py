"""Command line interface for Chain of Custody (coc)."""
import argparse
from pathlib import Path
import sys

from coc.check import run_check
from coc.init import run_init
from coc.trust import run_trust
from sidecar.heartbeat import resolve_witness_url
from verifier.__main__ import main as verifier_main


def _run_verify(config: Path, witness: str | None, node_id: str | None) -> int:
    """Run the verifier from the image; a localhost witness URL follows the same rule as the sidecar."""
    argv = ["--config", str(config)]
    if witness:
        witness, note = resolve_witness_url(witness)
        if note:
            sys.stderr.write(f"Note: {note}\n")  # stdout stays pure verdict JSON
        argv += ["--witness", witness]
    if node_id:
        argv += ["--node-id", node_id]
    return verifier_main(argv)


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

    # coc trust (witness laptop)
    trust_parser = subparsers.add_parser("trust", help="add a node's public key to the witness's witness_nodes.toml")
    trust_parser.add_argument("node_id", help="the node id, as in that node's [node] node_id")
    trust_parser.add_argument("public_key", help="the node's public key (64 hex characters, printed by coc init)")
    trust_parser.add_argument("--nodes-file", type=Path, default=Path("witness_nodes.toml"),
                              help="trusted-node file (default: witness_nodes.toml, as witness-compose.yml mounts it)")
    trust_parser.add_argument("--replace", action="store_true", help="replace a different key already trusted for this node")

    # coc verify
    verify_parser = subparsers.add_parser("verify", help="verify the log (same output and exit codes as python -m verifier)")
    verify_parser.add_argument("--config", type=Path, default=Path("config.toml"), help="profile configuration path")
    verify_parser.add_argument("--witness", default=None, help="witness service URL (for online checks)")
    verify_parser.add_argument("--node-id", default=None, help="node id to look up at the witness")

    args = parser.parse_args(argv)

    try:  # exit 2 = the tool itself failed, same meaning as the verifier
        if args.command == "init":
            return run_init(
                node_id=args.node_id,
                spool_path=args.spool,
                witness_url=args.witness_url,
                force=args.force,
                config_path=args.config,
            )
        if args.command == "check":
            return run_check(config_path=args.config)
        if args.command == "trust":
            return run_trust(args.node_id, args.public_key, args.nodes_file, replace=args.replace)
        if args.command == "verify":
            return _run_verify(args.config, args.witness, args.node_id)
    except Exception as exc:
        sys.stderr.write(f"Crash: {type(exc).__name__}: {exc}\n")
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
