"""
Multilingual orchestrator complexity keywords.

Each entry is a frozenset of lowercase strings/phrases checked against
the lowercased user prompt in OrchestratorAgent._is_complex().

The orchestrator imports ALL_COMPLEX_KEYWORDS — the union of ALL language
sets — so prompts in any supported language trigger multi-agent routing.

Adding a new language:
  1. Define a new frozenset (e.g. _DE for German).
  2. Add it to COMPLEX_KEYWORDS_BY_LANG.
  ALL_COMPLEX_KEYWORDS is rebuilt automatically.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# English
# ---------------------------------------------------------------------------
_EN: frozenset[str] = frozenset([
    # Research & information gathering
    "research", "investigate", "find out", "look up", "search for",
    "gather", "survey", "explore",
    # Analysis
    "analyze", "analyse", "examine", "evaluate", "assess", "review",
    "audit", "diagnose", "profile", "break down", "deep dive", "deep-dive",
    # Comparison
    "compare", "contrast", "differentiate", "versus", " vs ", "vs.",
    "benchmark", "pros and cons", "trade-off", "trade off",
    "advantages and disadvantages",
    # Synthesis / reporting
    "write a report", "create a document", "summarize", "summarise",
    "synthesize", "synthesise", "compile", "draft", "generate a report",
    # Planning / design
    "plan", "design", "architect", "implement", "develop", "build",
    "step by step", "roadmap", "strategy",
    # Brainstorming / ideation
    "brainstorm", "brain storm", "ideate", "generate ideas",
    "think of", "come up with", "suggestions for",
    # Deep reasoning
    "think thoroughly", "think deeply", "elaborate", "explain in detail",
    "in depth", "in-depth", "comprehensive", "thorough", "exhaustive",
    # Data / code
    "calculate", "compute", "visualize", "visualise", "refactor",
    "optimize", "optimise", "debug", "model",
    # Business analysis
    "requirements", "gap analysis", "process model", "workflow",
    "use case", "stakeholder",
])

# ---------------------------------------------------------------------------
# Polish
# ---------------------------------------------------------------------------
_PL: frozenset[str] = frozenset([
    # Research & information gathering
    "zbadaj", "zbadać", "wyszukaj", "poszukaj", "znajdź informacje",
    "dowiedz się", "sprawdź szczegółowo", "zbierz informacje", "zbierz dane",
    "zbierz", "przeprowadź badanie", "przeprowadź badania",
    # Analysis
    "przeanalizuj", "zanalizuj", "analizuj", "oceń szczegółowo",
    "przeprowadź audyt", "zdiagnozuj", "rozbij na części",
    "głęboka analiza", "dogłębna analiza", "szczegółowa analiza",
    "przejrzyj szczegółowo",
    # Comparison
    "porównaj", "porównanie", "zestawienie", "zestawić", "kontra",
    "benchmarking", "wady i zalety", "zalety i wady", "za i przeciw",
    "kompromis", "plusy i minusy",
    # Synthesis / reporting
    "napisz raport", "utwórz dokument", "stwórz dokument",
    "podsumuj", "przygotuj streszczenie", "zsyntetyzuj",
    "skompiluj", "napisz szkic", "wygeneruj raport", "przygotuj raport",
    # Planning / design
    "zaplanuj", "zaprojektuj", "zaimplementuj", "wdróż", "stwórz plan",
    "krok po kroku", "mapa drogowa", "plan działania", "plan projektu",
    "strategia działania",
    # Brainstorming / ideation
    "burza mózgów", "brainstorming", "generuj pomysły", "zaproponuj pomysły",
    "wymyśl rozwiązania", "zaproponuj rozwiązania", "sugestie dotyczące",
    "propozycje rozwiązań",
    # Deep reasoning
    "przemyśl dokładnie", "przemyśl gruntownie", "przemyśl dogłębnie",
    "rozwiń temat", "szczegółowo opisz", "wyjaśnij szczegółowo",
    "opisz szczegółowo", "dogłębnie", "kompleksowo", "gruntownie",
    "wyczerpująco", "kompleksowy opis", "wyczerpująca analiza",
    # Data / code
    "oblicz", "przelicz", "wizualizuj", "zobrazuj",
    "przebuduj kod", "zoptymalizuj", "napraw błędy", "debuguj",
    # Business analysis
    "wymagania funkcjonalne", "analiza luk", "model procesu",
    "przepływ pracy", "przypadek użycia", "scenariusz użycia",
    "interesariusz", "interesariusze", "diagram procesów",
    "specyfikacja wymagań",
])

# ---------------------------------------------------------------------------
# Registry — add new languages here
# ---------------------------------------------------------------------------
COMPLEX_KEYWORDS_BY_LANG: dict[str, frozenset[str]] = {
    "en": _EN,
    "pl": _PL,
}

# Union of all language sets — used by OrchestratorAgent._is_complex()
ALL_COMPLEX_KEYWORDS: frozenset[str] = frozenset().union(
    *COMPLEX_KEYWORDS_BY_LANG.values()
)
