
from warden.agent.models import EntityRef, LineageEdge, Provenance


class FakeMCPClient:
    def __init__(self) -> None:
        self.entities: dict[str, dict] = {}
        self.lineage: dict[str, list[LineageEdge]] = {}
        self.written_tags: dict[str, list[str]] = {}
        self.written_properties: dict[str, dict] = {}
        self.written_descriptions: dict[str, str] = {}
        self.saved_documents: list[dict] = []

    def seed_entity(self, urn: str, **fields) -> None:
        self.entities[urn] = {"urn": urn, **fields}

    def seed_lineage(self, urn: str, edges: list[LineageEdge]) -> None:
        self.lineage[urn] = edges

    async def search(self, query: str, entity_types: list[str] | None = None) -> dict:
        matches = [e for e in self.entities.values() if query.lower() in e["urn"].lower()]
        return {"total": len(matches), "results": matches}

    async def search_with_retry(self, query: str, entity_types=None) -> dict:
        return await self.search(query, entity_types)

    async def get_entities(self, urns: list[str]) -> dict:
        return {"entities": [self.entities[u] for u in urns if u in self.entities]}

    async def get_lineage(self, urn: str, direction="downstream", hops: int = 2) -> dict:
        edges = self.lineage.get(urn, [])
        return {"edges": [e.model_dump(mode="json") for e in edges]}

    async def grep_documents(self, pattern: str) -> dict:
        return {"matches": []}

    async def update_description(self, urn: str, description: str) -> dict:
        self.written_descriptions[urn] = description
        return {"success": True}

    async def add_tags(self, urn: str, tags: list[str]) -> dict:
        self.written_tags.setdefault(urn, []).extend(tags)
        return {"success": True}

    async def add_structured_properties(self, urn: str, properties: dict) -> dict:
        self.written_properties.setdefault(urn, {}).update(properties)
        return {"success": True}

    async def save_document(self, title: str, body: str, parent_urn: str | None = None) -> dict:
        doc = {"title": title, "body": body, "parent_urn": parent_urn}
        self.saved_documents.append(doc)
        return {"success": True, "id": len(self.saved_documents)}