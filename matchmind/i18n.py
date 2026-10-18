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


# Casual-fan lines, fully translated so a fan's ticker reads in their language with no LLM.
# Keys: (moment_type, sentiment) where sentiment is +1 good for the fan's club, -1 bad, 0 neutral;
# or just moment_type when the line does not depend on sentiment.
CASUAL: dict[str, dict] = {
    "en": {
        ("goal", 1): "Get in! {who} scores for {team}! {score}.", ("goal", -1): "Ouch. {who} scores for {team}. {score}.",
        ("goal", 0): "{who} scores for {team}! {score}.",
        ("big_chance", 1): "So close! {who} nearly scores for {team}.", ("big_chance", -1): "Phew, that was close. {who} nearly scored for {team}.",
        ("big_chance", 0): "Huge chance for {who}!",
        ("momentum_swing", 1): "{team} are on top right now. Keep it up!", ("momentum_swing", -1): "{team} are piling on the pressure. Hold on!",
        ("momentum_swing", 0): "{team} are on top right now.",
        ("card", 1): "{who} goes into the book.", ("card", -1): "{who} is booked. Careful now!", ("card", 0): "Yellow card for {who}.",
        "pressure_spike": "{team} hunt the ball down and win it back!", "pass_combo": "{team} are keeping the ball beautifully.",
        "great_pass": "What a pass from {who}!", "chaos_phase": "It's end-to-end out there!", "control_phase": "{team} are running the show.",
        "tempo_shift": "The pace of the game has changed.", "milestone": "{who} is everywhere today.",
        "substitution": "{who} comes on for {team}.", "stat_check": "Quick stat check: {score}.",
        "half_time": "Half-time: {score}.", "full_time": "Full-time: {score}.", "goals": "Goals",
    },
    "es": {
        ("goal", 1): "¡Vamos! ¡Gol de {who} para {team}! {score}.", ("goal", -1): "Uf. Gol de {who} para {team}. {score}.",
        ("goal", 0): "¡Gol de {who} para {team}! {score}.",
        ("big_chance", 1): "¡Uy, casi! {who} roza el gol para {team}.", ("big_chance", -1): "¡Uf, qué peligro! {who} casi marca para {team}.",
        ("big_chance", 0): "¡Ocasión enorme para {who}!",
        ("momentum_swing", 1): "{team} manda ahora mismo. ¡Seguid así!", ("momentum_swing", -1): "{team} aprieta mucho. ¡Aguantad!",
        ("momentum_swing", 0): "{team} domina ahora mismo.",
        ("card", 1): "Amarilla para {who}.", ("card", -1): "Amarilla para {who}. ¡Cuidado!", ("card", 0): "Tarjeta amarilla para {who}.",
        "pressure_spike": "¡{team} presiona y recupera el balón!", "pass_combo": "{team} mueve el balón de maravilla.",
        "great_pass": "¡Qué pase de {who}!", "chaos_phase": "¡Partido de ida y vuelta!", "control_phase": "{team} controla el partido.",
        "tempo_shift": "El ritmo del partido ha cambiado.", "milestone": "{who} está en todas partes hoy.",
        "substitution": "{who} entra en el campo con {team}.", "stat_check": "Repaso rápido: {score}.",
        "half_time": "Descanso: {score}.", "full_time": "Final: {score}.", "goals": "Goles",
    },
    "fr": {
        ("goal", 1): "Allez ! But de {who} pour {team} ! {score}.", ("goal", -1): "Aïe. But de {who} pour {team}. {score}.",
        ("goal", 0): "But de {who} pour {team} ! {score}.",
        ("big_chance", 1): "Si proche ! {who} a failli marquer pour {team}.", ("big_chance", -1): "Ouf, c'était chaud ! {who} a failli marquer pour {team}.",
        ("big_chance", 0): "Énorme occasion pour {who} !",
        ("momentum_swing", 1): "{team} domine en ce moment. Continuez !", ("momentum_swing", -1): "{team} met la pression. Tenez bon !",
        ("momentum_swing", 0): "{team} domine en ce moment.",
        ("card", 1): "Carton jaune pour {who}.", ("card", -1): "{who} est averti. Attention !", ("card", 0): "Carton jaune pour {who}.",
        "pressure_spike": "{team} presse et récupère le ballon !", "pass_combo": "{team} fait tourner le ballon à merveille.",
        "great_pass": "Quelle passe de {who} !", "chaos_phase": "Le match est complètement débridé !", "control_phase": "{team} contrôle le match.",
        "tempo_shift": "Le rythme du match a changé.", "milestone": "{who} est partout aujourd'hui.",
        "substitution": "{who} entre en jeu pour {team}.", "stat_check": "Petit point stats : {score}.",
        "half_time": "Mi-temps : {score}.", "full_time": "Fin du match : {score}.", "goals": "Buts",
    },
    "pt": {
        ("goal", 1): "Vamos! Gol de {who} para {team}! {score}.", ("goal", -1): "Ai. Gol de {who} para {team}. {score}.",
        ("goal", 0): "Gol de {who} para {team}! {score}.",
        ("big_chance", 1): "Quase! {who} por pouco não marca para {team}.", ("big_chance", -1): "Ufa, essa foi perto! {who} quase marcou para {team}.",
        ("big_chance", 0): "Chance enorme para {who}!",
        ("momentum_swing", 1): "{team} está por cima agora. Continua assim!", ("momentum_swing", -1): "{team} está pressionando muito. Aguenta!",
        ("momentum_swing", 0): "{team} está por cima agora.",
        ("card", 1): "Cartão amarelo para {who}.", ("card", -1): "{who} leva amarelo. Cuidado!", ("card", 0): "Cartão amarelo para {who}.",
        "pressure_spike": "{team} pressiona e recupera a bola!", "pass_combo": "{team} troca passes com muita qualidade.",
        "great_pass": "Que passe de {who}!", "chaos_phase": "Jogo lá e cá!", "control_phase": "{team} controla o jogo.",
        "tempo_shift": "O ritmo do jogo mudou.", "milestone": "{who} está em todo lugar hoje.",
        "substitution": "{who} entra em campo por {team}.", "stat_check": "Resumo rápido: {score}.",
        "half_time": "Intervalo: {score}.", "full_time": "Fim de jogo: {score}.", "goals": "Gols",
    },
    "hi": {
        ("goal", 1): "शानदार! {team} के लिए {who} का गोल! {score}।", ("goal", -1): "ओह। {team} के लिए {who} ने गोल किया। {score}।",
        ("goal", 0): "{team} के लिए {who} का गोल! {score}।",
        ("big_chance", 1): "बहुत क़रीब! {who} {team} के लिए गोल करते-करते रह गए।", ("big_chance", -1): "बाल-बाल बचे! {who} {team} के लिए लगभग गोल कर चुके थे।",
        ("big_chance", 0): "{who} के लिए बड़ा मौका!",
        ("momentum_swing", 1): "इस समय {team} हावी है। ऐसे ही खेलते रहो!", ("momentum_swing", -1): "{team} लगातार दबाव बना रहा है। डटे रहो!",
        ("momentum_swing", 0): "इस समय {team} हावी है।",
        ("card", 1): "{who} को पीला कार्ड।", ("card", -1): "{who} को पीला कार्ड। अब सावधान!", ("card", 0): "{who} को पीला कार्ड।",
        "pressure_spike": "{team} ने दबाव बनाकर गेंद वापस जीत ली!", "pass_combo": "{team} गेंद को शानदार तरीके से घुमा रहा है।",
        "great_pass": "{who} का क्या कमाल का पास!", "chaos_phase": "मैच में ज़बरदस्त उथल-पुथल है!", "control_phase": "{team} का मैच पर नियंत्रण है।",
        "tempo_shift": "मैच की रफ़्तार बदल गई है।", "milestone": "{who} आज हर जगह नज़र आ रहे हैं।",
        "substitution": "{who} {team} के लिए मैदान पर उतरे।", "stat_check": "आँकड़ों पर एक नज़र: {score}।",
        "half_time": "हाफ-टाइम: {score}।", "full_time": "फुल-टाइम: {score}।", "goals": "गोल",
    },
    "ar": {
        ("goal", 1): "هيا! هدف لـ{team} عن طريق {who}! {score}.", ("goal", -1): "آه. {who} يسجل لـ{team}. {score}.",
        ("goal", 0): "هدف لـ{team} عن طريق {who}! {score}.",
        ("big_chance", 1): "قريبة جدًا! {who} كاد يسجل لـ{team}.", ("big_chance", -1): "يا للخطر! {who} كاد يسجل لـ{team}.",
        ("big_chance", 0): "فرصة كبيرة لـ{who}!",
        ("momentum_swing", 1): "{team} يسيطر الآن. واصلوا!", ("momentum_swing", -1): "{team} يضغط بقوة. اصمدوا!",
        ("momentum_swing", 0): "{team} يسيطر الآن.",
        ("card", 1): "بطاقة صفراء لـ{who}.", ("card", -1): "بطاقة صفراء لـ{who}. انتبه!", ("card", 0): "بطاقة صفراء لـ{who}.",
        "pressure_spike": "{team} يضغط ويستعيد الكرة!", "pass_combo": "{team} يتناقل الكرة ببراعة.",
        "great_pass": "يا لها من تمريرة من {who}!", "chaos_phase": "مباراة مفتوحة ذهابًا وإيابًا!", "control_phase": "{team} يتحكم في المباراة.",
        "tempo_shift": "تغير إيقاع المباراة.", "milestone": "{who} في كل مكان اليوم.",
        "substitution": "{who} يدخل الملعب مع {team}.", "stat_check": "نظرة سريعة على الأرقام: {score}.",
        "half_time": "نهاية الشوط الأول: {score}.", "full_time": "نهاية المباراة: {score}.", "goals": "الأهداف",
    },
}


def casual_line(mtype: str, sentiment: int, lang: str, **kw: str) -> tuple[str, str]:
    """Returns (text, language actually used)."""
    table = CASUAL.get(lang) or CASUAL["en"]
    used = lang if lang in CASUAL else "en"
    tpl = table.get((mtype, sentiment)) or table.get((mtype, 0)) or table.get(mtype)
    if tpl is None:
        return "", used
    return tpl.format(**kw), used
