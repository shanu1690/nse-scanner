#!/bin/bash
# One-click start for the NSE Scanner web GUI.
cd "$(dirname "$0")"
.venv/bin/python webui.py
