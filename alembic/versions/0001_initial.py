"""初期スキーマ（PostGIS 拡張・全テーブル）

Revision ID: 0001
Revises: 
Create Date: 2026-09-27 04:55:44.319336
"""
from alembic import op
import sqlalchemy as sa
import geoalchemy2
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table('admin_users',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('totp_secret', sa.String(length=64), nullable=True),
    sa.Column('totp_enabled', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('username')
    )
    op.create_table('courses',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('slug', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=80), nullable=False),
    sa.Column('description', sa.String(length=500), nullable=False),
    sa.Column('is_public', sa.Boolean(), nullable=False),
    sa.Column('is_featured', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_table('daily_challenges',
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('sound_ids', postgresql.ARRAY(sa.String(length=16)), nullable=False),
    sa.PrimaryKeyConstraint('date')
    )
    op.create_table('daily_stats',
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('posts', sa.Integer(), nullable=False),
    sa.Column('plays', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('date')
    )
    op.create_table('fingerprints',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('sound_id', sa.String(length=16), nullable=False),
    sa.Column('duration_sec', sa.Float(), nullable=False),
    sa.Column('raw', postgresql.ARRAY(sa.Integer()), nullable=False),
    sa.Column('keys', postgresql.ARRAY(sa.Integer()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_fingerprints_keys', 'fingerprints', ['keys'], unique=False, postgresql_using='gin')
    op.create_index(op.f('ix_fingerprints_sound_id'), 'fingerprints', ['sound_id'], unique=False)
    op.create_table('leaderboard',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('nickname', sa.String(length=20), nullable=False),
    sa.Column('score', sa.Integer(), nullable=False),
    sa.Column('ip_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('date', 'ip_hash', name='uq_leaderboard_date_ip')
    )
    op.create_index('ix_leaderboard_date_score', 'leaderboard', ['date', 'score'], unique=False)
    op.create_table('ng_words',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('pattern', sa.String(length=200), nullable=False),
    sa.Column('is_regex', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('reporter_trust',
    sa.Column('ip_hash', sa.String(length=64), nullable=False),
    sa.Column('upheld', sa.Integer(), nullable=False),
    sa.Column('dismissed', sa.Integer(), nullable=False),
    sa.Column('trust', sa.Float(), nullable=False),
    sa.PrimaryKeyConstraint('ip_hash')
    )
    op.create_table('settings',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('sounds',
    sa.Column('id', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('title', sa.String(length=40), nullable=False),
    sa.Column('comment', sa.String(length=200), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('time_of_day', sa.String(length=16), nullable=True),
    sa.Column('weather', sa.String(length=16), nullable=True),
    sa.Column('season', sa.String(length=16), nullable=True),
    sa.Column('tags', postgresql.ARRAY(sa.String(length=16)), nullable=False),
    sa.Column('direction', sa.SmallInteger(), nullable=True),
    sa.Column('license', sa.String(length=16), nullable=False),
    sa.Column('location', geoalchemy2.types.Geography(geometry_type='POINT', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeogFromText', name='geography', nullable=False), nullable=False),
    sa.Column('lat', sa.Float(), nullable=False),
    sa.Column('lng', sa.Float(), nullable=False),
    sa.Column('precision', sa.String(length=8), nullable=False),
    sa.Column('muni_code', sa.String(length=8), nullable=True),
    sa.Column('pref_code', sa.SmallInteger(), nullable=True),
    sa.Column('pref_name', sa.String(length=8), nullable=True),
    sa.Column('city_name', sa.String(length=32), nullable=True),
    sa.Column('duration_sec', sa.Float(), nullable=True),
    sa.Column('webm_hash', sa.String(length=20), nullable=True),
    sa.Column('m4a_hash', sa.String(length=20), nullable=True),
    sa.Column('peaks_hash', sa.String(length=20), nullable=True),
    sa.Column('spectrogram_hash', sa.String(length=20), nullable=True),
    sa.Column('ogp_hash', sa.String(length=20), nullable=True),
    sa.Column('voice_ratio', sa.Float(), nullable=True),
    sa.Column('voice_flag', sa.Boolean(), nullable=False),
    sa.Column('clipping_ratio', sa.Float(), nullable=True),
    sa.Column('clipping_warning', sa.Boolean(), nullable=False),
    sa.Column('silence_ratio', sa.Float(), nullable=True),
    sa.Column('loudness_lufs', sa.Float(), nullable=True),
    sa.Column('game_ok', sa.Boolean(), nullable=False),
    sa.Column('play_count', sa.Integer(), nullable=False),
    sa.Column('nearby_count', sa.Integer(), nullable=False),
    sa.Column('rand', sa.Float(), server_default=sa.text('random()'), nullable=False),
    sa.Column('hidden_reason', sa.String(length=32), nullable=True),
    sa.Column('reject_reason', sa.String(length=64), nullable=True),
    sa.Column('delete_token_hash', sa.String(length=64), nullable=False),
    sa.Column('ip_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_sounds_deleted_at', 'sounds', ['deleted_at'], unique=False, postgresql_where=sa.text("status = 'deleted'"))
    op.create_index('ix_sounds_game_ok', 'sounds', ['rand'], unique=False, postgresql_where=sa.text("status = 'published' AND game_ok"))
    op.create_index('ix_sounds_ip_hash', 'sounds', ['ip_hash'], unique=False)
    op.create_index('ix_sounds_location_all', 'sounds', ['location'], unique=False, postgresql_using='gist')
    op.create_index('ix_sounds_location_published', 'sounds', ['location'], unique=False, postgresql_using='gist', postgresql_where=sa.text("status = 'published'"))
    op.create_index('ix_sounds_pref_status', 'sounds', ['pref_code', 'status', 'published_at'], unique=False)
    op.create_index('ix_sounds_published_plays', 'sounds', ['play_count'], unique=False, postgresql_where=sa.text("status = 'published'"))
    op.create_index('ix_sounds_published_rand', 'sounds', ['rand'], unique=False, postgresql_where=sa.text("status = 'published'"))
    op.create_index('ix_sounds_status_created', 'sounds', ['status', 'created_at'], unique=False)
    op.create_index('ix_sounds_tags', 'sounds', ['tags'], unique=False, postgresql_using='gin')
    op.create_table('audit_logs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('admin_id', sa.Integer(), nullable=True),
    sa.Column('admin_username', sa.String(length=64), nullable=False),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('target_type', sa.String(length=32), nullable=True),
    sa.Column('target_id', sa.String(length=64), nullable=True),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('ip_hash', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['admin_id'], ['admin_users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_audit_action', 'audit_logs', ['action'], unique=False)
    op.create_index('ix_audit_created', 'audit_logs', ['created_at'], unique=False)
    op.create_table('bans',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('ip_hash', sa.String(length=64), nullable=False),
    sa.Column('reason', sa.String(length=200), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('lifted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['admin_users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_bans_ip_hash'), 'bans', ['ip_hash'], unique=False)
    op.create_table('course_items',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('course_id', sa.Integer(), nullable=False),
    sa.Column('sound_id', sa.String(length=16), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('note', sa.String(length=200), nullable=False),
    sa.ForeignKeyConstraint(['course_id'], ['courses.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['sound_id'], ['sounds.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_course_items_course_pos', 'course_items', ['course_id', 'position'], unique=False)
    op.create_table('processing_jobs',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('sound_id', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('upload_path', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['sound_id'], ['sounds.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_jobs_sound', 'processing_jobs', ['sound_id'], unique=False)
    op.create_index('ix_jobs_status_created', 'processing_jobs', ['status', 'created_at'], unique=False)
    op.create_table('reports',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('sound_id', sa.String(length=16), nullable=False),
    sa.Column('ip_hash', sa.String(length=64), nullable=False),
    sa.Column('reason', sa.String(length=32), nullable=False),
    sa.Column('detail', sa.String(length=200), nullable=False),
    sa.Column('weight', sa.Float(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_by', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['resolved_by'], ['admin_users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['sound_id'], ['sounds.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('sound_id', 'ip_hash', name='uq_reports_sound_ip')
    )
    op.create_index('ix_reports_status_sound', 'reports', ['status', 'sound_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_reports_status_sound', table_name='reports')
    op.drop_table('reports')
    op.drop_index('ix_jobs_status_created', table_name='processing_jobs')
    op.drop_index('ix_jobs_sound', table_name='processing_jobs')
    op.drop_table('processing_jobs')
    op.drop_index('ix_course_items_course_pos', table_name='course_items')
    op.drop_table('course_items')
    op.drop_index(op.f('ix_bans_ip_hash'), table_name='bans')
    op.drop_table('bans')
    op.drop_index('ix_audit_created', table_name='audit_logs')
    op.drop_index('ix_audit_action', table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_index('ix_sounds_tags', table_name='sounds', postgresql_using='gin')
    op.drop_index('ix_sounds_status_created', table_name='sounds')
    op.drop_index('ix_sounds_published_rand', table_name='sounds', postgresql_where=sa.text("status = 'published'"))
    op.drop_index('ix_sounds_published_plays', table_name='sounds', postgresql_where=sa.text("status = 'published'"))
    op.drop_index('ix_sounds_pref_status', table_name='sounds')
    op.drop_index('ix_sounds_location_published', table_name='sounds', postgresql_using='gist', postgresql_where=sa.text("status = 'published'"))
    op.drop_index('ix_sounds_location_all', table_name='sounds', postgresql_using='gist')
    op.drop_index('ix_sounds_ip_hash', table_name='sounds')
    op.drop_index('ix_sounds_game_ok', table_name='sounds', postgresql_where=sa.text("status = 'published' AND game_ok"))
    op.drop_index('ix_sounds_deleted_at', table_name='sounds', postgresql_where=sa.text("status = 'deleted'"))
    op.drop_table('sounds')
    op.drop_table('settings')
    op.drop_table('reporter_trust')
    op.drop_table('ng_words')
    op.drop_index('ix_leaderboard_date_score', table_name='leaderboard')
    op.drop_table('leaderboard')
    op.drop_index(op.f('ix_fingerprints_sound_id'), table_name='fingerprints')
    op.drop_index('ix_fingerprints_keys', table_name='fingerprints', postgresql_using='gin')
    op.drop_table('fingerprints')
    op.drop_table('daily_stats')
    op.drop_table('daily_challenges')
    op.drop_table('courses')
    op.drop_table('admin_users')
