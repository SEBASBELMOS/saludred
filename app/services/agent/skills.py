"""Index and on-demand loading of skills (``skills/<name>/SKILL.xml``).

A skill file looks like::

    <skill name="..." description="...">
      <titulo>...</titulo>
      <paso n="1">...</paso>
      <nota>...</nota>
    </skill>

Only ``name`` + ``description`` go into the system prompt; ``cargar_skill`` returns
the body rendered as plain numbered text. The files ship with the code (trusted
input), so the standard library parser is enough; a file that does not parse or
does not follow the format is skipped with a warning, never a crash.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

FOLDER = Path(__file__).parent / "skills"
FILE_NAME = "SKILL.xml"
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    path: Path


def _render(root: ET.Element) -> str:
    lines: list[str] = []
    title = (root.findtext("titulo") or "").strip()
    if title:
        lines += [title, ""]
    steps = sorted(root.findall("paso"), key=lambda p: int(p.get("n", "0")))
    lines += [f"{p.get('n')}. {' '.join((p.text or '').split())}" for p in steps]
    notes = [" ".join((n.text or "").split()) for n in root.findall("nota")]
    if notes:
        lines += ["", "Notas:", *[f"- {n}" for n in notes]]
    return "\n".join(lines)


def _parse(path: Path) -> Skill:
    root = ET.parse(path).getroot()
    if root.tag != "skill":
        raise ValueError("root element must be <skill>")
    name = (root.get("name") or "").strip()
    description = (root.get("description") or "").strip()
    if not _NAME.match(name):
        raise ValueError(f"invalid skill name {name!r}")
    if not description:
        raise ValueError("missing description")
    steps = root.findall("paso")
    if not steps or any(not (p.get("n") or "").isdigit() or not (p.text or "").strip() for p in steps):
        raise ValueError("steps must be <paso n=\"1\">text</paso>")
    return Skill(name, description, _render(root), path)


@lru_cache(maxsize=1)
def catalog() -> dict[str, Skill]:
    """Skill name -> skill. Built from the folder, never from model text."""

    skills: dict[str, Skill] = {}
    for path in sorted(FOLDER.glob(f"*/{FILE_NAME}")):
        try:
            skill = _parse(path)
        except (ET.ParseError, ValueError, OSError) as error:
            logger.warning("Skill %s skipped: %s", path.parent.name, error)
            continue
        if skill.name in skills:
            logger.warning("Skill %s skipped: duplicated name %s", path.parent.name, skill.name)
            continue
        skills[skill.name] = skill
    return skills


def index() -> str:
    """One line per skill: the only part that lives in the system prompt."""

    return "\n".join(f"- {s.name}: {s.description}" for s in catalog().values())


def load(name: str) -> str:
    """Rendered body of a skill. A path is never built from ``name``: it is a lookup key.

    The pattern check rejects ``../``, separators and absolute paths before the
    lookup; the file the catalog found must also sit inside the skills folder.
    """

    available = catalog()
    if not isinstance(name, str) or not _NAME.match(name) or name not in available:
        return f"SKILL_NO_EXISTE: use una de {list(available)}"
    skill = available[name]
    if FOLDER.resolve() not in skill.path.resolve().parents:
        return f"SKILL_NO_EXISTE: use una de {list(available)}"
    return skill.body
