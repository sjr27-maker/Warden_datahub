"""Loads a coverage profile of the world into DataHub.

The underlying data is identical across profiles. What differs is what gets
emitted into the catalog — which is the point: nothing about the estate
changes between runs, only what DataHub knows about it.

This layer simulates ingestion connectors, so it uses the DataHub SDK
directly. The MCP-only constraint applies to Warden's agents, which read and
write the graph exclusively through mcp-server-datahub.
"""

import argparse
import logging
from pathlib import Path

import duckdb
from datahub.emitter.mce_builder import make_data_platform_urn, make_dataset_urn
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.emitter.rest_emitter import DatahubRestEmitter
from datahub.metadata.schema_classes import (
    BooleanTypeClass,
    DataPlatformInfoClass,
    DatasetLineageTypeClass,
    DatasetPropertiesClass,
    DateTypeClass,
    FineGrainedLineageClass,
    FineGrainedLineageDownstreamTypeClass,
    FineGrainedLineageUpstreamTypeClass,
    NumberTypeClass,
    OtherSchemaClass,
    PlatformTypeClass,
    SchemaFieldClass,
    SchemaFieldDataTypeClass,
    SchemaMetadataClass,
    StringTypeClass,
    UpstreamClass,
    UpstreamLineageClass,
)

from warden.agent.config import settings
from warden.registry import PlatformRecord
from world.estate import ESTATE, PROFILES, CoverageProfile

logger = logging.getLogger(__name__)

WAREHOUSE = Path(__file__).parent.parent / "world" / "warehouse.duckdb"
ENV = "PROD"

RAW_TABLES = [
    "raw_users",
    "raw_orders",
    "raw_order_items",
    "raw_payments",
    "raw_refunds",
    "finance_extract",
]

# dbt model -> upstream models. This is parsed lineage: what a real connector
# extracts from compiled SQL.
DBT_LINEAGE: dict[str, list[str]] = {
    "stg_users": ["raw_users"],
    "stg_orders": ["raw_orders"],
    "stg_order_items": ["raw_order_items"],
    "stg_payments": ["raw_payments"],
    "stg_refunds": ["raw_refunds"],
    # HOLE #2 — upstream is a human with a spreadsheet. Unknowable in any profile.
    "mart_finance_summary": [],
    "dim_customers": ["stg_users", "stg_orders"],
    "fct_orders": ["stg_orders", "stg_order_items"],
    "fct_revenue": ["stg_payments", "stg_orders", "stg_refunds"],
    "customer_ltv": ["fct_revenue"],
}

# HOLE #1 — real consumers, invisible unless the tableau connector runs.
TABLEAU_DASHBOARDS: dict[str, list[str]] = {
    "exec_revenue_overview": ["fct_revenue"],
    "customer_health": ["customer_ltv", "dim_customers"],
    "orders_operational": ["fct_orders"],
    "finance_quarterly": ["mart_finance_summary"],
}

# HOLE #3 — produced by a pandas transform. Inferable, never parseable.
PYTHON_OUTPUTS: dict[str, list[str]] = {
    "customer_segments": ["customer_ltv"],
}

DESCRIPTIONS: dict[tuple[str, str], str] = {
    ("duckdb", "finance_extract"): (
        "Hand-maintained quarterly extract pasted from a finance spreadsheet. "
        "No automated upstream exists."
    ),
    ("dbt", "mart_finance_summary"): (
        "Quarterly finance summary. Reads only from a hand-maintained extract, "
        "so its true origin is outside any system DataHub can observe."
    ),
    ("dbt", "stg_users"): (
        "Cleaned user dimension. Filters out test accounts via is_test — a rule "
        "downstream consumers rely on but which is not enforced anywhere."
    ),
}

