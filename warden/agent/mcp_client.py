import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from warden.agent.config import settings

logger = logging.getLogger(__name__)

_SEARCH_RETRY_DELAYS = (0.5, 1.0, 2.0)  # absorbs eventual-consistency after writes


class MCPClient:
    """Thin async wrapper. Construct via `async with MCPClient() as client:`."""

    def __init__(self) -> None:
        self._session: ClientSession | None = None
        self._exit_stack = None

    async def __aenter__(self) -> "MCPClient":
        server_params = StdioServerParameters(
            command="uvx",
            args=["mcp-server-datahub@latest"],
            env={
                "DATAHUB_GMS_URL": settings.datahub_gms_url,
                "DATAHUB_GMS_TOKEN": settings.datahub_gms_token,
                "TOOLS_IS_MUTATION_ENABLED": str(settings.mutation_tools_enabled).lower(),
            },
        )
        self._read, self._write = await stdio_client(server_params).__aenter__()
        self._session = ClientSession(self._read, self._write)
        await self._session.__aenter__()
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._session:
            await self._session.__aexit__(*exc)

    async def _call(self, tool: str, **kwargs: Any) -> dict:
        if not self._session:
            raise RuntimeError("MCPClient used outside `async with` context")
        result = await self._session.call_tool(tool, arguments=kwargs)
        # mcp tool results come back as content blocks; DataHub's server
        # returns JSON text in the first block.
        text = result.content[0].text
        return json.loads(text)

    # ---- reads ----

    async def search(self, query: str, entity_types: list[str] | None = None) -> dict:
        return await self._call("search", query=query, entity_types=entity_types or [])

    async def search_with_retry(self, query: str, entity_types: list[str] | None = None) -> dict:
        """Use after a write, when the entity may not be indexed yet."""
        last_result: dict = {}
        for delay in _SEARCH_RETRY_DELAYS:
            last_result = await self.search(query, entity_types)
            if last_result.get("total", 0) > 0:
                return last_result
            await asyncio.sleep(delay)
        logger.warning("search_with_retry exhausted retries for query=%r", query)
        return last_result

    async def get_entities(self, urns: list[str]) -> dict:
        return await self._call("get_entities", urns=urns)

    async def get_lineage(self, urn: str, direction: str = "downstream", hops: int = 2) -> dict:
        return await self._call("get_lineage", urn=urn, direction=direction, hops=hops)

    async def grep_documents(self, pattern: str) -> dict:
        return await self._call("grep_documents", pattern=pattern)

    # ---- writes (gated by TOOLS_IS_MUTATION_ENABLED on the server) ----

    async def update_description(self, urn: str, description: str) -> dict:
        return await self._call("update_description", urn=urn, description=description)

    async def add_tags(self, urn: str, tags: list[str]) -> dict:
        return await self._call("add_tags", urn=urn, tags=tags)

    async def add_structured_properties(self, urn: str, properties: dict) -> dict:
        return await self._call("add_structured_properties", urn=urn, properties=properties)

    async def save_document(self, title: str, body: str, parent_urn: str | None = None) -> dict:
        return await self._call("save_document", title=title, body=body, parent_urn=parent_urn)


@asynccontextmanager
async def mcp_client():
    """Convenience context manager: `async with mcp_client() as c:`"""
    client = MCPClient()
    async with client:
        yield client
