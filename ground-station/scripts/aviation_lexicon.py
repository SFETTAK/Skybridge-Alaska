"""
Aviation Lexicon & Post-Processor — SkyBridge Alaska
Domain-specific vocabulary and correction rules for ATC transcript cleanup.
Tuned for Ted Stevens Anchorage International (PANC) and Anchorage TRACON.
"""

import re

# ─────────────────────────────────────────────────────────────────────────────
# WHISPER PROMPT — primes the model with PANC-specific vocabulary
# ─────────────────────────────────────────────────────────────────────────────

WHISPER_PROMPT_PANC = (
    # Facilities
    "Anchorage Tower, Anchorage Ground, Anchorage Approach, Anchorage Departure, "
    "Anchorage Center, Anchorage Clearance, Ted Stevens Tower, "
    # Runways at PANC
    "runway seven left, runway seven right, runway two-five left, runway two-five right, "
    "runway one-five, runway three-three, "
    # Taxiways at PANC
    "taxi via Alpha, taxi via Bravo, taxi via Charlie, taxi via Delta, "
    "taxi via Echo, taxi via Foxtrot, taxi via Golf, taxi via Hotel, "
    "taxi via Juliet, taxi via Kilo, taxi via Lima, taxi via Mike, "
    "taxi via November, taxi via Papa, hold short runway, cross runway, "
    # Common ATC commands
    "cleared to land, cleared for takeoff, cleared for the option, "
    "line up and wait, position and hold, "
    "contact Tower, contact Ground, contact Approach, contact Departure, contact Center, "
    "squawk, ident, say again, say altitude, say airspeed, "
    "turn left heading, turn right heading, fly heading, "
    "climb and maintain, descend and maintain, maintain, "
    "flight level, angels, "
    "roger, wilco, affirmative, negative, unable, "
    "traffic, caution wake turbulence, wind shear, go around, "
    # Phonetic alphabet
    "Alpha, Bravo, Charlie, Delta, Echo, Foxtrot, Golf, Hotel, "
    "India, Juliet, Kilo, Lima, Mike, November, Oscar, Papa, "
    "Quebec, Romeo, Sierra, Tango, Uniform, Victor, Whiskey, X-ray, Yankee, Zulu, "
    # Number pronunciation
    "zero, one, two, three, four, five, six, seven, eight, niner, "
    "hundred, thousand, flight level, point, decimal, "
    # Common airlines at ANC
    "Alaska seven, Alaska one-five, United, Delta, FedEx, UPS, "
    "Polar, Atlas, Northern Pacific, Ravn, Everts, Era, "
    "November seven-three-four Charlie Bravo, "
    # Fixes/waypoints near ANC
    "PRIOR, PRIOR intersection, LULIE, TEDDY, ELLAM, BRILL, "
    "Merrill Field, Lake Hood, Elmendorf, "
    # ATIS/weather
    "information Alpha, information Bravo, altimeter, wind, visibility, ceiling, "
    "VFR, IFR, MVFR, LIFR"
)

# ─────────────────────────────────────────────────────────────────────────────
# REPETITION SUPPRESSOR — Whisper hallucinates loops on noise
# ─────────────────────────────────────────────────────────────────────────────

def _suppress_repetitions(text, max_repeats=3):
    """Detect and truncate repeated phrases/words that Whisper hallucinates."""
    # Pattern: same word repeated more than max_repeats times
    text = re.sub(
        r'\b(\w+(?:-\w+)?)((?:\s+\1){' + str(max_repeats) + r',})',
        lambda m: (m.group(1) + ' ') * min(max_repeats, m.group(0).count(m.group(1))),
        text
    )
    # Pattern: same number-phrase repeated (e.g. "six-point-three-six-point-three-six-point-three")
    text = re.sub(
        r'((?:\w+-){2,5}\w+?)(?:-\1){2,}',
        r'\1',
        text
    )
    # Pattern: "zero zero zero zero..." or "six six six six..."
    text = re.sub(
        r'\b(zero|one|two|three|four|five|six|seven|eight|niner?|nine)(?:\s+\1){3,}\b',
        lambda m: m.group(1),
        text, flags=re.IGNORECASE
    )
    return text.strip()

