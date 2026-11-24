"""The splice rule for body + suffix block (single implementation).

[2026-11-17 15:41 Feng] "I select a skill, then click send... the skill has a description; should that
description also appear in there? Right now you send it straight from the backend, but on the page you cannot
see the content with the skill."

-> the backend must return "the text that was actually sent" to the frontend for its local echo. The splice must
   match the kernel, otherwise the echo and the stored copy disagree and people get more confused. The rule comes
   from the kernel's `agx_universal_send` step (6):

      _suffix = (instruction_suffix or "").strip()
      _text_with_suffix = f"{text}\n\n{_suffix}" if text else _suffix

   so this is the only implementation; the action layer calls it instead of assembling on its own.
"""


def compose_with_suffix(text: str, suffix: str) -> str:
    """Splice the suffix block (e.g. "reference skills") into the body and return the text that is really sent.

    - suffix has its surrounding whitespace stripped first (matching the kernel) -- otherwise an extra blank line appears in the body
    - when text is empty, return only suffix
    - both empty -> empty string (callers already require a non-empty text; this merely avoids raising)
"""
    body = text or ""
    extra = (suffix or "").strip()
    if not extra:
        return body
    return f"{body}\n\n{extra}" if body else extra
