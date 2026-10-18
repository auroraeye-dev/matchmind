"""Offline translations for short broadcast labels.

Full-sentence translation is done by the LLM; offline mode translates labels
and keeps body text in English (marked with the source language).
"""

LANGUAGES = {"en": "English", "es": "Español", "fr": "Français", "pt": "Português", "hi": "हिन्दी", "ar": "العربية"}

LABELS: dict[str, dict[str, str]] = {
    "goal": {"en": "GOAL!", "es": "¡GOL!", "fr": "BUT !", "pt": "GOL!", "hi": "गोल!", "ar": "هدف!"},
    "big_chance": {"en": "Big chance", "es": "Ocasión clara", "fr": "Grosse occasion", "pt": "Grande chance", "hi": "बड़ा मौका", "ar": "فرصة كبيرة"},
    "momentum_swing": {"en": "Momentum shift", "es": "Cambio de dominio", "fr": "Bascule du momentum", "pt": "Virada de domínio", "hi": "रुख बदला", "ar": "تحول الزخم"},
    "pressure_spike": {"en": "Press wins it back", "es": "La presión recupera", "fr": "Le pressing paie", "pt": "Pressão recupera", "hi": "प्रेस से गेंद वापस", "ar": "الضغط يستعيد الكرة"},
    "pass_combo": {"en": "Keeping the ball", "es": "Posesión larga", "fr": "Longue possession", "pt": "Posse longa", "hi": "गेंद पर नियंत्रण", "ar": "استحواذ طويل"},
    "great_pass": {"en": "Pass of the match?", "es": "¿Pase del partido?", "fr": "Passe du match ?", "pt": "Passe da partida?", "hi": "मैच का बेहतरीन पास?", "ar": "تمريرة المباراة؟"},
    "chaos_phase": {"en": "End-to-end", "es": "Ida y vuelta", "fr": "Match débridé", "pt": "Lá e cá", "hi": "उथल-पुथल", "ar": "ذهاب وإياب"},
    "tempo_shift": {"en": "Tempo shift", "es": "Cambio de ritmo", "fr": "Changement de rythme", "pt": "Mudança de ritmo", "hi": "रफ़्तार बदली", "ar": "تغير الإيقاع"},
    "control_phase": {"en": "In control", "es": "Dominio", "fr": "Maîtrise", "pt": "Controle", "hi": "नियंत्रण", "ar": "سيطرة"},
    "milestone": {"en": "Milestone", "es": "Hito", "fr": "Cap franchi", "pt": "Marca", "hi": "उपलब्धि", "ar": "إنجاز"},
    "card": {"en": "Yellow card", "es": "Tarjeta amarilla", "fr": "Carton jaune", "pt": "Cartão amarelo", "hi": "पीला कार्ड", "ar": "بطاقة صفراء"},
    "substitution": {"en": "Substitution", "es": "Cambio", "fr": "Remplacement", "pt": "Substituição", "hi": "बदलाव", "ar": "تبديل"},
    "stat_check": {"en": "Stat check", "es": "Estadísticas", "fr": "Statistiques", "pt": "Estatísticas", "hi": "आँकड़े", "ar": "إحصائيات"},
    "half_time": {"en": "Half-time", "es": "Descanso", "fr": "Mi-temps", "pt": "Intervalo", "hi": "हाफ-टाइम", "ar": "الشوط الأول"},
    "full_time": {"en": "Full-time", "es": "Final", "fr": "Fin du match", "pt": "Fim de jogo", "hi": "फुल-टाइम", "ar": "نهاية المباراة"},
}


def label(key: str, lang: str) -> str:
    row = LABELS.get(key, {})
    return row.get(lang) or row.get("en") or key.replace("_", " ").title()