# ─────────────────────────────────────────────────────────────────────────────
# PHONETIC CORRECTIONS — fix common Whisper misrecognitions
# ─────────────────────────────────────────────────────────────────────────────

# Whisper often mishears the NATO phonetic alphabet
_PHONETIC_FIXES = {
    # Common Whisper errors → correct phonetic
    r'\byankee?\b': 'Yankee',
    r'\byank[ie]\b': 'Yankee',
    r'\blimo\b': 'Lima',
    r'\blima\b': 'Lima',
    r'\bjuliette?\b': 'Juliet',
    r'\bsiera\b': 'Sierra',
    r'\bwhiskey?\b': 'Whiskey',
    r'\bcharlie?\b': 'Charlie',
    r'\bfoxtrot\b': 'Foxtrot',
    r'\bx-?ray\b': 'X-ray',
    r'\bx ray\b': 'X-ray',
    r'\bpoppa\b': 'Papa',
    r'\bquebec\b': 'Quebec',
    r'\b(?:zulu|zoolu)\b': 'Zulu',
    r'\bgolph?\b': 'Golf',
    r'\becho\b': 'Echo',
    r'\b(?:tango|tanggo)\b': 'Tango',
    r'\bromeo\b': 'Romeo',
    r'\boscar\b': 'Oscar',
    r'\bindia\b': 'India',
    r'\buniform\b': 'Uniform',
    r'\bvictor\b': 'Victor',
    r'\bbravo\b': 'Bravo',
    r'\balpha\b': 'Alpha',
    r'\bdelta\b': 'Delta',
    r'\b(?:november)\b': 'November',
    r'\bkilo\b': 'Kilo',
    r'\b(?:hotel|hote?l)\b': 'Hotel',
    r'\bmikey?\b': 'Mike',
}

# ─────────────────────────────────────────────────────────────────────────────
# ATC COMMAND CORRECTIONS
# ─────────────────────────────────────────────────────────────────────────────

_ATC_FIXES = [
    # Facility names
    (r'\b(?:anger?|anchor|anker)\s*(?:age?|idge?)\s*(?:tower|tow[ae]r)\b', 'Anchorage Tower'),
    (r'\b(?:anger?|anchor|anker)\s*(?:age?|idge?)\s*(?:ground|groun?d)\b', 'Anchorage Ground'),
    (r'\b(?:anger?|anchor|anker)\s*(?:age?|idge?)\s*(?:approach|approch)\b', 'Anchorage Approach'),
    (r'\b(?:anger?|anchor|anker)\s*(?:age?|idge?)\s*(?:departure|departur)\b', 'Anchorage Departure'),
    (r'\b(?:anger?|anchor|anker)\s*(?:age?|idge?)\s*(?:center|centre|clearance)\b', 'Anchorage Center'),
    # Common command phrases
    (r'\bcleared?\s*(?:to|for|two)\s*land\b', 'cleared to land'),
    (r'\bcleared?\s*(?:for|four)\s*(?:take\s*off|takeoff)\b', 'cleared for takeoff'),
    (r'\bcontact\s*(?:down|groun?d|gown)\b', 'contact Ground'),
    (r'\bcontact\s*(?:tower?|tow[ae]r|power)\b', 'contact Tower'),
    (r'\bcontact\s*(?:approach|approch|a?proach)\b', 'contact Approach'),
    (r'\bcontact\s*(?:departure|departur|deparcher)\b', 'contact Departure'),
    (r'\bcontact\s*(?:center|centre|cen[td]er)\b', 'contact Center'),
    (r'\b(?:line\s*up\s*and|lineup\s*and?)\s*wait\b', 'line up and wait'),
    (r'\b(?:climb|clime?)\s*(?:and\s*)?maintain\b', 'climb and maintain'),
    (r'\b(?:descend|decent?d?|descent)\s*(?:and\s*)?maintain\b', 'descend and maintain'),
    (r'\bturn\s*(?:left|lift)\s*(?:heading|head|headin)\b', 'turn left heading'),
    (r'\bturn\s*(?:right|rite?)\s*(?:heading|head|headin)\b', 'turn right heading'),
    (r'\bfly\s*(?:heading|head|headin)\b', 'fly heading'),
    (r'\bgo\s*around\b', 'go around'),
    (r'\bsay\s*again\b', 'say again'),
    (r'\bsay\s*altitude\b', 'say altitude'),
    (r'\b(?:roger|rodger|rojer)\b', 'roger'),
    (r'\b(?:wilco|will\s*co)\b', 'wilco'),
    (r'\b(?:affirmative|affirm)\b', 'affirmative'),
    (r'\b(?:negative|negat?ive?)\b', 'negative'),
    (r'\bsquawk\b', 'squawk'),
    (r'\bident\b', 'ident'),
    (r'\b(?:hold\s*short)\b', 'hold short'),
    (r'\bcross\s*runway\b', 'cross runway'),
    # Runway references
    (r'\brunway\s*(\d)', r'runway \1'),
    # "taxi via" patterns
    (r'\btaxi\s*(?:via|by|bi|buy)\b', 'taxi via'),
]

