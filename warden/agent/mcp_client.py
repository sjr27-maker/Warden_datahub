"""Async wrapper over mcp-server-datahub, spawned as a stdio subprocess."""

import asyncio
import json
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from warden.agent.config import settings

logger = logging.getLogger(__name__)

_SEARCH_RETRY_DELAYS = (0.5, 1.0, 2.0)


class MCPClient:
    def __init__(self) -> None:
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None

    async def __aenter__(self) -> "MCPClient":
        self._stack = AsyncExitStack()
        server_params = StdioServerParameters(
            command="uvx",
            args=["mcp-server-datahub@latest"],
            env={
                "DATAHUB_GMS_URL": settings.datahub_gms_url,
                "DATAHUB_GMS_TOKEN": settings.datahub_gms_token,
                "TOOLS_IS_MUTATION_ENABLED": str(settings.mutation_tools_enabled).lower(),
            },
        )
        read, write = await self._stack.enter_async_context(stdio_client(server_params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._stack:
            await self._stack.aclose()

    async def _call(self, tool: str, **kwargs: Any) -> dict:
        if not self._session:
            raise RuntimeError("MCPClient used outside `async with` context")
        result = await self._session.call_tool(tool, arguments=kwargs)
        text = result.content[0].text
        return json.loads(text)

    # ---- reads ----

    async def search(self, query: str, entity_types: list[str] | None = None) -> dict:
        return await self._call("search", query=query, entity_types=entity_types or [])

    async def search_with_retry(self, query: str, entity_types: list[str] | None = None) -> dict:
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

    # ---- writes ----

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
    client = MCPClient()
    async with client:
        yield client