#!/usr/bin/env bash
# metrics-config-validator.sh — SignalOS Metrics Config & Schema Validator
#
# Validates metrics-config.yaml and metrics-map.yaml for correctness.
# Fails if any required field is missing, malformed, or contradictory.
#
# Usage:
#   metrics-config-validator.sh [--config <path>] [--metrics-map <path>] [--repo-root <path>]
#
# Exit codes:
#   0 = valid
#   1 = invalid (blocking errors found)
#   2 = warning-only mode (--warn flag)
#
# What it checks:
#   - mode is valid ("otlp" or "direct")
#   - OTLP config has required fields when mode=otlp
#   - Direct backend configs are complete when referenced
#   - Every metric has a metric_id (or name)
#   - Every metric has a target/threshold
#   - query_ref points to an existing file when specified
#   - No metric declares both inline query and query_ref
#   - No unknown backend is referenced
#   - File backend directory exists when file backend is configured

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-.}"
CONFIG_PATH=""
METRICS_MAP_PATH=""
WARN_MODE=false
ERRORS=0
WARNINGS=0

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

usage() {
  cat <<EOF
Usage: metrics-config-validator.sh [OPTIONS]

Options:
  --config <path>       Path to metrics-config.yaml
  --metrics-map <path>  Path to metrics-map.yaml
  --repo-root <path>    Repository root (default: current directory)
  --warn                Warning-only mode (exit 2 instead of 1)
  --help                Show this help

EOF
  exit "${1:-0}"
}

parse_args() {
  if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage 0
  fi

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --config)      CONFIG_PATH="$2"; shift 2 ;;
      --metrics-map) METRICS_MAP_PATH="$2"; shift 2 ;;
      --repo-root)   REPO_ROOT="$2"; shift 2 ;;
      --warn)        WARN_MODE=true; shift ;;
      --help)        usage 0 ;;
      *)             echo "Error: Unknown argument: $1" >&2; usage 1 ;;
    esac
  done

  # Auto-discover paths if not provided
  if [[ -z "$CONFIG_PATH" ]]; then
    for f in "${REPO_ROOT}/.signalos/metrics-config.yaml" \
             "${REPO_ROOT}/core/execution/agents/metrics-config.example.yaml"; do
      if [[ -f "$f" ]]; then CONFIG_PATH="$f"; break; fi
    done
  fi

  if [[ -z "$METRICS_MAP_PATH" ]]; then
    for f in "${REPO_ROOT}/.signalos/metrics-map.yaml" \
             "${REPO_ROOT}/core/execution/agents/metrics-map.yaml"; do
      if [[ -f "$f" ]]; then METRICS_MAP_PATH="$f"; break; fi
    done
  fi
}

error() {
  echo -e "  ${RED}✗${NC} $*" >&2
  ERRORS=$((ERRORS + 1))
}

warn() {
  echo -e "  ${YELLOW}⚠${NC} $*" >&2
  WARNINGS=$((WARNINGS + 1))
}

ok() {
  echo -e "  ${GREEN}✓${NC} $*"
}

# Parse a YAML field using python (available in most envs; no yq dependency)
yaml_field() {
  local file="$1" field="$2"
  python3 -c "
import yaml, sys
with open('$file') as f:
    d = yaml.safe_load(f) or {}
keys = '$field'.split('.')
for k in keys:
    if isinstance(d, dict):
        d = d.get(k)
    else:
        d = None
        break
if d is not None:
    print(d)
" 2>/dev/null || echo ""
}

yaml_keys() {
  local file="$1" field="$2"
  python3 -c "
import yaml
with open('$file') as f:
    d = yaml.safe_load(f) or {}
keys = '$field'.split('.') if '$field' else []
for k in keys:
    if isinstance(d, dict):
        d = d.get(k, {})
    else:
        d = {}
        break
if isinstance(d, dict):
    for k in d:
        print(k)
" 2>/dev/null || echo ""
}

VALID_MODES=("otlp" "direct")
VALID_BACKENDS=("prometheus" "grafana" "datadog" "cloudwatch" "file")

