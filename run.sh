#!/usr/bin/env bash
# Launch the Spine MCP server over stdio using its venv python.
cd "$(dirname "$0")"
exec ./venv/bin/python server.py
