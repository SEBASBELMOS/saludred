"""Shared Pydantic building blocks used by every API response."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageParams(BaseModel):
    """Pagination request parameters, validated at the dependency layer."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class Page(BaseModel, Generic[T]):
    """Envelope for paginated list responses."""

    items: list[T]
    total: int
    page: int
    page_size: int
