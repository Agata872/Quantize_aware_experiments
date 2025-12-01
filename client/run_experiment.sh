#!/bin/bash

# Define the parameters
ITERATIONS=20       # Number of iterations per gain value

for ((i=1; i<=ITERATIONS; i++))
do
    echo "Running iteration $i"

    # Run both scripts in parallel
    python3 usrp-cal-bf.py &
    pid1=$!

    python3 pilot.py &
    pid2=$!

    # Wait for both to finish
    wait $pid1
    wait $pid2

    echo "Sleeping 5 seconds before next iteration..."
    sleep 5
done