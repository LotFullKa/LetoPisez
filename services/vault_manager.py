from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple

import yaml

from config.settings import settings
from services.entities import (
    ItemEntity,
    LocationEntity,
    NPCEntity,
    ParsedLog,
    QuestEntity,
)


@dataclass
class FileStore:
    root: Path

    def resolve(self, relative: str | Path) -> Path:
        return (self.root / Path(relative)).resolve()

    def read(self, relative: str | Path) -> str | None:
        path = self.resolve(relative)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def write_atomic(self, relative: str | Path, content: str) -> None:
        path = self.resolve(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)


def _split_frontmatter(text: str) -> Tuple[Dict, str]:
    if not text.startswith("---"):
        return {}, text

    lines = text.splitlines(keepends=True)
    if not lines:
        return {}, text

    if not lines[0].lstrip().startswith("---"):
        return {}, text

    end_index = None
    for idx in range(1, len(lines)):
        if lines[idx].lstrip().startswith("---"):
            end_index = idx
            break

    if end_index is None:
        return {}, text

    frontmatter_raw = "".join(lines[1:end_index])
    body = "".join(lines[end_index + 1 :])

    data = yaml.safe_load(frontmatter_raw) or {}
    if not isinstance(data, dict):
        data = {}
    return data, body


def _build_markdown(frontmatter: Dict, body: str) -> str:
    fm_yaml = yaml.safe_dump(
        frontmatter,
        allow_unicode=True,
        sort_keys=False,
    )
    body = body.lstrip("\n")
    return f"---\n{fm_yaml}---\n\n{body}"


def _sanitize_name(name: str) -> str:
    cleaned = name.strip()
    for ch in ("/", "\\", ":", "*", "?", '"', "<", ">", "|"):
        cleaned = cleaned.replace(ch, "-")
    return cleaned or "unnamed"


def _wiki(name: str) -> str:
    return f"[[{name}]]"


class VaultManager:
    def __init__(self, root: Path) -> None:
        self.store = FileStore(root=root)

    def apply_parsed_log(self, parsed: ParsedLog) -> Path:
        parsed.raw_text = parsed.raw_text or ""

        for npc in parsed.npcs:
            self._upsert_npc(npc)
        for loc in parsed.locations:
            self._upsert_location(loc)
        for quest in parsed.quests:
            self._upsert_quest(quest)
        for item in parsed.items:
            self._upsert_item(item)

        session_path = self._write_session_log(parsed)
        return session_path

    def _write_session_log(self, parsed: ParsedLog) -> Path:
        session_dir = Path("SessionLogs")
        today = parsed.session_date or datetime.utcnow().date()
        timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        filename = f"{today.isoformat()}-{timestamp}.md"
        rel_path = session_dir / filename

        lines: list[str] = []
        lines.append(f"# Игровая сессия {today.isoformat()}")
        lines.append("")
        lines.append("## Сырой лог")
        lines.append("")
        lines.append(parsed.raw_text)
        lines.append("")

        if parsed.npcs or parsed.locations or parsed.quests or parsed.items:
            lines.append("## Сущности")
            lines.append("")

            if parsed.npcs:
                lines.append("### NPC")
                for npc in parsed.npcs:
                    lines.append(f"- {_wiki(npc.name)}")
                lines.append("")

            if parsed.locations:
                lines.append("### Локации")
                for loc in parsed.locations:
                    lines.append(f"- {_wiki(loc.name)}")
                lines.append("")

            if parsed.quests:
                lines.append("### Квесты")
                for quest in parsed.quests:
                    lines.append(f"- {_wiki(quest.name)}")
                lines.append("")

            if parsed.items:
                lines.append("### Предметы")
                for item in parsed.items:
                    lines.append(f"- {_wiki(item.name)}")
                lines.append("")

        content = "\n".join(lines)
        frontmatter = {
            "type": "session_log",
            "tags": ["session-log"],
            "date": today.isoformat(),
        }
        markdown = _build_markdown(frontmatter, content)
        self.store.write_atomic(rel_path, markdown)
        return self.store.resolve(rel_path)

    def _ensure_index(self, folder: str, query: str) -> None:
        rel_path = Path(folder) / "_Index.md"
        current = self.store.read(rel_path) or ""
        if query in current:
            return

        block = f"```dataview\n{query}\n```\n"
        if current.strip():
            new_content = current.rstrip() + "\n\n" + block
        else:
            new_content = block
        self.store.write_atomic(rel_path, new_content)

    def _upsert_npc(self, npc: NPCEntity) -> None:
        rel_path = Path("NPCs") / f"{_sanitize_name(npc.name)}.md"
        self._ensure_index("NPCs", 'TABLE status FROM "NPCs" WHERE type = "npc"')
        self._upsert_entity(
            rel_path,
            name=npc.name,
            description=npc.description,
            type_value="npc",
            default_tag="npc",
            status=npc.status,
        )

    def _upsert_location(self, loc: LocationEntity) -> None:
        rel_path = Path("Locations") / f"{_sanitize_name(loc.name)}.md"
        self._ensure_index(
            "Locations",
            'TABLE status FROM "Locations" WHERE type = "location"',
        )
        self._upsert_entity(
            rel_path,
            name=loc.name,
            description=loc.description,
            type_value="location",
            default_tag="location",
            status=loc.status,
        )

    def _upsert_quest(self, quest: QuestEntity) -> None:
        rel_path = Path("Quests") / f"{_sanitize_name(quest.name)}.md"
        self._ensure_index(
            "Quests",
            'TABLE status FROM "Quests" WHERE type = "quest"',
        )
        self._upsert_entity(
            rel_path,
            name=quest.name,
            description=quest.summary,
            type_value="quest",
            default_tag="quest",
            status=quest.status,
        )

    def _upsert_item(self, item: ItemEntity) -> None:
        rel_path = Path("Items") / f"{_sanitize_name(item.name)}.md"
        self._ensure_index(
            "Items",
            'TABLE status FROM "Items" WHERE type = "item"',
        )
        self._upsert_entity(
            rel_path,
            name=item.name,
            description=item.description,
            type_value="item",
            default_tag="item",
            status=item.status,
        )

    def _upsert_entity(
        self,
        rel_path: Path,
        *,
        name: str,
        description: str | None,
        type_value: str,
        default_tag: str,
        status: str | None,
    ) -> None:
        existing = self.store.read(rel_path) or ""
        if existing:
            frontmatter, body = _split_frontmatter(existing)
        else:
            frontmatter = {}
            body_lines = [f"# {name}", "", "## Описание", ""]
            if description:
                body_lines.append(description)
                body_lines.append("")
            body = "\n".join(body_lines)

        frontmatter.setdefault("type", type_value)
        tags = frontmatter.get("tags") or []
        if not isinstance(tags, list):
            tags = [str(tags)]
        if default_tag not in tags:
            tags.append(default_tag)
        frontmatter["tags"] = sorted(dict.fromkeys(tags))

        if status:
            frontmatter["status"] = status

        markdown = _build_markdown(frontmatter, body)
        self.store.write_atomic(rel_path, markdown)


vault_manager = VaultManager(settings.vault_path)

