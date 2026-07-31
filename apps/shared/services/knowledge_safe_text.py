import re
from collections.abc import Iterable, Mapping

SAFE_TEXT_MAX_LENGTH = 512
SAFE_LABEL_MAX_LENGTH = 255
SAFE_TOPICS_MAX_COUNT = 10
KB_SAFE_METADATA_KEYS = frozenset(
    {
        "safe_label",
        "kb_safe_description",
        "kb_safe_topics",
    }
)

_TOKEN_RE = re.compile(r"[가-힣]+[0-9]*|[A-Za-z0-9]+")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+")
_URL_RE = re.compile(r"https?://[^\s,;]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PATH_RE = re.compile(
    r"((?:[A-Za-z]:\\|\\\\)[^\s,;]+|/(?:[\w.\-]+/)+[\w.\-]+)",
    re.IGNORECASE,
)
_SECRET_KEY_VALUE_RE = re.compile(
    r"\b(?:api[_-]?key|token|password|secret|authorization|credential)"
    r"\s*[:=]\s*['\"]?[^'\"\s,;]+",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{8,}|ghp_[A-Za-z0-9_]{8,}|xox[baprs]-[A-Za-z0-9-]{8,}|"
    r"bearer\s+[A-Za-z0-9._\-]{8,}|eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)",
    re.IGNORECASE,
)
_KOREAN_SUFFIXES = (
    "으로",
    "에서",
    "에게",
    "부터",
    "까지",
    "처럼",
    "로",
    "와",
    "과",
    "을",
    "를",
    "은",
    "는",
    "이",
    "가",
    "의",
    "에",
    "도",
    "만",
)


def sanitize_safe_text(value: object, *, max_length: int = SAFE_TEXT_MAX_LENGTH) -> str | None:
    if not isinstance(value, str):
        return None
    text = _CONTROL_CHAR_RE.sub(" ", value)
    text = _SECRET_KEY_VALUE_RE.sub(" ", text)
    text = _SECRET_VALUE_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    text = _EMAIL_RE.sub(" ", text)
    text = _PATH_RE.sub(" ", text)
    normalized = " ".join(text.split())
    if not normalized:
        return None
    return normalized[:max_length]


def safe_label_from_text(value: object) -> str | None:
    return sanitize_safe_text(value, max_length=SAFE_LABEL_MAX_LENGTH)


def extract_safe_terms(*values: object, limit: int = 50) -> list[str]:
    terms: list[str] = []
    for value in values:
        text = sanitize_safe_text(value)
        if not text:
            continue
        for raw_token in _TOKEN_RE.findall(text.lower()):
            token = _normalize_token(raw_token)
            if len(token) < 2:
                continue
            if token not in terms:
                terms.append(token)
            if len(terms) >= limit:
                return terms
    return terms


def safe_topics_from_texts(
    values: Iterable[object],
    *,
    limit: int = SAFE_TOPICS_MAX_COUNT,
) -> list[str]:
    return extract_safe_terms(*list(values), limit=limit)


def sanitize_kb_safe_metadata(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}

    safe_metadata: dict[str, object] = {}
    safe_label = safe_label_from_text(value.get("safe_label"))
    if safe_label:
        safe_metadata["safe_label"] = safe_label

    safe_description = sanitize_safe_text(
        value.get("kb_safe_description") or value.get("safe_description")
    )
    if safe_description:
        safe_metadata["kb_safe_description"] = safe_description

    topics = _sanitize_topic_values(
        value.get("kb_safe_topics") or value.get("safe_topics")
    )
    if topics:
        safe_metadata["kb_safe_topics"] = topics

    return safe_metadata


def _sanitize_topic_values(value: object) -> list[str]:
    if isinstance(value, str):
        raw_values: Iterable[object] = re.split(r"[,;\n\r]+", value)
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, str)):
        raw_values = value
    else:
        return []

    topics: list[str] = []
    for item in raw_values:
        safe_topic = sanitize_safe_text(item, max_length=64)
        if not safe_topic or safe_topic in topics:
            continue
        topics.append(safe_topic)
        if len(topics) >= SAFE_TOPICS_MAX_COUNT:
            break
    return topics


def _normalize_token(token: str) -> str:
    normalized = token.strip().lower()
    if not normalized or not re.search(r"[가-힣]", normalized):
        return normalized
    for suffix in _KOREAN_SUFFIXES:
        if normalized.endswith(suffix) and len(normalized) - len(suffix) >= 2:
            return normalized[: -len(suffix)]
    return normalized
