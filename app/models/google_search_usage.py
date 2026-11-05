from __future__ import annotations

from sqlalchemy import CheckConstraint, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class GoogleSearchUsage(Base):
    __tablename__ = "google_search_monthly_usage"
    __table_args__ = (
        CheckConstraint("requests_used >= 0", name="ck_google_search_usage_nonnegative"),
    )

    month: Mapped[str] = mapped_column(String(7), primary_key=True)
    requests_used: Mapped[int] = mapped_column(Integer, nullable=False)
