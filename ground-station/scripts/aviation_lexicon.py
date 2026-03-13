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
    "runway seven left, runway seven right, runway two five left, runway two five right, "
    "runway one five, runway three three, "
    # Taxiways at PANC
    "taxi via Alpha, taxi via Bravo, taxi via Charlie, taxi via Delta, "
    "taxi via Echo, taxi via Foxtrot, taxi via Golf, taxi via Hotel, "
    "taxi via Juliet, taxi via Kilo, taxi via Lima, taxi via Mike, "
    "taxi via November, taxi via Papa, hold short runway, cross runway, "
    # Common ATC commands
    "cleared to land, cleared for takeoff, cleared for the option, "
    "line up and wait, position and hold, "
    "contact Tower one one eight point six, contact Ground one two one point niner, "
    "contact Approach one two four point two, contact Departure, contact Center, "
    "squawk one two zero zero, squawk four five one two, ident, "
    "say again, say altitude, say airspeed, say heading, "
    "turn left heading two seven zero, turn right heading three six zero, fly heading, "
    "climb and maintain one zero thousand, descend and maintain six thousand, "
    "maintain two five zero knots, reduce speed to one eight zero, "
    "flight level three five zero, flight level two four zero, "
    "roger, wilco, affirmative, negative, unable, "
    "traffic twelve o'clock five miles, caution wake turbulence, wind shear, go around, "
    # Tail numbers (N-numbers)
    "November seven three four Charlie Bravo, November five two one Alpha Sierra, "
    "November eight four six Papa, November one two three four five, "
    # Phonetic alphabet
    "Alpha, Bravo, Charlie, Delta, Echo, Foxtrot, Golf, Hotel, "
    "India, Juliet, Kilo, Lima, Mike, November, Oscar, Papa, "
    "Quebec, Romeo, Sierra, Tango, Uniform, Victor, Whiskey, X-ray, Yankee, Zulu, "
    # Number pronunciation — how pilots say digits
    "zero, one, two, three, four, fife, six, seven, eight, niner, "
    "hundred, thousand, point, decimal, "
    # Altitude examples
    "one zero thousand, one one thousand, one two thousand, "
    "three thousand, four thousand five hundred, "
    # Heading examples
    "heading zero niner zero, heading one eight zero, heading two seven zero, heading three six zero, "
    # Speed examples
    "speed two five zero, speed one eight zero knots, "
    # Altimeter examples
    "altimeter two niner niner two, altimeter three zero one five, "
    # Common airlines at ANC
    "Alaska five two seven, Alaska two three, Alaska seven six, "
    "United, Delta, FedEx heavy, UPS heavy, "
    "Polar one two, Atlas four five, Northern Pacific, Ravn, Everts, Era, "
    # Fixes/waypoints near ANC
    "PRIOR, PRIOR intersection, LULIE, TEDDY, ELLAM, BRILL, "
    "direct PRIOR, direct BRILL, "
    "Merrill Field, Lake Hood, Elmendorf, Joint Base, "
    # ATIS/weather
    "information Alpha, information Bravo, information Charlie, "
    "wind two one zero at one five, visibility one zero, ceiling three thousand broken, "
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


def _normalize_tail_number(text):
    """Convert 'November seven three four Charlie Bravo' → 'N734CB'."""
    _PHONETIC_TO_LETTER = {
        'alpha': 'A', 'bravo': 'B', 'charlie': 'C', 'delta': 'D',
        'echo': 'E', 'foxtrot': 'F', 'golf': 'G', 'hotel': 'H',
        'india': 'I', 'juliet': 'J', 'kilo': 'K', 'lima': 'L',
        'mike': 'M', 'november': 'N', 'oscar': 'O', 'papa': 'P',
        'quebec': 'Q', 'romeo': 'R', 'sierra': 'S', 'tango': 'T',
        'uniform': 'U', 'victor': 'V', 'whiskey': 'W', 'x-ray': 'X',
        'xray': 'X', 'yankee': 'Y', 'zulu': 'Z',
    }
    all_words = '|'.join(list(_SPOKEN_TO_DIGIT.keys()) + list(_PHONETIC_TO_LETTER.keys()))

    def replace_tail(m):
        words = m.group(1).split()
        result = 'N'
        for w in words:
            wl = w.lower()
            if wl in _SPOKEN_TO_DIGIT:
                result += _SPOKEN_TO_DIGIT[wl]
            elif wl in _PHONETIC_TO_LETTER:
                result += _PHONETIC_TO_LETTER[wl]
            else:
                result += w
        trail = m.group(2) or ''
        return result + trail

    return re.sub(
        r'November\s+((?:(?:' + all_words + r')\s+){2,5}(?:' + all_words + r'))(\s|$|[,.])',
        replace_tail, text, flags=re.IGNORECASE
    )


