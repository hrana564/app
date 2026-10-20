"""Start the API on the port the configuration names.

`API_PORT` in `.env` is the single answer to "where does the API live". The
server that binds it, the smoke test that calls it and the Vite dev proxy that
fronts it all read that one value, so none of them can drift away from the
others.

    python scripts/run_api.py [--reload]

`--reload` restarts the server when a file changes. It is for development
only - the reloader watches the filesystem, and a deployment should not have
that on.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402

from app.settings import settings  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reload",
        action="store_true",
        help="restart the server when a source file changes (development only)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="interface to bind (default: 127.0.0.1, loopback only)",
    )
    args = parser.parse_args()

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=settings.api_port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
