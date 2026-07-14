"""Add signed runtime endpoint policy and atomic multi-window reservation."""

from alembic import op

revision = "0002_atomic_reservation"
down_revision = "0001_host_rate_control"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE rate_control.rate_windows
          ADD COLUMN blocked_until timestamptz,
          ADD COLUMN limit_source_hash char(64)
            CHECK (limit_source_hash IS NULL OR limit_source_hash ~ '^[0-9a-f]{64}$')
        """
    )
    op.execute(
        """
        CREATE TABLE rate_control.endpoint_runtime_policies (
            endpoint_authority varchar(96) NOT NULL,
            endpoint_id varchar(128) NOT NULL,
            endpoint_catalog_hash char(64) NOT NULL
              CHECK (endpoint_catalog_hash ~ '^[0-9a-f]{64}$'),
            policy_payload_hash char(64) NOT NULL UNIQUE
              CHECK (policy_payload_hash ~ '^[0-9a-f]{64}$'),
            status varchar(24) NOT NULL CHECK (status IN ('SIGNED_RUNTIME','REVOKED')),
            allowed_callers varchar(96)[] NOT NULL CHECK (cardinality(allowed_callers) > 0),
            derived_operation_class varchar(48) NOT NULL,
            cost_vector jsonb NOT NULL CHECK (
              jsonb_typeof(cost_vector) = 'array' AND jsonb_array_length(cost_vector) > 0
            ),
            valid_from timestamptz NOT NULL,
            valid_until timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY(endpoint_authority, endpoint_id, endpoint_catalog_hash),
            CHECK (valid_until > valid_from)
        )
        """
    )
    op.execute(
        """
        CREATE TRIGGER endpoint_runtime_policies_append_only
          BEFORE UPDATE OR DELETE ON rate_control.endpoint_runtime_policies
          FOR EACH ROW EXECUTE FUNCTION rate_control.reject_append_only_mutation()
        """
    )
    op.execute(
        """
        ALTER TABLE rate_control.permits
          ADD COLUMN environment varchar(16) NOT NULL,
          ADD COLUMN endpoint_catalog_hash char(64) NOT NULL
            CHECK (endpoint_catalog_hash ~ '^[0-9a-f]{64}$'),
          ADD COLUMN derived_operation_class varchar(48) NOT NULL
        """
    )
    op.execute(
        """
        CREATE FUNCTION rate_control.reserve_permit(
            requested_permit_id varchar,
            requested_request_key varchar,
            requested_subject_service varchar,
            requested_subject_instance varchar,
            requested_environment varchar,
            requested_endpoint_authority varchar,
            requested_endpoint_id varchar,
            requested_catalog_hash char(64),
            requested_canonical_hash char(64),
            requested_parameter_hash char(64),
            requested_wire_hash char(64),
            requested_operation_facts_hash char(64),
            requested_capability_hash char(64),
            requested_document_hash char(64),
            requested_capability_nonce varchar,
            requested_fencing_epoch bigint,
            requested_expires_at timestamptz
        ) RETURNS TABLE(
            decision varchar,
            reason_code varchar,
            permit_id varchar,
            derived_operation_class varchar,
            fencing_epoch bigint,
            expires_at timestamptz
        )
        LANGUAGE plpgsql AS $$
        DECLARE
            now_at timestamptz := clock_timestamp();
            current_epoch bigint;
            policy_row rate_control.endpoint_runtime_policies%ROWTYPE;
            existing_permit rate_control.permits%ROWTYPE;
            window_row rate_control.rate_windows%ROWTYPE;
            cost_item jsonb;
            item_cost bigint;
            item_ceiling bigint;
            used_before bigint;
            used_after bigint;
        BEGIN
            SELECT epoch INTO current_epoch
              FROM rate_control.fencing_state WHERE singleton = true FOR UPDATE;
            IF current_epoch IS NULL OR current_epoch <> requested_fencing_epoch THEN
                RETURN QUERY SELECT 'DENIED'::varchar, 'RATE_FENCING_STALE'::varchar,
                  NULL::varchar, NULL::varchar, COALESCE(current_epoch, 0), NULL::timestamptz;
                RETURN;
            END IF;

            IF requested_expires_at <= now_at
               OR requested_expires_at > now_at + interval '5 seconds' THEN
                RETURN QUERY SELECT 'DENIED'::varchar, 'RATE_AUTHORITY_UNAVAILABLE'::varchar,
                  NULL::varchar, NULL::varchar, current_epoch, NULL::timestamptz;
                RETURN;
            END IF;

            SELECT * INTO existing_permit FROM rate_control.permits
              WHERE request_key = requested_request_key FOR UPDATE;
            IF FOUND THEN
                IF existing_permit.state = 'RESERVED'
                   AND existing_permit.expires_at > now_at
                   AND existing_permit.subject_caller_service = requested_subject_service
                   AND existing_permit.subject_caller_instance_id = requested_subject_instance
                   AND existing_permit.environment = requested_environment
                   AND existing_permit.endpoint_authority = requested_endpoint_authority
                   AND existing_permit.endpoint_id = requested_endpoint_id
                   AND existing_permit.endpoint_catalog_hash = requested_catalog_hash
                   AND existing_permit.canonical_request_hash = requested_canonical_hash
                   AND existing_permit.parameter_hash = requested_parameter_hash
                   AND existing_permit.wire_bytes_hash = requested_wire_hash
                   AND existing_permit.operation_facts_hash = requested_operation_facts_hash
                   AND existing_permit.capability_payload_hash = requested_capability_hash
                   AND existing_permit.gateway_request_document_hash = requested_document_hash
                   AND existing_permit.capability_nonce = requested_capability_nonce
                   AND existing_permit.fencing_epoch = requested_fencing_epoch THEN
                    RETURN QUERY SELECT 'GRANTED'::varchar, 'RATE_GRANTED'::varchar,
                      existing_permit.permit_id, existing_permit.derived_operation_class,
                      existing_permit.fencing_epoch, existing_permit.expires_at;
                ELSE
                    RETURN QUERY SELECT 'DENIED'::varchar, 'RATE_CAPABILITY_REPLAYED'::varchar,
                      NULL::varchar, NULL::varchar, current_epoch, NULL::timestamptz;
                END IF;
                RETURN;
            END IF;

            SELECT * INTO policy_row FROM rate_control.endpoint_runtime_policies AS policy
             WHERE policy.endpoint_authority = requested_endpoint_authority
               AND policy.endpoint_id = requested_endpoint_id
               AND policy.endpoint_catalog_hash = requested_catalog_hash;
            IF NOT FOUND OR policy_row.status <> 'SIGNED_RUNTIME'
               OR now_at < policy_row.valid_from OR now_at >= policy_row.valid_until THEN
                RETURN QUERY SELECT 'DENIED'::varchar, 'RATE_ENDPOINT_UNKNOWN'::varchar,
                  NULL::varchar, NULL::varchar, current_epoch, NULL::timestamptz;
                RETURN;
            END IF;
            IF NOT requested_subject_service = ANY(policy_row.allowed_callers) THEN
                RETURN QUERY SELECT 'DENIED'::varchar, 'RATE_CALLER_NOT_ALLOWED'::varchar,
                  NULL::varchar, NULL::varchar, current_epoch, NULL::timestamptz;
                RETURN;
            END IF;

            FOR cost_item IN
                SELECT value FROM jsonb_array_elements(policy_row.cost_vector)
                 ORDER BY value->>'rate_limit_type', value->>'scope_key_hash',
                          value->>'interval_name'
            LOOP
                item_cost := (cost_item->>'cost')::bigint;
                item_ceiling := (cost_item->>'ceiling_units')::bigint;
                IF item_cost < 0 OR item_ceiling < 1 THEN
                    RETURN QUERY SELECT 'DENIED'::varchar, 'RATE_AUTHORITY_UNAVAILABLE'::varchar,
                      NULL::varchar, NULL::varchar, current_epoch, NULL::timestamptz;
                    RETURN;
                END IF;
                SELECT * INTO window_row FROM rate_control.rate_windows
                 WHERE endpoint_authority = requested_endpoint_authority
                   AND scope_key_hash = cost_item->>'scope_key_hash'
                   AND rate_limit_type = cost_item->>'rate_limit_type'
                   AND interval_name = cost_item->>'interval_name'
                   AND window_start <= now_at AND window_end > now_at
                 FOR UPDATE;
                IF NOT FOUND THEN
                    RETURN QUERY SELECT 'DENIED'::varchar, 'RATE_AUTHORITY_UNAVAILABLE'::varchar,
                      NULL::varchar, NULL::varchar, current_epoch, NULL::timestamptz;
                    RETURN;
                END IF;
                IF window_row.blocked_until IS NOT NULL AND window_row.blocked_until > now_at THEN
                    RETURN QUERY SELECT 'DENIED'::varchar, 'RATE_SCOPE_BLOCKED'::varchar,
                      NULL::varchar, NULL::varchar, current_epoch, NULL::timestamptz;
                    RETURN;
                END IF;
                used_before := GREATEST(window_row.effective_used, window_row.observed_max);
                IF used_before + item_cost > LEAST(window_row.hard_limit, item_ceiling) THEN
                    RETURN QUERY SELECT 'DENIED'::varchar,
                      'RATE_CLASS_CEILING_EXCEEDED'::varchar, NULL::varchar, NULL::varchar,
                      current_epoch, NULL::timestamptz;
                    RETURN;
                END IF;
            END LOOP;

            INSERT INTO rate_control.capability_nonces(nonce,payload_hash,state,expires_at)
              VALUES(requested_capability_nonce, requested_capability_hash, 'RESERVED',
                     requested_expires_at)
              ON CONFLICT DO NOTHING;
            IF NOT FOUND THEN
                RETURN QUERY SELECT 'DENIED'::varchar, 'RATE_CAPABILITY_REPLAYED'::varchar,
                  NULL::varchar, NULL::varchar, current_epoch, NULL::timestamptz;
                RETURN;
            END IF;

            INSERT INTO rate_control.permits(
              permit_id,request_key,subject_caller_service,subject_caller_instance_id,
              environment,endpoint_authority,endpoint_id,endpoint_catalog_hash,
              derived_operation_class,canonical_request_hash,parameter_hash,wire_bytes_hash,
              operation_facts_hash,capability_payload_hash,gateway_request_document_hash,
              capability_nonce,fencing_epoch,state,reserved_at,expires_at
            ) VALUES (
              requested_permit_id,requested_request_key,requested_subject_service,
              requested_subject_instance,requested_environment,requested_endpoint_authority,
              requested_endpoint_id,requested_catalog_hash,policy_row.derived_operation_class,
              requested_canonical_hash,requested_parameter_hash,requested_wire_hash,
              requested_operation_facts_hash,requested_capability_hash,requested_document_hash,
              requested_capability_nonce,requested_fencing_epoch,'RESERVED',now_at,
              requested_expires_at
            );

            FOR cost_item IN
                SELECT value FROM jsonb_array_elements(policy_row.cost_vector)
                 ORDER BY value->>'rate_limit_type', value->>'scope_key_hash',
                          value->>'interval_name'
            LOOP
                item_cost := (cost_item->>'cost')::bigint;
                SELECT * INTO window_row FROM rate_control.rate_windows
                 WHERE endpoint_authority = requested_endpoint_authority
                   AND scope_key_hash = cost_item->>'scope_key_hash'
                   AND rate_limit_type = cost_item->>'rate_limit_type'
                   AND interval_name = cost_item->>'interval_name'
                   AND window_start <= now_at AND window_end > now_at
                 FOR UPDATE;
                used_before := GREATEST(window_row.effective_used, window_row.observed_max);
                used_after := used_before + item_cost;
                UPDATE rate_control.rate_windows
                   SET effective_used = used_after, updated_at = now_at
                 WHERE window_id = window_row.window_id;
                INSERT INTO rate_control.allocations(
                  permit_id,window_id,cost,effective_used_before,effective_used_after
                ) VALUES (
                  requested_permit_id,window_row.window_id,item_cost,used_before,used_after
                );
            END LOOP;

            RETURN QUERY SELECT 'GRANTED'::varchar, 'RATE_GRANTED'::varchar,
              requested_permit_id, policy_row.derived_operation_class, current_epoch,
              requested_expires_at;
        END $$
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION rate_control.reserve_permit("
        "varchar,varchar,varchar,varchar,varchar,varchar,varchar,"
        "char,char,char,char,char,char,char,varchar,bigint,timestamptz)"
    )
    op.execute(
        "ALTER TABLE rate_control.permits DROP COLUMN derived_operation_class, "
        "DROP COLUMN endpoint_catalog_hash, DROP COLUMN environment"
    )
    op.execute("DROP TABLE rate_control.endpoint_runtime_policies")
    op.execute(
        "ALTER TABLE rate_control.rate_windows DROP COLUMN limit_source_hash, "
        "DROP COLUMN blocked_until"
    )
