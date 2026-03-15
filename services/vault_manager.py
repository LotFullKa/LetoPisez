from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Set, Tuple

import yaml

from config.settings import settings
from services.entities import (
    ItemEntity,
    LocationEntity,
    NPCEntity,
    ParsedLog,
    QuestEntity,
)
from services.gemini_client import gemini_client


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


SECTION_HEADERS = ("## Описание", "## История", "## Связанные")


def _parse_body_sections(body: str) -> Tuple[str, Dict[str, str]]:
    """
    Split body into title block (first # line and any lines before first ##)
    and sections: Описание, История, Связанные.
    Returns (title_block, sections dict).
    """
    lines = body.splitlines(keepends=True)
    title_parts: List[str] = []
    sections: Dict[str, str] = {"Описание": "", "История": "", "Связанные": ""}
    current: str | None = None
    current_lines: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped == "## Описание":
            if current is not None:
                sections[current] = "".join(current_lines).strip()
            current = "Описание"
            current_lines = []
        elif stripped == "## История":
            if current is not None:
                sections[current] = "".join(current_lines).strip()
            current = "История"
            current_lines = []
        elif stripped == "## Связанные":
            if current is not None:
                sections[current] = "".join(current_lines).strip()
            current = "Связанные"
            current_lines = []
        elif current is not None:
            current_lines.append(line)
        else:
            title_parts.append(line)
    if current is not None:
        sections[current] = "".join(current_lines).strip()
    title_block = "".join(title_parts).strip()
    return title_block, sections


def _build_body_from_sections(title_block: str, sections: Dict[str, str]) -> str:
    """Build body from title and sections. Only include sections that have content."""
    parts: List[str] = [title_block] if title_block else []
    for header in SECTION_HEADERS:
        key = header[3:].strip()  # "## Описание" -> "Описание"
        content = sections.get(key, "").strip()
        if content:
            parts.append(header)
            parts.append("")
            parts.append(content)
            parts.append("")
    return "\n".join(parts).rstrip("\n") + "\n" if parts else ""


def _inject_wiki_links(text: str, known_names: List[str]) -> str:
    """
    Replace exact occurrences of known entity names with [[name]] wiki-links.
    Uses placeholders to avoid double-wrapping. Longest names first to avoid
    partial replacements.
    """
    if not text or not known_names:
        return text
    # Use placeholders so we don't replace inside already-wrapped [[...]]
    placeholders: Dict[str, str] = {}
    for name in sorted(known_names, key=lambda n: -len(n)):
        if name and name in text:
            placeholder = "\u0001WIKI_" + name + "\u0001"
            placeholders[placeholder] = _wiki(name)
            text = text.replace(name, placeholder)
    for placeholder, wiki in placeholders.items():
        text = text.replace(placeholder, wiki)
    return text


def _parse_related_links(section_content: str) -> Set[str]:
    """Extract linked names from Связанные section (lines with [[name]])."""
    if not section_content:
        return set()
    return set(re.findall(r"\[\[([^\]]+)\]\]", section_content))


def _format_related_section(related_names: Set[str]) -> str:
    """Format a set of names as markdown list with wiki links."""
    return "\n".join(f"- {_wiki(name)}" for name in sorted(related_names))


