"""
HTML Parser und Content Processor für Internal Fetch.

Features:
- HTML zu Text-Extraktion (entfernt Scripts, Styles, etc.)
- Strukturierte Extraktion (Headings, Links, Tabellen)
- Chunk-Verarbeitung für große Dokumente
- Section-Extraktion via CSS-Selektoren

Abhängigkeiten:
- beautifulsoup4 (optional, Fallback auf regex-basiertes Parsing)
- lxml (optional, für schnelleres Parsing)
"""

import re
import html
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

# Versuche BeautifulSoup zu importieren
try:
    from bs4 import BeautifulSoup, NavigableString, Tag
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    logger.warning("beautifulsoup4 nicht installiert - Fallback auf Regex-Parsing")


@dataclass
class ParsedHTML:
    """Ergebnis des HTML-Parsings."""
    title: str = ""
    text: str = ""
    headings: List[Dict[str, Any]] = field(default_factory=list)
    links: List[Dict[str, str]] = field(default_factory=list)
    tables: List[List[List[str]]] = field(default_factory=list)
    meta: Dict[str, str] = field(default_factory=dict)
    char_count: int = 0
    word_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "text": self.text,
            "headings": self.headings,
            "links": self.links,
            "tables": self.tables,
            "meta": self.meta,
            "char_count": self.char_count,
            "word_count": self.word_count,
        }


@dataclass
class ContentChunk:
    """Ein Chunk des geparsten Contents."""
    index: int
    text: str
    start_char: int
    end_char: int
    heading_context: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "text": self.text,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "heading_context": self.heading_context,
        }


# Tags die komplett entfernt werden (inkl. Inhalt)
REMOVE_TAGS = {
    "script", "style", "noscript", "iframe", "object", "embed",
    "svg", "canvas", "template", "head", "meta", "link",
}

# Tags die oft Navigation/Footer sind
NAVIGATION_TAGS = {"nav", "header", "footer", "aside"}

# Block-Level Tags die Zeilenumbrüche erzeugen
BLOCK_TAGS = {
    "p", "div", "section", "article", "main", "aside",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "dl", "dt", "dd",
    "table", "tr", "th", "td",
    "blockquote", "pre", "figure", "figcaption",
    "form", "fieldset", "legend",
    "br", "hr",
}


def parse_html(
    html_content: str,
    extract_mode: str = "text",
    remove_navigation: bool = True,
    remove_selectors: Optional[List[str]] = None,
    preserve_selectors: Optional[List[str]] = None,
) -> ParsedHTML:
    """
    Parst HTML und extrahiert sinnvollen Content.

    Args:
        html_content: Roher HTML-String
        extract_mode:
            - "text": Nur sichtbarer Text
            - "structured": Text + Headings + Links
            - "full": Alles inkl. Tabellen, Listen
        remove_navigation: Nav/Header/Footer entfernen
        remove_selectors: CSS-Selektoren die entfernt werden sollen
        preserve_selectors: Nur diese Selektoren behalten (Whitelist)

    Returns:
        ParsedHTML mit extrahiertem Content
    """
    if BS4_AVAILABLE:
        return _parse_with_bs4(
            html_content, extract_mode, remove_navigation,
            remove_selectors, preserve_selectors
        )
    else:
        return _parse_with_regex(html_content, extract_mode)


