"""Long-term memory store with three update policies.

append_only  every add/update is appended; nothing is ever removed (common naive baseline)
overwrite    an update replaces the slot's value immediately; forget deletes; no notion of time
temporal     an update takes effect at its valid_from (old value stays valid until then);
             temporary states expire; forget deletes; full history is kept for audit
"""

from dataclasses import asdict, dataclass, field
from datetime import date, datetime

POLICIES = ("append_only", "overwrite", "temporal")


def _day(value: str | None) -> date | None:
    return date.fromisoformat(value[:10]) if value else None


@dataclass
class MemoryItem:
    id: str
    slot: str
    label: str
    value: str
    created_at: str
    valid_from: str | None = None   # YYYY-MM-DD; None = from created_at
    expires_at: str | None = None   # YYYY-MM-DD inclusive
    superseded_at: str | None = None
    deleted_at: str | None = None
    source: str = ""
    scope: str = "topical"          # "global": applies to almost every answer (language, nickname, location...)
    extra: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        notes = []
        if self.valid_from and self.valid_from > self.created_at[:10]:
            notes.append(f"自{self.valid_from}起")
        if self.expires_at:
            notes.append(f"到{self.expires_at}为止")
        suffix = f"（{'，'.join(notes)}）" if notes else ""
        return f"{self.label}：{self.value}{suffix}"

    def to_dict(self) -> dict:
        return {**asdict(self), "text": self.text}


class MemoryStore:
    def __init__(self, policy: str):
        if policy not in POLICIES:
            raise ValueError(f"unknown policy {policy}")
        self.policy = policy
        self.items: list[MemoryItem] = []
        self._n = 0

    # ---------- writes ----------
    def _current(self, slot: str, at: str) -> list[MemoryItem]:
        return [m for m in self.items if m.slot == slot and m.deleted_at is None and m.superseded_at is None]

    def apply(self, op: dict, at: str, source: str = "") -> MemoryItem | None:
        kind = op["op"]
        if kind == "none":
            return None
        if kind == "forget":
            if self.policy != "append_only":  # the naive baseline has no delete path
                for m in self._current(op["slot"], at):
                    m.deleted_at = at
            return None
        if kind not in {"add", "update"}:
            raise ValueError(f"unknown op {kind}")
        self._n += 1
        valid_from = op.get("valid_from") if self.policy == "temporal" else None
        expires_at = op.get("expires_at") if self.policy == "temporal" else None
        item = MemoryItem(id=f"m{self._n}", slot=op["slot"], label=op.get("label", op["slot"]), value=op["value"],
                          created_at=at, valid_from=valid_from, expires_at=expires_at, source=source,
                          scope="global" if op.get("scope") == "global" else "topical")
        if self.policy != "append_only":
            switch = valid_from or at
            for old in self._current(op["slot"], at):
                old.superseded_at = switch
        self.items.append(item)
        return item

    # ---------- reads ----------
    def current(self) -> list[MemoryItem]:
        """Latest version of every slot that is not deleted (including not-yet-valid or expired ones).

        This is what the extractor is shown, so it can point updates at the right item.
        """
        return [m for m in self.items if m.deleted_at is None and m.superseded_at is None]

    def active(self, at: str) -> list[MemoryItem]:
        now = datetime.fromisoformat(at)
        today = now.date()
        out = []
        for m in self.items:
            if m.created_at > at:
                continue
            if self.policy == "append_only":
                out.append(m)
                continue
            if m.deleted_at and m.deleted_at <= at:
                continue
            if m.superseded_at and _le(m.superseded_at, at):
                continue
            if self.policy == "temporal":
                if m.valid_from and _day(m.valid_from) > today:
                    continue
                if m.expires_at and _day(m.expires_at) < today:
                    continue
            out.append(m)
        return out


def _le(stamp: str, at: str) -> bool:
    """A date-only stamp (valid_from) switches at the start of that day."""
    if len(stamp) == 10:
        return date.fromisoformat(stamp) <= datetime.fromisoformat(at).date()
    return stamp <= at
