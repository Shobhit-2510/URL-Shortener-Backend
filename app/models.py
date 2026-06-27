"""SQLAlchemy ORM models.

`links.id` is the monotonic counter that drives base62 code generation. We let
the database assign it (BIGSERIAL on Postgres, AUTOINCREMENT on SQLite) and
derive the code from it after flush — collision-free by construction.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Postgres uses BIGINT/BIGSERIAL; SQLite only autoincrements INTEGER PRIMARY KEY
# (its rowid alias), so the id column degrades to INTEGER there.
BigIntPK = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    pass


class Link(Base):
    __tablename__ = "links"

    # The counter. base62(id + code_offset) == code for auto-generated links.
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    long_url: Mapped[str] = mapped_column(Text, nullable=False)
    is_custom_alias: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Running total maintained asynchronously by the analytics worker.
    click_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)


class ClickStat(Base):
    """Per-day aggregated click counts, written only by the worker."""

    __tablename__ = "click_stats"

    link_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD
    count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)


Index("ix_click_stats_link_day", ClickStat.link_id, ClickStat.day)
