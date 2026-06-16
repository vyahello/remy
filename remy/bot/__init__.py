"""Private Telegram bot front-end for remy.

Step 1 (this code): Python does everything deterministic — receive the
clip, enforce the allow-list, download it, run remy's dry-run plan, and
reply. Step 3 (later) hands the judgment work — caption wording, output
review, approve/redo — to Claude Code over subscription OAuth. See
docs/BOT_ARCHITECTURE.md.
"""