# Column-level lineage for the models the scenarios touch. Real connectors
# derive this from compiled SQL; we declare it for the models that matter.
COLUMN_LINEAGE: dict[str, dict[str, list[tuple[str, str]]]] = {
    "fct_revenue": {
        "cust_id": [("dbt", "stg_orders.cust_id")],
        "gross_revenue_usd": [("dbt", "stg_payments.amount_usd")],
        "refunded_usd": [("dbt", "stg_refunds.refund_amount_usd")],
    },
    "fct_orders": {
        "cust_id": [("dbt", "stg_orders.cust_id")],
        "gross_value_usd": [("dbt", "stg_order_items.unit_price_usd")],
    },
    "dim_customers": {
        "user_id": [("dbt", "stg_users.user_id")],
        "order_count": [("dbt", "stg_orders.cust_id")],
    },
    "customer_ltv": {
        "cust_id": [("dbt", "fct_revenue.cust_id")],
        "lifetime_value_usd": [("dbt", "fct_revenue.net_revenue_usd")],
    },
}


def _field_urn(platform: str, qualified: str) -> str:
    table, column = qualified.rsplit(".", 1)
    return f"urn:li:schemaField:({_urn(platform, table)},{column})"


def _fine_grained(model: str) -> list[FineGrainedLineageClass]:
    spec = COLUMN_LINEAGE.get(model, {})
    return [
        FineGrainedLineageClass(
            upstreamType=FineGrainedLineageUpstreamTypeClass.FIELD_SET,
            downstreamType=FineGrainedLineageDownstreamTypeClass.FIELD,
            upstreams=[_field_urn(p, q) for p, q in upstreams],
            downstreams=[f"urn:li:schemaField:({_urn('dbt', model)},{column})"],
        )
        for column, upstreams in spec.items()
    ]


def _emitter() -> DatahubRestEmitter:
    return DatahubRestEmitter(
        gms_server=settings.datahub_gms_url,
        token=settings.datahub_gms_token or None,
    )


def _urn(platform: str, name: str) -> str:
    return make_dataset_urn(platform=platform, name=name, env=ENV)


def _upstream_platform(name: str) -> str:
    return "duckdb" if name in RAW_TABLES else "dbt"


def _map_type(native: str):
    """DuckDB native type -> DataHub type.

    The Assessor reasons about type narrowing and widening, so these must be
    accurate. Typing every column as string would make that analysis impossible.
    """
    t = native.upper()
    if any(k in t for k in ("INT", "DECIMAL", "DOUBLE", "FLOAT", "NUMERIC", "HUGEINT")):
        return NumberTypeClass()
    if "BOOL" in t:
        return BooleanTypeClass()
    if any(k in t for k in ("DATE", "TIMESTAMP", "TIME")):
        return DateTypeClass()
    return StringTypeClass()


def _duckdb_columns(table: str) -> list[tuple[str, str]]:
    if not WAREHOUSE.exists():
        raise FileNotFoundError(
            f"warehouse not found at {WAREHOUSE} — run `make build-world` first"
        )
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    try:
        rows = con.execute(f"describe {table}").fetchall()
        return [(r[0], r[1]) for r in rows]
    except duckdb.CatalogException:
        logger.warning("table %s not found in warehouse; skipping schema", table)
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
            type=SchemaFieldDataTypeClass(type=_map_type(dtype)),
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
            # Required by the Avro schema — a null here fails validation.
            platformSchema=OtherSchemaClass(rawSchema=""),
            fields=fields,
        ),
    )


def _lineage_mcp(
    downstream_urn: str,
    upstream_urns: list[str],
    fine_grained: list[FineGrainedLineageClass] | None = None,
) -> MetadataChangeProposalWrapper | None:
    if not upstream_urns:
        return None
    return MetadataChangeProposalWrapper(
        entityUrn=downstream_urn,
        aspect=UpstreamLineageClass(
            upstreams=[
                UpstreamClass(dataset=u, type=DatasetLineageTypeClass.TRANSFORMED)
                for u in upstream_urns
            ],
            fineGrainedLineages=fine_grained or None,
        ),
    )


def _properties_mcp(urn: str, description: str) -> MetadataChangeProposalWrapper:
    return MetadataChangeProposalWrapper(
        entityUrn=urn,
        aspect=DatasetPropertiesClass(description=description),
    )


class _Emission:
    """Small counter so each platform reports what it contributed."""

    def __init__(self, emitter: DatahubRestEmitter) -> None:
        self._emitter = emitter
        self.count = 0

    def __call__(self, mcp: MetadataChangeProposalWrapper | None) -> None:
        if mcp is None:
            return
        self._emitter.emit(mcp)
        self.count += 1


