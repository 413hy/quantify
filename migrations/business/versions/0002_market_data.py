"""Add immutable market-data archive, receipt, and quality evidence."""

from alembic import op

revision = "0002_market_data"
down_revision = "0001_business_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE market.raw_archive_objects (
            object_id varchar(64) PRIMARY KEY,
            object_path text NOT NULL UNIQUE,
            stream varchar(24) NOT NULL CHECK (stream IN ('depth','aggTrade')),
            symbol varchar(24) NOT NULL,
            hour_start timestamptz NOT NULL,
            schema_version varchar(32) NOT NULL,
            sha256 char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            size_bytes bigint NOT NULL CHECK (size_bytes > 0),
            row_count bigint NOT NULL CHECK (row_count > 0),
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (object_path, sha256, size_bytes)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE market.remote_archive_receipts (
            receipt_id varchar(64) PRIMARY KEY,
            object_id varchar(64) NOT NULL REFERENCES market.raw_archive_objects(object_id),
            remote_uri text NOT NULL,
            remote_etag text NOT NULL,
            object_sha256 char(64) NOT NULL CHECK (object_sha256 ~ '^[0-9a-f]{64}$'),
            object_size_bytes bigint NOT NULL CHECK (object_size_bytes > 0),
            signer_key_id varchar(128) NOT NULL,
            signature_base64 text NOT NULL,
            uploaded_at timestamptz NOT NULL,
            verified_at timestamptz NOT NULL,
            receipt_payload jsonb NOT NULL,
            receipt_hash char(64) NOT NULL CHECK (receipt_hash ~ '^[0-9a-f]{64}$'),
            UNIQUE (object_id, receipt_hash)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE market.data_manifests (
            manifest_id varchar(64) PRIMARY KEY,
            manifest_date date NOT NULL,
            previous_manifest_hash char(64),
            manifest_payload jsonb NOT NULL,
            manifest_hash char(64) NOT NULL UNIQUE CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
            created_at timestamptz NOT NULL,
            CHECK (
                previous_manifest_hash IS NULL
                OR previous_manifest_hash ~ '^[0-9a-f]{64}$'
            )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE market.data_quality_intervals (
            quality_id varchar(64) NOT NULL,
            symbol varchar(24) NOT NULL,
            interval_start timestamptz NOT NULL,
            interval_end timestamptz NOT NULL,
            status varchar(32) NOT NULL CHECK (status IN (
                'HEALTHY','WARMING_UP','STALE','GAP_DETECTED','CLOCK_UNSAFE','INVALID'
            )),
            last_update_id bigint,
            gap_count bigint NOT NULL DEFAULT 0 CHECK (gap_count >= 0),
            duplicate_count bigint NOT NULL DEFAULT 0 CHECK (duplicate_count >= 0),
            out_of_order_count bigint NOT NULL DEFAULT 0 CHECK (out_of_order_count >= 0),
            evidence jsonb NOT NULL,
            evidence_hash char(64) NOT NULL CHECK (evidence_hash ~ '^[0-9a-f]{64}$'),
            CHECK (interval_end > interval_start),
            PRIMARY KEY (quality_id, interval_start)
        )
        """
    )
    op.execute(
        "SELECT create_hypertable('market.data_quality_intervals','interval_start', "
        "if_not_exists => TRUE)"
    )
    for table in (
        "market.raw_archive_objects",
        "market.remote_archive_receipts",
        "market.data_manifests",
        "market.data_quality_intervals",
    ):
        trigger = table.replace(".", "_") + "_append_only"
        op.execute(
            f"CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION control.reject_append_only_mutation()"
        )


def downgrade() -> None:
    op.execute("DROP TABLE market.data_quality_intervals")
    op.execute("DROP TABLE market.data_manifests")
    op.execute("DROP TABLE market.remote_archive_receipts")
    op.execute("DROP TABLE market.raw_archive_objects")
