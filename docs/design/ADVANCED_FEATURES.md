# Design-Konzept: Advanced Features

**Version:** 1.0
**Datum:** 2026-03-15
**Status:** Draft
**Basiert auf:** Brainstorming-Session, Marktanalyse (Cursor, Claude Code, Windsurf, GitHub Copilot)

---

## Inhaltsverzeichnis

1. [Executive Summary](#1-executive-summary)
2. [Feature-Uebersicht](#2-feature-uebersicht)
3. [Feature 1: Self-Healing Code](#3-feature-1-self-healing-code)
4. [Feature 2: Token/Credit Tracking](#4-feature-2-tokencredit-tracking)
5. [Feature 3: DORA Metrics](#5-feature-3-dora-metrics)
6. [Feature 4: Automated PR Review](#6-feature-4-automated-pr-review)
7. [Feature 5: Parallel Agents](#7-feature-5-parallel-agents)
8. [Feature 6: Arena Mode](#8-feature-6-arena-mode)
9. [API-Spezifikation](#9-api-spezifikation)
10. [Datenmodelle](#10-datenmodelle)
11. [Implementierungsplan](#11-implementierungsplan)

---

## 1. Executive Summary

Dieses Design beschreibt sechs fortgeschrittene Features, inspiriert von Marktfuehrern wie Claude Code, Cursor, Windsurf und GitHub Copilot:

| Feature | Zweck | Aufwand | Impact | Phase |
|---------|-------|---------|--------|-------|
| **Self-Healing Code** | Auto-Fix nach Tool-Fehler | Mittel | Hoch | 1 |
| **Token/Credit Tracking** | Verbrauchs-Monitoring | Niedrig | Hoch | 1 |
| **DORA Metrics** | Engineering-Produktivitaet | Niedrig | Mittel | 1 |
| **Automated PR Review** | AI-gesteuerte Code Reviews | Hoch | Sehr Hoch | 2 |
| **Parallel Agents** | Multi-Task Execution | Sehr Hoch | Sehr Hoch | 2 |
| **Arena Mode** | Model-Vergleich mit Voting | Mittel | Hoch | 3 |

**Technologie-Stack:**
- Backend: FastAPI (bestehend), SQLite/PostgreSQL
- Frontend: Vanilla JS (bestehend)
- Git: libgit2 fuer Worktrees (Parallel Agents)
- Integration: GitHub Enterprise API

---

## 2. Feature-Uebersicht

### 2.1 Architektur-Integration

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           AI-ASSIST EXTENDED                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    ORCHESTRATOR (erweitert)                          │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │   │
│  │  │ Self-Healing │  │   Parallel   │  │    Arena     │               │   │
│  │  │    Engine    │  │    Agents    │  │     Mode     │               │   │
│  │  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘               │   │
│  │         │                 │                 │                        │   │
│  │         └─────────────────┼─────────────────┘                        │   │
│  │                           │                                          │   │
│  │                    ┌──────▼───────┐                                  │   │
│  │                    │ Agent Pool   │                                  │   │
│  │                    │ (max 8-16)   │                                  │   │
│  │                    └──────────────┘                                  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         SERVICES (neu)                               │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │   │
│  │  │    Token     │  │     DORA     │  │  PR Review   │               │   │
│  │  │   Tracker    │  │   Metrics    │  │   Service    │               │   │
│  │  └──────────────┘  └──────────────┘  └──────────────┘               │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         API ROUTES (neu)                             │   │
│  │  /api/tokens  │  /api/dora  │  /api/reviews  │  /api/agents         │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Feature 1: Self-Healing Code

### 3.1 Konzept

Self-Healing Code erkennt automatisch Fehler nach Tool-Ausfuehrungen und schlaegt Fixes vor oder wendet sie an.

**Inspiration:** GitHub Copilot Agent Mode, Cursor Self-Correct

### 3.2 Ablauf-Diagramm

```
Tool Execution
      │
      ▼
┌─────────────────┐
│  Result Check   │
│  success=false? │
├────────┬────────┤
│  Yes   │   No   │
│   │    │    │   │
│   ▼    │    ▼   │
│ Analyze│  Done  │
│ Error  │        │
└────┬───┴────────┘
     │
     ▼
┌─────────────────┐
│ Error Pattern   │
│   Matching      │
│  (patterns.db)  │
└────────┬────────┘
         │
    ┌────┴────┐
    │ Match?  │
    ├────┬────┤
    │Yes │ No │
    ▼    ▼    │
┌───────┐ ┌───────┐
│Pattern│ │  LLM  │
│  Fix  │ │ Query │
└───┬───┘ └───┬───┘
    │         │
    └────┬────┘
         │
         ▼
┌─────────────────┐
│  Generate Fix   │
│   Suggestion    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Auto-Apply or   │
│ User Confirm?   │
│ (based on mode) │
└────────┬────────┘
         │
    ┌────┴────┐
    │  Mode?  │
    ├────┬────┤
    │Auto│Conf│
    ▼    ▼    │
┌───────┐ ┌───────┐
│ Apply │ │ Show  │
│  Fix  │ │ Modal │
└───┬───┘ └───┬───┘
    │         │
    └────┬────┘
         │
         ▼
┌─────────────────┐
│   Re-Execute    │
│   (max 3x)      │
└─────────────────┘
```

### 3.3 Datenmodell

```typescript
interface SelfHealingConfig {
  enabled: boolean;
  autoApplyLevel: 'none' | 'safe' | 'all';  // none=immer fragen, safe=nur sichere Fixes
  maxRetries: number;                         // default: 3
  retryDelay: number;                         // ms zwischen Retries
  excludedTools: string[];                    // Tools ohne Self-Healing
}

interface HealingAttempt {
  id: string;
  timestamp: number;
  originalError: ToolError;
  patternMatch: ErrorPattern | null;
  suggestedFix: SuggestedFix;
  applied: boolean;
  success: boolean;
  retryCount: number;
}

interface SuggestedFix {
  type: 'edit_file' | 'run_command' | 'install_dependency' | 'config_change';
  description: string;
  changes: CodeChange[];
  confidence: number;  // 0.0 - 1.0
  safeToAutoApply: boolean;
}

interface ToolError {
  tool: string;
  errorType: string;
  errorMessage: string;
  stackTrace: string;
  context: {
    filePath?: string;
    lineNumber?: number;
    codeSnippet?: string;
  };
}
```

### 3.4 API

| Endpoint | Method | Beschreibung |
|----------|--------|--------------|
| `/api/healing/config` | GET/PUT | Self-Healing Konfiguration |
| `/api/healing/attempts` | GET | Liste der Healing-Versuche |
| `/api/healing/apply/{id}` | POST | Fix manuell anwenden |
| `/api/healing/dismiss/{id}` | POST | Vorschlag ablehnen |

### 3.5 UI-Integration

```
┌────────────────────────────────────────────────────────────────┐
│                    SELF-HEALING MODAL                          │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  ⚠️ Error Detected in: compile_validate                        │
│                                                                │
│  Error: SyntaxError at UserService.java:42                     │
│  > Missing semicolon                                           │
│                                                                │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │ Suggested Fix:                                            │ │
│  │                                                           │ │
│  │ - Line 42: return user                                    │ │
│  │ + Line 42: return user;                                   │ │
│  │                                                           │ │
│  │ Confidence: 98%  │  Safe to Auto-Apply: ✓                 │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                                │
│  Pattern Match: "Missing Semicolon" (seen 47x, 95% success)   │
│                                                                │
│  ┌──────────┐  ┌──────────┐  ┌─────────────────┐              │
│  │  Apply   │  │  Skip    │  │ Always Auto-Fix │              │
│  └──────────┘  └──────────┘  └─────────────────┘              │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

---

## 4. Feature 2: Token/Credit Tracking

### 4.1 Konzept

Transparentes Tracking von Token-Verbrauch pro Request, Session, User und Model.

**Inspiration:** Cursor Credit-System, Windsurf Usage Tracking

### 4.2 Datenmodell

```typescript
interface TokenUsage {
  id: string;
  timestamp: number;
  sessionId: string;
  userId: string;        // optional, fuer Multi-User

  // Request-Details
  requestType: 'chat' | 'tool' | 'enhancement' | 'review';
  model: string;

  // Token-Counts
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;

  // Kosten (optional)
  costUsd: number;       // basierend auf Model-Pricing

  // Context
  toolName?: string;
  chainId?: string;
}

interface UsageSummary {
  period: 'day' | 'week' | 'month';
  startDate: string;
  endDate: string;

  // Aggregierte Werte
  totalRequests: number;
  totalTokens: number;
  inputTokens: number;
  outputTokens: number;
  estimatedCostUsd: number;

  // Breakdown
  byModel: Record<string, TokenBreakdown>;
  byRequestType: Record<string, TokenBreakdown>;
  byHour: HourlyUsage[];

  // Limits
  budgetLimit?: number;
  budgetUsed: number;
  budgetRemaining: number;
}

interface TokenBreakdown {
  requests: number;
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  costUsd: number;
}

interface HourlyUsage {
  hour: string;  // "2026-03-15T14:00"
  tokens: number;
  requests: number;
}
```

### 4.3 Architektur

```
┌─────────────────────────────────────────────────────────────────┐
│                      TOKEN TRACKER SERVICE                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │   LLM Call   │───▶│ Token Count  │───▶│   Storage    │      │
│  │   Wrapper    │    │  Extractor   │    │   (SQLite)   │      │
│  └──────────────┘    └──────────────┘    └──────────────┘      │
│                                                   │             │
│                      ┌────────────────────────────┘             │
│                      ▼                                          │
│              ┌──────────────┐                                   │
│              │  Aggregator  │                                   │
│              │  (hourly)    │                                   │
│              └──────┬───────┘                                   │
│                     │                                           │
│         ┌───────────┼───────────┐                               │
│         ▼           ▼           ▼                               │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                        │
│  │  Daily   │ │  Weekly  │ │ Monthly  │                        │
│  │  Stats   │ │  Stats   │ │  Stats   │                        │
│  └──────────┘ └──────────┘ └──────────┘                        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 4.4 API

| Endpoint | Method | Beschreibung |
|----------|--------|--------------|
| `/api/tokens/usage` | GET | Aktuelle Nutzung (mit period-Filter) |
| `/api/tokens/breakdown` | GET | Detaillierter Breakdown |
| `/api/tokens/budget` | GET/PUT | Budget-Limits verwalten |
| `/api/tokens/export` | GET | CSV/JSON Export |
| `/api/tokens/alerts` | GET/POST | Budget-Warnungen |

### 4.5 Dashboard-Integration

```
┌────────────────────────────────────────────────────────────────┐
│                   TOKEN USAGE DASHBOARD                         │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐            │
│  │   Today     │  │   Budget    │  │   Avg/Day   │            │
│  │   45,230    │  │  75% used   │  │   52,100    │            │
│  │   tokens    │  │  [███████░] │  │   tokens    │            │
│  └─────────────┘  └─────────────┘  └─────────────┘            │
│                                                                │
│  Token Usage Over Time                                         │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │     ▄                                                     │ │
│  │    ██  ▄                      ▄                          │ │
│  │   ███ ██ ▄      ▄   ▄       ██                          │ │
│  │  ████ ██ █  ▄  ██  ██ ▄    ███  ▄                       │ │
│  │  ████████ █ ██ ██ ███ ██  ████ ██                       │ │
│  │  Mon Tue Wed Thu Fri Sat Sun                             │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                                │
│  By Model                     By Type                          │
│  ┌────────────────────┐      ┌────────────────────┐           │
│  │ gptoss120b   65%   │      │ chat        45%    │           │
│  │ mistral-678b 25%   │      │ tools       35%    │           │
│  │ other        10%   │      │ enhancement 20%    │           │
│  └────────────────────┘      └────────────────────┘           │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

---

## 5. Feature 3: DORA Metrics

### 5.1 Konzept

Integration der DORA (DevOps Research and Assessment) Metriken fuer Engineering-Produktivitaet.

**Die 4 DORA Metriken:**
1. **Deployment Frequency** - Wie oft wird deployed?
2. **Lead Time for Changes** - Zeit von Commit bis Production
3. **Change Failure Rate** - % fehlgeschlagener Deployments
4. **Mean Time to Recovery** - Zeit zur Fehlerbehebung

### 5.2 Datenquellen

```
┌─────────────────────────────────────────────────────────────────┐
│                    DORA METRICS COLLECTOR                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐    │
│  │   GitHub     │     │   Jenkins    │     │   Jira       │    │
│  │   Commits    │     │   Builds     │     │   Incidents  │    │
│  │   PRs        │     │   Deploys    │     │   Bugs       │    │
│  └──────┬───────┘     └──────┬───────┘     └──────┬───────┘    │
│         │                    │                    │             │
│         └────────────────────┼────────────────────┘             │
│                              │                                  │
│                       ┌──────▼───────┐                          │
│                       │   Metrics    │                          │
│                       │  Calculator  │                          │
│                       └──────┬───────┘                          │
│                              │                                  │
│         ┌─────────────┬──────┼──────┬─────────────┐            │
│         ▼             ▼      ▼      ▼             ▼            │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐   │
│  │ Deployment │ │ Lead Time  │ │  Change    │ │   MTTR     │   │
│  │ Frequency  │ │ for Change │ │ Failure    │ │            │   │
│  └────────────┘ └────────────┘ └────────────┘ └────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 5.3 Datenmodell

```typescript
interface DORAMetrics {
  period: string;  // "2026-W11", "2026-03"
  teamId?: string;

  deploymentFrequency: {
    count: number;
    perDay: number;
    level: 'elite' | 'high' | 'medium' | 'low';  // DORA Benchmarks
    trend: number;  // % change vs previous period
  };

  leadTimeForChanges: {
    medianHours: number;
    p90Hours: number;
    level: 'elite' | 'high' | 'medium' | 'low';
    trend: number;
  };

  changeFailureRate: {
    percentage: number;
    failedDeploys: number;
    totalDeploys: number;
    level: 'elite' | 'high' | 'medium' | 'low';
    trend: number;
  };

  meanTimeToRecovery: {
    medianHours: number;
    p90Hours: number;
    level: 'elite' | 'high' | 'medium' | 'low';
    trend: number;
  };

  overallLevel: 'elite' | 'high' | 'medium' | 'low';
}

// DORA Benchmark Thresholds
const DORA_BENCHMARKS = {
  deploymentFrequency: {
    elite: 'on-demand',      // Multiple deploys per day
    high: 'daily-weekly',    // Between once per day and once per week
    medium: 'weekly-monthly', // Between once per week and once per month
    low: 'monthly+'          // Less than once per month
  },
  leadTimeForChanges: {
    elite: 1,      // < 1 hour
    high: 24,      // < 1 day
    medium: 168,   // < 1 week
    low: 720       // < 1 month
  },
  changeFailureRate: {
    elite: 5,      // 0-5%
    high: 10,      // 5-10%
    medium: 15,    // 10-15%
    low: 100       // 15%+
  },
  meanTimeToRecovery: {
    elite: 1,      // < 1 hour
    high: 24,      // < 1 day
    medium: 168,   // < 1 week
    low: 720       // < 1 month
  }
};
```

### 5.4 API

| Endpoint | Method | Beschreibung |
|----------|--------|--------------|
| `/api/dora/metrics` | GET | Aktuelle DORA Metriken |
| `/api/dora/trends` | GET | Historische Trends |
| `/api/dora/sync` | POST | Manuelle Sync mit externen Systemen |
| `/api/dora/config` | GET/PUT | Integration-Konfiguration |

### 5.5 Dashboard-Karte

```
┌────────────────────────────────────────────────────────────────┐
│                      DORA METRICS                               │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  Overall: HIGH ●                          Week 11, 2026        │
│                                                                │
│  ┌─────────────────┐  ┌─────────────────┐                     │
│  │ Deploy Freq     │  │ Lead Time       │                     │
│  │ ████████░░ HIGH │  │ ██████████ ELITE│                     │
│  │ 4.2/day  ↑12%   │  │ 2.3h     ↓15%   │                     │
│  └─────────────────┘  └─────────────────┘                     │
│                                                                │
│  ┌─────────────────┐  ┌─────────────────┐                     │
│  │ Failure Rate    │  │ MTTR            │                     │
│  │ ██████░░░░ MED  │  │ ████████░░ HIGH │                     │
│  │ 8.5%     ↑2%    │  │ 4.1h     ↓8%    │                     │
│  └─────────────────┘  └─────────────────┘                     │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

---

## 6. Feature 4: Automated PR Review

### 6.1 Konzept

AI-gesteuerte automatische Code Reviews fuer Pull Requests mit Line-by-Line Kommentaren.

**Inspiration:** CodeRabbit, Anthropic Code Review, Graphite

### 6.2 Ablauf

```
GitHub Webhook
(PR opened/updated)
       │
       ▼
┌─────────────────┐
│  Receive PR     │
│  Event          │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Fetch Diff     │
│  & Context      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Code Analysis  │
│  ┌───────────┐  │
│  │ Security  │  │
│  │ Quality   │  │
│  │ Style     │  │
│  │ Tests     │  │
│  └───────────┘  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Generate Review │
│  Comments       │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Post to GitHub │
│  (via API)      │
└─────────────────┘
```

### 6.3 Datenmodell

```typescript
interface PRReviewRequest {
  repoOwner: string;
  repoName: string;
  prNumber: number;
  headSha: string;
  baseSha: string;

  // Review-Optionen
  reviewTypes: ReviewType[];
  customRules?: string[];  // Natural Language Rules
  severity: 'all' | 'medium+' | 'high+';
}

type ReviewType =
  | 'security'      // OWASP-basierte Security Checks
  | 'quality'       // Code Smells, Complexity
  | 'style'         // Code Style, Conventions
  | 'tests'         // Test Coverage, Missing Tests
  | 'performance'   // Performance Issues
  | 'documentation' // Missing Docs, Comments
  | 'custom';       // Benutzerdefinierte Regeln

interface PRReviewResult {
  id: string;
  prNumber: number;
  timestamp: number;
  status: 'pending' | 'completed' | 'failed';

  // Summary
  summary: {
    totalComments: number;
    bySeverity: Record<Severity, number>;
    byType: Record<ReviewType, number>;
    overallVerdict: 'approve' | 'request_changes' | 'comment';
  };

  // Detaillierte Comments
  comments: ReviewComment[];

  // Generierte Tests (optional)
  suggestedTests?: TestSuggestion[];
}

type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info';

interface ReviewComment {
  id: string;
  filePath: string;
  line: number;
  endLine?: number;
  side: 'LEFT' | 'RIGHT';

  type: ReviewType;
  severity: Severity;
  title: string;
  body: string;

  // Fix-Vorschlag
  suggestedFix?: {
    description: string;
    diff: string;
  };
}

interface TestSuggestion {
  targetFile: string;
  testFile: string;
  testCode: string;
  coverage: string[];  // Welche Methoden/Branches
}
```

### 6.4 Custom Review Rules (Natural Language)

```yaml
# .ai-assist/review-rules.yaml
rules:
  - name: "No hardcoded credentials"
    description: "Reject PRs with hardcoded passwords, API keys, or secrets"
    severity: critical

  - name: "Test coverage required"
    description: "New public methods must have corresponding unit tests"
    severity: high

  - name: "German comments"
    description: "All code comments should be in German"
    severity: low

  - name: "Error handling"
    description: "All database calls must have proper error handling"
    severity: medium
```

### 6.5 API

| Endpoint | Method | Beschreibung |
|----------|--------|--------------|
| `/api/reviews/trigger` | POST | Manuell Review starten |
| `/api/reviews/{id}` | GET | Review-Ergebnis abrufen |
| `/api/reviews/webhook` | POST | GitHub Webhook Endpoint |
| `/api/reviews/rules` | GET/PUT | Custom Rules verwalten |
| `/api/reviews/history` | GET | Review-Historie |

### 6.6 UI-Integration

```
┌────────────────────────────────────────────────────────────────┐
│                   PR REVIEW PANEL                               │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  PR #123: "Add user authentication"                            │
│  Status: ● Reviewing...  (12 files, +450 -120)                 │
│                                                                │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │ Summary                                                   │ │
│  │                                                           │ │
│  │  ● Critical: 1   ● High: 3   ● Medium: 7   ● Low: 12     │ │
│  │                                                           │ │
│  │  Verdict: REQUEST CHANGES                                 │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                                │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │ 📁 src/auth/UserService.java                              │ │
│  │                                                           │ │
│  │   Line 45 │ ● Critical │ Security                        │ │
│  │   > Hardcoded password detected: "admin123"              │ │
│  │   [View] [Suggested Fix] [Dismiss]                       │ │
│  │                                                           │ │
│  │   Line 78 │ ● High │ Quality                             │ │
│  │   > Method complexity too high (cyclomatic: 15)          │ │
│  │   [View] [Suggested Fix] [Dismiss]                       │ │
│  │                                                           │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                                │
│  [Post to GitHub]  [Generate Tests]  [Download Report]        │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

---

## 7. Feature 5: Parallel Agents

### 7.1 Konzept

Mehrere AI-Agents arbeiten parallel an verschiedenen Tasks in isolierten Git Worktrees.

**Inspiration:** Claude Code (16+ parallel), Cursor (8 parallel), Google Antigravity

### 7.2 Architektur

```
┌─────────────────────────────────────────────────────────────────┐
│                     PARALLEL AGENT ORCHESTRATOR                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    TASK QUEUE                             │  │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐         │  │
│  │  │ Task 1  │ │ Task 2  │ │ Task 3  │ │ Task 4  │  ...    │  │
│  │  │ Tests   │ │Refactor │ │  Docs   │ │ Review  │         │  │
│  │  └─────────┘ └─────────┘ └─────────┘ └─────────┘         │  │
│  └──────────────────────────────────────────────────────────┘  │
│                               │                                 │
│                               ▼                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    AGENT POOL (max 8)                     │  │
│  │                                                           │  │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐        │  │
│  │  │Agent 1  │ │Agent 2  │ │Agent 3  │ │Agent 4  │        │  │
│  │  │Worktree │ │Worktree │ │Worktree │ │Worktree │        │  │
│  │  │  /wt-1  │ │  /wt-2  │ │  /wt-3  │ │  /wt-4  │        │  │
│  │  │ Task 1  │ │ Task 2  │ │ Task 3  │ │ Task 4  │        │  │
│  │  │████░░░░ │ │██████░░ │ │████████ │ │██░░░░░░ │        │  │
│  │  └─────────┘ └─────────┘ └─────────┘ └─────────┘        │  │
│  │                                                           │  │
│  └──────────────────────────────────────────────────────────┘  │
│                               │                                 │
│                               ▼                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    RESULT MERGER                          │  │
│  │                                                           │  │
│  │  - Conflict Detection                                     │  │
│  │  - Automatic Merge (if possible)                         │  │
│  │  - User Resolution UI (if conflicts)                     │  │
│  │  - Final Commit Generation                               │  │
│  │                                                           │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 7.3 Datenmodell

```typescript
interface ParallelAgentConfig {
  maxAgents: number;          // default: 8
  worktreeBasePath: string;   // default: ".ai-worktrees"
  autoMerge: boolean;         // Automatisches Mergen bei keinen Konflikten
  cleanupOnComplete: boolean; // Worktrees nach Completion loeschen
}

interface AgentTask {
  id: string;
  type: 'implement' | 'refactor' | 'test' | 'document' | 'review' | 'fix';
  description: string;

  // Scope
  targetFiles?: string[];
  targetDirectories?: string[];

  // Dependencies
  dependsOn?: string[];     // Task IDs
  blockedBy?: string[];     // Task IDs

  // Status
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  progress: number;         // 0-100

  // Results
  result?: AgentTaskResult;
}

interface AgentInstance {
  id: string;
  taskId: string | null;
  worktreePath: string;
  branchName: string;
  status: 'idle' | 'working' | 'merging' | 'error';
  startedAt: number;

  // Metrics
  tokensUsed: number;
  toolCalls: number;
}

interface AgentTaskResult {
  success: boolean;
  changedFiles: FileChange[];
  commits: CommitInfo[];
  summary: string;

  // Merge Info
  mergeStatus: 'pending' | 'merged' | 'conflict';
  conflicts?: ConflictInfo[];
}

interface ConflictInfo {
  filePath: string;
  ourChanges: string;
  theirChanges: string;
  suggestedResolution?: string;
}
```

### 7.4 Git Worktree Management

```bash
# Worktree erstellen
git worktree add .ai-worktrees/wt-{task_id} -b ai-task-{task_id}

# Nach Completion
git worktree remove .ai-worktrees/wt-{task_id}
git branch -d ai-task-{task_id}

# Mergen (wenn kein Konflikt)
git merge ai-task-{task_id} --no-ff -m "AI: {task_description}"
```

### 7.5 API

| Endpoint | Method | Beschreibung |
|----------|--------|--------------|
| `/api/agents/tasks` | GET/POST | Tasks verwalten |
| `/api/agents/tasks/{id}` | GET/DELETE | Einzelnen Task |
| `/api/agents/tasks/{id}/cancel` | POST | Task abbrechen |
| `/api/agents/pool` | GET | Agent-Pool Status |
| `/api/agents/merge/{id}` | POST | Manuelles Mergen |
| `/api/agents/conflicts/{id}` | GET/POST | Konflikt-Resolution |

### 7.6 UI

```
┌────────────────────────────────────────────────────────────────┐
│                   PARALLEL AGENTS                               │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  Active: 4/8 agents  │  Queue: 3 tasks  │  Completed: 12      │
│                                                                │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │ Agent Pool                                                │ │
│  │                                                           │ │
│  │  ┌─────────────────┐  ┌─────────────────┐                │ │
│  │  │ Agent 1         │  │ Agent 2         │                │ │
│  │  │ ████████░░ 80%  │  │ ██████░░░░ 60%  │                │ │
│  │  │ "Write tests"   │  │ "Refactor auth" │                │ │
│  │  │ 12 tool calls   │  │ 8 tool calls    │                │ │
│  │  └─────────────────┘  └─────────────────┘                │ │
│  │                                                           │ │
│  │  ┌─────────────────┐  ┌─────────────────┐                │ │
│  │  │ Agent 3         │  │ Agent 4         │                │ │
│  │  │ ██████████ 100% │  │ ██░░░░░░░░ 20%  │                │ │
│  │  │ "Update docs"   │  │ "Fix bug #123"  │                │ │
│  │  │ ✓ Ready to merge│  │ 3 tool calls    │                │ │
│  │  └─────────────────┘  └─────────────────┘                │ │
│  │                                                           │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                                │
│  Queued Tasks:                                                 │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │ 1. "Add validation to UserController" (waiting)          │ │
│  │ 2. "Generate API documentation" (waiting)                │ │
│  │ 3. "Optimize database queries" (blocked by #1)           │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                                │
│  [Add Task]  [Merge All Ready]  [Cancel All]                  │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

---

## 8. Feature 6: Arena Mode

### 8.1 Konzept

Side-by-Side Vergleich von Model-Outputs mit Blind-Voting zur Qualitaetsbewertung.

**Inspiration:** Windsurf Arena Mode, Chatbot Arena

### 8.2 Ablauf

```
User Prompt
     │
     ▼
┌─────────────────┐
│  Send to both   │
│    Models       │
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
┌───────┐ ┌───────┐
│Model A│ │Model B│
│(blind)│ │(blind)│
└───┬───┘ └───┬───┘
    │         │
    └────┬────┘
         │
         ▼
┌─────────────────┐
│  Display Both   │
│  (anonymized)   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   User Votes    │
│  A > B / B > A  │
│     / Tie       │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Reveal Models  │
│  + Save Stats   │
└─────────────────┘
```

### 8.3 Datenmodell

```typescript
interface ArenaSession {
  id: string;
  timestamp: number;
  userId: string;

  prompt: string;
  context?: string;

  // Models (hidden until vote)
  modelA: string;
  modelB: string;

  // Responses
  responseA: string;
  responseB: string;

  // Metrics
  latencyA: number;  // ms
  latencyB: number;
  tokensA: number;
  tokensB: number;

  // Vote
  vote: 'A' | 'B' | 'tie' | null;
  votedAt?: number;

  // Optional Feedback
  feedback?: string;
}

interface ModelStats {
  model: string;

  // Win/Loss
  wins: number;
  losses: number;
  ties: number;
  totalMatches: number;
  winRate: number;

  // ELO Rating (optional)
  eloRating: number;

  // Performance
  avgLatency: number;
  avgTokens: number;

  // Matchup-spezifisch
  vsStats: Record<string, {
    wins: number;
    losses: number;
    ties: number;
  }>;
}
```

### 8.4 API

| Endpoint | Method | Beschreibung |
|----------|--------|--------------|
| `/api/arena/start` | POST | Neuen Arena-Match starten |
| `/api/arena/{id}` | GET | Match-Details |
| `/api/arena/{id}/vote` | POST | Vote abgeben |
| `/api/arena/stats` | GET | Model-Statistiken |
| `/api/arena/history` | GET | Match-Historie |

### 8.5 UI

```
┌────────────────────────────────────────────────────────────────┐
│                      ARENA MODE                                 │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  Your Prompt: "Implement a binary search function in Python"  │
│                                                                │
│  ┌────────────────────────┐  ┌────────────────────────────┐   │
│  │      Response A        │  │      Response B            │   │
│  │      (Model ???)       │  │      (Model ???)           │   │
│  ├────────────────────────┤  ├────────────────────────────┤   │
│  │                        │  │                            │   │
│  │ def binary_search(arr, │  │ def binary_search(         │   │
│  │   target):             │  │   array: list,             │   │
│  │   left, right = 0,     │  │   target: int) -> int:     │   │
│  │     len(arr) - 1       │  │   """Binary search with    │   │
│  │   while left <= right: │  │   O(log n) complexity"""   │   │
│  │     mid = (left+right) │  │   lo, hi = 0, len(array)-1 │   │
│  │           // 2         │  │   while lo <= hi:          │   │
│  │     ...                │  │     ...                    │   │
│  │                        │  │                            │   │
│  ├────────────────────────┤  ├────────────────────────────┤   │
│  │ Latency: 1.2s          │  │ Latency: 0.8s              │   │
│  │ Tokens: 245            │  │ Tokens: 312                │   │
│  └────────────────────────┘  └────────────────────────────┘   │
│                                                                │
│  Which response is better?                                     │
│                                                                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                     │
│  │  A wins  │  │   Tie    │  │  B wins  │                     │
│  └──────────┘  └──────────┘  └──────────┘                     │
│                                                                │
│  ─────────────────────────────────────────────────────────────│
│  After voting, models will be revealed!                        │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

---

## 9. API-Spezifikation

### 9.1 Neue Routen-Uebersicht

```
/api/healing/
├── GET    /config              # Self-Healing Config
├── PUT    /config              # Config aktualisieren
├── GET    /attempts            # Liste der Healing-Versuche
├── POST   /apply/{id}          # Fix anwenden
└── POST   /dismiss/{id}        # Vorschlag ablehnen

/api/tokens/
├── GET    /usage               # Token-Nutzung
├── GET    /breakdown           # Detaillierter Breakdown
├── GET    /budget              # Budget-Info
├── PUT    /budget              # Budget setzen
├── GET    /export              # CSV/JSON Export
└── GET/POST /alerts            # Budget-Warnungen

/api/dora/
├── GET    /metrics             # DORA Metriken
├── GET    /trends              # Historische Trends
├── POST   /sync                # Manuelle Sync
└── GET/PUT /config             # Integration-Config

/api/reviews/
├── POST   /trigger             # Review starten
├── GET    /{id}                # Review-Ergebnis
├── POST   /webhook             # GitHub Webhook
├── GET/PUT /rules              # Custom Rules
└── GET    /history             # Review-Historie

/api/agents/
├── GET/POST /tasks             # Tasks verwalten
├── GET/DELETE /tasks/{id}      # Einzelner Task
├── POST   /tasks/{id}/cancel   # Task abbrechen
├── GET    /pool                # Agent-Pool Status
├── POST   /merge/{id}          # Manuelles Mergen
└── GET/POST /conflicts/{id}    # Konflikt-Resolution

/api/arena/
├── POST   /start               # Match starten
├── GET    /{id}                # Match-Details
├── POST   /{id}/vote           # Vote abgeben
├── GET    /stats               # Model-Statistiken
└── GET    /history             # Match-Historie
```

---

## 10. Datenmodelle

### 10.1 Neue SQLite-Tabellen

```sql
-- Token Tracking
CREATE TABLE token_usage (
    id TEXT PRIMARY KEY,
    timestamp INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    user_id TEXT,
    request_type TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    cost_usd REAL,
    tool_name TEXT,
    chain_id TEXT
);

CREATE INDEX idx_token_usage_timestamp ON token_usage(timestamp);
CREATE INDEX idx_token_usage_session ON token_usage(session_id);

-- DORA Metrics
CREATE TABLE dora_metrics (
    id TEXT PRIMARY KEY,
    period TEXT NOT NULL,          -- "2026-W11"
    team_id TEXT,
    deployment_count INTEGER,
    deployment_per_day REAL,
    lead_time_median_hours REAL,
    lead_time_p90_hours REAL,
    failure_rate_percent REAL,
    mttr_median_hours REAL,
    overall_level TEXT,
    created_at TEXT
);

-- PR Reviews
CREATE TABLE pr_reviews (
    id TEXT PRIMARY KEY,
    repo_owner TEXT NOT NULL,
    repo_name TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    status TEXT NOT NULL,
    verdict TEXT,
    total_comments INTEGER,
    data TEXT,                      -- JSON blob
    created_at TEXT,
    completed_at TEXT
);

-- Agent Tasks
CREATE TABLE agent_tasks (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    progress INTEGER DEFAULT 0,
    target_files TEXT,              -- JSON array
    depends_on TEXT,                -- JSON array
    result TEXT,                    -- JSON blob
    created_at TEXT,
    started_at TEXT,
    completed_at TEXT
);

-- Arena Matches
CREATE TABLE arena_matches (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    prompt TEXT NOT NULL,
    model_a TEXT NOT NULL,
    model_b TEXT NOT NULL,
    response_a TEXT,
    response_b TEXT,
    latency_a INTEGER,
    latency_b INTEGER,
    tokens_a INTEGER,
    tokens_b INTEGER,
    vote TEXT,
    feedback TEXT,
    created_at TEXT,
    voted_at TEXT
);
```

---

## 11. Implementierungsplan

### Phase 1: Quick Wins (2 Wochen)

| Task | Beschreibung | Aufwand |
|------|-------------|---------|
| 1.1 | Token Tracker Service | 2 Tage |
| 1.2 | Token Tracker API | 1 Tag |
| 1.3 | Token Dashboard UI | 2 Tage |
| 1.4 | DORA Metrics Service | 2 Tage |
| 1.5 | DORA Dashboard Integration | 1 Tag |
| 1.6 | Self-Healing Basis | 2 Tage |

### Phase 2: Core Features (4 Wochen)

| Task | Beschreibung | Aufwand |
|------|-------------|---------|
| 2.1 | PR Review Service | 5 Tage |
| 2.2 | GitHub Integration | 3 Tage |
| 2.3 | Review UI Panel | 3 Tage |
| 2.4 | Custom Rules Engine | 2 Tage |
| 2.5 | Parallel Agents Core | 5 Tage |
| 2.6 | Git Worktree Manager | 2 Tage |
| 2.7 | Agent UI | 3 Tage |

### Phase 3: Advanced (2 Wochen)

| Task | Beschreibung | Aufwand |
|------|-------------|---------|
| 3.1 | Arena Mode Backend | 3 Tage |
| 3.2 | Arena UI | 2 Tage |
| 3.3 | Model Statistics | 2 Tage |
| 3.4 | Self-Healing Erweitert | 3 Tage |

### Phase 4: Polish (1 Woche)

| Task | Beschreibung | Aufwand |
|------|-------------|---------|
| 4.1 | Integration Tests | 2 Tage |
| 4.2 | Performance Optimization | 2 Tage |
| 4.3 | Dokumentation | 1 Tag |

---

## Anhang

### A. Referenzen

- [DORA Metrics](https://dora.dev/research/)
- [GitHub Copilot Agent Mode](https://github.blog/news-insights/product-news/github-copilot-the-agent-awakens/)
- [CodeRabbit](https://www.coderabbit.ai/)
- [Git Worktrees](https://git-scm.com/docs/git-worktree)

### B. Entscheidungen

| Entscheidung | Option A | Option B | Gewaehlt | Grund |
|--------------|----------|----------|----------|-------|
| Token Storage | SQLite | PostgreSQL | SQLite | Einfachheit, keine neue Dependency |
| Worktree Lib | libgit2 | git CLI | git CLI | Stabiler, weniger Komplexitaet |
| PR Review | Webhook | Polling | Webhook | Echtzeit, weniger Last |

### C. Offene Fragen

1. Max Anzahl paralleler Agents? (Default: 8)
2. Token-Budget pro User oder global?
3. Arena: ELO-Rating oder einfache Win/Loss?
4. PR Review: Eigenes GitHub App oder bestehende Credentials?
