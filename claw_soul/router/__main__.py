"""Entry point for the router service.

Run with:  python -m claw_soul.router  (or uvicorn claw_soul.router:create_router_app)
"""

import logging
import os

import uvicorn

from .app import create_router_app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    app = create_router_app()
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "7788")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