def _parse_with_bs4(
    html_content: str,
    extract_mode: str,
    remove_navigation: bool,
    remove_selectors: Optional[List[str]],
    preserve_selectors: Optional[List[str]],
) -> ParsedHTML:
    """Parsing mit BeautifulSoup."""
    # Parser wählen (lxml ist schneller, html.parser ist Fallback)
    try:
        soup = BeautifulSoup(html_content, "lxml")
    except Exception:
        soup = BeautifulSoup(html_content, "html.parser")

    result = ParsedHTML()

    # Title extrahieren
    title_tag = soup.find("title")
    if title_tag:
        result.title = title_tag.get_text(strip=True)

    # Meta-Tags extrahieren
    for meta in soup.find_all("meta"):
        name = meta.get("name", meta.get("property", ""))
        content = meta.get("content", "")
        if name and content:
            result.meta[name] = content

    # Zu entfernende Tags löschen
    for tag_name in REMOVE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Navigation entfernen wenn gewünscht
    if remove_navigation:
        for tag_name in NAVIGATION_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()

    # Custom Selektoren entfernen
    if remove_selectors:
        for selector in remove_selectors:
            try:
                for elem in soup.select(selector):
                    elem.decompose()
            except Exception as e:
                logger.debug(f"Selector '{selector}' fehlgeschlagen: {e}")

    # Preserve-Selektoren: Nur diese behalten
    if preserve_selectors:
        preserved_content = []
        for selector in preserve_selectors:
            try:
                preserved_content.extend(soup.select(selector))
            except Exception:
                pass

        if preserved_content:
            # Neuen Container mit nur den preserved Elements
            new_soup = BeautifulSoup("<div></div>", "html.parser")
            container = new_soup.div
            for elem in preserved_content:
                container.append(elem)
            soup = new_soup

    # Headings extrahieren (für structured/full mode)
    if extract_mode in ("structured", "full"):
        for level in range(1, 7):
            for heading in soup.find_all(f"h{level}"):
                result.headings.append({
                    "level": level,
                    "text": heading.get_text(strip=True),
                    "id": heading.get("id", ""),
                })

    # Links extrahieren (für structured/full mode)
    if extract_mode in ("structured", "full"):
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            text = link.get_text(strip=True)
            if href and text and not href.startswith(("#", "javascript:")):
                result.links.append({
                    "href": href,
                    "text": text[:100],  # Max 100 Zeichen
                })

    # Tabellen extrahieren (nur für full mode)
    if extract_mode == "full":
        for table in soup.find_all("table"):
            table_data = []
            for row in table.find_all("tr"):
                cells = []
                for cell in row.find_all(["td", "th"]):
                    cells.append(cell.get_text(strip=True))
                if cells:
                    table_data.append(cells)
            if table_data:
                result.tables.append(table_data)

    # Text extrahieren
    text = _extract_text_from_soup(soup)
    result.text = text
    result.char_count = len(text)
    result.word_count = len(text.split())

    return result


def _extract_text_from_soup(soup) -> str:
    """Extrahiert bereinigten Text aus BeautifulSoup-Objekt."""
    # Zeilenumbrüche für Block-Elemente einfügen
    for tag in soup.find_all(BLOCK_TAGS):
        if tag.string:
            continue
        # Newline vor Block-Element
        tag.insert_before("\n")

    # Text extrahieren
    text = soup.get_text(separator=" ")

    # Bereinigen
    lines = []
    for line in text.splitlines():
        line = line.strip()
        # Mehrfache Leerzeichen reduzieren
        line = re.sub(r'\s+', ' ', line)
        if line:
            lines.append(line)

    # Mehrfache Leerzeilen reduzieren
    text = "\n".join(lines)
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def _parse_with_regex(html_content: str, extract_mode: str) -> ParsedHTML:
    """Fallback-Parsing mit Regex wenn BeautifulSoup nicht verfügbar."""
    result = ParsedHTML()

    # Title extrahieren
    title_match = re.search(r'<title[^>]*>([^<]+)</title>', html_content, re.IGNORECASE)
    if title_match:
        result.title = html.unescape(title_match.group(1).strip())

    # Script, Style, etc. entfernen
    text = html_content
    for tag in REMOVE_TAGS:
        text = re.sub(rf'<{tag}[^>]*>.*?</{tag}>', '', text, flags=re.IGNORECASE | re.DOTALL)

    # HTML-Kommentare entfernen
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)

    # Block-Tags durch Newlines ersetzen
    for tag in BLOCK_TAGS:
        text = re.sub(rf'</?{tag}[^>]*>', '\n', text, flags=re.IGNORECASE)

    # Alle übrigen Tags entfernen
    text = re.sub(r'<[^>]+>', ' ', text)

    # HTML-Entities dekodieren
    text = html.unescape(text)

    # Bereinigen
    lines = []
    for line in text.splitlines():
        line = re.sub(r'\s+', ' ', line.strip())
        if line:
            lines.append(line)

    text = "\n".join(lines)
    text = re.sub(r'\n{3,}', '\n\n', text)

    result.text = text.strip()
    result.char_count = len(result.text)
    result.word_count = len(result.text.split())

    # Headings extrahieren (basic)
    if extract_mode in ("structured", "full"):
        for level in range(1, 7):
            for match in re.finditer(rf'<h{level}[^>]*>([^<]+)</h{level}>', html_content, re.IGNORECASE):
                result.headings.append({
                    "level": level,
                    "text": html.unescape(match.group(1).strip()),
                    "id": "",
                })

    # Links extrahieren (basic)
    if extract_mode in ("structured", "full"):
        for match in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]+)</a>', html_content, re.IGNORECASE):
            href, text = match.groups()
            if href and text and not href.startswith(("#", "javascript:")):
                result.links.append({
                    "href": href,
                    "text": html.unescape(text.strip())[:100],
                })

    return result


