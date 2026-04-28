"""Auth DTOs — login + refresh request/response shapes."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    """POST /auth/login body."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=2, max_length=50)
    password: str = Field(min_length=4, max_length=128)


class TokenResponse(BaseModel):
    """Token issuance response (login + refresh both return this)."""

    access_token: str = Field(serialization_alias="accessToken")
    refresh_token: str = Field(serialization_alias="refreshToken")
    token_type: str = Field(default="Bearer", serialization_alias="tokenType")
    expires_in: int = Field(serialization_alias="expiresIn")  # seconds


class RefreshRequest(BaseModel):
    """POST /auth/refresh body."""

    # populate_by_name=True : accept BOTH `refreshToken` (camelCase, mirrors the
    # Java side's JSON contract) AND `refresh_token` (snake_case, Python idiom
    # for tests using kwargs). Without it, Pydantic raises AttributeError on
    # `body.refresh_token` access when alias is set.
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    refresh_token: str = Field(min_length=1, alias="refreshToken")