def _normalize_speed(text):
    """Convert 'speed two five zero' → 'speed 250'."""
    def replace_speed(m):
        prefix = m.group(1)
        words = m.group(2).split()
        digits = ''.join(_SPOKEN_TO_DIGIT.get(w.lower(), w) for w in words)
        trail = m.group(3) or ''
        return f"{prefix} {digits}{trail}"
    digit_pat = '|'.join(_SPOKEN_TO_DIGIT.keys())
    return re.sub(
        r'(speed\s*(?:to\s*)?|knots?\s+)((?:(?:' + digit_pat + r')\s+){1,2}(?:' + digit_pat + r'))(\s|$|[,.])',
        replace_speed, text, flags=re.IGNORECASE
    )


def _normalize_altitude_thousands(text):
    """Convert 'one zero thousand' → '10,000', 'four thousand five hundred' → '4,500'."""
    digit_pat = '|'.join(_SPOKEN_TO_DIGIT.keys())

    # "one zero thousand" / "one one thousand" etc.
    def replace_ten_thousands(m):
        prefix = m.group(1)
        words = m.group(2).split()
        digits = ''.join(_SPOKEN_TO_DIGIT.get(w.lower(), w) for w in words)
        trail = m.group(3) or ''
        return f"{prefix} {digits},000{trail}"
    text = re.sub(
        r'(maintain|climb and maintain|descend and maintain|at)\s+((?:(?:' + digit_pat + r')\s+){1,2}(?:' + digit_pat + r'))\s+thousand(\s|$|[,.])',
        replace_ten_thousands, text, flags=re.IGNORECASE
    )

    # "four thousand five hundred"
    def replace_x_thousand_y_hundred(m):
        prefix = m.group(1)
        tdig = _SPOKEN_TO_DIGIT.get(m.group(2).lower(), m.group(2))
        hdig = _SPOKEN_TO_DIGIT.get(m.group(3).lower(), m.group(3))
        trail = m.group(4) or ''
        return f"{prefix} {tdig},{hdig}00{trail}"
    text = re.sub(
        r'(maintain|climb and maintain|descend and maintain|at)\s+(' + digit_pat + r')\s+thousand\s+(' + digit_pat + r')\s+hundred(\s|$|[,.])',
        replace_x_thousand_y_hundred, text, flags=re.IGNORECASE
    )

    # Simple "X thousand" — "four thousand"
    def replace_simple_thousand(m):
        prefix = m.group(1)
        tdig = _SPOKEN_TO_DIGIT.get(m.group(2).lower(), m.group(2))
        trail = m.group(3) or ''
        return f"{prefix} {tdig},000{trail}"
    text = re.sub(
        r'(maintain|climb and maintain|descend and maintain|at)\s+(' + digit_pat + r')\s+thousand(\s|$|[,.])',
        replace_simple_thousand, text, flags=re.IGNORECASE
    )

    return text


