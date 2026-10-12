"""Run state: goal, plan, run memory (facts with provenance), counters."""
from __future__ import annotations   # lets the annotation below say Step without importing planner (it imports us)
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .planner import Step


@dataclass
class Fact:
    key: str
    value: str
    evidence: str  # where it was seen (e.g. "invoice.pdf p1: 'Total 1,18,000.00'")


@dataclass
class RunState:
    goal: str
    run_id: str
    plan: list[Step] = field(default_factory=list)
    step_index: int = 0
    memory: dict[str, Fact] = field(default_factory=dict)
    # UPPERCASE invoice numbers whose Save was clicked (even if the page then failed). policy.py blocks a second
    # submit of any of them. loop.py removes one when finance_get_invoice says it is not there: the idempotency rule.
    submitted: set[str] = field(default_factory=set)
    retries: int = 0
    replans: int = 0
    steps_used: int = 0
    verified: bool = False
    status: str = "running"  # running|success|partial|failed|needs_human
    answer: str = ""         # the summary the model gave to finish(): the answer to the goal
    answer_evidence: list = field(default_factory=list)
    notes: list = field(default_factory=list)   # short copies of earlier observations, the model only sees the last one in full

    def remember(self, key, value, evidence):
        self.memory[key] = Fact(key, value, evidence)

    def recall(self, key=None):
        """One Fact (None if unknown) when a key is given, otherwise a copy of all facts."""
        if key:
            return self.memory.get(key)
        return dict(self.memory)