def _emit_platforms(emit: _Emission) -> None:
    """Create every dataPlatform entity in the estate, regardless of profile.

    An organisation knows Tableau exists even before configuring its connector.
    """
    for spec in ESTATE:
        emit(
            MetadataChangeProposalWrapper(
                entityUrn=make_data_platform_urn(spec.name),
                aspect=DataPlatformInfoClass(
                    name=spec.name,
                    displayName=spec.name.title(),
                    type=PlatformTypeClass.OTHERS,
                    datasetNameDelimiter=".",
                ),
            )
        )


def _emit_duckdb(emit: _Emission) -> None:
    for table in RAW_TABLES:
        urn = _urn("duckdb", table)
        emit(_schema_mcp(urn, table, "duckdb"))
        if desc := DESCRIPTIONS.get(("duckdb", table)):
            emit(_properties_mcp(urn, desc))


def _emit_dbt(emit: _Emission) -> None:
    for model, upstreams in DBT_LINEAGE.items():
        urn = _urn("dbt", model)
        emit(_schema_mcp(urn, model, "dbt"))
        emit(
            _lineage_mcp(
                urn,
                [_urn(_upstream_platform(u), u) for u in upstreams],
                _fine_grained(model),
            )
        )
        if desc := DESCRIPTIONS.get(("dbt", model)):
            emit(_properties_mcp(urn, desc))


def _emit_tableau(emit: _Emission) -> None:
    for dashboard, upstreams in TABLEAU_DASHBOARDS.items():
        urn = _urn("tableau", dashboard)
        emit(_properties_mcp(urn, f"Tableau dashboard: {dashboard.replace('_', ' ')}"))
        emit(_lineage_mcp(urn, [_urn("dbt", u) for u in upstreams]))


def _emit_python(emit: _Emission, profile: CoverageProfile) -> None:
    for output, upstreams in PYTHON_OUTPUTS.items():
        urn = _urn("python", output)
        emit(_schema_mcp(urn, output, "python"))
        # The edge exists in reality but no parser produces it. Only a profile
        # that permits inference emits it.
        if profile.emit_inferred_edges:
            emit(_lineage_mcp(urn, [_urn("dbt", u) for u in upstreams]))


def _emit_registry(profile: CoverageProfile, emit: _Emission) -> int:
    """Declare what exists and what has a lineage connector — including
    platforms this profile cannot see.

    Without this, coverage is self-certifying: an agent that only counts what
    it retrieved always reports complete coverage.
    """
    records = [
        PlatformRecord(
            platform=spec.name,
            lineage_connector_configured=spec.name in profile.platforms,
            expected_entity_count=spec.entity_count,
            note=spec.note,
            hosts_consumers=spec.hosts_consumers,
        )
        for spec in ESTATE
    ]
    for record in records:
        emit(
            MetadataChangeProposalWrapper(
                entityUrn=record.registry_urn,
                aspect=DatasetPropertiesClass(
                    name=f"registry.{record.platform}",
                    description=f"Warden coverage registry entry for {record.platform}.",
                    customProperties=record.to_custom_properties(),
                ),
            )
        )
    return len(records)


def ingest_profile(profile: CoverageProfile) -> tuple[int, int]:
    emit = _Emission(_emitter())

    _emit_platforms(emit)

    if "duckdb" in profile.platforms:
        _emit_duckdb(emit)
    if "dbt" in profile.platforms:
        _emit_dbt(emit)
    if "tableau" in profile.platforms:
        _emit_tableau(emit)
    if "python" in profile.platforms:
        _emit_python(emit, profile)

    platforms = _emit_registry(profile, emit)
    return emit.count, platforms


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Ingest a coverage profile into DataHub.")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="dark")
    args = parser.parse_args()

    profile = PROFILES[args.profile]
    dark = ", ".join(profile.platforms_present_without_lineage) or "—"

    print(f"ingesting profile '{profile.name}'")
    print(f"  platforms with lineage : {', '.join(profile.platforms)}")
    print(f"  platforms left dark    : {dark}")

    aspects, platforms = ingest_profile(profile)

    print(f"  emitted {aspects} aspects")
    print(f"  registry written for {platforms} platforms")
    print("\ndone. search is eventually consistent — allow a few seconds before querying.")


if __name__ == "__main__":
    main()
