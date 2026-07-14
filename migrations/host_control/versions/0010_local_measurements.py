"""Expose one read-only startup measurement snapshot to the runtime role."""

from alembic import op

revision = "0010_local_measurements"
down_revision = "0009_runtime_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION rate_control.read_startup_measurements() RETURNS jsonb
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path TO pg_catalog, rate_control
        AS $$
          SELECT jsonb_build_object(
            'database_authority', jsonb_build_object(
              'database', current_database(),
              'migration_head', (
                SELECT version_num FROM public.alembic_version
              ),
              'wal_recovery_point',
                'wal-lsn-' || replace(pg_current_wal_lsn()::text, '/', '-'),
              'fencing_epoch', fencing.epoch,
              'fencing_owner_instance_id', fencing.allocator_instance_id,
              'read_write',
                NOT pg_is_in_recovery()
                AND current_setting('default_transaction_read_only') <> 'on',
              'independent_business_database', true
            ),
            'nonce_permit_integrity', jsonb_build_object(
              'outstanding_reserved_permit_count', (
                SELECT count(*) FROM rate_control.permits
                 WHERE state = 'RESERVED'
              ),
              'duplicate_capability_nonce_count', (
                SELECT count(*) FROM (
                  SELECT capability_nonce FROM rate_control.permits
                   GROUP BY capability_nonce HAVING count(*) > 1
                ) AS duplicate_nonce
              ),
              'consumed_without_gateway_count', (
                SELECT count(*) FROM rate_control.permits
                 WHERE state = 'CONSUMED' AND gateway_instance_id IS NULL
              ),
              'outcome_missing_past_deadline_count', (
                SELECT count(*)
                  FROM rate_control.consume_decisions AS decision
                  LEFT JOIN rate_control.send_outcomes AS outcome
                    ON outcome.permit_id = decision.permit_id
                WHERE decision.decision = 'CONSUME_GRANTED'
                   AND decision.send_deadline < clock_timestamp()
                   AND outcome.permit_id IS NULL
              )
            ),
            'active_authority_blocks', (
              SELECT COALESCE(
                jsonb_agg(block.endpoint_authority ORDER BY block.endpoint_authority),
                '[]'::jsonb
              )
                FROM rate_control.authority_blocks AS block
               WHERE block.blocked_until IS NULL
                  OR block.blocked_until > clock_timestamp()
            )
          )
          FROM rate_control.fencing_state AS fencing
          WHERE fencing.singleton = true
        $$;
        REVOKE ALL ON FUNCTION rate_control.read_startup_measurements() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION rate_control.read_startup_measurements()
          TO aiq_rate_authority;
        """
    )
    op.execute(
        """
        CREATE FUNCTION rate_control.read_startup_observations(
            requested_authorities varchar[],
            requested_after timestamptz
        ) RETURNS TABLE(payload jsonb, payload_hash char(64), occurred_at timestamptz)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path TO pg_catalog, rate_control
        AS $$
          SELECT observation.payload, observation.payload_hash,
                 observation.occurred_at
            FROM rate_control.observations AS observation
           WHERE observation.observation_type IN (
             'ServerTimeObservation',
             'ExchangeRateLimitObservation',
             'ConnectionStateObservation'
           )
             AND requested_after IS NOT NULL
             AND observation.endpoint_authority = ANY(requested_authorities)
             AND observation.occurred_at >= GREATEST(
               requested_after,
               clock_timestamp() - interval '300 seconds'
             )
           ORDER BY observation.occurred_at, observation.message_id
        $$;
        REVOKE ALL ON FUNCTION rate_control.read_startup_observations(varchar[],timestamptz)
          FROM PUBLIC;

        REVOKE SELECT ON ALL TABLES IN SCHEMA rate_control
          FROM aiq_rate_authority;
        GRANT SELECT ON rate_control.fencing_state,
                        rate_control.endpoint_runtime_policies,
                        rate_control.rate_windows,
                        rate_control.allocations,
                        rate_control.permits
          TO aiq_rate_authority;

        REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA rate_control
          FROM aiq_rate_authority;
        DO $$
        DECLARE
            function_identity text;
        BEGIN
            FOR function_identity IN
                SELECT procedure.oid::regprocedure::text
                  FROM pg_proc AS procedure
                  JOIN pg_namespace AS namespace
                    ON namespace.oid = procedure.pronamespace
                 WHERE namespace.nspname = 'rate_control'
                   AND procedure.proname IN (
                     'acquire_fencing_lease',
                     'reserve_permit_v2',
                     'consume_permit_v2',
                     'record_gateway_message',
                     'read_startup_measurements',
                     'read_startup_observations'
                   )
            LOOP
                EXECUTE format(
                  'GRANT EXECUTE ON FUNCTION %s TO aiq_rate_authority',
                  function_identity
                );
            END LOOP;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        GRANT SELECT ON ALL TABLES IN SCHEMA rate_control
          TO aiq_rate_authority;
        GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA rate_control
          TO aiq_rate_authority;
        DROP FUNCTION rate_control.read_startup_observations(varchar[],timestamptz);
        DROP FUNCTION rate_control.read_startup_measurements();
        """
    )
