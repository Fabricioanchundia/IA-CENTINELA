"""Phase 8: defences against prompt injection in the text the agent reads.

Log lines, Kubernetes event messages and old postmortems are written by the system under
diagnosis, or by anyone who can make it log something (a product name, a header, a pod
annotation). A language model cannot reliably tell data from instructions, so the defences
that matter live in code:

1. **Quarantine.** `InjectionDetector` flags untrusted text that talks to the model instead of
   describing the system ("ignore previous instructions", fake "SYSTEM:" turns, our own JSON
   field names, the `</evidence>` delimiter, letters spaced out to dodge filters, Spanish
   variants). Flagged text never reaches the prompt: a placeholder says it was removed.
2. **Corroboration** (`trusted_signals`). An action may only target a component that a
   *trusted* signal points at: its own alert, failing calls measured by Prometheus, or its
   Kubernetes state. Untrusted text can inform a diagnosis but can never be the only reason to
   touch something.
3. **Second opinion.** When something was quarantined, the agent proposes an action only if
   the rule-based baseline (which reads no free text) blames the same component.

The detector is a filter, not a proof: paraphrased social engineering without any keyword
gets through it, which is why 2 and 3 exist. `centinela redteam` measures all of this.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # evidence.py imports this module
    from centinela.agent.evidence import Evidence

# Evidence kinds produced by our own measurements, never by free text from the system.
TRUSTED_KINDS = frozenset({"alert", "dependencies", "cache", "workloads", "pods"})

_ZERO_WIDTH = re.compile("[​-‏⁠﻿]")
# Runs of single characters separated by one space/dot/dash/underscore: "I G N O R E".
_SPACED = re.compile(r"(?<!\w)(?:\w[ ._\-]){3,}\w(?!\w)")

RULES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern))
    for name, pattern in (
        (
            "override",
            r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(previous|prior|above|"
            r"earlier|all|any|your|the|these|system)\b.{0,30}\b(instructions?|rules?|prompts?|"
            r"guidelines|directions|context)\b",
        ),
        (
            "override_es",
            r"\b(ignora|ignore|olvida|omite|descarta)\w*\b.{0,40}\b(instrucciones|reglas|"
            r"indicaciones|prompt)\b",
        ),
        (
            "new_instructions",
            r"\b(new|updated|real|actual|nuevas?)\s+(instructions?|instrucciones)\b",
        ),
        ("role_marker", r"(^|[\s\"'>\]\[|:;.,])(system|assistant|developer)\s*:"),
        ("chat_template", r"<\|?(im_start|im_end|system|endoftext)\|?>|\[/?inst\]|<<sys>>"),
        ("delimiter", r"</?\s*evidence\s*>"),
        ("schema_field", r"\b(root_cause_service|recommended_action|action_target)\b"),
        ("fake_evidence_id", r"\[e\d{1,3}\]"),
        (
            "addresses_the_model",
            r"\b(note|message|attention|dear|hey|hello)\b.{0,12}\b(ai|llm|model|assistant|agent|"
            r"centinela|language model|chatbot|gpt|qwen)\b",
        ),
        (
            "role_play",
            r"\b(you are now|from now on,? you|pretend to be|eres ahora|a partir de ahora)\b",
        ),
    )
)


def normalize(text: str) -> str:
    """Lower-case, NFKC (full-width and styled letters become ASCII), no zero-width
    characters, single spaces, and spaced-out letters joined back into words."""
    flat = unicodedata.normalize("NFKC", text)
    flat = _ZERO_WIDTH.sub("", flat).lower()
    flat = _SPACED.sub(lambda m: re.sub(r"[ ._\-]", "", m.group(0)), flat)
    return re.sub(r"\s+", " ", flat)


@dataclass(frozen=True)
class Finding:
    where: str  # e.g. "logs orders", "k8s_event redis-0", "history #3"
    rules: tuple[str, ...]
    chars: int


class InjectionDetector:
    def rules(self, text: str) -> tuple[str, ...]:
        """Names of the rules the text matches; empty when it looks like ordinary data."""
        flat = normalize(text)
        return tuple(name for name, pattern in RULES if pattern.search(flat))

    def suspicious(self, text: str) -> bool:
        return bool(self.rules(text))


def placeholder(finding: Finding) -> str:
    return (
        f"[QUARANTINED: {finding.chars} characters removed, possible prompt injection "
        f"({', '.join(finding.rules)})]"
    )


def trusted_signals(evidence: "Evidence") -> set[str]:
    """Components that our own measurements (not free text) show as involved."""
    return {s for item in evidence.items if item.kind in TRUSTED_KINDS for s in item.services}
