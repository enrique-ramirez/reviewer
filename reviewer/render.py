"""Turning model output into GitHub markdown.

Every finding arrives with two renderings from a single model call: ``human``
for the inline comment, ``agent_task`` for the collapsible block. Because both
come from the same call, the agent block cannot describe a fix the human comment
did not ask for.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MARKER_PREFIX = "<!-- blinky"

#: What the marker used to be. Comments already posted on live pull requests
#: carry it, and they are still ours — a rename must not orphan them.
LEGACY_MARKER_PREFIXES = ("<!-- pr-reviewer",)

SEVERITY_ORDER = {"blocker": 0, "correctness": 1, "nit": 2, "note": 3}
SEVERITY_LABEL = {
    "blocker": "Blocker",
    "correctness": "Correctness",
    "nit": "Nit",
    "note": "Note",
}


@dataclass
class Finding:
    axis: str
    severity: str
    path: str
    line: int | None
    side: str
    title: str
    human: str
    agent_task: str
    confidence: str = "high"

    @property
    def rank(self) -> int:
        return SEVERITY_ORDER.get(self.severity, 99)


def parse_findings(payload: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    for raw in payload.get("findings") or []:
        if not isinstance(raw, dict):
            continue
        severity = str(raw.get("severity") or "note").lower().strip()
        if severity not in SEVERITY_ORDER:
            severity = "note"
        line = raw.get("line")
        try:
            line_no = int(line) if line is not None else None
        except (TypeError, ValueError):
            line_no = None
        side = str(raw.get("side") or "RIGHT").upper()
        if side not in ("RIGHT", "LEFT"):
            side = "RIGHT"
        findings.append(
            Finding(
                axis=str(raw.get("axis") or "standards").lower(),
                severity=severity,
                path=str(raw.get("path") or "").strip(),
                line=line_no,
                side=side,
                title=str(raw.get("title") or "").strip(),
                human=str(raw.get("human") or "").strip(),
                agent_task=str(raw.get("agent_task") or "").strip(),
                confidence=str(raw.get("confidence") or "high").lower(),
            )
        )
    return sorted(findings, key=lambda f: (f.rank, f.path, f.line or 0))


def marker(head_sha: str) -> str:
    return f"{MARKER_PREFIX}:v1:{head_sha[:12]} -->"


def is_ours(body: str) -> bool:
    body = body or ""
    return MARKER_PREFIX in body or any(
        prefix in body for prefix in LEGACY_MARKER_PREFIXES
    )


def inline_comment_body(finding: Finding) -> str:
    label = SEVERITY_LABEL.get(finding.severity, "Note")
    head = f"**{label}"
    if finding.title:
        head += f" — {finding.title}"
    head += "**"
    parts = [head, "", finding.human]
    if finding.confidence == "low":
        parts += ["", "_I am not certain about this one — tell me if I have it wrong._"]
    return "\n".join(parts)


def agent_block(findings: list[Finding], language_note: str = "") -> str:
    """The collapsible copy-paste block.

    Written for an agent to act on: ordered by severity, one task per finding,
    with an instruction to verify each claim before changing anything. The
    verification instruction is first because it applies to every branch below
    it.
    """
    actionable = [f for f in findings if f.agent_task]
    if not actionable:
        return ""

    lines = [
        "<details>",
        "<summary><b>Prompt for AI agents</b> — copy this into your agent</summary>",
        "",
        "```markdown",
        "You are picking up review feedback on a pull request you have checked out.",
        "",
        "Work through the numbered items below in order. For each one:",
        "",
        "1. Read the file and lines it names, and decide whether the claim is true.",
        "2. If it is true, make the change and say what you changed.",
        "3. If it is not true, or it is true but the fix would be wrong here, leave",
        "   the code alone and write one or two sentences explaining why, quoting the",
        "   code that shows it. That reply goes back to the reviewer.",
        "4. If you cannot tell, say so and name what you would need to check.",
        "",
        "The review was written from a diff. It can be wrong about code it could not",
        "see. Verifying each item against the codebase is the job; applying them all",
        "on faith is not.",
        "",
        "Do not touch anything that is not listed here.",
        "",
    ]
    if language_note:
        lines += [language_note, ""]
    lines.append("---")
    lines.append("")

    for index, finding in enumerate(actionable, start=1):
        label = SEVERITY_LABEL.get(finding.severity, "Note")
        location = finding.path + (f":{finding.line}" if finding.line else "")
        lines.append(f"## {index}. [{label}] {location}")
        lines.append("")
        lines.append(finding.agent_task)
        lines.append("")

    lines += ["```", "", "</details>"]
    return "\n".join(lines)


def _counts_line(findings: list[Finding]) -> str:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    if not counts:
        return "Nothing to raise."
    ordered = sorted(counts.items(), key=lambda kv: SEVERITY_ORDER.get(kv[0], 99))
    return ", ".join(
        f"{count} {SEVERITY_LABEL.get(sev, sev).lower()}{'s' if count != 1 else ''}"
        for sev, count in ordered
    )


def summary_body(
    *,
    head_sha: str,
    summary_text: str,
    findings: list[Finding],
    event: str,
    coverage_note: str,
    invite_wave_off: bool,
    manual_reason: str = "",
    language_note: str = "",
) -> str:
    parts = [
        marker(head_sha),
        "🤖 **AI review** — written by Claude, posted from my account. "
        "I read it before it went up only if it says so below; treat it as a "
        "first pass, not a verdict.",
        "",
        summary_text.strip(),
        "",
        f"**{_counts_line(findings)}**",
    ]

    if coverage_note:
        parts += ["", f"<sub>Coverage: {coverage_note}</sub>"]

    if invite_wave_off:
        parts += [
            "",
            "None of these block the merge. If you would rather not take them, "
            "reply here saying so and I will approve as-is on the next pass — "
            "no need to change anything.",
        ]

    if manual_reason:
        parts += [
            "",
            f"_Holding off on approving this one for a human to look at: {manual_reason}._",
        ]

    block = agent_block(findings, language_note)
    if block:
        parts += ["", block]

    return "\n".join(parts)


def approval_body(head_sha: str, summary_text: str, coverage_note: str) -> str:
    parts = [
        marker(head_sha),
        "🤖 **AI review** — written by Claude, posted from my account.",
        "",
        summary_text.strip() or "Looks good to me.",
    ]
    if coverage_note:
        parts += ["", f"<sub>Coverage: {coverage_note}</sub>"]
    return "\n".join(parts)


def thread_reply_body(text: str, *, resolving: bool) -> str:
    suffix = "\n\n_Resolving this thread._" if resolving else ""
    return f"{MARKER_PREFIX}:reply -->\n{text.strip()}{suffix}"


def capped_notice(text: str) -> str:
    return (
        f"{MARKER_PREFIX}:capped -->\n"
        f"{text.strip()}\n\n"
        "_I have gone back and forth on this one enough times. Leaving it here "
        "for a human to settle — it will not block anything else in this review._"
    )
