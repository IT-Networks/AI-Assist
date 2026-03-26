# Skill Commands - Benutzerhandbuch

**Version:** 2.0
**Datum:** 2026-03-23

> **Migration:** Die ehemaligen MCP-Commands wurden zu Skills migriert.
> Diese arbeiten nun mit dem Skill-System zusammen für Enterprise-Erweiterungen.

---

## Übersicht

Die Skill-Commands `/brainstorm` und `/design` bieten:

- **Multi-Source Research** - Automatische Recherche in Skills, Handbuch, Confluence und Web
- **Enterprise-Kontext** - Firmeninterne Richtlinien und Standards werden automatisch einbezogen
- **Strukturierte Ausgabe** - Use Cases, Stakeholder-Mapping, UML-Diagramme, API-Specs
- **Datenschutz** - Interne Daten werden nicht an externe Web-Suchen gesendet

---

## /brainstorm - Ideenentwicklung

### Verwendung

```
/brainstorm [Thema oder Feature-Idee]
```

### Beispiele

```
/brainstorm Neues Kundenportal mit Self-Service-Funktionen
/brainstorm Wie können wir die Batch-Verarbeitung optimieren?
/brainstorm Payment-Integration für den Webshop
```

### Ausgabe enthält

| Sektion | Beschreibung |
|---------|--------------|
| **Executive Summary** | Kurze Zusammenfassung der Idee |
| **Use Cases** | Strukturierte Use-Case-Beschreibungen mit Akteuren, Auslösern, Ablauf |
| **Stakeholder-Mapping** | Tabelle mit Stakeholdern, Rollen, Interesse, Einfluss |
| **Risiken** | Identifizierte Risiken und Herausforderungen |
| **Annahmen** | Getroffene Annahmen |
| **Offene Fragen** | Noch zu klärende Punkte als Checkliste |
| **Kontext-Diagramm** | ASCII/Mermaid-Diagramm des Systems im Kontext |

### Research-Quellen

Je nach Konfiguration werden folgende Quellen durchsucht:

- **Skills** - Interne Wissensbasen und Richtlinien
- **Handbuch** - Service-Dokumentation
- **Confluence** - Wiki-Seiten und Spaces
- **Web** - Externe Best Practices (nur für technische Queries, anonymisiert)

---

## /design - Architektur & Design

### Verwendung

```
/design [Komponente oder System]
```

### Beispiele

```
/design REST API für Benutzer-Authentifizierung
/design Microservice-Architektur für Order-Management
/design Datenmodell für Produktkatalog
```

### Ausgabe enthält

| Sektion | Beschreibung |
|---------|--------------|
| **Design Overview** | Architektur-Überblick und Designziele |
| **Komponenten** | Detaillierte Komponentenbeschreibungen |
| **Sequenzdiagramm** | Mermaid-Diagramm für Hauptabläufe |
| **Komponenten-Diagramm** | ASCII-Diagramm der Architektur |
| **Datenmodell (ERD)** | Entity-Relationship-Diagramm |
| **API-Spezifikation** | OpenAPI-konforme Endpunkt-Beschreibungen |
| **Entscheidungsprotokoll** | Tabelle mit Entscheidungen, Alternativen, Begründungen |

### Diagramm-Formate

- **Mermaid** - Für Web-Rendering (Sequenz, Klassen, ERD)
- **ASCII** - Für Terminal/Text-Ausgabe (Komponenten, Kontext)
- **PlantUML** - Optional für Java-basiertes Rendering

---

## Research-Konfiguration

### Scopes

| Scope | Web erlaubt | Beschreibung |
|-------|-------------|--------------|
| `internal-only` | Nein | Nur interne Quellen (Skills, Handbuch, Confluence) |
| `external-safe` | Ja* | Web für technische Queries, anonymisiert |
| `all` | Ja | Alle Quellen ohne Einschränkung |

*Bei `external-safe` werden interne Informationen aus Web-Queries entfernt:
- Service-Namen (z.B. `OrderService` → `Service`)
- Projekt-Codes (z.B. `PROJ-123` → entfernt)
- IP-Adressen und interne URLs
- Personen-Namen

### Query-Klassifikation

Das System klassifiziert Anfragen automatisch:

| Klassifikation | Web erlaubt | Beispiele |
|----------------|-------------|-----------|
| **TECHNICAL** | Ja | "Spring Boot REST API", "React Hooks" |
| **BUSINESS** | Mit Vorsicht | "Bestellprozess optimieren" |
| **INTERNAL** | Nein | "OrderService aufrufen", "PROJ-123 Status" |
| **MIXED** | Teilweise | "Best Practices für unseren Bestellprozess" |

---

## Skills-Verwaltung

### Command-getriggerte Skills

Skills können automatisch für bestimmte Commands aktiviert werden.

**Konfiguration in der Skill-YAML:**

```yaml
activation:
  mode: command-trigger
  trigger_commands:
    - brainstorm
    - design
```

### UI-Verwaltung

1. Öffne `/skills` im Browser
2. Wechsle zum Tab "Commands"
3. Hier siehst du:
   - Welche Commands welche Skills aktivieren
   - Research-Scope und Quellen-Konfiguration
   - Toggle zum Aktivieren/Deaktivieren von Skills

### Research-Quellen umschalten

Im "Commands" Tab kannst du:

1. **Scope wählen** - Intern / Extern Safe / Alle
2. **Quellen togglen** - Skills, Handbuch, Confluence, Web einzeln aktivieren/deaktivieren

---

## Beispiel-Skills

### Enterprise Brainstorm Skill

Datei: `skills/enterprise-brainstorm.yaml`

```yaml
name: Enterprise Brainstorming
type: documentation
activation:
  mode: command-trigger
  trigger_commands:
    - brainstorm

research:
  scope: external-safe
  allowed_sources:
    - skills
    - handbook
    - confluence
    - web

output:
  templates:
    - name: Executive Summary
      required: true
    - name: Use Cases
      required: true
      format: |
        ### UC-XX: [Titel]
        **Akteur:** [Wer führt aus]
        **Auslöser:** [Was startet den Use Case]
        **Ablauf:** [Schritte]
        **Ergebnis:** [Erwartetes Ergebnis]
```

### Enterprise Design Skill

Datei: `skills/enterprise-design.yaml`

```yaml
name: Enterprise Design
type: documentation
activation:
  mode: command-trigger
  trigger_commands:
    - design

research:
  scope: external-safe
  allowed_sources:
    - skills
    - handbook
    - confluence
    - web

output:
  diagrams:
    - type: sequence
      format: mermaid
    - type: component
      format: ascii
    - type: erd
      format: mermaid
```

---

## API-Endpunkte

### Research API

| Endpunkt | Methode | Beschreibung |
|----------|---------|--------------|
| `/api/research/classify` | POST | Klassifiziert eine Query |
| `/api/research/sanitize` | POST | Entfernt sensible Daten aus Query |
| `/api/research/execute` | POST | Führt Multi-Source Research aus |
| `/api/research/sources` | GET | Listet verfügbare Quellen |

### Output API

| Endpunkt | Methode | Beschreibung |
|----------|---------|--------------|
| `/api/output/diagram` | POST | Generiert ein Diagramm |
| `/api/output/brainstorm` | POST | Formatiert Brainstorm-Output |
| `/api/output/design` | POST | Formatiert Design-Output |
| `/api/output/diagram-types` | GET | Listet Diagramm-Typen |

### Skills API

| Endpunkt | Methode | Beschreibung |
|----------|---------|--------------|
| `/api/skills/command-triggers` | GET | Zeigt Command-Skill-Zuordnungen |
| `/api/skills/for-command/{cmd}` | GET | Skills für einen Command |

---

## Fehlerbehebung

### "Web-Recherche nicht erlaubt"

- **Ursache:** Query wurde als INTERNAL klassifiziert
- **Lösung:** Formuliere die Anfrage technischer oder entferne interne Referenzen

### "Keine Ergebnisse gefunden"

- **Ursache:** Keine passenden Skills/Handbuch-Einträge
- **Lösung:** Prüfe ob Skills aktiviert sind, erweitere die Suchbegriffe

### "Skill nicht für Command aktiv"

- **Ursache:** Skill hat `trigger_commands` nicht konfiguriert
- **Lösung:** Bearbeite den Skill und füge den Command zu `trigger_commands` hinzu

---

## Design Persistence

Designs werden automatisch als Markdown-Dateien gespeichert für persistente Referenz.

### Speicherort

```
docs/designs/
├── index.json              # Index aller Designs
├── brainstorm/
│   └── 2026-03-16_feature-name.md
└── design/
    └── 2026-03-16_api-name.md
```

### Dateiformat

Jede Design-Datei enthält:
- **YAML-Frontmatter** mit Metadaten (ID, Status, Tags, Quellen)
- **Markdown-Inhalt** mit dem eigentlichen Design
- **Implementation Tracking** Tabelle für Verknüpfung mit Code

### Status-Workflow

| Status | Bedeutung |
|--------|-----------|
| `draft` | Entwurf, noch in Bearbeitung |
| `approved` | Freigegeben für Implementation |
| `implemented` | Vollständig umgesetzt |
| `archived` | Archiviert, nicht mehr aktiv |

### API-Verwendung

```bash
# Design speichern
POST /api/designs/save
{
  "type": "design",
  "title": "Payment API",
  "content": "# Payment API\n\n...",
  "tags": ["api", "payment"]
}

# Designs auflisten
GET /api/designs?type=design&status=approved

# Design laden (für /implement Referenz)
GET /api/designs/{design-id}

# Status aktualisieren
PUT /api/designs/{design-id}/status
{"status": "implemented"}

# Implementation verknüpfen
POST /api/designs/{design-id}/link
{"files": ["app/services/payment.py"], "commit": "abc123"}
```

### Integration mit /implement

```
/implement --design design-2026-03-16-001
```

Das Design wird als Kontext geladen und nach Abschluss werden
die erstellten Dateien automatisch verknüpft.

---

## Best Practices

1. **Spezifische Anfragen** - Je genauer die Anfrage, desto bessere Ergebnisse
2. **Technische Begriffe** - Ermöglichen Web-Recherche für Best Practices
3. **Keine internen Namen in Anfragen** - Wenn Web-Recherche gewünscht ist
4. **Skills für Domänen erstellen** - Firmenspezifisches Wissen in Skills ablegen
5. **Research-Scope bewusst wählen** - `internal-only` für sensible Themen
6. **Designs speichern** - Wichtige Designs persistieren für spätere Referenz