# ─────────────────────────────────────────────────────────────────────────────
# NUMBER NORMALIZATION — standardize aviation number readbacks
# ─────────────────────────────────────────────────────────────────────────────

_SPOKEN_TO_DIGIT = {
    'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'tree': '3',
    'four': '4', 'five': '5', 'fife': '5', 'six': '6', 'seven': '7',
    'eight': '8', 'nine': '9', 'niner': '9',
}


def _normalize_squawk(text):
    """Convert 'squawk one two zero zero' → 'squawk 1200'."""
    def replace_squawk(m):
        words = m.group(1).split()
        digits = ''.join(_SPOKEN_TO_DIGIT.get(w.lower(), w) for w in words)
        trail = m.group(2) or ''
        return f"squawk {digits}{trail}"
    digit_pat = '|'.join(_SPOKEN_TO_DIGIT.keys())
    return re.sub(
        r'squawk\s+((?:(?:' + digit_pat + r')\s+){3}(?:' + digit_pat + r'))(\s|$|[,.])',
        replace_squawk, text, flags=re.IGNORECASE
    )


def _normalize_heading(text):
    """Convert 'heading two seven zero' → 'heading 270'."""
    def replace_heading(m):
        prefix = m.group(1)
        words = m.group(2).split()
        digits = ''.join(_SPOKEN_TO_DIGIT.get(w.lower(), w) for w in words)
        trail = m.group(3) or ''
        return f"{prefix} {digits}{trail}"
    digit_pat = '|'.join(_SPOKEN_TO_DIGIT.keys())
    return re.sub(
        r'(heading|fly heading)\s+((?:(?:' + digit_pat + r')\s+){2}(?:' + digit_pat + r'))(\s|$|[,.])',
        replace_heading, text, flags=re.IGNORECASE
    )


def _normalize_altitude(text):
    """Convert 'flight level three five zero' → 'FL350'."""
    def replace_fl(m):
        words = m.group(1).split()
        digits = ''.join(_SPOKEN_TO_DIGIT.get(w.lower(), w) for w in words)
        trail = m.group(2) or ''
        return f"FL{digits}{trail}"
    digit_pat = '|'.join(_SPOKEN_TO_DIGIT.keys())
    return re.sub(
        r'flight\s*level\s+((?:(?:' + digit_pat + r')\s+){1,2}(?:' + digit_pat + r'))(\s|$|[,.])',
        replace_fl, text, flags=re.IGNORECASE
    )


def _normalize_frequency(text):
    """Convert 'one two six point four' → '126.4'."""
    def replace_freq(m):
        prefix = m.group(1)
        whole_words = m.group(2).split()
        decimal_words = m.group(3).split()
        whole = ''.join(_SPOKEN_TO_DIGIT.get(w.lower(), w) for w in whole_words)
        dec = ''.join(_SPOKEN_TO_DIGIT.get(w.lower(), w) for w in decimal_words)
        return f"{prefix} {whole}.{dec}"
    digit_pat = '|'.join(_SPOKEN_TO_DIGIT.keys())
    return re.sub(
        r'(contact\s+\w+|on)\s+((?:(?:' + digit_pat + r')\s*){2,3})\s*(?:point|decimal)\s*((?:(?:' + digit_pat + r')\s*){1,3})',
        replace_freq, text, flags=re.IGNORECASE
    )


