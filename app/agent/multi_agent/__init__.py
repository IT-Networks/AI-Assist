"""
Multi-Agent Team System – Konfigurierbare Agenten-Teams fuer komplexe Aufgaben.

Architektur (inspiriert von open-multi-agent):
- MultiAgentOrchestrator: Goal → TaskDAG → Parallel Execution → Synthesis
- TeamAgent (SubAgent): Agent mit Team-Context, Tool-Whitelist, Mini-LLM-Loop
- TaskScheduler: Topologische Sortierung + Scheduling-Strategien
- AgentPool: Semaphore-basierte parallele Ausfuehrung
- MessageBus: In-Memory Agent-zu-Agent Kommunikation

Feature-Flag: multi_agent.enabled in config.yaml
"""
