"""Skill references (agx_skill) -> instruction annotation.

After selecting "skills to use for this task" in the unified input bar, the selected skills are rendered into a
note appended to the final instruction, so the executor knows which skill to read and whose steps to follow.

Design principles (consistent with the project's iron rules):
  * **Valid skills only**: the data source is realdata.skills(), whose SQL already carries `active_status = 1`;
    an archived/nonexistent skill_id is always an explicit error (fail-closed), never silently dropped --
    otherwise the user believes a skill was attached when none was: the worst kind of silent degradation.
  * **A pointer only, not the body**: the body can be long, so the instruction carries only skill_id + name +
    a one-line description, and explicitly requires the executor to call `agx_skill_get` before working --
    saving tokens and guaranteeing the latest body is read.
"""

from __future__ import annotations

from .realdata import skills as _all_skills

# cap on how many skills one task may reference (to keep the instruction from being flooded; exceeding it is an explicit error)
MAX_SKILLS = 8


class SkillRefError(Exception):
    """Skill reference resolution failed (carries a business error code; the route maps it to an HTTP error)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _dedupe(ids: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in ids:
        sid = str(raw or "").strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        out.append(sid)
    return out


def resolve_skill_block(skill_ids: list[str] | None) -> str:
    """Render the selected skills into a text block that can be appended to instruction (empty string when nothing is selected).

    Raises:
        SkillRefError: TOO_MANY_SKILLS / SKILL_NOT_FOUND (including retired ones).
"""
    ids = _dedupe(list(skill_ids or []))
    if not ids:
        return ""
    if len(ids) > MAX_SKILLS:
        raise SkillRefError(
            "TOO_MANY_SKILLS",
            f"单次最多引用 {MAX_SKILLS} 个技能（已选 {len(ids)} 个）",
        )

    # valid skills only (the SQL already filters active_status=1)
    index = {str(s.get("id")): s for s in _all_skills()}
    missing = [sid for sid in ids if sid not in index]
    if missing:
        raise SkillRefError(
            "SKILL_NOT_FOUND",
            f"技能不存在或已停用：{', '.join(missing)}（只能引用状态为「有效」的技能）",
        )

    lines = [
        "【本次执行需参考的技能（来源：技能共享库 agx_skill，均已确认为有效状态）】",
        "执行前请先调用 agx_skill_get(skill_id=...) 读取技能完整正文，再按其中的步骤/命令执行；"
        "技能与本指令冲突时，以本指令为准。",
    ]
    for sid in ids:
        s = index[sid]
        name = s.get("name") or sid
        category = s.get("category") or "other"
        desc = (s.get("description") or "").strip()
        tail = f"：{desc}" if desc else ""
        lines.append(f"- {name}（skill_id={sid}，分类={category}）{tail}")
    return "\n".join(lines)