# ─────────────────────────────────────────────────────────────────────────────
# JUNK FILTER — detect and discard hallucinated nonsense
# ─────────────────────────────────────────────────────────────────────────────

# Words that should NEVER appear in ATC comms — hallucination markers
_HALLUCINATION_MARKERS = {
    'village', 'child', 'brother', 'mother', 'father', 'kitchen',
    'bedroom', 'dinner', 'breakfast', 'lunch', 'school', 'church',
    'movie', 'music', 'song', 'dance', 'football', 'soccer',
    'monkey', 'monkeys', 'peanut', 'stadium', 'sucker',
    'excited', 'beautiful', 'terrible', 'wonderful',
    'subscribe', 'youtube', 'instagram', 'facebook', 'twitter',
    'podcast', 'episode', 'chapter', 'story', 'movie',
}

# Words that commonly appear in REAL ATC comms
_ATC_KEYWORDS = {
    'runway', 'taxi', 'tower', 'ground', 'approach', 'departure', 'center',
    'cleared', 'contact', 'heading', 'maintain', 'climb', 'descend',
    'squawk', 'ident', 'roger', 'wilco', 'affirmative', 'negative',
    'traffic', 'caution', 'hold', 'short', 'cross', 'takeoff',
    'land', 'position', 'turn', 'left', 'right', 'direct',
    'altitude', 'flight', 'level', 'thousand', 'hundred',
    'alpha', 'bravo', 'charlie', 'delta', 'echo', 'foxtrot',
    'golf', 'hotel', 'india', 'juliet', 'kilo', 'lima', 'mike',
    'november', 'oscar', 'papa', 'quebec', 'romeo', 'sierra',
    'tango', 'uniform', 'victor', 'whiskey', 'yankee', 'zulu',
    'heavy', 'super', 'alaska', 'united', 'fedex', 'polar',
    'wind', 'visibility', 'ceiling', 'altimeter', 'atis',
    'information', 'copy', 'frequency', 'point', 'decimal',
}


def score_aviation_relevance(text):
    """Score 0.0–1.0 how likely this text is real ATC comms vs hallucination."""
    words = set(text.lower().split())
    if not words:
        return 0.0

    hallucination_hits = len(words & _HALLUCINATION_MARKERS)
    atc_hits = len(words & _ATC_KEYWORDS)

    # Any hallucination marker is a strong negative signal
    if hallucination_hits > 0:
        return max(0.0, (atc_hits - hallucination_hits * 3) / max(len(words), 1))

    # Score based on ATC keyword density
    return min(1.0, atc_hits / max(len(words) * 0.3, 1))


# ─────────────────────────────────────────────────────────────────────────────
# MAIN POST-PROCESSOR
# ─────────────────────────────────────────────────────────────────────────────

def post_process(text, min_relevance=0.1):
    """Apply all aviation corrections to a raw Whisper transcript.

    Returns (cleaned_text, relevance_score).
    If relevance_score < min_relevance, the transcript is likely hallucination.
    """
    if not text or not text.strip():
        return "", 0.0

    # Step 1: Suppress repetitive hallucinations
    text = _suppress_repetitions(text)

    # Step 2: Apply phonetic alphabet corrections
    for pattern, replacement in _PHONETIC_FIXES.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # Step 3: Apply ATC command corrections
    for pattern, replacement in _ATC_FIXES:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # Step 4: Normalize numbers
    text = _normalize_squawk(text)
    text = _normalize_heading(text)
    text = _normalize_altitude(text)
    text = _normalize_frequency(text)

    # Step 5: Clean up whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    # Step 6: Score aviation relevance
    score = score_aviation_relevance(text)

    return text, score
