import re


def strip_html(html: str) -> str:
    """Remove HTML tags and normalize whitespace."""
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s{2,}", " ", text).strip()


def chunk_text(text: str, max_chars: int) -> list[str]:
    """Split text into chunks of at most max_chars, breaking at newlines."""
    if len(text) <= max_chars:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_chars:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_chars)
        if split_at == -1:
            split_at = max_chars
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks
