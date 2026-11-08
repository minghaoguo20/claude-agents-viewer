"""Entry point: python -m viewer [--config PATH] [--port PORT]."""
from __future__ import annotations

import argparse
import errno
import logging
import sys
import dataclasses
from pathlib import Path

from .config import load_config
from .server import make_server

log = logging.getLogger("viewer")


def port_arg(text: str) -> int:
    """Argparse type: an integer TCP port in 1-65535."""
    try:
        port = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid port {text!r}: not an integer") from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"invalid port {port}: must be in 1-65535")
    return port


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="viewer", description="Claude Agents Viewer")
    parser.add_argument("--config", type=Path, default=None, help="path to config.ini")
    parser.add_argument("--port", type=port_arg, default=None,
                        help="listen port (overrides [server] port in config.ini)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        config = load_config(args.config)
    except ValueError as err:
        log.error("%s", err)
        return 2
    if args.port is not None:
        config = dataclasses.replace(config, port=args.port)
    try:
        server = make_server(config)
    except OSError as err:
        if err.errno == errno.EADDRINUSE:
            log.error("port %s is already in use; set [server] port in config.ini or pass --port",
                      config.port)
        else:
            log.error("cannot listen on %s:%s: %s", config.host, config.port, err.strerror or err)
        return 1
    host, port = server.server_address[:2]
    log.info("serving on http://%s:%s/ (Ctrl-C to stop)", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
