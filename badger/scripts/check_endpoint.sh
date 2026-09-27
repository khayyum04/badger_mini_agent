#!/usr/bin/env bash
# Verify VPN + 1Password refs + model id + tool calling, without Docker:
#   op run --env-file=badger/.env.op -- ./badger/scripts/check_endpoint.sh
set -euo pipefail
: "${LLM_BASE_URL:?launch via op run --env-file=badger/.env.op --}"
echo "== models served =="
curl -s -m 20 "$LLM_BASE_URL/models" -H "Authorization: Bearer $LLM_API_KEY" \
  | python3 -c 'import json,sys; print([m["id"] for m in json.load(sys.stdin)["data"]])'
echo "== LLM_MODEL=$LLM_MODEL: one bash tool call through mini-swe-agent's litellm model =="
cd "$(dirname "$0")/.." && python3 - <<'PY'
from badger_mini.harbor_agent import _load_config, _model_config
from minisweagent.models import get_model
m = get_model(config=_model_config(_load_config()["model"], None))
msg = m.query([{"role": "system", "content": "Use the bash tool."},
               {"role": "user", "content": "Run a command that prints the current directory."}])
u = msg["extra"]["response"]["usage"]
print("actions:", msg["extra"]["actions"], "| finish_reason:", msg["extra"]["response"]["choices"][0]["finish_reason"],
      "| tokens in/out:", u["prompt_tokens"], u["completion_tokens"])
PY
