# Context Trimming Optimization - Design Document

## Aktuelle Implementierung

### 1. Trimming-Funktion (`orchestrator.py`)

```python
def _trim_messages_to_limit(messages, max_tokens):
    # Priorität:
    # 1. System-Prompt bleibt unverändert
    # 2. Letzte User-Nachricht bleibt unverändert
    # 3. Tool-Ergebnisse werden gekürzt (größte zuerst)
    # 4. Ältere Assistant-Nachrichten werden gekürzt
```

**Ablauf:**
1. Schätzt aktuelle Token-Anzahl mit `estimate_messages_tokens()`
2. Findet große Tool-Ergebnisse (>500 Token)
3. Sortiert nach Größe (größte zuerst)
4. Kürzt auf Ziel-Länge mit `truncate_text_to_tokens()`
5. Bei Bedarf: Ältere Assistant-Nachrichten kürzen

### 2. read_file Tool (`tools.py`)

```python
async def read_file(path, encoding, offset, limit, show_line_numbers):
    content = file_path.read_text()
    lines = content.splitlines()
    # Offset/Limit anwenden
    # Mit Zeilennummern formatieren
    return formatted_content
```

**Aktuell:**
- Liest komplette Datei (oder Bereich mit offset/limit)
- Keine automatische Größenbegrenzung
- Zeilennummern erhöhen Größe (~10% Overhead)

---

## Probleme der aktuellen Implementierung

### Problem 1: Blindes Abschneiden
```
Datei: 2000 Zeilen Code
Gekürzt: Zeilen 1-500 behalten
Verloren: Zeilen 501-2000 (möglicherweise relevanter Code!)
```

### Problem 2: Keine semantische Awareness
- Edit-Anfrage: "Ändere Funktion XY"
- Trimming entfernt Funktion XY weil sie am Ende der Datei steht

### Problem 3: Unnötige Daten im Kontext
- Zeilennummern verbrauchen Token
- Volle Datei gelesen, aber nur Teil relevant
- Import-Statements und Boilerplate nehmen Platz weg

### Problem 4: Modell-Switch ohne Kontext-Reduktion
```
1. read_file (qwen-7b): 8000 Token Dateiinhalt → OK
2. edit_file (mistral-678b): 8000 Token > 24000 Limit → Trimming nötig
```

---

## Optimierungsvorschläge

### Option A: Pre-Trimming in read_file (Empfohlen)

**Idee:** Dateiinhalt bereits beim Lesen begrenzen.

```python
# In read_file Tool:
MAX_FILE_TOKENS = 4000  # Konfigurierbar

async def read_file(path, ...):
    content = file_path.read_text()
    lines = content.splitlines()

    estimated_tokens = estimate_tokens("\n".join(lines))

    if estimated_tokens > MAX_FILE_TOKENS:
        # Strategie 1: Anfang + Ende behalten
        head_lines = lines[:100]
        tail_lines = lines[-100:]
        middle_indicator = f"\n... [{len(lines)-200} Zeilen ausgelassen] ...\n"
        content = "\n".join(head_lines) + middle_indicator + "\n".join(tail_lines)

        # Strategie 2: Nur Anfang + Hinweis
        # content = "\n".join(lines[:200]) + f"\n... [{len(lines)-200} weitere Zeilen, nutze offset/limit]"

    return formatted_content
```

**Vorteile:**
- Kontext bleibt kontrolliert
- User sieht sofort was verfügbar ist
- Explizite Aufforderung für gezielte Abfragen

**Implementierung:** ~30min

---

### Option B: Semantisches Trimming für Code

**Idee:** Code-Struktur verstehen und intelligent kürzen.

```python
def smart_trim_code(content: str, language: str, max_tokens: int) -> str:
    """Kürzt Code unter Beibehaltung der Struktur."""

    # 1. Parse Code-Struktur
    if language in ("python", "java"):
        functions = extract_functions(content)
        classes = extract_classes(content)
        imports = extract_imports(content)

    # 2. Prioritäten:
    # - Imports immer behalten (klein, wichtig)
    # - Klassen-/Funktionssignaturen behalten
    # - Funktionskörper kürzen (docstrings + ...[code])

    # 3. Output:
    # class MyClass:
    #     """Docstring..."""
    #
    #     def method1(self, arg: str) -> bool:
    #         """Does X."""
    #         ... [15 Zeilen Code]
    #
    #     def method2(self):
    #         ... [8 Zeilen Code]
```

**Vorteile:**
- Behält Überblick über Dateistruktur
- KI kann gezielt nachfragen ("Zeige mir method1 komplett")
- Maximale Information pro Token

**Aufwand:** 2-3h (mit tree-sitter oder regex-basiert)

---

### Option C: Intelligentes Message-Trimming

**Idee:** Beim Trimming die letzte User-Anfrage berücksichtigen.

