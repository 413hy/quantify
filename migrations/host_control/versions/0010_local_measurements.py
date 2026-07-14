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


def downgrade() -> None:
    op.execute("DROP FUNCTION rate_control.read_startup_measurements()")