def chunk_content(
    text: str,
    max_chunk_size: int = 8000,
    overlap: int = 200,
    split_by: str = "semantic",
    headings: Optional[List[Dict[str, Any]]] = None,
) -> List[ContentChunk]:
    """
    Teilt großen Content in verarbeitbare Chunks.

    Args:
        text: Zu teilender Text
        max_chunk_size: Maximale Chunk-Größe in Zeichen
        overlap: Überlappung zwischen Chunks
        split_by: Splitting-Strategie
            - "semantic": An Überschriften/Absätzen
            - "sentence": An Satzenden
            - "fixed": Feste Zeichenzahl
        headings: Optional - Headings für Kontext

    Returns:
        Liste von ContentChunk-Objekten
    """
    if not text or len(text) <= max_chunk_size:
        return [ContentChunk(
            index=0,
            text=text,
            start_char=0,
            end_char=len(text),
            heading_context="",
        )]

    if split_by == "semantic":
        return _chunk_semantic(text, max_chunk_size, overlap, headings)
    elif split_by == "sentence":
        return _chunk_by_sentence(text, max_chunk_size, overlap)
    else:
        return _chunk_fixed(text, max_chunk_size, overlap)


def _chunk_semantic(
    text: str,
    max_chunk_size: int,
    overlap: int,
    headings: Optional[List[Dict[str, Any]]],
) -> List[ContentChunk]:
    """Semantisches Chunking an Absätzen und Überschriften."""
    chunks = []

    # An doppelten Newlines (Absätze) splitten
    paragraphs = re.split(r'\n\n+', text)

    current_chunk = ""
    current_start = 0
    chunk_index = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # Passt Paragraph in aktuellen Chunk?
        if len(current_chunk) + len(para) + 2 <= max_chunk_size:
            if current_chunk:
                current_chunk += "\n\n"
            current_chunk += para
        else:
            # Aktuellen Chunk speichern
            if current_chunk:
                end_char = current_start + len(current_chunk)
                chunks.append(ContentChunk(
                    index=chunk_index,
                    text=current_chunk,
                    start_char=current_start,
                    end_char=end_char,
                    heading_context=_find_heading_context(current_start, headings),
                ))
                chunk_index += 1

                # Überlappung berechnen
                if overlap > 0:
                    overlap_text = current_chunk[-overlap:] if len(current_chunk) > overlap else current_chunk
                    current_start = end_char - len(overlap_text)
                    current_chunk = overlap_text + "\n\n" + para
                else:
                    current_start = end_char
                    current_chunk = para
            else:
                current_chunk = para

    # Letzten Chunk speichern
    if current_chunk:
        chunks.append(ContentChunk(
            index=chunk_index,
            text=current_chunk,
            start_char=current_start,
            end_char=current_start + len(current_chunk),
            heading_context=_find_heading_context(current_start, headings),
        ))

    return chunks


def _chunk_by_sentence(
    text: str,
    max_chunk_size: int,
    overlap: int,
) -> List[ContentChunk]:
    """Chunking an Satzenden."""
    # Sätze erkennen (vereinfacht)
    sentences = re.split(r'(?<=[.!?])\s+', text)

    chunks = []
    current_chunk = ""
    current_start = 0
    chunk_index = 0

    for sentence in sentences:
        if len(current_chunk) + len(sentence) + 1 <= max_chunk_size:
            if current_chunk:
                current_chunk += " "
            current_chunk += sentence
        else:
            if current_chunk:
                end_char = current_start + len(current_chunk)
                chunks.append(ContentChunk(
                    index=chunk_index,
                    text=current_chunk,
                    start_char=current_start,
                    end_char=end_char,
                ))
                chunk_index += 1
                current_start = end_char - overlap if overlap > 0 else end_char
                current_chunk = current_chunk[-overlap:] + " " + sentence if overlap > 0 else sentence
            else:
                current_chunk = sentence

    if current_chunk:
        chunks.append(ContentChunk(
            index=chunk_index,
            text=current_chunk,
            start_char=current_start,
            end_char=current_start + len(current_chunk),
        ))

    return chunks


