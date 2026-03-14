"""
Generiert realistische Mock-Analytics-Daten und erstellt einen Report.

Simuliert:
- 50 Chains ueber 7 Tage
- Verschiedene Modelle (Sonnet, Haiku, Opus)
- Verschiedene Query-Kategorien
- Tool-Loops und Fehler
- Erfolgreiche und fehlgeschlagene Chains
"""

import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Projekt-Root zum Path hinzufuegen
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.services.report_generator import ReportGenerator
from app.services.pattern_detector import PatternDetector


def generate_mock_data(output_dir: Path, num_chains: int = 50, days: int = 7):
    """Generiert realistische Mock-Daten."""

    random.seed(42)  # Reproduzierbar

    models = [
        ("claude-3-5-sonnet", 0.5),   # 50% Wahrscheinlichkeit
        ("claude-3-haiku", 0.35),     # 35%
        ("claude-3-opus", 0.15),      # 15%
    ]

    categories = [
        "code_search",
        "error_debug",
        "documentation",
        "api",
        "database",
        "config",
    ]

    tool_sequences = [
        # Normale Sequenzen
        [("search_code", True, 150), ("read_file", True, 80)],
        [("search_code", True, 200), ("read_file", True, 100), ("analyze_code", True, 300)],
        [("api_call", True, 1500), ("parse_response", True, 50)],
        [("read_file", True, 60), ("write_file", True, 100)],

        # Sequenzen mit Loops (Probleme)
        [("search_code", True, 200), ("search_code", True, 200), ("search_code", True, 200), ("read_file", True, 100)],
        [("api_call", False, 5000), ("api_call", False, 5000), ("api_call", True, 1500)],

        # Fehler-Sequenzen
        [("write_file", False, 50), ("read_file", True, 60)],
        [("api_call", False, 10000)],
        [("search_code", True, 8000)],  # Langsam
    ]

    error_types = ["connection", "permission", "validation", "not_found", "rate_limit"]

    chains = []

    for i in range(num_chains):
        # Zufaelliges Datum in den letzten N Tagen
        days_ago = random.randint(0, days - 1)
        timestamp = datetime.utcnow() - timedelta(days=days_ago, hours=random.randint(0, 23))

        # Modell waehlen (gewichtet)
        model = random.choices(
            [m[0] for m in models],
            weights=[m[1] for m in models]
        )[0]

        # Kategorien waehlen
        num_categories = random.randint(1, 2)
        chain_categories = random.sample(categories, num_categories)

        # Tool-Sequenz waehlen
        tool_sequence = random.choice(tool_sequences)

        # Tool-Chain erstellen
        tool_chain = []
        for j, (tool, success, base_duration) in enumerate(tool_sequence):
            duration = base_duration + random.randint(-50, 100)
            duration = max(10, duration)  # Mindestens 10ms

            tool_data = {
                "tool": tool,
                "status": "success" if success else "error",
                "duration_ms": duration,
            }

            if not success:
                tool_data["error_type"] = random.choice(error_types)

            tool_chain.append(tool_data)

        # Erfolg basierend auf Tool-Erfolgen
        all_success = all(t["status"] == "success" for t in tool_chain)
        final_status = "resolved" if all_success and random.random() > 0.1 else "failed"

        # Chain erstellen
        chain = {
            "chain_id": f"c_{i:04d}",
            "ts": timestamp.isoformat(),
            "query_hash": f"{random.randint(100000, 999999):x}",
            "query_categories": chain_categories,
            "model": model,
            "settings": {
                "temperature": round(random.uniform(0.0, 1.0), 2),
                "max_tokens": random.choice([2048, 4096, 8192]),
            },
            "tool_chain": tool_chain,
            "total_iterations": len(tool_chain),
            "final_status": final_status,
            "duration_ms": sum(t["duration_ms"] for t in tool_chain) + random.randint(100, 500),
        }

        chains.append((timestamp, chain))

    # Nach Datum gruppieren und speichern
    by_date = {}
    for timestamp, chain in chains:
        date_str = timestamp.strftime("%Y-%m-%d")
        if date_str not in by_date:
            by_date[date_str] = []
        by_date[date_str].append(chain)

    # Dateien schreiben
    output_dir.mkdir(parents=True, exist_ok=True)

    for date_str, date_chains in by_date.items():
        date_dir = output_dir / date_str
        date_dir.mkdir(parents=True, exist_ok=True)

        with open(date_dir / "chains.jsonl", "w", encoding="utf-8") as f:
            for chain in date_chains:
                f.write(json.dumps(chain, ensure_ascii=False) + "\n")

        print(f"  {date_str}: {len(date_chains)} chains")

    return len(chains)


def main():
    print("=" * 70)
    print("MOCK ANALYTICS GENERATOR")
    print("=" * 70)

    # Output-Verzeichnis
    output_dir = project_root / "data" / "analytics_mock"

    print(f"\n[1/3] Generiere Mock-Daten in: {output_dir}")
    num_chains = generate_mock_data(output_dir, num_chains=50, days=7)
    print(f"      {num_chains} Chains generiert")

    # Pattern-Analyse
    print(f"\n[2/3] Fuehre Pattern-Analyse durch...")
    detector = PatternDetector(str(output_dir))
    patterns = detector.analyze(days=7)

    print(f"      - Chains analysiert: {patterns.analyzed_chains}")
    print(f"      - Loops erkannt: {len(patterns.loops_detected)}")
    print(f"      - Sequenzen gefunden: {len(patterns.frequent_sequences)}")
    print(f"      - Fehlermuster: {len(patterns.failure_patterns)}")

    # Report generieren
    print(f"\n[3/3] Generiere Report...")
    generator = ReportGenerator(str(output_dir))
    report = generator.generate(days=7)

    # Report speichern
    report_path = generator.save_report(report, "analysis_report.md")
    print(f"      Report gespeichert: {report_path}")

    # Report-Summary ausgeben
    print("\n" + "=" * 70)
    print("REPORT SUMMARY")
    print("=" * 70)
    print(f"  Total Chains: {report.summary['total_chains']}")
    print(f"  Success Rate: {report.summary['success_rate']}%")
    print(f"  Avg Iterations: {report.summary['avg_iterations']}")
    print(f"  Recommendations: {len(report.recommendations)}")

    if report.recommendations:
        print("\n  Top Empfehlungen:")
        for i, rec in enumerate(report.recommendations[:3], 1):
            print(f"    [{rec['priority']}] {rec['title']}")

    print("\n" + "=" * 70)
    print(f"Report-Datei: {report_path}")
    print("=" * 70)

    return str(report_path)


if __name__ == "__main__":
    report_path = main()
