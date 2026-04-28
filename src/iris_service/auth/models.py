"""Auth ORM entities — AppUser + RefreshToken.

Mirrors Java's :
- `AppUser.java` — credentials + role storage
- `RefreshToken.java` — server-side refresh token tracking (rotation +
  revocation support)
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from iris_service.db.base import Base


class AppUser(Base):
    """`app_user` table — application user credentials."""

    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    # bcrypt hash, never plaintext (length 255 to accommodate any bcrypt cost)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # ROLE_ADMIN | ROLE_USER | ROLE_READER (mirrors Java enum-as-string pattern)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="ROLE_USER")
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)


class RefreshToken(Base):
    """`refresh_token` table — server-side refresh token registry.

    Each issued refresh token is recorded so it can be revoked or rotated
    on use (= Spring's RefreshToken entity in the Java mirror).
    """

    __tablename__ = "refresh_token"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(512), nullable=False, unique=True, index=True)
    username: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
