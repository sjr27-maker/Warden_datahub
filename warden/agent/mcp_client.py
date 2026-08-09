"""Async wrapper over mcp-server-datahub, spawned as a stdio subprocess.
Every DataHub read or write goes through this file — no other module imports
mcp directly. Signatures verified against a live server's tool schema.
"""

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
        # Drop None values — the server rejects unexpected nulls on some tools.
        args = {k: v for k, v in kwargs.items() if v is not None}
        result = await self._session.call_tool(tool, arguments=args)
        blocks = [b.text for b in result.content if hasattr(b, "text")]
        raw = "\n".join(blocks)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Not every DataHub MCP tool returns JSON; some return formatted text.
            return {"raw": raw}

    # ---- reads ----

    async def search(
        self,
        query: str = "*",
        filter: str | None = None,
        num_results: int = 10,
        sort_by: str | None = None,
    ) -> dict:
        return await self._call(
            "search", query=query, filter=filter, num_results=num_results, sort_by=sort_by
        )

    async def search_with_retry(self, query: str, filter: str | None = None) -> dict:
        """Use after a write — DataHub search is eventually consistent."""
        last: dict = {}
        for delay in _SEARCH_RETRY_DELAYS:
            last = await self.search(query, filter=filter)
            if last.get("total", 0) or last.get("results"):
                return last
            await asyncio.sleep(delay)
        logger.warning("search_with_retry exhausted retries for query=%r", query)
        return last

    async def get_entities(self, urns: list[str] | str) -> dict:
        return await self._call("get_entities", urns=urns)

    async def get_lineage(
        self,
        urn: str,
        column: str | None = None,
        query: str | None = None,
        filter: str | None = None,
    ) -> dict:
        """`column` is accepted by the tool schema but its behaviour is unverified —
        see NOTES.md. Warden must not assume column-level traversal works."""
        return await self._call("get_lineage", urn=urn, column=column, query=query, filter=filter)

    async def get_lineage_paths_between(
        self,
        source_urn: str,
        target_urn: str,
        source_column: str | None = None,
        target_column: str | None = None,
    ) -> dict:
        return await self._call(
            "get_lineage_paths_between",
            source_urn=source_urn,
            target_urn=target_urn,
            source_column=source_column,
            target_column=target_column,
        )

    async def list_schema_fields(self, urn: str, keywords: list[str] | None = None) -> dict:
        return await self._call("list_schema_fields", urn=urn, keywords=keywords)

    async def get_dataset_queries(self, urn: str, column: str | None = None) -> dict:
        """Real query history — the strongest available relevance signal."""
        return await self._call("get_dataset_queries", urn=urn, column=column)

    async def grep_documents(self, urns: list[str], pattern: str, context_chars: int = 200) -> dict:
        """Note: requires explicit URNs. There is no global document grep."""
        return await self._call(
            "grep_documents", urns=urns, pattern=pattern, context_chars=context_chars
        )

    async def search_documents(self, query: str = "*", num_results: int = 10) -> dict:
        return await self._call("search_documents", query=query, num_results=num_results)

    # ---- writes ----

    async def add_tags(
        self, tag_urns: list[str], entity_urns: list[str], column_paths: list[str] | None = None
    ) -> dict:
        return await self._call(
            "add_tags", tag_urns=tag_urns, entity_urns=entity_urns, column_paths=column_paths
        )

    async def add_terms(self, term_urns: list[str], entity_urns: list[str]) -> dict:
        return await self._call("add_terms", term_urns=term_urns, entity_urns=entity_urns)

    async def add_owners(self, owner_urns: list[str], entity_urns: list[str]) -> dict:
        return await self._call("add_owners", owner_urns=owner_urns, entity_urns=entity_urns)

    async def update_description(
        self, entity_urn: str, description: str, operation: str = "replace"
    ) -> dict:
        return await self._call(
            "update_description",
            entity_urn=entity_urn,
            description=description,
            operation=operation,
        )

    async def add_structured_properties(
        self, property_values: dict[str, list], entity_urns: list[str]
    ) -> dict:
        """property_values maps structured-property URNs to lists of values."""
        return await self._call(
            "add_structured_properties",
            property_values=property_values,
            entity_urns=entity_urns,
        )

    async def save_document(
        self,
        document_type: str,
        title: str,
        content: str,
        urn: str | None = None,
    ) -> dict:
        """document_type is one of: Insight, Decision, FAQ, Analysis, Summary,
        Recommendation, Note, Context. Warden uses Decision for held decisions
        and Analysis for completed run reports."""
        return await self._call(
            "save_document",
            document_type=document_type,
            title=title,
            content=content,
            urn=urn,
        )


@asynccontextmanager
async def mcp_client():
    client = MCPClient()
    async with client:
        yield client
