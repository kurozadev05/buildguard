"""Tiny label translator (English / Hindi). Rule text stays English; labels are localised."""
STATUS = {
    "VERIFIED": {"en": "Verified", "hi": "सत्यापित"},
    "REVIEW_REQUIRED": {"en": "Review Required", "hi": "समीक्षा आवश्यक"},
    "FLAGGED": {"en": "Flagged", "hi": "चिह्नित (फ़्लैग)"},
    "PENDING": {"en": "Pending", "hi": "लंबित"},
}
RISK = {
    "Low": {"en": "Low", "hi": "कम"},
    "Moderate": {"en": "Moderate", "hi": "मध्यम"},
    "High": {"en": "High", "hi": "उच्च"},
}
TEST = {
    "slump": {"en": "Slump test", "hi": "स्लम्प परीक्षण"},
    "fresh_temperature": {"en": "Fresh concrete temperature", "hi": "ताज़ा कंक्रीट का तापमान"},
    "cube_compressive_strength": {"en": "Cube compressive strength", "hi": "क्यूब संपीडन शक्ति परीक्षण"},
    "core_strength": {"en": "Core strength test", "hi": "कोर शक्ति परीक्षण"},
    "upv": {"en": "Ultrasonic pulse velocity (UPV)", "hi": "अल्ट्रासोनिक पल्स वेलोसिटी (UPV)"},
    "rebound_hammer": {"en": "Rebound hammer", "hi": "रिबाउंड हैमर"},
}


def pick_lang(accept_language: str | None, explicit: str | None = None) -> str:
    if explicit in ("en", "hi"):
        return explicit
    if accept_language and accept_language.strip().lower().startswith("hi"):
        return "hi"
    return "en"


def _get(table: dict, key: str, lang: str) -> str:
    entry = table.get(key)
    return entry.get(lang, entry["en"]) if entry else key


def status_label(key: str, lang: str = "en") -> str:
    return _get(STATUS, key, lang)


def risk_label(key: str, lang: str = "en") -> str:
    return _get(RISK, key, lang)


def test_label(key: str, lang: str = "en") -> str:
    return _get(TEST, key, lang)
