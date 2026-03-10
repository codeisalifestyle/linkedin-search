"""Data models for linkedin-search."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SearchType(str, Enum):
    STANDARD = "standard"
    COMPANY = "company"


class PersonProfile(BaseModel):
    """Normalized person profile record."""

    name: str = Field(min_length=1)
    headline: Optional[str] = None
    location: Optional[str] = None
    company: Optional[str] = None
    profile_url: str = Field(min_length=1)
    search_type: SearchType


class StandardSearchConfig(BaseModel):
    query: str = Field(min_length=1)
    max_results: int = Field(default=100, ge=1, le=1000)
    location: Optional[str] = None


class CompanySearchConfig(BaseModel):
    company_url: str = Field(min_length=1)
    max_results: int = Field(default=100, ge=1, le=1000)
    keyword: Optional[str] = None
    location: Optional[str] = None

