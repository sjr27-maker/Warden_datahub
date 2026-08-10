"""Capture the current profile's graph for offline replay."""

import argparse
import asyncio
import logging

from warden.agent.mcp_client import mcp_client
from warden.snapshot import capture, write

DEMO_ROOTS = ["stg_orders", "stg_order_items", "stg_payments", "stg_refunds"]


def _quiet_mcp_logging() -> None:
    """The MCP server logs full GraphQL query text at DEBUG, which buries the
    only output that matters here."""
    for name in ("mcp_server_datahub", "mcp", "httpx", "datahub"):
        logging.getLogger(name).setLevel(logging.WARNING)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=["dark", "covered"], required=True)
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    _quiet_mcp_logging()

    async with mcp_client() as client:
        snapshot = await capture(client, args.profile, DEMO_ROOTS)

    path = write(snapshot)
    print(f"captured profile '{args.profile}'")
    print(f"  registry entries : {len(snapshot.registry)}")
    print(f"  search queries   : {len(snapshot.search)}")
    print(f"  lineage queries  : {len(snapshot.lineage)}")
    print(f"  written to       : {path}")


if __name__ == "__main__":
    asyncio.run(main())