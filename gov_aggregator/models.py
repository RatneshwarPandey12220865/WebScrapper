from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from gov_aggregator.database import Base


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    site_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    ministry: Mapped[str] = mapped_column(String(200), index=True)
    name: Mapped[str] = mapped_column(String(200))
    source_url: Mapped[str] = mapped_column(String(1000))
    base_url: Mapped[str] = mapped_column(String(1000))
    parser: Mapped[str] = mapped_column(String(50))
    render_js: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    items: Mapped[list["NewsItem"]] = relationship(back_populates="site", cascade="all, delete-orphan")


class NewsItem(Base):
    __tablename__ = "news_items"
    __table_args__ = (UniqueConstraint("site_id", "normalized_title", name="uq_site_title"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    ministry: Mapped[str] = mapped_column(String(200), index=True)
    site_key: Mapped[str] = mapped_column(String(120), index=True)
    title: Mapped[str] = mapped_column(String(1000))
    normalized_title: Mapped[str] = mapped_column(String(1000), index=True)
    link: Mapped[str] = mapped_column(String(1200))
    source_url: Mapped[str] = mapped_column(String(1200))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    is_pdf: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    site: Mapped[Site] = relationship(back_populates="items")
