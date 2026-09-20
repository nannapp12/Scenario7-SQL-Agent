#!/usr/bin/env sh
# ONE-TIME SETUP - run once, save the printed IDs (e.g. into a .env file)
set -eu

AGENT_ID=$(ant beta:agents create < coding-assistant.agent.yaml --transform id -r)
ENV_ID=$(ant beta:environments create < coding-assistant.environment.yaml --transform id -r)

echo "AGENT_ID=$AGENT_ID"
echo "ENV_ID=$ENV_ID"

# CI sync, when you edit the YAML later:
#   ant beta:agents update --agent-id "$AGENT_ID" --version N < coding-assistant.agent.yaml
