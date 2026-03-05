# AI-Assist Evolution - Datenmodelle

## 1. Skill Definition (YAML)

```yaml
# Vollständiges Skill-Schema
Skill:
  id: string                    # Eindeutige ID (slug)
  name: string                  # Anzeigename
  description: string           # Beschreibung
  version: string               # Versionsnummer
  type: enum                    # knowledge | prompt | tool | hybrid

  activation:
    mode: enum                  # always | on-demand | auto
    trigger_words: string[]     # Für auto-Aktivierung

  system_prompt: string         # LLM System-Prompt

  knowledge_sources:            # Wissensquellen
    - type: enum                # pdf | markdown | text | html
      path: string              # Dateipfad
      content: string?          # Inline-Content (für type=text)
      chunk_size: int           # Tokens pro Chunk (default: 1000)
      chunk_overlap: int        # Überlappung (default: 100)

  tools: ToolDefinition[]       # Optionale Skill-Tools

  metadata:
    author: string
    created: datetime
    tags: string[]
```

## 2. Tool Definitions

```yaml
ToolDefinition:
  name: string                  # Tool-Name (snake_case)
  description: string           # Beschreibung für LLM
  is_write_operation: bool      # Benötigt Bestätigung?
  parameters:                   # JSON Schema
    type: object
    properties: {...}
    required: string[]

# Verfügbare Tools
Tools:
  # Suche
  - search_code:
      query: string
      language: enum [java, python, all]
      top_k: int (default: 5)

  - search_handbook:
      query: string
      service_filter: string?
      top_k: int (default: 5)

  - search_skills:
      query: string
      skill_ids: string[]?
      top_k: int (default: 5)

  - search_confluence:
      query: string
      space: string?
      top_k: int (default: 5)

  - search_pdf:
      pdf_id: string
      query: string
      top_k: int (default: 5)

  # Dateien
  - read_file:
      path: string
      encoding: string (default: utf-8)

  - write_file:  # requires confirmation
      path: string
      content: string

  - edit_file:   # requires confirmation
      path: string
      old_string: string
      new_string: string

  - list_files:
      path: string
      pattern: string (default: *)
      recursive: bool (default: false)

  # Analyse
  - get_service_info:
      service_id: string

  - get_class_summary:
      file_path: string
```

## 3. Agent Events (SSE/WebSocket)

```yaml
AgentEvent:
  type: enum
  data: any

EventTypes:
  - token:
      token: string             # Streaming-Token

  - tool_start:
      tool_call_id: string
      name: string
      arguments: object

  - tool_result:
      tool_call_id: string
      result: any
      error: string?

  - confirm_required:
      operation_id: string
      tool_call: ToolCall
      preview:
        type: enum [diff, new_file]
        content: string

  - tool_cancelled:
      tool_call_id: string
      reason: string

  - write_success:
      path: string
      operation: enum [create, update]

  - done:
      response: string
      sources_used: SourceReference[]
      tokens_used: int
```

## 4. API Request/Response Models

```yaml
# Chat Request
AgentChatRequest:
  session_id: string
  message: string
  model: string?
  active_skills: string[]       # Skill-IDs
  mode: enum [read_only, write_with_confirm]
  context_sources:              # Explizit angehängte Quellen
    java_files: string[]
    python_files: string[]
    pdf_ids: string[]
    confluence_page_ids: string[]

# Skill CRUD
SkillCreateRequest:
  name: string
  description: string
  type: enum
  activation_mode: enum
  trigger_words: string[]?
  system_prompt: string
  knowledge_sources: KnowledgeSource[]

SkillFromPDFRequest:
  pdf_id: string                # Hochgeladene PDF
  name: string
  description: string
  trigger_words: string[]
  system_prompt: string
  chunk_size: int?
  selected_pages: int[]?        # Nur bestimmte Seiten

# Handbook Search
HandbookSearchRequest:
  query: string
  service_filter: string?
  tab_filter: string?
  top_k: int (default: 5)

HandbookSearchResult:
  file_path: string
  service_name: string
  tab_name: string
  title: string
  snippet: string
  rank: float

# File Operations
WriteFileRequest:
  path: string
  content: string
  create_backup: bool (default: true)

WriteFilePreview:
  path: string
  is_new: bool
  diff: string?
  old_content: string?
  new_content: string

EditFileRequest:
  path: string
  old_string: string
  new_string: string

ConfirmOperationRequest:
  operation_id: string
  confirmed: bool
```

## 5. Frontend State

```typescript
interface State {
  // Session
  sessionId: string;
  mode: 'read_only' | 'write_with_confirm';

  // Skills
  availableSkills: Skill[];
  activeSkillIds: Set<string>;

  // Context
  loadedFiles: ContextFile[];
  tokens: { used: number; max: number };

  // Chat
  messages: Message[];
  isStreaming: boolean;
  pendingConfirmation: Confirmation | null;

  // Explorer
  activeTab: 'java' | 'python' | 'handbook' | 'skills';
  tree: TreeNode;
  searchQuery: string;

  // Indexes
  javaIndexStatus: IndexStatus;
  pythonIndexStatus: IndexStatus;
  handbookIndexStatus: IndexStatus;
}

interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  toolCalls: ToolCall[];
  sources: Source[];
  timestamp: Date;
}

interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  status: 'running' | 'success' | 'error' | 'pending' | 'cancelled';
  result?: unknown;
  error?: string;
  diff?: string;
}

interface Confirmation {
  operationId: string;
  toolCall: ToolCall;
  preview: { type: 'diff' | 'new_file'; content: string };
}

interface Skill {
  id: string;
  name: string;
  description: string;
  type: 'knowledge' | 'prompt' | 'tool' | 'hybrid';
  activationMode: 'always' | 'on-demand' | 'auto';
  isActive: boolean;
}

interface ContextFile {
  id: string;
  path: string;
  label: string;
  source: 'java' | 'python' | 'handbook' | 'pdf' | 'confluence';
  tokens: number;
}
```

## 6. Datenbankschema-Übersicht

```
┌─────────────────────────────────────────────────────────────────┐
│                        SQLite Databases                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  java_index.db                    python_index.db               │
│  ┌─────────────────────┐          ┌─────────────────────┐       │
│  │ java_fts (FTS5)     │          │ python_fts (FTS5)   │       │
│  │ java_files          │          │ python_files        │       │
│  │ java_index_meta     │          │ python_index_meta   │       │
│  └─────────────────────┘          └─────────────────────┘       │
│                                                                  │
│  handbook_index.db                skills_index.db               │
│  ┌─────────────────────┐          ┌─────────────────────┐       │
│  │ handbook_fts (FTS5) │          │ skills              │       │
│  │ handbook_services   │          │ skill_knowledge_fts │       │
│  │ handbook_fields     │          │   (FTS5)            │       │
│  │ handbook_meta       │          └─────────────────────┘       │
│  └─────────────────────┘                                        │
│                                                                  │
│  sessions.db (optional)                                         │
│  ┌─────────────────────┐                                        │
│  │ sessions            │                                        │
│  │ session_messages    │                                        │
│  │ session_skills      │                                        │
│  └─────────────────────┘                                        │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```
