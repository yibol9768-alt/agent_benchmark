#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 2 ]; then
  echo "Usage: $0 <swe_bench_pro_root> <dockerhub_username> [dataset_split] [instance_slice] [model_name]"
  exit 1
fi

SWE_BENCH_PRO_ROOT="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$1")"
DOCKERHUB_USERNAME="$2"
DATASET_SPLIT="${3:-test}"
INSTANCE_SLICE="${4:-:1}"
MODEL_NAME="${5:-${GLM_MODEL:-glm-5}}"
if [[ "$MODEL_NAME" != */* ]]; then
  MODEL_NAME="openai/$MODEL_NAME"
fi

SWE_AGENT_DIR="$SWE_BENCH_PRO_ROOT/SWE-agent"
VENV_DIR="$SWE_AGENT_DIR/.venv"
CONFIG_DIR="$SWE_AGENT_DIR/sweagent_wrapper_configs"
RESULT_DIR="$SWE_AGENT_DIR/sweagent_results/swebench_pro"
WRAPPER_CONFIG_PATH="$CONFIG_DIR/glm_smoke.yaml"
INSTANCES_PATH="$SWE_AGENT_DIR/data/instances.yaml"
STRICT_AGENT_CONFIG_PATH="/Users/zsk8888/Desktop/lyb/agent_benchmark/configs/swebench_pro/sweagent_glm_strict_thought_action.yaml"
RUN_NAME="glm_smoke_strict"

DEPLOYMENT_TYPE="docker"
DEPLOYMENT_EXTRA=$'    --instances.deployment.pull=missing \\\n    --instances.deployment.platform linux/amd64 \\\n    --instances.deployment.docker_args=--entrypoint= \\\n    --instances.deployment.startup_timeout 1800'
if command -v modal >/dev/null 2>&1 && [ -f "$HOME/.modal.toml" ]; then
  DEPLOYMENT_TYPE="modal"
  DEPLOYMENT_EXTRA=$'    --instances.deployment.startup_timeout 1800 \\\n    --instances.deployment.runtime_timeout 3600'
fi

if [ -z "${GLM_API_KEY:-}" ] || [ -z "${GLM_BASE_URL:-}" ]; then
  echo "GLM_API_KEY and GLM_BASE_URL must be exported"
  exit 1
fi

if [ ! -f "$SWE_AGENT_DIR/pyproject.toml" ]; then
  echo "SWE-agent submodule missing or incomplete. Run scripts/setup_swebench_pro.sh first."
  exit 1
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "Virtualenv missing. Run scripts/setup_swebench_pro.sh first."
  exit 1
fi

source "$VENV_DIR/bin/activate"
export OPENAI_API_KEY="$GLM_API_KEY"
export OPENAI_BASE_URL="$GLM_BASE_URL"

mkdir -p "$CONFIG_DIR"
mkdir -p "$RESULT_DIR"
mkdir -p "$SWE_AGENT_DIR/data"

echo "Generating official SWE-agent instances.yaml"
python "$SWE_BENCH_PRO_ROOT/helper_code/generate_sweagent_instances.py" \
  --dockerhub_username "$DOCKERHUB_USERNAME" \
  --dataset_split "$DATASET_SPLIT" \
  --output_path "$INSTANCES_PATH"

echo "Writing SWE-agent .env"
cat > "$SWE_AGENT_DIR/.env" <<EOF
OPENAI_API_KEY=${GLM_API_KEY}
OPENAI_BASE_URL=${GLM_BASE_URL}
EOF

echo "Writing wrapper config"
cat > "$WRAPPER_CONFIG_PATH" <<EOF
output_dir: sweagent_results/swebench_pro/${RUN_NAME}
sweagent_command: |
  sweagent run-batch \
    --config ${STRICT_AGENT_CONFIG_PATH} \
    --output_dir {output_dir} \
    --redo_existing=True \
    --num_workers 1 \
    --random_delay_multiplier 1 \
    --instances.type file \
    --instances.path data/instances.yaml \
    --instances.slice ${INSTANCE_SLICE} \
    --instances.shuffle=False \
    --instances.deployment.type=${DEPLOYMENT_TYPE} \
${DEPLOYMENT_EXTRA} \
    --agent.model.name ${MODEL_NAME} \
    --agent.model.api_base \$OPENAI_BASE_URL \
    --agent.model.api_key \$OPENAI_API_KEY \
    --agent.model.per_instance_cost_limit 0 \
    --agent.model.per_instance_call_limit 8 \
    --agent.model.max_input_tokens 0 \
    --agent.model.max_output_tokens 0
EOF

echo "Running official SWE-agent wrapper smoke test with deployment=${DEPLOYMENT_TYPE}"
(
  cd "$SWE_AGENT_DIR"
  python sweagent_wrapper.py glm_smoke.yaml
)

echo "Gathering patches for official evaluator"
python "$SWE_BENCH_PRO_ROOT/helper_code/gather_patches.py" \
  --directory "$SWE_AGENT_DIR/sweagent_results/swebench_pro/${RUN_NAME}" \
  --prefix "${RUN_NAME}" \
  --output "$RESULT_DIR/${RUN_NAME}_patches.json"

echo "Smoke run complete"
echo "Predictions: $RESULT_DIR/${RUN_NAME}_patches.json"
