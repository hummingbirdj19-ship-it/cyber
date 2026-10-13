"""Witness FastAPI app: heartbeat and checkpoint intake, history queries, silence sweep."""
import argparse
import asyncio
import logging
import os
import sys
import threading
import time
import tomllib
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Query
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from witness import checks, store

logger = logging.getLogger("witness")
ALERT_THROTTLE_S = 10
SWEEP_EVERY_S = 2.0  # LATE is a few seconds' scale, so the sweep runs often
MAX_BODY_BYTES = 16 * 1024  # real heartbeats and checkpoints are under 1 KiB
# Defaults when no profile is given (witness laptop, I2); they match config.example.toml.
DEFAULT_INTERVAL_S = 30
DEFAULT_LATE_GRACE_S = 10
LATE_GRACE_ENV = "COC_LATE_GRACE_S"
DEFAULT_NODES_FILE = "data/witness_nodes.toml"
DEFAULT_DB_PATH = "data/witness.db"
DEFAULT_PORT = 8800


class BodyLimit:
    """ASGI middleware: 413 for a declared body over MAX_BODY_BYTES; stop reading a longer chunked one."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap the inner ASGI app."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Check Content-Length up front, then count the bytes actually received."""
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        length = dict(scope["headers"]).get(b"content-length", b"0")
        if not length.isdigit() or int(length) > MAX_BODY_BYTES:
            too_large = JSONResponse({"status": "invalid", "errors": ["body too large"]}, status_code=413)
            return await too_large(scope, receive, send)
        seen = 0

        async def limited() -> Message:
            """Pass body chunks through, stopping once the total is too large."""
            nonlocal seen
            msg = await receive()
            seen += len(msg.get("body", b""))
            if seen > MAX_BODY_BYTES:
                raise ValueError("request body too large")  # FastAPI turns this into a 400
            return msg

        await self.app(scope, limited, send)


def iso_utc(epoch: float) -> str:
    """Format an epoch time as YYYY-MM-DDTHH:MM:SSZ."""
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def witness_settings(
    config_path: Path | None = None,
    nodes_file: Path | None = None,
    interval_s: float | None = None,
    db_path: Path | None = None,
    port: int | None = None,
    late_grace_s: float | None = None,
) -> dict[str, Any]:
    """Resolve witness settings: explicit value, else the profile (if any), else the default."""
    config: dict[str, Any] = {}
    if config_path is not None:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
    hb, wit = config.get("heartbeat", {}), config.get("witness", {})
    return {
        "interval_s": float(interval_s if interval_s is not None else hb.get("interval_s", DEFAULT_INTERVAL_S)),
        "nodes_file": Path(nodes_file or wit.get("nodes_file", DEFAULT_NODES_FILE)),
        "db_path": Path(db_path or wit.get("db_path", DEFAULT_DB_PATH)),
        "port": int(port if port is not None else wit.get("port", DEFAULT_PORT)),
        "late_grace_s": _late_grace(late_grace_s, wit),
    }


def _late_grace(explicit: float | None, wit: dict[str, Any]) -> float:
    """LATE grace: the flag, else COC_LATE_GRACE_S (how witness-compose.yml sets it), else the profile, else 10."""
    if explicit is not None:
        return float(explicit)
    env = os.environ.get(LATE_GRACE_ENV, "").strip()
    if env:
        try:
            return float(env)
        except ValueError:
            raise ValueError(f"{LATE_GRACE_ENV} must be a number of seconds, got {env!r}") from None
    return float(wit.get("late_grace_s", DEFAULT_LATE_GRACE_S))