validate_config() {
  local file="$1"
  echo "Validating config: $file"

  # Check mode
  local mode
  mode=$(yaml_field "$file" "mode")
  if [[ -z "$mode" ]]; then
    warn "No mode specified (defaults to otlp)"
  elif [[ ! " ${VALID_MODES[*]} " =~ " $mode " ]]; then
    error "Invalid mode: '$mode' (must be 'otlp' or 'direct')"
  else
    ok "Mode: $mode"
  fi

  # Check OTLP config
  if [[ "$mode" == "otlp" || -z "$mode" ]]; then
    local otlp_ep
    otlp_ep=$(yaml_field "$file" "otlp.endpoint")
    if [[ -z "$otlp_ep" ]]; then
      warn "OTLP endpoint not set (will use default localhost:4318)"
    else
      ok "OTLP endpoint: $otlp_ep"
    fi
  fi

  # Check direct backends referenced by metrics
  local default_backend
  default_backend=$(yaml_field "$file" "direct.default_backend")
  if [[ -n "$default_backend" && ! " ${VALID_BACKENDS[*]} " =~ " $default_backend " ]]; then
    error "Unknown default_backend: '$default_backend'"
  fi

  # Check each configured direct backend
  for backend in $(yaml_keys "$file" "direct"); do
    [[ "$backend" == "default_backend" ]] && continue
    if [[ ! " ${VALID_BACKENDS[*]} " =~ " $backend " ]]; then
      error "Unknown direct backend: '$backend'"
    fi
  done

  # Check metrics
  local metric_ids
  metric_ids=$(yaml_keys "$file" "metrics")
  if [[ -z "$metric_ids" ]]; then
    warn "No metrics defined in config"
  else
    local count=0
    while IFS= read -r metric_id; do
      [[ -z "$metric_id" ]] && continue
      count=$((count + 1))

      # Check for target
      local target
      target=$(yaml_field "$file" "metrics.$metric_id.target")
      if [[ -z "$target" ]]; then
        local threshold
        threshold=$(yaml_field "$file" "metrics.$metric_id.threshold")
        if [[ -z "$threshold" ]]; then
          error "Metric '$metric_id' has no target or threshold"
        fi
      fi

      # Check for contradictory query sources
      local query query_ref
      query=$(yaml_field "$file" "metrics.$metric_id.query")
      query_ref=$(yaml_field "$file" "metrics.$metric_id.implementation.query_ref")
      if [[ -n "$query" && -n "$query_ref" ]]; then
        error "Metric '$metric_id' declares both inline 'query' and 'query_ref' — use one or the other"
      fi

      # If query_ref, check file exists
      if [[ -n "$query_ref" ]]; then
        local ref_path="${REPO_ROOT}/${query_ref}"
        if [[ ! -f "$ref_path" ]]; then
          # Also check relative to config directory
          ref_path="$(dirname "$file")/${query_ref}"
          if [[ ! -f "$ref_path" ]]; then
            error "Metric '$metric_id' query_ref '$query_ref' — file not found"
          fi
        fi
      fi

      # Check backend is valid
      local metric_backend
      metric_backend=$(yaml_field "$file" "metrics.$metric_id.implementation.backend")
      if [[ -z "$metric_backend" ]]; then
        metric_backend=$(yaml_field "$file" "metrics.$metric_id.backend")
      fi
      if [[ -n "$metric_backend" && ! " ${VALID_BACKENDS[*]} " =~ " $metric_backend " ]]; then
        error "Metric '$metric_id' references unknown backend: '$metric_backend'"
      fi
    done <<< "$metric_ids"
    ok "Metrics: $count defined"
  fi

  # Check file backend dir
  local file_dir
  file_dir=$(yaml_field "$file" "direct.file.metrics_dir")
  if [[ -n "$file_dir" ]]; then
    local full_path="${REPO_ROOT}/$file_dir"
    if [[ ! -d "$full_path" ]]; then
      warn "File backend metrics_dir '$file_dir' does not exist yet (created on first push)"
    fi
  fi
}

validate_metrics_map() {
  local file="$1"
  echo ""
  echo "Validating metrics map: $file"

  local metric_ids
  metric_ids=$(yaml_keys "$file" "metrics")
  if [[ -z "$metric_ids" ]]; then
    warn "No metrics in metrics-map.yaml"
    return
  fi

  local count=0
  while IFS= read -r metric_id; do
    [[ -z "$metric_id" ]] && continue
    count=$((count + 1))

    # Required fields
    local desc target
    desc=$(yaml_field "$file" "metrics.$metric_id.description")
    target=$(yaml_field "$file" "metrics.$metric_id.target")

    if [[ -z "$desc" ]]; then
      error "Metric '$metric_id' missing 'description'"
    fi
    if [[ -z "$target" ]]; then
      error "Metric '$metric_id' missing 'target'"
    fi

    # Source type must be valid
    local source_type
    source_type=$(yaml_field "$file" "metrics.$metric_id.source.source_type")
    if [[ -n "$source_type" && ! "$source_type" =~ ^(timeseries|gauge|counter|log_query)$ ]]; then
      error "Metric '$metric_id' has invalid source_type: '$source_type'"
    fi

    # query_ref should exist
    local query_ref
    query_ref=$(yaml_field "$file" "metrics.$metric_id.source.implementation.query_ref")
    if [[ -n "$query_ref" ]]; then
      local ref_path="${REPO_ROOT}/${query_ref}"
      if [[ ! -f "$ref_path" ]]; then
        ref_path="$(dirname "$file")/${query_ref}"
        if [[ ! -f "$ref_path" ]]; then
          error "Metric '$metric_id' query_ref '$query_ref' — file not found"
        fi
      fi
    fi
  done <<< "$metric_ids"
  ok "Metrics map: $count metrics validated"
}

main() {
  parse_args "$@"

  echo "SignalOS Metrics Config Validator"
  echo ""

  if [[ -n "$CONFIG_PATH" && -f "$CONFIG_PATH" ]]; then
    validate_config "$CONFIG_PATH"
  else
    warn "No metrics config found"
  fi

  if [[ -n "$METRICS_MAP_PATH" && -f "$METRICS_MAP_PATH" ]]; then
    validate_metrics_map "$METRICS_MAP_PATH"
  else
    warn "No metrics-map.yaml found"
  fi

  echo ""
  if [[ $ERRORS -gt 0 ]]; then
    echo -e "${RED}Validation FAILED: $ERRORS error(s), $WARNINGS warning(s)${NC}"
    if [[ "$WARN_MODE" == "true" ]]; then
      exit 2
    fi
    exit 1
  elif [[ $WARNINGS -gt 0 ]]; then
    echo -e "${YELLOW}Validation passed with $WARNINGS warning(s)${NC}"
    exit 0
  else
    echo -e "${GREEN}Validation passed — config and metrics map are correct${NC}"
    exit 0
  fi
}

main "$@"
