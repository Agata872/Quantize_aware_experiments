#!/bin/bash

ROLE="$1"   # pilot 或 usrp

if [[ -z "$ROLE" ]]; then
    echo "Usage: $0 {pilot|usrp}"
    exit 1
fi

echo "Running one iteration as role: $ROLE"

if [[ "$ROLE" == "pilot" ]]; then
    python3 Rx.py
elif [[ "$ROLE" == "usrp" ]]; then
    python3 Tx.py
else
    echo "Unknown ROLE '$ROLE'. Expected: pilot or usrp"
    exit 1
fi

echo "Sleeping 5 seconds before next iteration..."
sleep 5
