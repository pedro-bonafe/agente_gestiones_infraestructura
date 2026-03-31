"""
Query rewriter: expands user search terms using a domain-specific synonym dictionary.
Suggests a category_hint when the terms strongly imply a specific category.
No LLM call — pure dictionary lookup + normalization.
"""

import unicodedata

# ---------------------------------------------------------------------------
# Domain synonym dictionary
# Keys are normalized (no accents, uppercase). Values are lists of search terms
# to use in LIKE clauses, plus an optional category_hint.
# ---------------------------------------------------------------------------

DOMAIN_SYNONYMS: dict[str, dict] = {
    # Infraestructura vial
    "PAVIMENTO": {
        "terms": ["pavimento", "pavimentacion", "asfalto", "calzada", "carpeta"],
        "category": "Infraestructura vial",
    },
    "RUTA": {
        "terms": ["ruta", "camino", "vialidad", "RP", "RN", "provincial"],
        "category": "Infraestructura vial",
    },
    "PUENTE": {
        "terms": ["puente", "pasarela", "alcantarilla", "paso"],
        "category": "Infraestructura vial",
    },
    "CORDON": {
        "terms": ["cordon", "cuneta", "vereda", "banquina", "cordón"],
        "category": "Infraestructura vial",
    },
    "GUARDARAIL": {
        "terms": ["guardarail", "barrera", "valla", "defensa"],
        "category": "Infraestructura vial",
    },
    "SEÑAL": {
        "terms": ["señal", "señaletic", "demarcacion", "cartel", "semaforo"],
        "category": "Infraestructura vial",
    },

    # Agua y saneamiento
    "AGUA": {
        "terms": ["agua", "provision", "potable", "acueducto", "perforacion"],
        "category": "Agua y saneamiento",
    },
    "CLOACA": {
        "terms": ["cloaca", "desague", "saneamiento", "colector", "efluente"],
        "category": "Agua y saneamiento",
    },
    "INUNDACION": {
        "terms": ["inundacion", "anegamiento", "pluvial", "lluvia", "zanja"],
        "category": "Agua y saneamiento",
    },
    "CISTERNA": {
        "terms": ["cisterna", "tanque", "reservorio", "aljibe"],
        "category": "Agua y saneamiento",
    },

    # Obras públicas
    "VIVIENDA": {
        "terms": ["vivienda", "casa", "habitacion", "construccion", "SEMILLA"],
        "category": "Obras públicas",
    },
    "EDIFICIO": {
        "terms": ["edificio", "municipal", "salon", "escuela", "hospital"],
        "category": "Obras públicas",
    },

    # Obra eléctrica / energía
    "LUZ": {
        "terms": ["luz", "electricidad", "alumbrado", "tendido", "transformador"],
        "category": "Obra eléctrica / energía",
    },
    "ENERGIA": {
        "terms": ["energia", "electrica", "ET", "linea", "media tension"],
        "category": "Obra eléctrica / energía",
    },

    # Obra de gas
    "GAS": {
        "terms": ["gas", "gasoducto", "red de gas", "provision de gas"],
        "category": "Obra de gas",
    },

    # Desarrollo social
    "SOCIAL": {
        "terms": ["social", "asistencia", "subsidio", "ayuda"],
        "category": "Desarrollo social",
    },

    # Educación
    "EDUCACION": {
        "terms": ["educacion", "escuela", "colegio", "jardin", "universidad"],
        "category": "Educación",
    },

    # Salud
    "SALUD": {
        "terms": ["salud", "hospital", "centro de salud", "medico", "sanitario"],
        "category": "Salud",
    },

    # Deportes
    "DEPORTES": {
        "terms": ["deporte", "cancha", "polideportivo", "playon", "estadio"],
        "category": "Deportes",
    },

    # Cultura / eventos
    "CULTURA": {
        "terms": ["cultura", "evento", "fiesta", "festival", "teatro"],
        "category": "Cultura / eventos",
    },

    # Gestión municipal
    "FOCOM": {
        "terms": ["FOCOM", "fondo", "comunal"],
        "category": "Gestión municipal / institucional",
    },
    "MUNICIPAL": {
        "terms": ["municipal", "municipio", "intendente", "concejo"],
        "category": "Gestión municipal / institucional",
    },

    # Estado
    "URGENTE": {
        "terms": ["urgente", "urgencia", "emergencia"],
        "category": None,
    },
}

# Category name normalization: user terms → exact BQ category value
CATEGORY_ALIASES: dict[str, str] = {
    "VIAL": "Infraestructura vial",
    "INFRAESTRUCTURA VIAL": "Infraestructura vial",
    "CAMINOS": "Infraestructura vial",
    "AGUA": "Agua y saneamiento",
    "SANEAMIENTO": "Agua y saneamiento",
    "GAS": "Obra de gas",
    "ELECTRICA": "Obra eléctrica / energía",
    "ELECTRICIDAD": "Obra eléctrica / energía",
    "LUZ": "Obra eléctrica / energía",
    "SALUD": "Salud",
    "EDUCACION": "Educación",
    "DEPORTES": "Deportes",
    "CULTURA": "Cultura / eventos",
    "SOCIAL": "Desarrollo social",
    "OBRAS": "Obras públicas",
    "OBRAS PUBLICAS": "Obras públicas",
    "MUNICIPAL": "Gestión municipal / institucional",
    "INSTITUCIONAL": "Gestión municipal / institucional",
    "COOPERATIVAS": "Cooperativas y mutuales",
    "MUTUALES": "Cooperativas y mutuales",
    "AYUDA": "Ayuda a instituciones",
}


def _normalize(text: str) -> str:
    nfd = unicodedata.normalize("NFD", text)
    without_accents = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return " ".join(without_accents.upper().split())


class RewriteResult:
    def __init__(self, expanded_terms: list[str], category_hint: str | None):
        self.expanded_terms = expanded_terms
        self.category_hint = category_hint


def rewrite(terms: list[str], category_hint: str | None = None) -> RewriteResult:
    """
    Expand search terms using domain synonyms.
    Returns RewriteResult with expanded terms and optional category_hint.
    category_hint from caller takes priority over inferred one.
    """
    if not terms:
        return RewriteResult([], category_hint)

    all_terms: list[str] = []
    inferred_category: str | None = None

    for term in terms:
        norm = _normalize(term)
        all_terms.append(term)  # always include original

        if norm in DOMAIN_SYNONYMS:
            entry = DOMAIN_SYNONYMS[norm]
            all_terms.extend(entry["terms"])
            if entry.get("category") and not inferred_category:
                inferred_category = entry["category"]
        else:
            # partial match
            for key, entry in DOMAIN_SYNONYMS.items():
                if key in norm or norm in key:
                    all_terms.extend(entry["terms"])
                    if entry.get("category") and not inferred_category:
                        inferred_category = entry["category"]
                    break

    # Deduplicate preserving order
    seen: set[str] = set()
    unique_terms: list[str] = []
    for t in all_terms:
        tl = t.lower()
        if tl not in seen:
            seen.add(tl)
            unique_terms.append(t)

    final_category = category_hint or inferred_category
    return RewriteResult(unique_terms, final_category)


def resolve_category(raw: str | None) -> str | None:
    """Normalize a raw category string to an exact BQ category name."""
    if not raw:
        return None
    norm = _normalize(raw)
    return CATEGORY_ALIASES.get(norm, raw)
