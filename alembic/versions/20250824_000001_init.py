# alembic/versions/20250824_000001_init.py
"""initial schema

Revision ID: 000001_init
Revises:
Create Date: 2025-08-24 00:00:01
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '000001_init'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('stations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(128), nullable=False, unique=True),
        sa.Column('password_hash', sa.String(256), nullable=False),
        sa.Column('token_salt', sa.String(64), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(), nullable=True),
        sa.Column('last_default_origin', sa.String(8), nullable=True),
        sa.Column('last_origin_lat', sa.Float(), nullable=True),
        sa.Column('last_origin_lon', sa.Float(), nullable=True),
    )
    op.create_table('snapshots',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('station_id', sa.Integer(), sa.ForeignKey('stations.id'), nullable=False),
        sa.Column('generated_at', sa.DateTime(), nullable=False),
        sa.Column('window_hours', sa.Integer(), nullable=False, server_default='24'),
        sa.Column('inventory_last_update', sa.String(64), nullable=True),
        sa.UniqueConstraint('station_id','generated_at', name='uq_snap_station_gen')
    )
    op.create_index('ix_snapshot_generated_at', 'snapshots', ['generated_at'])

    op.create_table('flows',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('snapshot_id', sa.Integer(), sa.ForeignKey('snapshots.id'), nullable=False),
        sa.Column('origin', sa.String(8), nullable=False),
        sa.Column('dest', sa.String(8), nullable=False),
        sa.Column('direction', sa.String(10), nullable=False),
        sa.Column('legs', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('weight_lbs', sa.Float(), nullable=False, server_default='0'),
    )
    op.create_index('ix_flows_route_dir', 'flows', ['origin','dest','direction'])
    op.create_index('ix_flows_snapshot', 'flows', ['snapshot_id'])

    op.create_table('flights',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('station_id', sa.Integer(), sa.ForeignKey('stations.id'), nullable=False),
        sa.Column('aoct_flight_id', sa.Integer(), nullable=True),
        sa.Column('flight_code', sa.String(32), nullable=True, unique=True),
        sa.Column('tail', sa.String(16), nullable=True),
        sa.Column('direction', sa.String(10), nullable=True),
        sa.Column('origin', sa.String(8), nullable=True),
        sa.Column('dest', sa.String(8), nullable=True),
        sa.Column('cargo_type', sa.String(128), nullable=True),
        sa.Column('cargo_weight_lbs', sa.Float(), nullable=True),
        sa.Column('takeoff_hhmm', sa.String(4), nullable=True),
        sa.Column('eta_hhmm', sa.String(4), nullable=True),
        sa.Column('is_ramp_entry', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('complete', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('remarks', sa.Text(), nullable=True),
        sa.Column('first_seen_at', sa.DateTime(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_flights_origin_dest_dir', 'flights', ['origin','dest','direction'])
    op.create_index('ix_flights_complete_seen', 'flights', ['complete','last_seen_at'])
    op.create_index('ix_flights_station_aoct', 'flights', ['station_id','aoct_flight_id'])

    op.create_table('ingest_log',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('station_id', sa.Integer(), sa.ForeignKey('stations.id'), nullable=False),
        sa.Column('received_at', sa.DateTime(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('raw', sa.Text(), nullable=True),
    )

    op.create_table('airports',
        sa.Column('code', sa.String(8), primary_key=True),
        sa.Column('lat', sa.Float(), nullable=False),
        sa.Column('lon', sa.Float(), nullable=False),
    )
    op.create_index('ix_airports_code', 'airports', ['code'])

def downgrade():
    op.drop_table('airports')
    op.drop_table('ingest_log')
    op.drop_index('ix_flights_station_aoct', table_name='flights')
    op.drop_index('ix_flights_complete_seen', table_name='flights')
    op.drop_index('ix_flights_origin_dest_dir', table_name='flights')
    op.drop_table('flights')
    op.drop_index('ix_flows_snapshot', table_name='flows')
    op.drop_index('ix_flows_route_dir', table_name='flows')
    op.drop_table('flows')
    op.drop_index('ix_snapshot_generated_at', table_name='snapshots')
    op.drop_table('snapshots')
    op.drop_table('stations')