def _chunk_fixed(
    text: str,
    max_chunk_size: int,
    overlap: int,
) -> List[ContentChunk]:
    """Festes Chunking nach Zeichenzahl."""
    chunks = []
    start = 0
    chunk_index = 0

    while start < len(text):
        end = min(start + max_chunk_size, len(text))

        # Versuche an Wortgrenze zu enden
        if end < len(text):
            space_pos = text.rfind(" ", start, end)
            if space_pos > start + max_chunk_size // 2:
                end = space_pos

        chunks.append(ContentChunk(
            index=chunk_index,
            text=text[start:end],
            start_char=start,
            end_char=end,
        ))

        chunk_index += 1
        start = end - overlap if overlap > 0 else end

    return chunks


def _find_heading_context(position: int, headings: Optional[List[Dict[str, Any]]]) -> str:
    """Findet den Heading-Kontext für eine Position."""
    if not headings:
        return ""

    # Vereinfachte Implementierung - in Produktion würde man
    # die Position der Headings im Originaltext tracken
    return ""


def extract_section(
    html_content: str,
    selector: str,
    include_children: bool = True,
) -> Optional[ParsedHTML]:
    """
    Extrahiert einen bestimmten Abschnitt aus HTML.

    Args:
        html_content: HTML-Dokument
        selector: CSS-Selektor, Heading-Text oder Element-ID
        include_children: Unterelemente einschließen

    Returns:
        ParsedHTML für den Abschnitt oder None
    """
    if not BS4_AVAILABLE:
        logger.warning("Section-Extraktion benötigt beautifulsoup4")
        return None

    try:
        soup = BeautifulSoup(html_content, "lxml")
    except Exception:
        soup = BeautifulSoup(html_content, "html.parser")

    element = None

    # 1. Versuche als CSS-Selektor
    try:
        element = soup.select_one(selector)
    except Exception:
        pass

    # 2. Versuche als ID
    if element is None:
        element = soup.find(id=selector.lstrip("#"))

    # 3. Versuche als Heading-Text
    if element is None:
        for level in range(1, 7):
            for heading in soup.find_all(f"h{level}"):
                if selector.lower() in heading.get_text(strip=True).lower():
                    element = heading
                    break
            if element:
                break

    if element is None:
        return None

    # Bei Headings: Alle Inhalte bis zum nächsten gleichen/höheren Heading
    if element.name and element.name.startswith("h") and include_children:
        level = int(element.name[1])
        content_elements = [element]

        for sibling in element.find_next_siblings():
            if sibling.name and sibling.name.startswith("h"):
                sibling_level = int(sibling.name[1])
                if sibling_level <= level:
                    break
            content_elements.append(sibling)

        # Neuen Container erstellen
        container_html = "".join(str(el) for el in content_elements)
        return parse_html(container_html, extract_mode="structured")

    # Normales Element
    return parse_html(str(element), extract_mode="structured")


def format_parsed_output(
    parsed: ParsedHTML,
    max_length: int = 30000,
    include_toc: bool = True,
    include_links: bool = True,
) -> Tuple[str, int]:
    """
    Formatiert ParsedHTML für die Ausgabe.

    Args:
        parsed: Geparster HTML-Content
        max_length: Maximale Ausgabelänge
        include_toc: Inhaltsverzeichnis einfügen
        include_links: Links-Liste einfügen

    Returns:
        Tuple (formatierter Text, Anzahl verbleibender Zeichen)
    """
    output = ""

    # Title
    if parsed.title:
        output += f"# {parsed.title}\n\n"

    # Inhaltsverzeichnis
    if include_toc and parsed.headings:
        output += "## Inhaltsverzeichnis\n"
        for h in parsed.headings[:20]:  # Max 20 Headings
            indent = "  " * (h["level"] - 1)
            output += f"{indent}- {h['text']}\n"
        output += "\n"

    # Haupttext
    text = parsed.text
    remaining = 0

    if len(output) + len(text) > max_length:
        available = max_length - len(output) - 200  # Reserve für Footer
        text = text[:available]
        remaining = parsed.char_count - len(text)
        text += f"\n\n... [+{remaining:,} Zeichen nicht angezeigt]"

    output += text

    # Links
    if include_links and parsed.links and remaining == 0:
        links_section = "\n\n## Links\n"
        for link in parsed.links[:30]:  # Max 30 Links
            links_section += f"- [{link['text']}]({link['href']})\n"

        if len(output) + len(links_section) <= max_length:
            output += links_section

    return output, remaining
