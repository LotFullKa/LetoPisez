from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field


class NPCEntity(BaseModel):
    name: str
    description: Optional[str] = None
    status: Optional[str] = None
    tags: Optional[List[str]] = None
    links_to_locations: Optional[List[str]] = Field(default=None, alias="links_to_locations")
    related_npcs: Optional[List[str]] = None
    history_snippet: Optional[str] = None


class LocationEntity(BaseModel):
    name: str
    description: Optional[str] = None
    status: Optional[str] = None
    region: Optional[str] = None
    related_npcs: Optional[List[str]] = None
    related_locations: Optional[List[str]] = None
    history_snippet: Optional[str] = None


class QuestEntity(BaseModel):
    name: str
    summary: Optional[str] = None
    status: Optional[str] = None
    related_npcs: Optional[List[str]] = None
    related_locations: Optional[List[str]] = None


class ItemEntity(BaseModel):
    name: str
    description: Optional[str] = None
    owner: Optional[str] = None
    status: Optional[str] = None
    related_npcs: Optional[List[str]] = None


class ParsedLog(BaseModel):
    raw_text: str = ""
    session_date: Optional[date] = None
    npcs: List[NPCEntity] = Field(default_factory=list)
    locations: List[LocationEntity] = Field(default_factory=list)
    quests: List[QuestEntity] = Field(default_factory=list)
    items: List[ItemEntity] = Field(default_factory=list)

