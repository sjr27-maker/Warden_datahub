"""Loads a coverage profile of the world into DataHub.

The data is identical across profiles. What differs is what gets emitted —
which is the point: nothing about the estate changes, only what the catalog
knows about it.
"""

import argparse
import asyncio
import logging
from pathlib import Path

import duckdb
from datahub.emitter.mce_builder import make_data_platform_urn, make_dataset_urn
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.emitter.rest_emitter import DatahubRestEmitter
from datahub.metadata.schema_classes import (
    DatasetLineageTypeClass,
    DatasetPropertiesClass,
    SchemaFieldClass,
    SchemaFieldDataTypeClass,
    SchemaMetadataClass,
    StringTypeClass,
    UpstreamClass,
    UpstreamLineageClass,
)

from warden.agent.config import settings
from warden.agent.mcp_client import mcp_client
from warden.registry import PlatformRecord, write_registry
from world.estate import ESTATE, PROFILES, CoverageProfile

logger = logging.getLogger(__name__)

WAREHOUSE = Path(__file__).parent.parent / "world" / "warehouse.duckdb"
ENV = "PROD"

# dbt model -> its upstream models. Parsed lineage: what a real connector extracts.
DBT_LINEAGE: dict[str, list[str]] = {
    "stg_users": ["raw_users"],
    "stg_orders": ["raw_orders"],
    "stg_order_items": ["raw_order_items"],
    "stg_payments": ["raw_payments"],
    "stg_refunds": ["raw_refunds"],
    "mart_finance_summary": [],  # HOLE #2 — upstream is a human with a spreadsheet
    "dim_customers": ["stg_users", "stg_orders"],
    "fct_orders": ["stg_orders", "stg_order_items"],
    "fct_revenue": ["stg_payments", "stg_orders", "stg_refunds"],
    "customer_ltv": ["fct_revenue"],
}

RAW_TABLES = [
    "raw_users",
    "raw_orders",
    "raw_order_items",
    "raw_payments",
    "raw_refunds",
    "finance_extract",
]

# HOLE #1 — real consumers, invisible unless the tableau connector runs.
TABLEAU_DASHBOARDS: dict[str, list[str]] = {
    "exec_revenue_overview": ["fct_revenue"],
    "customer_health": ["customer_ltv", "dim_customers"],
    "orders_operational": ["fct_orders"],
    "finance_quarterly": ["mart_finance_summary"],
}

# HOLE #3 — produced by pandas. Inferable, never parseable.
PYTHON_OUTPUTS: dict[str, list[str]] = {
    "customer_segments": ["customer_ltv"],
}


def _emitter() -> DatahubRestEmitter:
    return DatahubRestEmitter(
        gms_server=settings.datahub_gms_url,
        token=settings.datahub_gms_token or None,
    )


def _urn(platform: str, name: str) -> str:
    return make_dataset_urn(platform=platform, name=name, env=ENV)


def _duckdb_columns(table: str) -> list[tuple[str, str]]:
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    try:
        rows = con.execute(f"describe {table}").fetchall()
        return [(r[0], r[1]) for r in rows]
    except Exception:
        logger.warning("table %s not found in warehouse", table)
        return []
    finally:
        con.close()


def _schema_mcp(urn: str, table: str, platform: str) -> MetadataChangeProposalWrapper | None:
    columns = _duckdb_columns(table)
    if not columns:
        return None
    fields = [
        SchemaFieldClass(
            fieldPath=name,
            type=SchemaFieldDataTypeClass(type=StringTypeClass()),
            nativeDataType=dtype,
        )
        for name, dtype in columns
    ]
    return MetadataChangeProposalWrapper(
        entityUrn=urn,
        aspect=SchemaMetadataClass(
            schemaName=table,
            platform=make_data_platform_urn(platform),
            version=0,
            hash="",
            platformSchema=None,
            fields=fields,
        ),
    )


def _lineage_mcp(
    downstream_urn: str, upstream_urns: list[str]
) -> MetadataChangeProposalWrapper | None:
    if not upstream_urns:
        return None
    return MetadataChangeProposalWrapper(
        entityUrn=downstream_urn,
        aspect=UpstreamLineageClass(
            upstreams=[
                UpstreamClass(dataset=u, type=DatasetLineageTypeClass.TRANSFORMED)
                for u in upstream_urns
            ]
        ),
    )


def _properties_mcp(urn: str, description: str) -> MetadataChangeProposalWrapper:
    return MetadataChangeProposalWrapper(
        entityUrn=urn,
        aspect=DatasetPropertiesClass(description=description),
    )


def ingest_profile(profile: CoverageProfile) -> None:
    emitter = _emitter()
    emitted = 0

    def emit(mcp: MetadataChangeProposalWrapper | None) -> None:
        nonlocal emitted
        if mcp is not None:
            emitter.emit(mcp)
            emitted += 1

    if "duckdb" in profile.platforms:
        for table in RAW_TABLES:
            urn = _urn("duckdb", table)
            emit(_schema_mcp(urn, table, "duckdb"))
            if table == "finance_extract":
                emit(
                    _properties_mcp(
                        urn,
                        "Hand-maintained quarterly extract pasted from a finance "
                        "spreadsheet. No automated upstream exists.",
                    )
                )

    if "dbt" in profile.platforms:
        for model, upstreams in DBT_LINEAGE.items():
            urn = _urn("dbt", model)
            emit(_schema_mcp(urn, model, "dbt"))
            upstream_urns = [
                _urn("duckdb" if u in RAW_TABLES else "dbt", u) for u in upstreams
            ]
            emit(_lineage_mcp(urn, upstream_urns))

    if "tableau" in profile.platforms:
        for dashboard, upstreams in TABLEAU_DASHBOARDS.items():
            urn = _urn("tableau", dashboard)
            emit(_properties_mcp(urn, f"Tableau dashboard: {dashboard}"))
            emit(_lineage_mcp(urn, [_urn("dbt", u) for u in upstreams]))

    if "python" in profile.platforms:
        for output, upstreams in PYTHON_OUTPUTS.items():
            urn = _urn("python", output)
            emit(_schema_mcp(urn, output, "python"))
            if profile.emit_inferred_edges:
                emit(_lineage_mcp(urn, [_urn("dbt", u) for u in upstreams]))

    print(f"  emitted {emitted} aspects for profile '{profile.name}'")


async def ingest_registry(profile: CoverageProfile) -> None:
    """Declare what exists and what has a connector — including platforms this
    profile cannot see. Without this, coverage is self-certifying."""
    records = [
        PlatformRecord(
            platform=spec.name,
            lineage_connector_configured=spec.name in profile.platforms,
            expected_entity_count=spec.entity_count,
            note=spec.note,
        )
        for spec in ESTATE
    ]
    async with mcp_client() as client:
        await write_registry(client, records)
    print(f"  registry written for {len(records)} platforms")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=sorted(PROFILES), default="dark")
    args = parser.parse_args()

    profile = PROFILES[args.profile]
    print(f"ingesting profile '{profile.name}'")
    ingest_profile(profile)
    asyncio.run(ingest_registry(profile))
    print("done. note: search is eventually consistent — allow a few seconds.")


if __name__ == "__main__":
    main()