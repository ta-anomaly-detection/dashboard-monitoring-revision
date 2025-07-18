#!/bin/bash

# Log start time
START_TIME=$(date '+%Y-%m-%d %H:%M:%S')
echo "Start time: $START_TIME"

# Run `hey` POST request
hey -n 10000 -c 20 \
  -m POST \
  -T "application/json" \
  -H "Authorization: 5550c0e4-a22d-4ec5-a192-3f427e6cb0fd" \
  -d '{"first_name":"John","last_name":"Doe","email":"john@example.com","phone":"08123456789"}' \
  http://localhost:3000/api/contacts

# Log end time
END_TIME=$(date '+%Y-%m-%d %H:%M:%S')
echo "End time: $END_TIME"
