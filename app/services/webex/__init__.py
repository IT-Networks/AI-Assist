"""
Webex-Bot Subsystems (Sprint 1+).

Modulares Package fuer die Webex-Chat-Bot-Integration. Die Legacy-
Facade lebt weiterhin in ``app/services/webex_bot_service.py``; neue
Features (SQLite-Persistenz, StatusEditor, ErrorPolicyGate) werden
schrittweise in diesem Package aufgebaut und vom Handler genutzt,
sobald ``webex.bot.edit_in_place`` aktiviert ist.
"""