def _normalize_runway(text):
    """Convert 'runway seven left' → 'runway 7L', 'runway two five right' → 'runway 25R'."""
    _RWY_SUFFIX = {'left': 'L', 'right': 'R', 'center': 'C'}
    digit_pat = '|'.join(_SPOKEN_TO_DIGIT.keys())

    def replace_rwy(m):
        words = m.group(1).split()
        digits = ''.join(_SPOKEN_TO_DIGIT.get(w.lower(), w) for w in words)
        suffix = _RWY_SUFFIX.get(m.group(2).lower(), '') if m.group(2) else ''
        trail = m.group(3) or ''
        return f"runway {digits}{suffix}{trail}"

    return re.sub(
        r'runway\s+((?:(?:' + digit_pat + r')\s+){0,1}(?:' + digit_pat + r'))\s*(?:(left|right|center))?(\s|$|[,.])',
        replace_rwy, text, flags=re.IGNORECASE
    )


def _normalize_callsign(text):
    """Convert 'Alaska five two seven' → 'Alaska 527'."""
    _AIRLINES = [
        'Alaska', 'United', 'Delta', 'FedEx', 'UPS', 'Polar', 'Atlas',
        'Ravn', 'Everts', 'Era', 'Cargolux', 'Korean', 'Nippon',
    ]
    digit_pat = '|'.join(_SPOKEN_TO_DIGIT.keys())
    airline_pat = '|'.join(_AIRLINES)

    def replace_callsign(m):
        airline = m.group(1)
        words = m.group(2).split()
        digits = ''.join(_SPOKEN_TO_DIGIT.get(w.lower(), w) for w in words)
        # Add "heavy"/"super" suffix if present
        suffix = m.group(3) or ''
        trail = m.group(4) or ''
        return f"{airline} {digits}{suffix}{trail}"

    return re.sub(
        r'(' + airline_pat + r')\s+((?:(?:' + digit_pat + r')\s+){0,3}(?:' + digit_pat + r'))(\s+(?:heavy|super))?(\s|$|[,.])',
        replace_callsign, text, flags=re.IGNORECASE
    )


def _normalize_altimeter(text):
    """Convert 'altimeter two niner niner two' → 'altimeter 29.92'."""
    digit_pat = '|'.join(_SPOKEN_TO_DIGIT.keys())

    def replace_altimeter(m):
        words = m.group(1).split()
        digits = ''.join(_SPOKEN_TO_DIGIT.get(w.lower(), w) for w in words)
        trail = m.group(2) or ''
        if len(digits) == 4:
            return f"altimeter {digits[:2]}.{digits[2:]}{trail}"
        return f"altimeter {digits}{trail}"

    return re.sub(
        r'altimeter\s+((?:(?:' + digit_pat + r')\s+){3}(?:' + digit_pat + r'))(\s|$|[,.])',
        replace_altimeter, text, flags=re.IGNORECASE
    )


def _normalize_traffic(text):
    """Convert 'traffic twelve o'clock five miles' → 'traffic 12 o\'clock 5 miles'."""
    _SPOKEN_NUMBERS = {
        'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5',
        'six': '6', 'seven': '7', 'eight': '8', 'nine': '9', 'ten': '10',
        'eleven': '11', 'twelve': '12',
    }
    def replace_clock(m):
        num = _SPOKEN_NUMBERS.get(m.group(1).lower(), m.group(1))
        return f"traffic {num} o'clock"
    text = re.sub(
        r"traffic\s+(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+o'?\s*clock",
        replace_clock, text, flags=re.IGNORECASE
    )
    return text


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
    'speed', 'knots', 'miles', 'clock', 'report', 'say',
    'reduce', 'increase', 'expect', 'vectors', 'proceed',
    'identified', 'radar', 'handoff', 'approved', 'request',
    'clearance', 'readback', 'correction', 'disregard',
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
    text = _normalize_tail_number(text)
    text = _normalize_callsign(text)
    text = _normalize_runway(text)
    text = _normalize_squawk(text)
    text = _normalize_heading(text)
    text = _normalize_altitude(text)
    text = _normalize_altitude_thousands(text)
    text = _normalize_speed(text)
    text = _normalize_frequency(text)
    text = _normalize_altimeter(text)
    text = _normalize_traffic(text)

    # Step 5: Clean up whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    # Step 6: Score aviation relevance
    score = score_aviation_relevance(text)

    return text, score