class VaultManager:
    def __init__(self, root: Path) -> None:
        self.store = FileStore(root=root)

    def apply_parsed_log(self, parsed: ParsedLog) -> Path:
        parsed.raw_text = parsed.raw_text or ""
        session_date = parsed.session_date or datetime.utcnow().date()
        known_names: List[str] = []
        for n in parsed.npcs:
            known_names.append(n.name)
        for loc in parsed.locations:
            known_names.append(loc.name)
        for q in parsed.quests:
            known_names.append(q.name)
        for it in parsed.items:
            known_names.append(it.name)
        known_names = list(dict.fromkeys(known_names))

        for npc in parsed.npcs:
            self._upsert_npc(npc, session_date, known_names)
        for loc in parsed.locations:
            self._upsert_location(loc, session_date, known_names)
        for quest in parsed.quests:
            self._upsert_quest(quest, known_names)
        for item in parsed.items:
            self._upsert_item(item, known_names)

        session_path = self._write_session_log(parsed)
        return session_path

    def collect_campaign_corpus(self) -> str:
        """
        Собрать текстовую «летопись» кампании из файлов SessionLogs.

        Сейчас для простоты берём целиком содержимое markdown-файлов сессий.
        При необходимости можно сузить до секций «Сырой лог» и т.п.
        """
        session_dir = self.store.resolve("SessionLogs")
        if not session_dir.exists() or not session_dir.is_dir():
            return ""

        parts: list[str] = []
        for path in sorted(session_dir.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if text.strip():
                parts.append(text)

        return "\n\n---\n\n".join(parts)

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
            inline_parts: list[str] = []
            if parsed.npcs:
                inline_parts.append("Участвовали: " + ", ".join(_wiki(n.name) for n in parsed.npcs))
            if parsed.locations:
                inline_parts.append("Локации: " + ", ".join(_wiki(l.name) for l in parsed.locations))
            if parsed.quests:
                inline_parts.append("Квесты: " + ", ".join(_wiki(q.name) for q in parsed.quests))
            if parsed.items:
                inline_parts.append("Предметы: " + ", ".join(_wiki(i.name) for i in parsed.items))
            if inline_parts:
                lines.append(" ".join(inline_parts))
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

    def _upsert_npc(
        self,
        npc: NPCEntity,
        session_date: date | None,
        known_names: List[str],
    ) -> None:
        rel_path = Path("NPCs") / f"{_sanitize_name(npc.name)}.md"
        self._ensure_index("NPCs", 'TABLE status FROM "NPCs" WHERE type = "npc"')
        existing = self.store.read(rel_path) or ""
        if existing:
            frontmatter, body = _split_frontmatter(existing)
            title_block, sections = _parse_body_sections(body)
            desc = sections.get("Описание", "").strip()
            if npc.description:
                desc = _inject_wiki_links(npc.description, known_names)
            sections["Описание"] = desc or sections.get("Описание", "")
            if npc.history_snippet and session_date:
                date_str = session_date.isoformat()
                new_block = f"### {date_str}\n\n" + _inject_wiki_links(npc.history_snippet, known_names) + "\n\n"
                sections["История"] = (sections.get("История", "").strip() + "\n\n" + new_block).strip()
            existing_related = _parse_related_links(sections.get("Связанные", ""))
            new_related = set()
            if npc.related_npcs:
                new_related.update(npc.related_npcs)
            if npc.links_to_locations:
                new_related.update(npc.links_to_locations)
            sections["Связанные"] = _format_related_section(existing_related | new_related)
        else:
            frontmatter = {}
            title_block = f"# {npc.name}"
            desc = _inject_wiki_links(npc.description or "", known_names)
            sections = {"Описание": desc, "История": "", "Связанные": ""}
            if npc.history_snippet and session_date:
                date_str = session_date.isoformat()
                sections["История"] = f"### {date_str}\n\n" + _inject_wiki_links(npc.history_snippet, known_names)
            related = set()
            if npc.related_npcs:
                related.update(npc.related_npcs)
            if npc.links_to_locations:
                related.update(npc.links_to_locations)
            sections["Связанные"] = _format_related_section(related)
        frontmatter.setdefault("type", "npc")
        tags = list(frontmatter.get("tags") or [])
        if "npc" not in tags:
            tags.append("npc")
        frontmatter["tags"] = sorted(dict.fromkeys(tags))
        if npc.status:
            frontmatter["status"] = npc.status
        body = _build_body_from_sections(title_block, sections)
        self.store.write_atomic(rel_path, _build_markdown(frontmatter, body))

    def _upsert_location(
        self,
        loc: LocationEntity,
        session_date: date | None,
        known_names: List[str],
    ) -> None:
        rel_path = Path("Locations") / f"{_sanitize_name(loc.name)}.md"
        self._ensure_index(
            "Locations",
            'TABLE status FROM "Locations" WHERE type = "location"',
        )
        existing = self.store.read(rel_path) or ""
        if existing:
            frontmatter, body = _split_frontmatter(existing)
            title_block, sections = _parse_body_sections(body)
            if loc.description:
                sections["Описание"] = _inject_wiki_links(loc.description, known_names)
            else:
                sections["Описание"] = sections.get("Описание", "")
            if loc.history_snippet and session_date:
                date_str = session_date.isoformat()
                new_block = f"### {date_str}\n\n" + _inject_wiki_links(loc.history_snippet, known_names) + "\n\n"
                sections["История"] = (sections.get("История", "").strip() + "\n\n" + new_block).strip()
            existing_related = _parse_related_links(sections.get("Связанные", ""))
            new_related = set()
            if loc.related_npcs:
                new_related.update(loc.related_npcs)
            if loc.related_locations:
                new_related.update(loc.related_locations)
            sections["Связанные"] = _format_related_section(existing_related | new_related)
        else:
            frontmatter = {}
            title_block = f"# {loc.name}"
            desc = _inject_wiki_links(loc.description or "", known_names)
            sections = {"Описание": desc, "История": "", "Связанные": ""}
            if loc.history_snippet and session_date:
                date_str = session_date.isoformat()
                sections["История"] = f"### {date_str}\n\n" + _inject_wiki_links(loc.history_snippet, known_names)
            related = set()
            if loc.related_npcs:
                related.update(loc.related_npcs)
            if loc.related_locations:
                related.update(loc.related_locations)
            sections["Связанные"] = _format_related_section(related)
        frontmatter.setdefault("type", "location")
        tags = list(frontmatter.get("tags") or [])
        if "location" not in tags:
            tags.append("location")
        frontmatter["tags"] = sorted(dict.fromkeys(tags))
        if loc.status:
            frontmatter["status"] = loc.status
        body = _build_body_from_sections(title_block, sections)
        self.store.write_atomic(rel_path, _build_markdown(frontmatter, body))

    def _upsert_quest(self, quest: QuestEntity, known_names: List[str]) -> None:
        rel_path = Path("Quests") / f"{_sanitize_name(quest.name)}.md"
        self._ensure_index(
            "Quests",
            'TABLE status FROM "Quests" WHERE type = "quest"',
        )
        existing = self.store.read(rel_path) or ""
        if existing:
            frontmatter, body = _split_frontmatter(existing)
            title_block, sections = _parse_body_sections(body)
            if quest.summary:
                sections["Описание"] = _inject_wiki_links(quest.summary, known_names)
            else:
                sections["Описание"] = sections.get("Описание", "")
            existing_related = _parse_related_links(sections.get("Связанные", ""))
            related = set(existing_related)
            if quest.related_npcs:
                related.update(quest.related_npcs)
            if quest.related_locations:
                related.update(quest.related_locations)
            sections["Связанные"] = _format_related_section(related)
        else:
            frontmatter = {}
            title_block = f"# {quest.name}"
            desc = _inject_wiki_links(quest.summary or "", known_names)
            sections = {"Описание": desc, "История": "", "Связанные": ""}
            related = set()
            if quest.related_npcs:
                related.update(quest.related_npcs)
            if quest.related_locations:
                related.update(quest.related_locations)
            sections["Связанные"] = _format_related_section(related)
        frontmatter.setdefault("type", "quest")
        tags = list(frontmatter.get("tags") or [])
        if "quest" not in tags:
            tags.append("quest")
        frontmatter["tags"] = sorted(dict.fromkeys(tags))
        if quest.status:
            frontmatter["status"] = quest.status
        body = _build_body_from_sections(title_block, sections)
        self.store.write_atomic(rel_path, _build_markdown(frontmatter, body))

    def _upsert_item(self, item: ItemEntity, known_names: List[str]) -> None:
        rel_path = Path("Items") / f"{_sanitize_name(item.name)}.md"
        self._ensure_index(
            "Items",
            'TABLE status FROM "Items" WHERE type = "item"',
        )
        existing = self.store.read(rel_path) or ""
        if existing:
            frontmatter, body = _split_frontmatter(existing)
            title_block, sections = _parse_body_sections(body)
            if item.description:
                sections["Описание"] = _inject_wiki_links(item.description, known_names)
            else:
                sections["Описание"] = sections.get("Описание", "")
            related = _parse_related_links(sections.get("Связанные", ""))
            if item.owner:
                related.add(item.owner)
            if item.related_npcs:
                related.update(item.related_npcs)
            sections["Связанные"] = _format_related_section(related)
        else:
            frontmatter = {}
            title_block = f"# {item.name}"
            desc = _inject_wiki_links(item.description or "", known_names)
            if item.owner:
                desc = (desc + "\n\nВладелец: " + _wiki(item.owner)).strip() if desc else ("Владелец: " + _wiki(item.owner))
            sections = {"Описание": desc, "История": "", "Связанные": ""}
            related = set()
            if item.owner:
                related.add(item.owner)
            if item.related_npcs:
                related.update(item.related_npcs)
            sections["Связанные"] = _format_related_section(related)
        frontmatter.setdefault("type", "item")
        tags = list(frontmatter.get("tags") or [])
        if "item" not in tags:
            tags.append("item")
        frontmatter["tags"] = sorted(dict.fromkeys(tags))
        if item.status:
            frontmatter["status"] = item.status
        body = _build_body_from_sections(title_block, sections)
        self.store.write_atomic(rel_path, _build_markdown(frontmatter, body))

    def list_entity_names(self, folder: str) -> List[str]:
        """List display names of entities in a vault folder (e.g. NPCs, Locations)."""
        dir_path = self.store.resolve(folder)
        if not dir_path.exists() or not dir_path.is_dir():
            return []
        names: List[str] = []
        for path in sorted(dir_path.glob("*.md")):
            if path.name.startswith("_"):
                continue
            content = self.store.read(Path(folder) / path.name)
            if not content:
                continue
            _, body = _split_frontmatter(content)
            lines = body.strip().splitlines()
            for line in lines:
                line = line.strip()
                if line.startswith("# ") and not line.startswith("## "):
                    name = line[2:].strip()
                    if name:
                        names.append(name)
                    break
        return names

    def _collect_all_entity_names(self) -> List[str]:
        """Collect names from NPCs, Locations, Quests, Items for wiki-link injection."""
        names: List[str] = []
        for folder in ("NPCs", "Locations", "Quests", "Items"):
            names.extend(self.list_entity_names(folder))
        return list(dict.fromkeys(names))

    def refresh_descriptions_from_corpus(
        self,
        corpus: str,
        entity_type: str,
    ) -> int:
        """
        Update entity descriptions (and Связанные) from campaign corpus via Gemini.
        entity_type is "npc" or "location". Returns number of files updated.
        """
        folder = "NPCs" if entity_type == "npc" else "Locations"
        names = self.list_entity_names(folder)
        if not names:
            return 0
        try:
            summaries = gemini_client.update_entity_summaries(corpus, entity_type, names)
        except Exception:
            return 0
        all_names = self._collect_all_entity_names()
        updated = 0
        for item in summaries:
            name = item.get("name")
            if not name:
                continue
            rel_path = Path(folder) / f"{_sanitize_name(name)}.md"
            existing = self.store.read(rel_path)
            if not existing:
                continue
            frontmatter, body = _split_frontmatter(existing)
            title_block, sections = _parse_body_sections(body)
            desc = item.get("updated_description", "")
            sections["Описание"] = _inject_wiki_links(desc, all_names)
            related = set()
            for r in item.get("related_npcs") or []:
                related.add(r)
            for r in item.get("related_locations") or []:
                related.add(r)
            existing_related = _parse_related_links(sections.get("Связанные", ""))
            sections["Связанные"] = _format_related_section(existing_related | related)
            body = _build_body_from_sections(title_block, sections)
            self.store.write_atomic(rel_path, _build_markdown(frontmatter, body))
            updated += 1
        return updated

vault_manager = VaultManager(settings.vault_path)

