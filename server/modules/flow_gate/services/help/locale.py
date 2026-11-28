"""Language names only; no service or database dependencies."""
SUPPORTED_LOCALES = ("ko", "en", "ja")
FALLBACK_LOCALE = "ko"

def normalize_locale(candidate):
    value = (candidate or "").strip()
    return value if value in SUPPORTED_LOCALES else FALLBACK_LOCALE
