"""Retrieval quality check: does a provider put the right rule section near the top for real queries?

A case is hit at rank r when the r-th returned chunk lies in a relevant section: a section path (without the
book title) matches a chunk's path itself or any of its ancestors/descendants on "Parent > Child" boundaries.

Run: python -m rules_service.evaluation --book data/srd.md --mode keyword|rag|hybrid
"""

import argparse
import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rules_service.build import DEFAULT_CACHE_DIR, MODES, build_rules_service
from rules_service.service import RuleChunk

DEFAULT_CASES = Path(__file__).with_name("gold_queries.json")
DEFAULT_KS = (1, 3, 5)


class Searchable(Protocol):
    def search(self, query: str, top_k: int = 3) -> list[RuleChunk]: ...


@dataclass(frozen=True)
class GoldCase:
    query: str
    relevant: tuple[str, ...]
    lang: str = "en"

    def matches(self, chunk: RuleChunk) -> bool:
        path = f" > {chunk.path} > "
        return any(f" > {section} > " in path for section in self.relevant)


@dataclass(frozen=True)
class CaseResult:
    case: GoldCase
    rank: int | None  # 1-based rank of the first relevant chunk within max_k, None if absent


@dataclass(frozen=True)
class EvalReport:
    results: tuple[CaseResult, ...]
    max_k: int

    def recall_at(self, k: int, lang: str | None = None) -> float:
        picked = [r for r in self.results if lang is None or r.case.lang == lang]
        return sum(r.rank is not None and r.rank <= k for r in picked) / len(picked) if picked else 0.0

    def mrr(self, lang: str | None = None) -> float:
        picked = [r for r in self.results if lang is None or r.case.lang == lang]
        return sum(1 / r.rank for r in picked if r.rank) / len(picked) if picked else 0.0

    @property
    def languages(self) -> list[str]:
        return sorted({r.case.lang for r in self.results})

    def misses(self, k: int) -> list[CaseResult]:
        return [r for r in self.results if r.rank is None or r.rank > k]


def load_cases(path: str | Path = DEFAULT_CASES) -> list[GoldCase]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [GoldCase(item["query"], tuple(item["relevant"]), item.get("lang", "en")) for item in raw]


def evaluate(provider: Searchable, cases: Sequence[GoldCase], max_k: int = max(DEFAULT_KS)) -> EvalReport:
    results = []
    for case in cases:
        hits = provider.search(case.query, top_k=max_k)
        rank = next((i for i, chunk in enumerate(hits, start=1) if case.matches(chunk)), None)
        results.append(CaseResult(case, rank))
    return EvalReport(tuple(results), max_k)


def format_report(report: EvalReport, ks: Sequence[int] = DEFAULT_KS) -> str:
    groups = [(None, "all")] + [(lang, lang) for lang in report.languages]
    count = defaultdict(int)
    for result in report.results:
        count[result.case.lang] += 1
    header = f"{'queries':<9}{'n':>4}" + "".join(f"{'R@' + str(k):>8}" for k in ks) + f"{'MRR':>8}"
    lines = [header]
    for lang, label in groups:
        n = len(report.results) if lang is None else count[lang]
        lines.append(
            f"{label:<9}{n:>4}"
            + "".join(f"{report.recall_at(k, lang):>8.2f}" for k in ks)
            + f"{report.mrr(lang):>8.2f}"
        )
    missed = report.misses(max(ks))
    if missed:
        lines.append(f"\nnot found in top {max(ks)} ({len(missed)}):")
        lines += [f"  [{r.case.lang}] {r.case.query}" for r in missed]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--book", type=Path, required=True)
    parser.add_argument("--mode", choices=MODES, default="keyword")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)
    provider = build_rules_service(args.book, args.mode, args.cache_dir, args.model)
    report = evaluate(provider, load_cases(args.cases))
    print(f"mode={args.mode}  cases={len(report.results)}\n")
    print(format_report(report))


if __name__ == "__main__":
    main()
