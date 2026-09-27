from __future__ import annotations

import datetime as dt
from typing import Any

from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class SoundStatus:
    PROCESSING = "processing"
    PUBLISHED = "published"
    PENDING = "pending_review"
    HIDDEN = "hidden"
    REJECTED = "rejected"
    FAILED = "failed"
    DELETED = "deleted"
    PURGED = "purged"

    ALL = (PROCESSING, PUBLISHED, PENDING, HIDDEN, REJECTED, FAILED, DELETED, PURGED)


class Sound(Base):
    __tablename__ = "sounds"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default=SoundStatus.PROCESSING, nullable=False)
    title: Mapped[str] = mapped_column(String(40), nullable=False)
    comment: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    recorded_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    time_of_day: Mapped[str | None] = mapped_column(String(16))
    weather: Mapped[str | None] = mapped_column(String(16))
    season: Mapped[str | None] = mapped_column(String(16))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(16)), default=list, nullable=False)
    direction: Mapped[int | None] = mapped_column(SmallInteger)
    license: Mapped[str] = mapped_column(String(16), nullable=False)

    location: Mapped[Any] = mapped_column(Geography(geometry_type="POINT", srid=4326, spatial_index=False))
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    precision: Mapped[str] = mapped_column(String(8), default="exact", nullable=False)

    muni_code: Mapped[str | None] = mapped_column(String(8))
    pref_code: Mapped[int | None] = mapped_column(SmallInteger)
    pref_name: Mapped[str | None] = mapped_column(String(8))
    city_name: Mapped[str | None] = mapped_column(String(32))

    duration_sec: Mapped[float | None] = mapped_column(Float)
    webm_hash: Mapped[str | None] = mapped_column(String(20))
    m4a_hash: Mapped[str | None] = mapped_column(String(20))
    peaks_hash: Mapped[str | None] = mapped_column(String(20))
    spectrogram_hash: Mapped[str | None] = mapped_column(String(20))
    ogp_hash: Mapped[str | None] = mapped_column(String(20))

    voice_ratio: Mapped[float | None] = mapped_column(Float)
    voice_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    clipping_ratio: Mapped[float | None] = mapped_column(Float)
    clipping_warning: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    silence_ratio: Mapped[float | None] = mapped_column(Float)
    loudness_lufs: Mapped[float | None] = mapped_column(Float)

    game_ok: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    play_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    nearby_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rand: Mapped[float] = mapped_column(Float, server_default=text("random()"), nullable=False)

    hidden_reason: Mapped[str | None] = mapped_column(String(32))
    reject_reason: Mapped[str | None] = mapped_column(String(64))
    delete_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    reports: Mapped[list[Report]] = relationship(back_populates="sound", lazy="raise")

    __table_args__ = (
        Index(
            "ix_sounds_location_published",
            "location",
            postgresql_using="gist",
            postgresql_where=text("status = 'published'"),
        ),
        Index("ix_sounds_location_all", "location", postgresql_using="gist"),
        Index("ix_sounds_status_created", "status", "created_at"),
        Index(
            "ix_sounds_published_plays",
            "play_count",
            postgresql_where=text("status = 'published'"),
        ),
        Index("ix_sounds_published_rand", "rand", postgresql_where=text("status = 'published'")),
        Index("ix_sounds_pref_status", "pref_code", "status", "published_at"),
        Index("ix_sounds_tags", "tags", postgresql_using="gin"),
        Index("ix_sounds_ip_hash", "ip_hash"),
        Index("ix_sounds_deleted_at", "deleted_at", postgresql_where=text("status = 'deleted'")),
        Index("ix_sounds_game_ok", "rand", postgresql_where=text("status = 'published' AND game_ok")),
    )

    @property
    def is_public(self) -> bool:
        return self.status == SoundStatus.PUBLISHED


class Fingerprint(Base):
    __tablename__ = "fingerprints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sound_id: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    duration_sec: Mapped[float] = mapped_column(Float, nullable=False)
    raw: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)
    keys: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (Index("ix_fingerprints_keys", "keys", postgresql_using="gin"),)


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    sound_id: Mapped[str] = mapped_column(ForeignKey("sounds.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    upload_path: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    __table_args__ = (
        Index("ix_jobs_status_created", "status", "created_at"),
        Index("ix_jobs_sound", "sound_id"),
    )


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sound_id: Mapped[str] = mapped_column(ForeignKey("sounds.id", ondelete="CASCADE"), nullable=False)
    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))

    sound: Mapped[Sound] = relationship(back_populates="reports", lazy="raise")

    __table_args__ = (
        UniqueConstraint("sound_id", "ip_hash", name="uq_reports_sound_ip"),
        Index("ix_reports_status_sound", "status", "sound_id"),
    )


class ReporterTrust(Base):
    __tablename__ = "reporter_trust"

    ip_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    upheld: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    dismissed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    trust: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)


class Ban(Base):
    __tablename__ = "bans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    lifted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class NgWord(Base):
    __tablename__ = "ng_words"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pattern: Mapped[str] = mapped_column(String(200), nullable=False)
    is_regex: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="moderator", nullable=False)
    totp_secret: Mapped[str | None] = mapped_column(String(64))
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    admin_username: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        Index("ix_audit_created", "created_at"),
        Index("ix_audit_action", "action"),
    )


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    items: Mapped[list[CourseItem]] = relationship(
        back_populates="course", order_by="CourseItem.position", lazy="raise", cascade="all, delete-orphan"
    )


class CourseItem(Base):
    __tablename__ = "course_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), nullable=False)
    sound_id: Mapped[str] = mapped_column(ForeignKey("sounds.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str] = mapped_column(String(200), default="", nullable=False)

    course: Mapped[Course] = relationship(back_populates="items", lazy="raise")
    sound: Mapped[Sound] = relationship(lazy="raise")

    __table_args__ = (Index("ix_course_items_course_pos", "course_id", "position"),)


class DailyChallenge(Base):
    __tablename__ = "daily_challenges"

    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    sound_ids: Mapped[list[str]] = mapped_column(ARRAY(String(16)), nullable=False)


class LeaderboardEntry(Base):
    __tablename__ = "leaderboard"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    nickname: Mapped[str] = mapped_column(String(20), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("date", "ip_hash", name="uq_leaderboard_date_ip"),
        Index("ix_leaderboard_date_score", "date", "score"),
    )


class DailyStat(Base):
    __tablename__ = "daily_stats"

    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    posts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    plays: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


__all__ = [
    "AdminUser",
    "AuditLog",
    "Ban",
    "Base",
    "Course",
    "CourseItem",
    "DailyChallenge",
    "DailyStat",
    "Fingerprint",
    "LeaderboardEntry",
    "NgWord",
    "ProcessingJob",
    "Report",
    "ReporterTrust",
    "Setting",
    "Sound",
    "SoundStatus",
    "func",
]
