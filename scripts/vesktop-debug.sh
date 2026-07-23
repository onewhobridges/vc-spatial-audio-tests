#!/bin/bash
# Launch Vesktop with remote debugging and capture renderer console to a log file.
pkill -x vesktop 2>/dev/null
sleep 1
vesktop --remote-debugging-port=9222 --remote-allow-origins='*' &
python3 "$(dirname "$0")/capture-logs.py"