def create_app(
    config_path: Path | None = None,
    now: Callable[[], float] = time.time,
    nodes_file: Path | None = None,
    interval_s: float | None = None,
    db_path: Path | None = None,
    late_grace_s: float | None = None,
) -> FastAPI:
    """Build the witness app; `now` is the witness clock. Explicit arguments override the profile."""
    settings = witness_settings(config_path, nodes_file, interval_s, db_path, late_grace_s=late_grace_s)
    interval_s, late_grace_s = settings["interval_s"], settings["late_grace_s"]
    if interval_s <= 0:
        raise ValueError("interval_s must be greater than 0")
    if late_grace_s <= 0:
        raise ValueError("late_grace_s must be greater than 0")
    nodes = checks.load_nodes(settings["nodes_file"])  # refuses missing or folder
    conn = store.connect(settings["db_path"])
    lock = threading.Lock()
    started_at = now()

    def record(node_id: str, alerts: list[dict[str, str]], epoch: float) -> None:
        """Store alerts, skipping an identical one stored in the last few seconds (flood guard)."""
        # Untrusted node_ids share one bucket, so random ids cannot bypass the throttle and fill the store.
        bucket = node_id if node_id in nodes else checks.UNKNOWN_NODE
        for a in alerts:
            if not store.alert_recorded_since(conn, bucket, a["code"], a["detail"], epoch - ALERT_THROTTLE_S):
                store.insert_alert(conn, bucket, a["code"], a["detail"], iso_utc(epoch), epoch)
                if bucket != node_id:
                    logger.warning("%s from unknown node_id %r", a["code"], node_id)

    def sweep_silence() -> list[dict[str, str]]:
        """Raise LATE and SILENCE for every trusted node that has gone quiet; return what was raised."""
        epoch, raised = now(), []
        with lock, store.write_transaction(conn):
            for node_id in nodes:
                for alert in (checks.late_alert(conn, node_id, epoch, late_grace_s, started_at),
                              checks.silence_alert(conn, node_id, epoch, interval_s, started_at)):
                    if alert:
                        store.insert_alert(conn, node_id, alert["code"], alert["detail"], iso_utc(epoch), epoch)
                        raised.append({"node_id": node_id, **alert})
        return raised

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        """Run the periodic SILENCE sweep while the server is up."""
        async def loop() -> None:
            while True:
                await asyncio.sleep(SWEEP_EVERY_S)
                try:
                    await asyncio.to_thread(sweep_silence)
                except Exception:
                    logger.exception("silence sweep failed")
        task = asyncio.create_task(loop())
        try:
            yield
        finally:
            task.cancel()

    app = FastAPI(title="Chain of Custody witness", lifespan=lifespan)
    app.add_middleware(BodyLimit)
    app.state.conn, app.state.sweep_silence = conn, sweep_silence

    @app.post("/heartbeat")
    def post_heartbeat(body: Any = Body(...)) -> Any:
        """Accept a signed heartbeat; answer ok or the alerts it raised."""
        errors = checks.payload_errors(body, checks.HB_FIELDS, checks.HB_OPTIONAL)
        if errors:
            return JSONResponse({"status": "invalid", "errors": errors}, status_code=422)
        epoch = now()
        with lock, store.write_transaction(conn):
            if checks.signature_ok(nodes, body):
                alerts = checks.evaluate_heartbeat(conn, body)
                if not store.heartbeat_exists(conn, body["node_id"], body["hb_counter"], body["signature"]):
                    fresh = not any(a["code"] == "COUNTER_REPLAY" for a in alerts)
                    store.insert_heartbeat(conn, body, fresh, iso_utc(epoch), epoch)
            else:
                alerts = [checks.bad_signature_alert(nodes, body)]
            record(body["node_id"], alerts, epoch)
        return {"status": "alert", "alerts": alerts} if alerts else {"status": "ok"}

    @app.post("/checkpoint")
    def post_checkpoint(body: Any = Body(...)) -> Any:
        """Accept a signed checkpoint; reject it with 400 if the signature is not trusted."""
        errors = checks.payload_errors(body, checks.CP_FIELDS)
        if errors:
            return JSONResponse({"status": "invalid", "errors": errors}, status_code=422)
        epoch = now()
        with lock, store.write_transaction(conn):
            if not checks.signature_ok(nodes, body):
                alerts = [checks.bad_signature_alert(nodes, body)]
                record(body["node_id"], alerts, epoch)
                return JSONResponse({"status": "rejected", "alerts": alerts}, status_code=400)
            alerts, duplicate = checks.evaluate_checkpoint(conn, body)
            if not duplicate:
                store.insert_checkpoint(conn, body, iso_utc(epoch), epoch)
            record(body["node_id"], alerts, epoch)
        return {"status": "stored", "alerts": alerts} if alerts else {"status": "stored"}

    @app.get("/heartbeats")
    def get_heartbeats(node_id: str, limit: int | None = Query(None, ge=1, le=100000)) -> list[dict[str, Any]]:
        """List the newest stored heartbeats of a node, oldest first."""
        with lock:
            return store.list_rows(conn, "heartbeats", node_id, limit)

    @app.get("/checkpoints")
    def get_checkpoints(node_id: str, limit: int | None = Query(None, ge=1, le=100000)) -> list[dict[str, Any]]:
        """List the newest stored checkpoints of a node, oldest first."""
        with lock:
            return store.list_rows(conn, "checkpoints", node_id, limit)

    @app.get("/alerts")
    def get_alerts(
        node_id: str, code: str | None = None, limit: int | None = Query(None, ge=1, le=100000)
    ) -> list[dict[str, Any]]:
        """List the newest stored alerts of a node, optionally only one code."""
        with lock:
            return store.list_rows(conn, "alerts", node_id, limit, code)

    return app


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: serve the witness; flags override the profile, which is optional."""
    import uvicorn

    # Show the witness's own INFO lines (settings, silence sweep) next to uvicorn's.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="python -m witness.app", description=__doc__)
    parser.add_argument("--config", type=Path, default=None,
                        help="profile configuration path (default: config.toml if present, else built-in defaults)")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (use 0.0.0.0 in a container)")
    parser.add_argument("--nodes-file", type=Path, default=None, help="override [witness] nodes_file")
    parser.add_argument("--interval-s", type=float, default=None,
                        help="the sidecar's [heartbeat] interval_s; sets the SILENCE window (default 30)")
    parser.add_argument("--late-grace-s", type=float, default=None,
                        help="override [witness] late_grace_s: LATE once a promised heartbeat is this late (default 10)")
    parser.add_argument("--db-path", type=Path, default=None, help="override [witness] db_path")
    parser.add_argument("--port", type=int, default=None, help="override [witness] port (default 8800)")
    args = parser.parse_args(argv)
    config_path = args.config
    if config_path is None and Path("config.toml").is_file():
        config_path = Path("config.toml")
    try:
        if config_path is not None and not config_path.is_file():
            raise FileNotFoundError(f"config file '{config_path}' is missing")
        settings = witness_settings(config_path, args.nodes_file, args.interval_s, args.db_path, args.port,
                                    args.late_grace_s)
        app = create_app(config_path, nodes_file=args.nodes_file, interval_s=args.interval_s, db_path=args.db_path,
                         late_grace_s=args.late_grace_s)
    except (FileNotFoundError, ValueError) as exc:  # missing config, missing/folder/malformed nodes_file
        logger.error("%s", exc)
        sys.stderr.write(f"Error: {exc}\n")
        return 2
    logger.info("Witness: nodes_file=%s db=%s interval_s=%s (SILENCE after %ss without a heartbeat; "
                "LATE %ss after a promised one is due)", settings["nodes_file"], settings["db_path"],
                settings["interval_s"], 2 * settings["interval_s"] + checks.SILENCE_MARGIN_S, settings["late_grace_s"])
    uvicorn.run(app, host=args.host, port=settings["port"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
