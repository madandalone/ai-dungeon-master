import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
MAX_CHUNK_CHARS = 1500


@dataclass(frozen=True)
class RuleChunk:
    title: str
    text: str
    path: str = ""


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) > 2]


def load_text(path: str | Path) -> str:
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    return path.read_text(encoding="utf-8")


def _pieces(body: str, max_chars: int) -> list[str]:
    pieces = []
    for para in re.split(r"\n\s*\n", body):
        if len(para) <= max_chars:
            pieces.append(para)
            continue
        buf = ""
        for line in para.splitlines():
            if buf and len(buf) + len(line) + 1 > max_chars:
                pieces.append(buf)
                buf = ""
            buf += line + "\n"
        pieces.append(buf)
    return pieces


def _split_long(title: str, path: str, body: str, max_chars: int) -> list[RuleChunk]:
    chunks, buf = [], ""
    for piece in _pieces(body, max_chars):
        if buf and len(buf) + len(piece) > max_chars:
            chunks.append(RuleChunk(title, buf.strip(), path))
            buf = ""
        buf += piece + "\n\n"
    if buf.strip():
        chunks.append(RuleChunk(title, buf.strip(), path))
    return chunks


def split_into_chunks(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[RuleChunk]:
    sections: list[tuple[str, str, list[str]]] = [("", "", [])]
    stack: list[tuple[int, str]] = []
    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            level, title = len(match.group(1)), match.group(2)
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            sections.append((title, " > ".join(t for _, t in stack), []))
        else:
            sections[-1][2].append(line)
    chunks = []
    for title, path, lines in sections:
        body = "\n".join(lines).strip()
        if body:
            chunks.extend(_split_long(title, path, body, max_chars))
    return chunks


def format_rules(chunks: list[RuleChunk], max_chars: int = 4000) -> str:
    parts, used = [], 0
    for chunk in chunks:
        part = f"## {chunk.title}\n{chunk.text}" if chunk.title else chunk.text
        if used + len(part) > max_chars:
            break
        parts.append(part)
        used += len(part)
    return "\n\n".join(parts)


class RulesService:
    """Naive keyword retrieval (TF-IDF) over rule book chunks."""

    def __init__(self, chunks: list[RuleChunk]):
        self.chunks = chunks
        self._tf = [Counter(tokenize(f"{c.title} {c.title} {c.text}")) for c in chunks]
        df = Counter(t for tf in self._tf for t in tf)
        n = len(chunks)
        self._idf = {t: math.log(1 + n / d) for t, d in df.items()}

    @classmethod
    def from_text(cls, text: str) -> "RulesService":
        return cls(split_into_chunks(text))

    @classmethod
    def from_file(cls, path: str | Path) -> "RulesService":
        return cls.from_text(load_text(path))

    def search(self, query: str, top_k: int = 3) -> list[RuleChunk]:
        terms = set(tokenize(query))
        scored = []
        for chunk, tf in zip(self.chunks, self._tf):
            total = sum(tf.values()) or 1
            score = sum(tf[t] / total * self._idf[t] for t in terms if t in tf)
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda s: s[0], reverse=True)
        return [c for _, c in scored[:top_k]]

    def get_rules(self, query: str, top_k: int = 3, max_chars: int = 4000) -> str:
        return format_rules(self.search(query, top_k), max_chars)
