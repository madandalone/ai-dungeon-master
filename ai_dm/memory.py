"""Local retrieval over accepted history supplied by the caller; no session cache."""

import re
from collections import Counter
from dataclasses import dataclass

from ai_dm.models import TurnHistoryEntry, TurnRequest

RECENT_LIMIT = 10
MEMORY_LIMIT = 3
_WORDS = re.compile(r"[^\W\d_]+", re.UNICODE)
_STOP_WORDS = frozenset("""
    и или а но в во на с со к ко у о об от до за из по для при через
    я ты он она оно мы вы они это этот эта эти тот та те его её ее их
    мой моя мои свой своя свои мне меня тебе тебя нам нас вам вас
    не ни да нет же ли бы уже ещё еще теперь снова здесь там как что
    чтобы где когда который которая которые был была были быть есть
    хочу буду продолжаю пожалуйста осмотр
    the a an and or but in on at to of for from with is are was were
    i you he she it we they this that my your again please
""".split())


def _terms(text: str) -> set[str]:
    return {word for word in _WORDS.findall(text.casefold().replace("ё", "е"))
            if len(word) > 2 and word not in _STOP_WORDS}


def history_record(entry: TurnHistoryEntry) -> dict[str, str]:
    """Prompt view of an entry: character_id is an opaque Engine id the DM cannot resolve."""
    return entry.model_dump(exclude={"character_id"})


def _identity(entry: TurnHistoryEntry) -> tuple[str, str, str]:
    return entry.player_action, entry.narrative, entry.dm_notes


@dataclass(frozen=True)
class HistorySelection:
    recent: tuple[dict[str, str], ...]
    # Ordered by relevance, then recency; rendered in chronological order.
    memories: tuple[dict, ...]

    @property
    def count(self) -> int:
        return len(self.recent) + len(self.memories)

    def blocks(self, count: int) -> tuple[list[dict[str, str]], list[dict]]:
        """Drop lowest-ranked memories first, then whole oldest recent turns."""
        count = max(0, min(count, self.count))
        recent_count = min(count, len(self.recent))
        recent = list(self.recent[-recent_count:]) if recent_count else []
        memories = sorted(self.memories[:max(0, count - len(self.recent))],
                          key=lambda entry: entry["history_position"])
        return recent, memories


def select_history(request: TurnRequest) -> HistorySelection:
    """Keep ten recent turns and retrieve up to three distinct older records.

    Exact word overlap only: action matches outrank location-only matches;
    rarer matching words score higher, ties prefer more recent records.
    """
    split = max(0, len(request.history) - RECENT_LIMIT)
    recent = tuple(history_record(entry) for entry in request.history[split:])
    action_terms = _terms(request.player_action)
    location_terms = _terms(request.game_state.location)
    query = action_terms | location_terms
    if not split or not query:
        return HistorySelection(recent, ())

    seen = {_identity(entry) for entry in request.history[split:]}
    candidates = []
    for position in range(split - 1, -1, -1):
        entry = request.history[position]
        identity = _identity(entry)
        if identity in seen:
            continue
        seen.add(identity)
        terms = _terms(" ".join(identity)) & query
        if terms:
            candidates.append((position, entry, terms))
    frequency = Counter(term for _, _, terms in candidates for term in terms)

    def rank(candidate):
        position, _, terms = candidate
        action_score = sum(1 / frequency[term] for term in sorted(terms & action_terms))
        location_score = sum(1 / frequency[term] for term in sorted(terms & location_terms))
        return action_score, location_score, position

    selected = sorted(candidates, key=rank, reverse=True)[:MEMORY_LIMIT]
    memories = tuple({"history_position": position + 1, **history_record(entry)}
                     for position, entry, _ in selected)
    return HistorySelection(recent, memories)
