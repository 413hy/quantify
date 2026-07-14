"""Create independent host rate-control authority."""

from alembic import op

revision = "0001_host_rate_control"
down_revision = None
branch_labels = ("host_control",)
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA rate_control")
    op.execute(
        """
        CREATE TABLE rate_control.fencing_state (
            singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
            epoch bigint NOT NULL CHECK (epoch >= 1),
            allocator_instance_id varchar(128) NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "INSERT INTO rate_control.fencing_state(singleton, epoch, allocator_instance_id) "
        "VALUES (true, 1, 'UNINITIALIZED')"
    )
    op.execute(
        """
        CREATE TABLE rate_control.rate_windows (
            window_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            endpoint_authority varchar(96) NOT NULL,
            scope_key_hash char(64) NOT NULL CHECK (scope_key_hash ~ '^[0-9a-f]{64}$'),
            rate_limit_type varchar(32) NOT NULL,
            interval_name varchar(32) NOT NULL,
            window_start timestamptz NOT NULL,
            window_end timestamptz NOT NULL,
            effective_used bigint NOT NULL DEFAULT 0 CHECK (effective_used >= 0),
            observed_max bigint NOT NULL DEFAULT 0 CHECK (observed_max >= 0),
            hard_limit bigint NOT NULL CHECK (hard_limit > 0),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE(endpoint_authority, scope_key_hash, rate_limit_type, interval_name, window_start),
            CHECK (window_end > window_start)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE rate_control.capability_nonces (
            nonce varchar(160) PRIMARY KEY,
            payload_hash char(64) NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
            state varchar(16) NOT NULL DEFAULT 'RESERVED'
                CHECK (state IN ('RESERVED','CONSUMED','EXPIRED')),
            expires_at timestamptz NOT NULL,
            consumed_at timestamptz
        )
        """
    )
    op.execute(
        """
        CREATE TABLE rate_control.permits (
            permit_id varchar(64) PRIMARY KEY,
            request_key varchar(160) NOT NULL UNIQUE,
            subject_caller_service varchar(96) NOT NULL,
            subject_caller_instance_id varchar(128) NOT NULL,
            endpoint_authority varchar(96) NOT NULL,
            endpoint_id varchar(128) NOT NULL,
            canonical_request_hash char(64) NOT NULL CHECK (canonical_request_hash ~ '^[0-9a-f]{64}$'),
            parameter_hash char(64) NOT NULL CHECK (parameter_hash ~ '^[0-9a-f]{64}$'),
            wire_bytes_hash char(64) NOT NULL CHECK (wire_bytes_hash ~ '^[0-9a-f]{64}$'),
            operation_facts_hash char(64) NOT NULL CHECK (operation_facts_hash ~ '^[0-9a-f]{64}$'),
            capability_payload_hash char(64) NOT NULL CHECK (capability_payload_hash ~ '^[0-9a-f]{64}$'),
            gateway_request_document_hash char(64) NOT NULL
                CHECK (gateway_request_document_hash ~ '^[0-9a-f]{64}$'),
            capability_nonce varchar(160) NOT NULL UNIQUE REFERENCES rate_control.capability_nonces(nonce),
            fencing_epoch bigint NOT NULL CHECK (fencing_epoch >= 1),
            state varchar(16) NOT NULL DEFAULT 'RESERVED'
                CHECK (state IN ('RESERVED','CONSUMED','EXPIRED')),
            reserved_at timestamptz NOT NULL,
            expires_at timestamptz NOT NULL,
            consumed_at timestamptz,
            gateway_instance_id varchar(128),
            CHECK (expires_at > reserved_at),
            CHECK ((state = 'CONSUMED') = (consumed_at IS NOT NULL))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE rate_control.allocations (
            allocation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            permit_id varchar(64) NOT NULL REFERENCES rate_control.permits(permit_id),
            window_id bigint NOT NULL REFERENCES rate_control.rate_windows(window_id),
            cost bigint NOT NULL CHECK (cost >= 0),
            effective_used_before bigint NOT NULL CHECK (effective_used_before >= 0),
            effective_used_after bigint NOT NULL CHECK (effective_used_after >= effective_used_before),
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE(permit_id, window_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE rate_control.observations (
            observation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            message_id varchar(64) NOT NULL UNIQUE,
            observation_type varchar(48) NOT NULL,
            endpoint_authority varchar(96) NOT NULL,
            payload jsonb NOT NULL,
            payload_hash char(64) NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
            occurred_at timestamptz NOT NULL,
            recorded_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION rate_control.reject_append_only_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'append-only table % cannot be mutated', TG_TABLE_NAME;
        END $$
        """
    )
    for table in ("allocations", "observations"):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE "
            f"ON rate_control.{table} FOR EACH ROW EXECUTE FUNCTION "
            "rate_control.reject_append_only_mutation()"
        )
    op.execute(
        """
        CREATE FUNCTION rate_control.consume_permit(
            requested_permit_id varchar,
            requested_canonical_hash char(64),
            requested_parameter_hash char(64),
            requested_wire_hash char(64),
            requested_operation_facts_hash char(64),
            requested_capability_hash char(64),
            requested_document_hash char(64),
            requested_fencing_epoch bigint,
            consuming_gateway_instance_id varchar
        ) RETURNS TABLE(decision varchar, reason_code varchar, send_deadline timestamptz)
        LANGUAGE plpgsql AS $$
        DECLARE
            permit_row rate_control.permits%ROWTYPE;
            current_epoch bigint;
            now_at timestamptz := clock_timestamp();
        BEGIN
            SELECT epoch INTO current_epoch
              FROM rate_control.fencing_state WHERE singleton = true FOR UPDATE;
            IF current_epoch IS NULL OR current_epoch <> requested_fencing_epoch THEN
                RETURN QUERY SELECT 'CONSUME_DENIED'::varchar, 'FENCING_EPOCH_MISMATCH'::varchar, NULL::timestamptz;
                RETURN;
            END IF;

            SELECT * INTO permit_row FROM rate_control.permits
              WHERE permit_id = requested_permit_id FOR UPDATE;
            IF NOT FOUND THEN
                RETURN QUERY SELECT 'CONSUME_DENIED'::varchar, 'PERMIT_NOT_FOUND'::varchar, NULL::timestamptz;
                RETURN;
            END IF;
            IF permit_row.state <> 'RESERVED' THEN
                RETURN QUERY SELECT 'CONSUME_DENIED'::varchar, 'PERMIT_NOT_RESERVED'::varchar, NULL::timestamptz;
                RETURN;
            END IF;
            IF now_at >= permit_row.expires_at THEN
                UPDATE rate_control.permits SET state = 'EXPIRED'
                  WHERE permit_id = requested_permit_id;
                UPDATE rate_control.capability_nonces SET state = 'EXPIRED'
                  WHERE nonce = permit_row.capability_nonce AND state = 'RESERVED';
                RETURN QUERY SELECT 'CONSUME_DENIED'::varchar, 'PERMIT_EXPIRED'::varchar, NULL::timestamptz;
                RETURN;
            END IF;
            IF permit_row.canonical_request_hash <> requested_canonical_hash
               OR permit_row.parameter_hash <> requested_parameter_hash
               OR permit_row.wire_bytes_hash <> requested_wire_hash
               OR permit_row.operation_facts_hash <> requested_operation_facts_hash
               OR permit_row.capability_payload_hash <> requested_capability_hash
               OR permit_row.gateway_request_document_hash <> requested_document_hash
               OR permit_row.fencing_epoch <> requested_fencing_epoch THEN
                RETURN QUERY SELECT 'CONSUME_DENIED'::varchar, 'PERMIT_BINDING_MISMATCH'::varchar, NULL::timestamptz;
                RETURN;
            END IF;

            UPDATE rate_control.capability_nonces
               SET state = 'CONSUMED', consumed_at = now_at
             WHERE nonce = permit_row.capability_nonce
               AND payload_hash = requested_capability_hash
               AND state = 'RESERVED'
               AND expires_at > now_at;
            IF NOT FOUND THEN
                RETURN QUERY SELECT 'CONSUME_DENIED'::varchar, 'CAPABILITY_NONCE_NOT_RESERVED'::varchar, NULL::timestamptz;
                RETURN;
            END IF;

            UPDATE rate_control.permits
               SET state = 'CONSUMED', consumed_at = now_at,
                   gateway_instance_id = consuming_gateway_instance_id
             WHERE permit_id = requested_permit_id;
            RETURN QUERY SELECT 'CONSUME_GRANTED'::varchar, 'RATE_PERMIT_CONSUMED'::varchar,
              LEAST(now_at + interval '50 milliseconds', permit_row.expires_at);
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA rate_control CASCADE")
