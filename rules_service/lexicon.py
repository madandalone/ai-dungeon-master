"""Russian player text -> English rule vocabulary, so keyword search works on an English rule book.

The SRD is English and players write Russian, so word overlap is zero and keyword search finds nothing.
Without an embedding model, the cheapest bridge is a small bilingual glossary of rules terms: each entry maps
Russian word stems to the English names of the SRD sections they refer to, and `expand_query` appends those
names to the query. It is deliberately coarse: it covers the rules a player is likely to invoke (the chapters
on ability checks, movement, resting, combat, conditions and traps), not the whole book.

A stem matches any word that contains it, so "прыг" covers "прыгнуть", "перепрыгнуть" and "прыгаю"; "ё" and "е"
are treated as the same letter. A stem
shorter than four letters must equal a whole word; a stem with a space matches that phrase. Generic words
(damage, health, "can I") are left out on purpose because they would add noise to every query.
"""

import re

from rules_service.service import RuleChunk, format_rules

MIN_STEM_LENGTH = 4

# (Russian stems, English terms appended to the query)
ENTRIES: tuple[tuple[tuple[str, ...], str], ...] = (
    # --- Using Ability Scores
    (("прыг", "прыж"), "jump jumping long jump"),
    (("падени", "падает", "падаю", "падаем", "упад", "упаст", "упал", "свалил", "обрыв"), "falling fall"),
    (("задерж", "дыхани", "задох", "утону", "тонет", "тонут", "тону"), "suffocating hold breath"),
    (("лаз", "карабк", "взобр", "полз", "плыть", "плыву", "плаван"), "climbing swimming and crawling"),
    (("скорост", "пешком", "путешеств", "дневной переход"), "speed travel pace"),
    (("труднопроход", "заросл", "болот"), "difficult terrain"),
    (("ночев", "переночев", "выспа", "полный отдых", "полностью восстанов"), "long rest"),
    (("привал", "коротк"), "short rest"),
    (("отдых", "отдохн"), "resting rest"),
    (("темнот", "тьм", "факел", "видимост", "темно"), "vision and light darkness darkvision"),
    (("голод", "жажд", "провиант"), "food and water"),
    (("преимуществ", "помех"), "advantage and disadvantage"),
    (("спасброс",), "saving throws"),
    (("навык", "проверк"), "ability checks skills"),
    (("пассивн",), "passive checks perception"),
    (("мастерств",), "proficiency bonus"),
    (("модификатор",), "ability scores and modifiers"),
    (("груз", "поднят", "поднима", "нести", "несу", "переноск", "вес", "веса", "весит"), "lifting and carrying encumbrance"),
    (("взаимодейств", "рычаг", "открыть дверь"), "interacting with objects"),
    # --- The Order of Combat
    (("инициатив", "очерёдн", "очередн"), "initiative"),
    (("внезапн", "засад", "врасплох", "неожиданн"), "surprise"),
    (("укрыти", "прикрыт", "баррикад"), "cover"),
    (("спрят", "прячу", "прячь", "скрыт", "подкрад", "незаметн"), "hide hiding stealth"),
    (("захват", "схват", "удержа", "хватаю"), "grappling grappled"),
    (("толкн", "толкаю", "сбить", "сбиваю"), "shoving a creature prone"),
    (("убегает", "отходит", "отступ", "возможност", "сбегает"), "opportunity attacks disengage"),
    (("двумя", "двух оруж", "левой руке"), "two-weapon fighting"),
    (("лучник", "стрел", "выстрел", "арбалет", "метнуть", "метаю", "дальн"), "ranged attacks range"),
    (("верхом", "лошад", "всадник", "скакун", "оседла"), "mounted combat mount"),
    (("подводн", "нырн", "нырял", "водой"), "underwater combat"),
    (("критическ",), "critical hits"),
    (("смерт", "умира", "умер", "умрёт"), "death saving throws dropping to 0 hit points"),
    (("бессознат", "обморок", "потерял созн", "без созн"), "unconscious falling unconscious dropping to 0 hit points"),
    (("стабилиз", "кровотеч"), "stabilizing a creature"),
    (("лечен", "лечу", "исцел", "зелье", "зелья"), "healing"),
    (("временн",), "temporary hit points"),
    (("сопротивл", "уязвим"), "damage resistance and vulnerability"),
    (("помочь", "помогаю", "помощь", "помог"), "help action"),
    (("подготов", "приготов", "дожидаюсь", "реакци"), "ready action reaction"),
    (("уклон", "уворач", "уворот"), "dodge action"),
    (("рывок", "ускоря", "бегу"), "dash action"),
    (("обыск", "обыскив", "поиск"), "search action"),
    (("размер", "протисну", "тесн"), "creature size squeezing into a smaller space"),
    (("полёт", "полет", "летать", "летаю", "летит", "летающ", "парит"), "flying movement"),
    (("безуми", "сошёл с ума", "помешат"), "madness"),
    # --- Conditions
    (("ослепл", "слепот", "слепой"), "blinded"),
    (("очарова", "околдова"), "charmed"),
    (("оглох", "глухой", "глухота", "оглуш"), "deafened stunned"),
    (("ошеломл",), "stunned"),
    (("испуган", "напуган", "страх", "ужас"), "frightened"),
    (("схвачен",), "grappled"),
    (("недеесп", "обездвиж"), "incapacitated"),
    (("паралич", "парализ"), "paralyzed"),
    (("окамене", "окаменел"), "petrified"),
    (("отрав", "ядом", "яда", "ядов"), "poisoned poison"),
    (("лежа", "сбит", "ничком", "на земл"), "prone being prone"),
    (("опутан", "связан", "скован"), "restrained"),
    (("невидим",), "invisible"),
    (("истощен", "измотан"), "exhaustion"),
    # --- Situational rules
    (("ловушк", "капкан", "западн"), "traps detecting and disabling a trap"),
    (("болезн", "заразил", "чума", "лихорадк"), "diseases"),
)


def _normalize(text: str) -> str:
    """Lowercase and fold "ё" into "е": people type both, and a glossary must not care which."""
    return text.lower().replace("ё", "е")


_STEMS = [(_normalize(stem), terms) for stems, terms in ENTRIES for stem in stems]
_WORD_RE = re.compile(r"[а-я]+")


def expand_query(query: str) -> str:
    """Append the English rule vocabulary for every glossary entry the Russian words of `query` hit.

    A query without Russian rule words is returned unchanged, so English queries behave exactly as before.
    """
    tokens = _WORD_RE.findall(_normalize(query))
    if not tokens:
        return query
    text = " ".join(tokens)
    added: list[str] = []
    for stem, terms in _STEMS:
        if " " in stem:
            matched = stem in text
        elif len(stem) < MIN_STEM_LENGTH:
            matched = stem in tokens
        else:
            matched = any(stem in token for token in tokens)
        if matched:
            added.extend(word for word in terms.split() if word not in added)
    return f"{query} {' '.join(added)}" if added else query


class LexiconRulesService:
    """Keyword search whose queries first pass through `expand_query`. Same interface as RulesService."""

    def __init__(self, inner):
        self._inner = inner

    @property
    def chunks(self) -> list[RuleChunk]:
        return self._inner.chunks

    def search(self, query: str, top_k: int = 3) -> list[RuleChunk]:
        return self._inner.search(expand_query(query), top_k)

    def get_rules(self, query: str, top_k: int = 3, max_chars: int = 4000) -> str:
        return format_rules(self.search(query, top_k), max_chars)