```python
def _trim_messages_to_limit(messages, max_tokens, user_intent=None):
    # 1. Analysiere User-Intent
    if user_intent:
        keywords = extract_keywords(user_intent)
        # z.B. ["Funktion", "calculateTax", "ändern"]

    # 2. Bewerte Tool-Ergebnisse nach Relevanz
    for tool_result in tool_results:
        if contains_keywords(tool_result.content, keywords):
            tool_result.priority = HIGH
        else:
            tool_result.priority = LOW

    # 3. Trimme niedrig-priorisierte zuerst
    for result in sorted(tool_results, key=lambda r: r.priority):
        if tokens_to_remove <= 0:
            break
        # Kürze dieses Ergebnis
```

**Vorteile:**
- Behält relevanten Kontext
- Weniger "blinde" Kürzung
- Bessere Ergebnisse bei Edit-Operationen

**Aufwand:** 1-2h

---

### Option D: File-Chunking mit Kontext-Fenster

**Idee:** Große Dateien in Chunks aufteilen, nur relevante Chunks behalten.

```python
def chunk_file(content: str, chunk_size: int = 500) -> List[Chunk]:
    """Teilt Datei in überlappende Chunks."""
    lines = content.splitlines()
    chunks = []

    for i in range(0, len(lines), chunk_size - 50):  # 50 Zeilen Overlap
        chunk_lines = lines[i:i + chunk_size]
        chunks.append(Chunk(
            start_line=i + 1,
            end_line=i + len(chunk_lines),
            content="\n".join(chunk_lines),
            summary=summarize_chunk(chunk_lines)  # Optional: LLM-Zusammenfassung
        ))

    return chunks

# Bei read_file:
# - Gib Chunk-Übersicht zurück statt vollständiger Datei
# - "Datei hat 5 Chunks: [Imports], [Klasse A], [Klasse B], [Tests], [Main]"
# - User/KI kann gezielt Chunks anfordern
```

**Vorteile:**
- Skaliert für sehr große Dateien
- Ermöglicht gezielte Navigation
- Kombinierbar mit Zusammenfassungen

**Aufwand:** 2-4h

---

## Empfohlene Implementierung

### Phase 1: Quick Wins (30min)

1. **Max-Größe in read_file:**
```yaml
# config.yaml
file_operations:
  max_read_tokens: 4000  # Neue Option
  truncation_strategy: "head_tail"  # oder "head_only"
```

2. **Zeilennummern optional:**
```python
# Zeilennummern nur bei kleinen Dateien
show_line_numbers = len(lines) < 200
```

### Phase 2: Verbessertes Trimming (1-2h)

1. **Relevanz-basiertes Trimming:**
```python
# Behalte Tool-Ergebnisse die Keywords der User-Anfrage enthalten
user_message = messages[-1]["content"]
keywords = extract_keywords(user_message)
```

2. **Strukturiertes Code-Trimming:**
```python
# Für .py/.java Dateien: Behalte Signaturen
if file_ext in (".py", ".java"):
    content = extract_signatures_and_docstrings(content)
```

### Phase 3: Advanced Features (2-4h)

1. **Chunk-basierte Navigation**
2. **LLM-basierte Zusammenfassungen**
3. **Caching von Dateianalysen**

---

## Konfiguration

```yaml
# config.yaml
context_trimming:
  enabled: true

  # Pre-Trimming bei read_file
  max_file_tokens: 4000
  truncation_strategy: "head_tail"  # head_only, smart, semantic

  # Post-Trimming im Orchestrator
  trim_priority: "size_first"  # relevance_first, age_first
  keep_last_n_tool_results: 3

  # Semantisches Trimming (optional)
  semantic_trimming:
    enabled: true
    languages: ["python", "java"]
    keep_signatures: true
    keep_docstrings: true
```

---

## Metriken zur Erfolgsmessung

1. **500-Error-Rate**: Sollte auf ~0% sinken
2. **Durchschnittliche Kontext-Größe**: Tracking vor/nach Trimming
3. **Edit-Erfolgsrate**: Wie oft gelingt Edit nach read_file?
4. **Token-Effizienz**: Relevante Info / Gesamte Tokens

---

---

## KRITISCHER BUG GEFUNDEN

### ConversationSummarizer wird nicht verwendet!

**Status:** Der `ConversationSummarizer` existiert in `app/core/conversation_summarizer.py` aber wird **nirgendwo aufgerufen**!

```python
# In orchestrator.py:
from app.core.conversation_summarizer import get_summarizer  # Importiert
# ... aber get_summarizer() wird nie aufgerufen!
```

**Auswirkung:**
- Bei langen Chats wachsen die Messages unbegrenzt
- Führt zu 500-Fehlern wenn Kontext-Limit überschritten wird
- Das Trimming greift erst beim LLM-Aufruf, aber da ist es oft zu spät

**Fix erforderlich:**
```python
# Vor jedem LLM-Aufruf den Summarizer aufrufen:
summarizer = get_summarizer()
messages = await summarizer.summarize_if_needed(
    messages,
    target_tokens=model_limit - 2000  # Puffer für Response
)
```

---

## Nächste Schritte

- [x] Phase 1: Max-Größe in read_file implementieren (llm_context_limits)
- [ ] **KRITISCH**: ConversationSummarizer aktivieren
- [ ] Phase 2: Relevanz-basiertes Trimming
- [ ] Config-Optionen hinzufügen
- [ ] Logging/Metriken für Trimming-Operationen
