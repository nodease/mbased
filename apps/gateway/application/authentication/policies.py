import unicodedata


def normalize_login_account(account: str) -> str:
    normalized = unicodedata.normalize("NFKC", account).strip().casefold()
    if not normalized:
        raise ValueError("account identity is empty")
    return normalized
