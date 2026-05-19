#!/usr/bin/env bash
# metrics-adapter.sh — SignalOS Observability Adapter
#
# Two observability modes:
#   STANDARD MODE (OTLP)  — canonical architecture. Exports metrics via
#       OTLP/HTTP to any OpenTelemetry-compatible collector. The collector
#       routes to Prometheus, Grafana, Datadog, CloudWatch, or any other
#       backend. SignalOS does not need to know the backend.
#   COMPATIBILITY MODE (direct) — for simple deployments. Talks directly
#       to one of four built-in backends: prometheus, grafana, datadog,
#       cloudwatch. No collector required.
#
# Three internal layers:
#   1. Event contract    — signal_metric_read, signal_metric_push,
#                          signal_check_staleness (SignalOS-native verbs)
#   2. Reporter layer    — audit_trail (always on), stdout, jsonl
#   3. Transport layer   — otlp (standard) or direct backend (compat)
#
# Usage:
#   metrics-adapter.sh read   --metric <name> [--mode otlp|direct] [options]
#   metrics-adapter.sh push   --metric <name> --value <val> [options]
#   metrics-adapter.sh check  --config <path>
#   metrics-adapter.sh poll   --config <path> --signal-log <path>
#   metrics-adapter.sh list-backends
#
# Exit: 0 = success, 1 = error, 2 = stale data, 3 = backend unavailable
#
# ── Mode Precedence Rules ────────────────────────────────────────────────
#
#   Situation                              Effective mode
#   ─────────────────────────────────────  ──────────────
#   no mode specified, push operation      OTLP (standard)
#   no mode specified, read operation      direct (OTLP is push-only)
#   --backend <name> flag present          direct (implicit)
#   --mode otlp explicit                   OTLP
#   --mode direct explicit                 direct
#   config says mode: otlp                 OTLP, unless operation is read
#   config says mode: direct               direct
#   SIGNALOS_METRICS_BACKEND env set       direct (implicit)
#
#   Flag > env var > config file > operation default.
#
# ── Transport Failure Policy ─────────────────────────────────────────────
#
#   - AUDIT_TRAIL.jsonl is MANDATORY. Every read/push/poll always writes
#     an audit entry, regardless of whether the external transport succeeds.
#   - External transport success is OPTIONAL unless a release policy
#     (e.g. Gate 5) explicitly requires live metric verification.
#   - Transport failure returns exit 3 but does NOT prevent audit logging.
#   - SignalOS internal evidence and external backend delivery are
#     independent concerns. Do not confuse them.
#
# ─────────────────────────────────────────────────────────────────────────
#
# OTLP environment variables (standard, from OpenTelemetry spec):
#   OTEL_EXPORTER_OTLP_ENDPOINT     Collector endpoint (default: http://localhost:4318)
#   OTEL_EXPORTER_OTLP_HEADERS      Auth headers (e.g. "Authorization=Bearer xxx")
#   OTEL_SERVICE_NAME                Service name (default: signalos)
#   OTEL_RESOURCE_ATTRIBUTES         Additional resource attributes
#
# Direct-backend environment variables:
#   SIGNALOS_METRICS_BACKEND         Override default backend (prometheus|grafana|datadog|cloudwatch)
#   SIGNALOS_METRICS_CONFIG          Override config path
#   PROM_TOKEN / GRAFANA_API_KEY / DD_API_KEY / DD_APP_KEY / AWS_REGION

set -euo pipefail

# ============================================================================
# Configuration
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || true)}"
REPO_ROOT="${REPO_ROOT:-.}"
AUDIT_LOG=""  # deferred — set in resolve_paths() after parse_args

# Mode: "otlp" (standard) or "direct" (compatibility)
MODE=""
COMMAND=""
METRIC_NAME=""
METRIC_VALUE=""
BACKEND=""
CONFIG_PATH="${SIGNALOS_METRICS_CONFIG:-}"
INTERVAL=3600
SIGNAL_LOG=""
STALENESS_HOURS=2
QUERY=""
LABELS=""
PUSH_JOB="signalos"

# OTLP settings (standard mode)
OTLP_ENDPOINT="${OTEL_EXPORTER_OTLP_ENDPOINT:-http://localhost:4318}"
OTLP_HEADERS="${OTEL_EXPORTER_OTLP_HEADERS:-}"
OTLP_SERVICE="${OTEL_SERVICE_NAME:-signalos}"
OTLP_RESOURCE_ATTRS="${OTEL_RESOURCE_ATTRIBUTES:-}"

# Direct backend settings (compatibility mode)
ENDPOINT=""
AUTH_TOKEN=""
DATASOURCE_UID=""
NAMESPACE="SignalOS"
REGION=""
SITE="datadoghq.com"
PUSHGATEWAY=""

# Colours
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

AVAILABLE_BACKENDS=("otlp" "prometheus" "grafana" "datadog" "cloudwatch" "file")

# File backend settings (mock/CI/demo)
FILE_METRICS_DIR=""

# ============================================================================
# Usage
# ============================================================================

usage() {
  cat <<EOF
metrics-adapter.sh — SignalOS Observability Adapter

  Standard mode:  OTLP export to any OpenTelemetry collector (canonical)
  Compat mode:    Direct backend (prometheus|grafana|datadog|cloudwatch)

Usage:
  metrics-adapter.sh <command> [options]

Commands:
  read           Read a metric value
  push           Push a metric value
  check          Validate endpoints from config
  poll           Continuous reading loop for Signal Window
  list-backends  Show available backends and their status

Mode selection:
  --mode otlp          Standard mode — export via OTLP/HTTP (default)
  --mode direct        Compatibility mode — talk to backend directly
  --backend <name>     Direct backend: prometheus|grafana|datadog|cloudwatch
                       (implies --mode direct)

OTLP options (standard mode):
  --otlp-endpoint <url>    Collector endpoint (or OTEL_EXPORTER_OTLP_ENDPOINT)
  --otlp-headers <hdrs>    Auth headers (or OTEL_EXPORTER_OTLP_HEADERS)
  --otlp-service <name>    Service name (or OTEL_SERVICE_NAME, default: signalos)

Read options:
  --metric <name>        Metric name (required)
  --query <string>       Backend-native query (direct mode only)
  --endpoint <url>       Backend endpoint (direct mode only)
  --auth-token <token>   Auth token (direct mode only)

Push options:
  --metric <name>        Metric name (required)
  --value <number>       Metric value (required)
  --labels <k=v,...>     Comma-separated labels
  --job <name>           Push job name (default: signalos)

Check / Poll options:
  --config <path>        YAML config file path
  --signal-log <path>    Signal Window log path (poll only)
  --interval <seconds>   Poll interval (default: 3600)
  --staleness <hours>    Stale data threshold (default: 2)

Global:
  --repo-root <path>     Override repo root for audit trail
  --help                 Show this help

EOF
  exit "${1:-0}"
}

# ============================================================================
# Argument Parsing
# ============================================================================

parse_args() {
  if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage 0
  fi

  COMMAND="${1:-}"
  shift || true

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --mode)           MODE="$2"; shift 2 ;;
      --metric)         METRIC_NAME="$2"; shift 2 ;;
      --value)          METRIC_VALUE="$2"; shift 2 ;;
      --backend)        BACKEND="$2"; MODE="direct"; shift 2 ;;
      --endpoint)       ENDPOINT="$2"; shift 2 ;;
      --query)          QUERY="$2"; shift 2 ;;
      --auth-token)     AUTH_TOKEN="$2"; shift 2 ;;
      --config)         CONFIG_PATH="$2"; shift 2 ;;
      --interval)       INTERVAL="$2"; shift 2 ;;
      --signal-log)     SIGNAL_LOG="$2"; shift 2 ;;
      --staleness)      STALENESS_HOURS="$2"; shift 2 ;;
      --labels)         LABELS="$2"; shift 2 ;;
      --job)            PUSH_JOB="$2"; shift 2 ;;
      --repo-root)      REPO_ROOT="$2"; shift 2 ;;
      --pushgateway)    PUSHGATEWAY="$2"; shift 2 ;;
      --datasource-uid) DATASOURCE_UID="$2"; shift 2 ;;
      --namespace)      NAMESPACE="$2"; shift 2 ;;
      --region)         REGION="$2"; shift 2 ;;
      --site)           SITE="$2"; shift 2 ;;
      --otlp-endpoint)  OTLP_ENDPOINT="$2"; shift 2 ;;
      --otlp-headers)   OTLP_HEADERS="$2"; shift 2 ;;
      --otlp-service)   OTLP_SERVICE="$2"; shift 2 ;;
      --help)           usage 0 ;;
      *)                echo "Error: Unknown argument: $1" >&2; usage 1 ;;
    esac
  done

  # Apply env var overrides
  if [[ -z "$BACKEND" && -n "${SIGNALOS_METRICS_BACKEND:-}" ]]; then
    BACKEND="$SIGNALOS_METRICS_BACKEND"
    MODE="direct"
  fi
}

# ============================================================================
# Layer 1: SignalOS Event Contract
# ============================================================================
# These are the canonical SignalOS metric verbs. They are backend-agnostic.
# The transport layer (OTLP or direct) implements the actual I/O.

signal_metric_read() {
  local metric="$1"
  local mode="${MODE:-otlp}"

  if [[ "$mode" == "otlp" ]]; then
    otlp_read "$metric"
  else
    direct_read "$metric"
  fi
}

signal_metric_push() {
  local metric="$1" value="$2" labels="${3:-}" job="${4:-$PUSH_JOB}"
  local mode="${MODE:-otlp}"

  if [[ "$mode" == "otlp" ]]; then
    otlp_push "$metric" "$value" "$labels"
  else
    direct_push "$metric" "$value" "$labels" "$job"
  fi
}

signal_check_staleness() {
  local data_ts="$1"
  if [[ -z "$data_ts" ]]; then
    return 0
  fi
  local now_epoch data_epoch age_hours
  now_epoch=$(date -u +%s)
  data_epoch=$(date -d "$data_ts" +%s 2>/dev/null || echo "$now_epoch")
  age_hours=$(( (now_epoch - data_epoch) / 3600 ))
  if [[ $age_hours -gt $STALENESS_HOURS ]]; then
    log_warn "Data is ${age_hours}h old (threshold: ${STALENESS_HOURS}h)"
    return 2
  fi
  return 0
}

# ============================================================================
# Layer 2: Reporter (always-on audit trail + stdout)
# ============================================================================

log_info()  { echo -e "${CYAN}[metrics]${NC} $*" >&2; }
log_ok()    { echo -e "${GREEN}[metrics] ✓${NC} $*" >&2; }
log_warn()  { echo -e "${YELLOW}[metrics] ⚠${NC} $*" >&2; }
log_error() { echo -e "${RED}[metrics] ✗${NC} $*" >&2; }

require_cmd() {
  if ! command -v "$1" &>/dev/null; then
    log_error "Required command not found: $1"
    exit 1
  fi
}

audit_write() {
  local action="$1" detail="$2"
  local ts
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  mkdir -p "$(dirname "$AUDIT_LOG")"
  echo "{\"ts\":\"$ts\",\"actor\":\"metrics-adapter\",\"role\":\"system\",\"action\":\"$action\",\"detail\":\"$detail\"}" >> "$AUDIT_LOG"
}

# ============================================================================
# Layer 3a: OTLP Transport (Standard Mode)
# ============================================================================
# Exports metrics via OTLP/HTTP to any OpenTelemetry-compatible collector.
# The collector handles routing to the actual backend.
# Follows OTLP/HTTP JSON spec: https://opentelemetry.io/docs/specs/otlp/

otlp_build_headers() {
  local headers=(-H "Content-Type: application/json")

  if [[ -n "$OTLP_HEADERS" ]]; then
    # Parse OTEL_EXPORTER_OTLP_HEADERS format: key1=value1,key2=value2
    IFS=',' read -ra PAIRS <<< "$OTLP_HEADERS"
    for pair in "${PAIRS[@]}"; do
      local key="${pair%%=*}"
      local val="${pair#*=}"
      headers+=(-H "$key: $val")
    done
  fi

  echo "${headers[@]}"
}

otlp_build_resource() {
  local attrs='[{"key":"service.name","value":{"stringValue":"'"$OTLP_SERVICE"'"}}]'

  if [[ -n "$OTLP_RESOURCE_ATTRS" ]]; then
    # Parse key=value,key=value format into OTLP resource attributes
    local extra_attrs=""
    IFS=',' read -ra PAIRS <<< "$OTLP_RESOURCE_ATTRS"
    for pair in "${PAIRS[@]}"; do
      local key="${pair%%=*}"
      local val="${pair#*=}"
      extra_attrs="$extra_attrs,{\"key\":\"$key\",\"value\":{\"stringValue\":\"$val\"}}"
    done
    attrs="[{\"key\":\"service.name\",\"value\":{\"stringValue\":\"$OTLP_SERVICE\"}}$extra_attrs]"
  fi

  echo "$attrs"
}

otlp_push() {
  local metric="$1" value="$2" labels="${3:-}"
  require_cmd curl
  require_cmd jq

  local ts_nano
  ts_nano=$(( $(date -u +%s) * 1000000000 ))

  # Build OTLP attributes from labels
  local attributes="[]"
  if [[ -n "$labels" ]]; then
    attributes=$(echo "$labels" | tr ',' '\n' | while IFS='=' read -r k v; do
      echo "{\"key\":\"$k\",\"value\":{\"stringValue\":\"$v\"}}"
    done | jq -s '.')
  fi

  local resource_attrs
  resource_attrs=$(otlp_build_resource)

  # Build OTLP/HTTP JSON payload (ExportMetricsServiceRequest)
  local payload
  payload=$(jq -n \
    --argjson resource_attrs "$resource_attrs" \
    --arg metric "$metric" \
    --argjson value "$value" \
    --argjson ts "$ts_nano" \
    --argjson attrs "$attributes" \
    '{
      resourceMetrics: [{
        resource: { attributes: $resource_attrs },
        scopeMetrics: [{
          scope: { name: "signalos.metrics-adapter", version: "1.0.1" },
          metrics: [{
            name: $metric,
            gauge: {
              dataPoints: [{
                timeUnixNano: ($ts | tostring),
                asDouble: $value,
                attributes: $attrs
              }]
            }
          }]
        }]
      }]
    }')

  local url="${OTLP_ENDPOINT}/v1/metrics"

  # Build header array
  local -a curl_args=(-sf --max-time 15 -H "Content-Type: application/json")
  if [[ -n "$OTLP_HEADERS" ]]; then
    IFS=',' read -ra PAIRS <<< "$OTLP_HEADERS"
    for pair in "${PAIRS[@]}"; do
      local key="${pair%%=*}"
      local val="${pair#*=}"
      curl_args+=(-H "$key: $val")
    done
  fi

  curl "${curl_args[@]}" -d "$payload" "$url" >/dev/null 2>&1 || {
    log_error "Failed to push to OTLP collector at $url"
    audit_write "metric-push-fail" "mode=otlp metric=$metric endpoint=$OTLP_ENDPOINT"
    exit 3
  }

  log_ok "Pushed $metric=$value via OTLP ($OTLP_ENDPOINT)"
  audit_write "metric-push" "mode=otlp metric=$metric value=$value endpoint=$OTLP_ENDPOINT"
}

otlp_read() {
  # OTLP is a push protocol — it does not support reading/querying.
  # For reads in standard mode, query the collector's backend directly
  # or use Prometheus remote-read through the collector.
  local metric="$1"

  log_warn "OTLP is push-only. Falling back to direct mode for read."
  log_warn "Hint: metrics-adapter.sh read --metric $metric --mode direct --backend prometheus --endpoint <url>"
  audit_write "metric-read-fallback" "mode=otlp→direct metric=$metric reason=otlp_push_only"

  # Attempt fallback to direct mode if a backend is configured
  if [[ -n "$CONFIG_PATH" && -f "$CONFIG_PATH" ]]; then
    local fallback_backend
    fallback_backend=$(parse_config_field "$CONFIG_PATH" "direct.default_backend")
    if [[ -n "$fallback_backend" ]]; then
      BACKEND="$fallback_backend"
      load_config_defaults
      direct_read "$metric"
      return $?
    fi
  fi

  log_error "No direct backend configured for fallback read. Configure direct.default_backend in config."
  exit 3
}

otlp_check() {
  # Check OTLP collector health
  local url="${OTLP_ENDPOINT}/v1/metrics"

  # Some collectors expose a health endpoint; try common patterns
  if curl -sf --max-time 5 "${OTLP_ENDPOINT}/health" >/dev/null 2>&1; then
    return 0
  fi

  # Try a minimal POST to the metrics endpoint (empty payload = rejected but reachable)
  local http_code
  http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 \
    -H "Content-Type: application/json" \
    -d '{}' "$url" 2>/dev/null || echo "000")

  # Any HTTP response (even 400/415) means the collector is reachable
  if [[ "$http_code" != "000" ]]; then
    return 0
  fi

  return 1
}

# ============================================================================
# Layer 3b: Direct Transport (Compatibility Mode)
# ============================================================================

# --- Config loading (compatibility mode only) ---

parse_config_field() {
  local config_path="$1" field_path="$2"
  python3 -c "
import yaml, json, sys
with open('$config_path') as f:
    data = yaml.safe_load(f)
parts = '$field_path'.split('.')
current = data
for p in parts:
    if isinstance(current, dict) and p in current:
        current = current[p]
    else:
        current = ''
        break
if isinstance(current, (dict, list)):
    print(json.dumps(current))
else:
    print(current if current is not None else '')
" 2>/dev/null
}

load_config_defaults() {
  if [[ -z "$CONFIG_PATH" || ! -f "$CONFIG_PATH" ]]; then
    return 0
  fi

  # Detect mode from config
  local config_mode
  config_mode=$(parse_config_field "$CONFIG_PATH" "mode")
  [[ -z "$MODE" && -n "$config_mode" ]] && MODE="$config_mode"

  # Load OTLP settings from config
  if [[ "${MODE:-otlp}" == "otlp" ]]; then
    local cfg_endpoint cfg_headers cfg_service
    cfg_endpoint=$(parse_config_field "$CONFIG_PATH" "otlp.endpoint")
    cfg_headers=$(parse_config_field "$CONFIG_PATH" "otlp.headers_env")
    cfg_service=$(parse_config_field "$CONFIG_PATH" "otlp.service_name")
    [[ -n "$cfg_endpoint" && "$OTLP_ENDPOINT" == "http://localhost:4318" ]] && OTLP_ENDPOINT="$cfg_endpoint"
    [[ -n "$cfg_headers" ]] && OTLP_HEADERS="${!cfg_headers:-$OTLP_HEADERS}"
    [[ -n "$cfg_service" ]] && OTLP_SERVICE="$cfg_service"
  fi

  # Load direct backend settings
  local effective_backend="${BACKEND:-$(parse_config_field "$CONFIG_PATH" "direct.default_backend")}"
  BACKEND="${effective_backend:-prometheus}"

  case "$BACKEND" in
    prometheus)
      [[ -z "$ENDPOINT" ]] && ENDPOINT=$(parse_config_field "$CONFIG_PATH" "direct.prometheus.endpoint")
      [[ -z "$PUSHGATEWAY" ]] && PUSHGATEWAY=$(parse_config_field "$CONFIG_PATH" "direct.prometheus.pushgateway")
      local token_env
      token_env=$(parse_config_field "$CONFIG_PATH" "direct.prometheus.auth_token_env")
      [[ -z "$AUTH_TOKEN" && -n "$token_env" ]] && AUTH_TOKEN="${!token_env:-}"
      ;;
    grafana)
      [[ -z "$ENDPOINT" ]] && ENDPOINT=$(parse_config_field "$CONFIG_PATH" "direct.grafana.endpoint")
      [[ -z "$DATASOURCE_UID" ]] && DATASOURCE_UID=$(parse_config_field "$CONFIG_PATH" "direct.grafana.datasource_uid")
      local gkey_env
      gkey_env=$(parse_config_field "$CONFIG_PATH" "direct.grafana.api_key_env")
      [[ -z "$AUTH_TOKEN" && -n "$gkey_env" ]] && AUTH_TOKEN="${!gkey_env:-}"
      ;;
    datadog)
      local dd_api_env dd_app_env
      dd_api_env=$(parse_config_field "$CONFIG_PATH" "direct.datadog.api_key_env")
      dd_app_env=$(parse_config_field "$CONFIG_PATH" "direct.datadog.app_key_env")
      [[ -n "$dd_api_env" ]] && export DD_API_KEY="${!dd_api_env:-${DD_API_KEY:-}}"
      [[ -n "$dd_app_env" ]] && export DD_APP_KEY="${!dd_app_env:-${DD_APP_KEY:-}}"
      local site_val
      site_val=$(parse_config_field "$CONFIG_PATH" "direct.datadog.site")
      [[ -n "$site_val" ]] && SITE="$site_val"
      ;;
    cloudwatch)
      [[ -z "$NAMESPACE" ]] && NAMESPACE=$(parse_config_field "$CONFIG_PATH" "direct.cloudwatch.namespace")
      [[ -z "$REGION" ]] && REGION=$(parse_config_field "$CONFIG_PATH" "direct.cloudwatch.region")
      [[ -z "$NAMESPACE" ]] && NAMESPACE="SignalOS"
      ;;
  esac
}

# --- Direct read/push dispatch ---

direct_read() {
  local metric="$1"
  BACKEND="${BACKEND:-prometheus}"

  case "$BACKEND" in
    prometheus)  prometheus_read "$metric" "${QUERY:-$metric}" "$ENDPOINT" ;;
    grafana)     grafana_read "$metric" "${QUERY:-$metric}" "$ENDPOINT" ;;
    datadog)     datadog_read "$metric" "${QUERY:-}" ;;
    cloudwatch)  cloudwatch_read "$metric" "${QUERY:-}" ;;
    file)        file_read "$metric" ;;
    *)           log_error "Unknown direct backend: $BACKEND"; exit 1 ;;
  esac
}

direct_push() {
  local metric="$1" value="$2" labels="$3" job="${4:-$PUSH_JOB}"
  BACKEND="${BACKEND:-prometheus}"

  case "$BACKEND" in
    prometheus)  prometheus_push "$metric" "$value" "$labels" "$job" ;;
    grafana)     grafana_push ;;
    datadog)     datadog_push "$metric" "$value" "$labels" "$job" ;;
    cloudwatch)  cloudwatch_push "$metric" "$value" "$labels" "$job" ;;
    file)        file_push "$metric" "$value" "$labels" ;;
    *)           log_error "Unknown direct backend: $BACKEND"; exit 1 ;;
  esac
}

# --- Prometheus ---

prometheus_read() {
  local metric="$1" query="${2:-$metric}" endpoint="${3:-$ENDPOINT}"
  require_cmd curl; require_cmd jq

  [[ -z "$endpoint" ]] && { log_error "Prometheus endpoint required"; exit 1; }

  local response
  response=$(curl -sf --max-time 15 \
    ${AUTH_TOKEN:+-H "Authorization: Bearer $AUTH_TOKEN"} \
    --data-urlencode "query=$query" \
    "${endpoint}/api/v1/query" 2>/dev/null) || {
    log_error "Failed to query Prometheus at $endpoint"
    audit_write "metric-read-fail" "mode=direct backend=prometheus metric=$metric"
    exit 3
  }

  local status
  status=$(echo "$response" | jq -r '.status' 2>/dev/null)
  [[ "$status" != "success" ]] && { log_error "Prometheus query failed"; exit 1; }

  local value
  value=$(echo "$response" | jq -r '
    if .data.result | length > 0 then .data.result[0].value[1]
    else "NO_DATA" end
  ' 2>/dev/null)

  local data_ts
  data_ts=$(echo "$response" | jq -r '
    if .data.result | length > 0 then .data.result[0].value[0] | tostring
    else "" end
  ' 2>/dev/null)

  if [[ -n "$data_ts" && "$data_ts" != "null" ]]; then
    local epoch_int="${data_ts%%.*}"
    data_ts=$(date -u -d "@$epoch_int" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "")
  fi

  if ! signal_check_staleness "$data_ts"; then
    echo "$value"; exit 2
  fi

  echo "$value"
  audit_write "metric-read" "mode=direct backend=prometheus metric=$metric value=$value"
}

prometheus_push() {
  local metric="$1" value="$2" labels="$3" job="${4:-$PUSH_JOB}"
  require_cmd curl

  [[ -z "$PUSHGATEWAY" ]] && { log_error "PushGateway URL required"; exit 1; }

  local label_str=""
  if [[ -n "$labels" ]]; then
    label_str=$(echo "$labels" | sed 's/\([^=,]*\)=\([^,]*\)/\1="\2"/g; s/^/{/; s/$/}/')
  fi

  curl -sf --max-time 10 \
    ${AUTH_TOKEN:+-H "Authorization: Bearer $AUTH_TOKEN"} \
    --data-binary "${metric}${label_str} ${value}" \
    "${PUSHGATEWAY}/metrics/job/${job}" 2>/dev/null || {
    log_error "Failed to push to PushGateway at $PUSHGATEWAY"
    audit_write "metric-push-fail" "mode=direct backend=prometheus metric=$metric"
    exit 3
  }

  log_ok "Pushed $metric=$value to PushGateway ($job)"
  audit_write "metric-push" "mode=direct backend=prometheus metric=$metric value=$value"
}

prometheus_check() {
  local endpoint="$1"
  curl -sf --max-time 5 "${endpoint}/-/healthy" >/dev/null 2>&1 || \
  curl -sf --max-time 5 "${endpoint}/api/v1/status/buildinfo" >/dev/null 2>&1
}

# --- Grafana ---

grafana_read() {
  local metric="$1" query="${2:-$metric}" endpoint="${3:-$ENDPOINT}"
  require_cmd curl; require_cmd jq

  [[ -z "$endpoint" ]] && { log_error "Grafana endpoint required"; exit 1; }
  [[ -z "$AUTH_TOKEN" ]] && { log_error "Grafana API key required"; exit 1; }

  local ds_uid="${DATASOURCE_UID:-prometheus}"
  local now_ms=$(( $(date -u +%s) * 1000 ))
  local from_ms=$(( now_ms - 3600000 ))

  local payload
  payload=$(jq -n \
    --arg ds_uid "$ds_uid" --arg query "$query" \
    --argjson from "$from_ms" --argjson to "$now_ms" \
    '{queries:[{refId:"A",datasource:{uid:$ds_uid},expr:$query,instant:true,maxDataPoints:1}],from:($from|tostring),to:($to|tostring)}')

  local response
  response=$(curl -sf --max-time 15 \
    -H "Authorization: Bearer $AUTH_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$payload" "${endpoint}/api/ds/query" 2>/dev/null) || {
    log_error "Failed to query Grafana at $endpoint"
    audit_write "metric-read-fail" "mode=direct backend=grafana metric=$metric"
    exit 3
  }

  local value
  value=$(echo "$response" | jq -r '.results.A.frames[0].data.values[1][0] // "NO_DATA"' 2>/dev/null)
  [[ "$value" == "null" || -z "$value" ]] && value="NO_DATA"

  echo "$value"
  audit_write "metric-read" "mode=direct backend=grafana metric=$metric value=$value"
}

grafana_push() {
  log_error "Grafana does not support direct push. Use Prometheus PushGateway or InfluxDB instead."
  exit 1
}

grafana_check() {
  local endpoint="$1" token="$2"
  curl -sf --max-time 5 -H "Authorization: Bearer $token" "${endpoint}/api/health" >/dev/null 2>&1
}

# --- Datadog ---

datadog_read() {
  local metric="$1" query="${2:-"avg:${metric}{*}"}"
  require_cmd curl; require_cmd jq

  local api_key="${DD_API_KEY:-}" app_key="${DD_APP_KEY:-}"
  [[ -z "$api_key" || -z "$app_key" ]] && { log_error "DD_API_KEY + DD_APP_KEY required"; exit 1; }

  local now_epoch=$(date -u +%s)
  local from_epoch=$(( now_epoch - 3600 ))

  local response
  response=$(curl -sf --max-time 15 \
    -H "DD-API-KEY: $api_key" -H "DD-APPLICATION-KEY: $app_key" \
    -G "https://api.${SITE}/api/v1/query" \
    --data-urlencode "query=$query" \
    --data-urlencode "from=$from_epoch" \
    --data-urlencode "to=$now_epoch" 2>/dev/null) || {
    log_error "Failed to query Datadog"
    audit_write "metric-read-fail" "mode=direct backend=datadog metric=$metric"
    exit 3
  }

  local value
  value=$(echo "$response" | jq -r '
    if .series | length > 0 then .series[0].pointlist[-1][1] | tostring
    else "NO_DATA" end
  ' 2>/dev/null)

  echo "$value"
  audit_write "metric-read" "mode=direct backend=datadog metric=$metric value=$value"
}

datadog_push() {
  local metric="$1" value="$2" labels="$3" _job="$4"
  require_cmd curl; require_cmd jq

  local api_key="${DD_API_KEY:-}"
  [[ -z "$api_key" ]] && { log_error "DD_API_KEY required"; exit 1; }

  local now_epoch=$(date -u +%s)

  local tags_json="[]"
  [[ -n "$labels" ]] && tags_json=$(echo "$labels" | tr ',' '\n' | jq -R '.' | jq -s '.')

  local payload
  payload=$(jq -n --arg m "$metric" --argjson t "$now_epoch" --argjson v "$value" --argjson tags "$tags_json" \
    '{series:[{metric:$m,type:0,points:[[$t,$v]],tags:$tags}]}')

  curl -sf --max-time 10 \
    -H "DD-API-KEY: $api_key" -H "Content-Type: application/json" \
    -d "$payload" "https://api.${SITE}/api/v1/series" 2>/dev/null || {
    log_error "Failed to push to Datadog"
    audit_write "metric-push-fail" "mode=direct backend=datadog metric=$metric"
    exit 3
  }

  log_ok "Pushed $metric=$value to Datadog ($SITE)"
  audit_write "metric-push" "mode=direct backend=datadog metric=$metric value=$value"
}

datadog_check() {
  local api_key="${DD_API_KEY:-}"
  [[ -z "$api_key" ]] && return 1
  curl -sf --max-time 5 -H "DD-API-KEY: $api_key" "https://api.${SITE}/api/v1/validate" >/dev/null 2>&1
}

# --- CloudWatch ---

cloudwatch_read() {
  local metric="$1" query="${2:-}"
  require_cmd aws; require_cmd jq

  local region_flag=""
  [[ -n "$REGION" ]] && region_flag="--region $REGION"

  local now_ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  local start_ts=$(date -u -d "1 hour ago" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || \
                   date -u -v-1H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)

  local metric_queries
  if [[ "$query" == "{"* || "$query" == "["* ]]; then
    metric_queries="$query"
  else
    metric_queries=$(jq -n --arg m "$metric" --arg ns "$NAMESPACE" \
      '[{"Id":"m1","MetricStat":{"Metric":{"Namespace":$ns,"MetricName":$m},"Period":300,"Stat":"Average"}}]')
  fi

  local response
  # shellcheck disable=SC2086
  response=$(aws cloudwatch get-metric-data $region_flag \
    --start-time "$start_ts" --end-time "$now_ts" \
    --metric-data-queries "$metric_queries" 2>/dev/null) || {
    log_error "Failed to query CloudWatch for $metric"
    audit_write "metric-read-fail" "mode=direct backend=cloudwatch metric=$metric"
    exit 3
  }

  local value
  value=$(echo "$response" | jq -r '
    if .MetricDataResults | length > 0 and (.MetricDataResults[0].Values | length > 0)
    then .MetricDataResults[0].Values[0] | tostring
    else "NO_DATA" end
  ' 2>/dev/null)

  echo "$value"
  audit_write "metric-read" "mode=direct backend=cloudwatch metric=$metric value=$value"
}

cloudwatch_push() {
  local metric="$1" value="$2" labels="$3" _job="$4"
  require_cmd aws

  local region_flag=""
  [[ -n "$REGION" ]] && region_flag="--region $REGION"

  local dimensions_flag=""
  if [[ -n "$labels" ]]; then
    local dims=""
    IFS=',' read -ra PAIRS <<< "$labels"
    for pair in "${PAIRS[@]}"; do
      local key="${pair%%=*}" val="${pair#*=}"
      [[ -n "$dims" ]] && dims="$dims "
      dims="${dims}Name=$key,Value=$val"
    done
    dimensions_flag="--dimensions $dims"
  fi

  # shellcheck disable=SC2086
  aws cloudwatch put-metric-data $region_flag \
    --namespace "$NAMESPACE" --metric-name "$metric" \
    --value "$value" --unit "None" $dimensions_flag 2>/dev/null || {
    log_error "Failed to push to CloudWatch"
    audit_write "metric-push-fail" "mode=direct backend=cloudwatch metric=$metric"
    exit 3
  }

  log_ok "Pushed $metric=$value to CloudWatch ($NAMESPACE)"
  audit_write "metric-push" "mode=direct backend=cloudwatch metric=$metric value=$value"
}

cloudwatch_check() {
  local region_flag=""
  [[ -n "$REGION" ]] && region_flag="--region $REGION"
  # shellcheck disable=SC2086
  aws cloudwatch list-metrics $region_flag --max-items 1 >/dev/null 2>&1
}

# --- File / Mock Backend ---
# Reads/writes JSON files in a local directory. No network required.
# Used for: CI pipelines, demos, capability audits, pre-handoff validation,
# offline testing, and any environment without a live observability stack.
#
# Directory structure:
#   .signalos/mock-metrics/<metric_name>.json
# File format:
#   {"value": 42, "timestamp": "2026-04-17T10:15:00Z", "labels": {"env": "ci"}}

file_read() {
  local metric="$1"
  require_cmd jq

  local metrics_dir="${FILE_METRICS_DIR:-${REPO_ROOT}/.signalos/mock-metrics}"

  if [[ ! -d "$metrics_dir" ]]; then
    log_warn "File backend: directory $metrics_dir does not exist"
    audit_write "metric-read" "mode=direct backend=file metric=$metric status=no_dir"
    echo "NO_DATA"
    return 0
  fi

  local filepath="${metrics_dir}/${metric}.json"

  if [[ ! -f "$filepath" ]]; then
    log_warn "File backend: no data for $metric at $filepath"
    audit_write "metric-read" "mode=direct backend=file metric=$metric status=no_data"
    echo "NO_DATA"
    return 0
  fi

  # Validate JSON before parsing
  if ! jq empty "$filepath" 2>/dev/null; then
    log_error "File backend: $filepath is not valid JSON"
    audit_write "metric-read" "mode=direct backend=file metric=$metric status=corrupt_json"
    echo "NO_DATA"
    return 0
  fi

  local value ts
  value=$(jq -r '.value // empty' "$filepath" 2>/dev/null || echo "")
  ts=$(jq -r '.timestamp // empty' "$filepath" 2>/dev/null || echo "")

  if [[ -z "$value" ]]; then
    log_warn "File backend: $filepath exists but has no .value field"
    audit_write "metric-read" "mode=direct backend=file metric=$metric status=malformed"
    echo "NO_DATA"
    return 0
  fi

  # Staleness check on file timestamp
  if [[ -n "$ts" ]]; then
    signal_check_staleness "$ts" || true
  fi

  log_ok "Read $metric=$value from file backend ($filepath)"
  audit_write "metric-read" "mode=direct backend=file metric=$metric value=$value"
  echo "$value"
}

file_push() {
  local metric="$1" value="$2" labels="${3:-}"
  require_cmd jq

  local metrics_dir="${FILE_METRICS_DIR:-${REPO_ROOT}/.signalos/mock-metrics}"
  mkdir -p "$metrics_dir"

  local filepath="${metrics_dir}/${metric}.json"
  local ts
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  local labels_json="{}"
  if [[ -n "$labels" ]]; then
    labels_json=$(echo "$labels" | tr ',' '\n' | awk -F= '{printf "\"%s\":\"%s\",", $1, $2}' | sed 's/,$//' | awk '{print "{"$0"}"}')
  fi

  jq -n --arg v "$value" --arg ts "$ts" --argjson l "$labels_json" \
    '{value: ($v | tonumber), timestamp: $ts, labels: $l}' > "$filepath"

  log_ok "Pushed $metric=$value to file backend ($filepath)"
  audit_write "metric-push" "mode=direct backend=file metric=$metric value=$value"
}

file_check() {
  local metrics_dir="${FILE_METRICS_DIR:-${REPO_ROOT}/.signalos/mock-metrics}"
  [[ -d "$metrics_dir" ]]
}

# ============================================================================
# Commands
# ============================================================================

cmd_read() {
  [[ -z "$METRIC_NAME" ]] && { log_error "--metric is required for read"; exit 1; }
  [[ -n "$CONFIG_PATH" ]] && load_config_defaults

  # Default to direct mode for reads (OTLP is push-only)
  [[ -z "$MODE" && -n "$BACKEND" ]] && MODE="direct"
  [[ -z "$MODE" ]] && MODE="direct"

  log_info "Reading $METRIC_NAME (mode=$MODE)"
  signal_metric_read "$METRIC_NAME"
}

cmd_push() {
  [[ -z "$METRIC_NAME" || -z "$METRIC_VALUE" ]] && { log_error "--metric and --value required for push"; exit 1; }
  [[ -n "$CONFIG_PATH" ]] && load_config_defaults

  # Default to OTLP for pushes
  [[ -z "$MODE" ]] && MODE="otlp"

  log_info "Pushing $METRIC_NAME=$METRIC_VALUE (mode=$MODE)"
  signal_metric_push "$METRIC_NAME" "$METRIC_VALUE" "$LABELS" "$PUSH_JOB"
}

cmd_check() {
  [[ -z "$CONFIG_PATH" ]] && { log_error "--config required for check"; exit 1; }
  [[ ! -f "$CONFIG_PATH" ]] && { log_error "Config not found: $CONFIG_PATH"; exit 1; }

  load_config_defaults

  echo -e "${BLUE}Checking observability endpoints from $CONFIG_PATH${NC}"
  echo ""

  local total=0 ok=0 fail=0

  # Check OTLP collector
  local otlp_configured
  otlp_configured=$(parse_config_field "$CONFIG_PATH" "otlp.endpoint")
  if [[ -n "$otlp_configured" || "${MODE:-otlp}" == "otlp" ]]; then
    total=$((total + 1))
    echo -e "  ${BLUE}[standard]${NC} OTLP collector at $OTLP_ENDPOINT"
    if otlp_check; then
      echo -e "    ${GREEN}✓${NC} reachable"
      ok=$((ok + 1))
    else
      echo -e "    ${RED}✗${NC} unreachable"
      fail=$((fail + 1))
    fi
  fi

  # Check direct backends
  for backend in prometheus grafana datadog cloudwatch file; do
    local has_config
    has_config=$(parse_config_field "$CONFIG_PATH" "direct.$backend")

    if [[ -n "$has_config" && "$has_config" != "{}" && "$has_config" != "" ]]; then
      total=$((total + 1))
      BACKEND="$backend"
      load_config_defaults

      local check_result=false
      case "$backend" in
        prometheus)  prometheus_check "${ENDPOINT:-}" && check_result=true ;;
        grafana)     grafana_check "${ENDPOINT:-}" "${AUTH_TOKEN:-}" && check_result=true ;;
        datadog)     datadog_check && check_result=true ;;
        cloudwatch)  cloudwatch_check && check_result=true ;;
        file)        file_check && check_result=true ;;
      esac

      echo -e "  ${BLUE}[compat]${NC} $backend"
      if [[ "$check_result" == "true" ]]; then
        echo -e "    ${GREEN}✓${NC} reachable"
        ok=$((ok + 1))
      else
        echo -e "    ${RED}✗${NC} unreachable or auth failed"
        fail=$((fail + 1))
      fi
    fi
  done

  # List configured metrics
  local metrics_json
  metrics_json=$(parse_config_field "$CONFIG_PATH" "metrics")
  if [[ -n "$metrics_json" && "$metrics_json" != "[]" ]]; then
    echo ""
    local metric_count
    metric_count=$(echo "$metrics_json" | jq 'length' 2>/dev/null || echo 0)
    echo -e "${BLUE}Configured metrics: $metric_count${NC}"
    echo "$metrics_json" | jq -r '.[] | "  - \(.name) [\(.backend // "default")]  query: \(.query // "auto")"' 2>/dev/null
  fi

  echo ""
  echo "Endpoint check: $total tested, $ok reachable, $fail unreachable"
  audit_write "endpoints-check" "total=$total ok=$ok fail=$fail"

  [[ $fail -gt 0 ]] && exit 1
  return 0
}

cmd_poll() {
  [[ -z "$CONFIG_PATH" || -z "$SIGNAL_LOG" ]] && { log_error "--config and --signal-log required"; exit 1; }
  [[ ! -f "$CONFIG_PATH" ]] && { log_error "Config not found: $CONFIG_PATH"; exit 1; }

  load_config_defaults
  log_info "Polling metrics every ${INTERVAL}s → $SIGNAL_LOG"

  if [[ ! -f "$SIGNAL_LOG" ]]; then
    {
      echo "# Signal Window Readings"
      echo ""
      echo "| Timestamp | Metric | Mode | Backend | Value | Threshold | Direction | Status |"
      echo "|---|---|---|---|---|---|---|---|"
    } > "$SIGNAL_LOG"
  fi

  local metrics_json
  metrics_json=$(parse_config_field "$CONFIG_PATH" "metrics")
  [[ -z "$metrics_json" || "$metrics_json" == "[]" ]] && { log_warn "No metrics defined."; exit 0; }

  local default_backend
  default_backend=$(parse_config_field "$CONFIG_PATH" "direct.default_backend")
  default_backend="${default_backend:-prometheus}"

  local ts
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  echo "$metrics_json" | jq -c '.[]' 2>/dev/null | while IFS= read -r metric_def; do
    local name backend query threshold direction
    name=$(echo "$metric_def" | jq -r '.name')
    backend=$(echo "$metric_def" | jq -r ".backend // \"$default_backend\"")
    query=$(echo "$metric_def" | jq -r '.query // ""')
    threshold=$(echo "$metric_def" | jq -r '.threshold // 0')
    direction=$(echo "$metric_def" | jq -r '.direction // "above"')

    BACKEND="$backend"; MODE="direct"; QUERY="$query"
    load_config_defaults

    local value
    value=$(direct_read "$name" 2>/dev/null) || value="ERROR"

    local status="READING"
    if [[ "$value" != "ERROR" && "$value" != "NO_DATA" ]]; then
      if [[ "$direction" == "above" ]]; then
        (( $(echo "$value >= $threshold" | bc -l 2>/dev/null || echo 0) )) && status="ABOVE_THRESHOLD" || status="BELOW_THRESHOLD"
      else
        (( $(echo "$value <= $threshold" | bc -l 2>/dev/null || echo 0) )) && status="BELOW_THRESHOLD" || status="ABOVE_THRESHOLD"
      fi
    elif [[ "$value" == "NO_DATA" ]]; then
      status="NO_DATA"
    fi

    echo "| $ts | $name | direct | $backend | $value | $threshold | $direction | $status |" >> "$SIGNAL_LOG"
    audit_write "metric-poll" "metric=$name backend=$backend value=$value status=$status"
  done

  log_ok "Recorded readings to $SIGNAL_LOG"
}

cmd_list_backends() {
  echo -e "${BLUE}SignalOS Observability Adapter — Backends${NC}"
  echo ""
  echo -e "  ${GREEN}[standard]${NC}  otlp"
  echo "              OTLP/HTTP export to any OpenTelemetry collector"
  echo "              The collector routes to your backend (canonical architecture)"
  echo "              Auth: OTEL_EXPORTER_OTLP_HEADERS env var"
  echo "              Requires: curl, jq"
  echo ""
  echo -e "  ${BLUE}[compat]${NC}    prometheus"
  echo "              PromQL query + PushGateway push"
  echo "              Auth: Bearer token (optional)"
  echo "              Requires: curl, jq"
  echo ""
  echo -e "  ${BLUE}[compat]${NC}    grafana"
  echo "              Datasource proxy query API (read-only)"
  echo "              Auth: API key (required)"
  echo "              Requires: curl, jq"
  echo ""
  echo -e "  ${BLUE}[compat]${NC}    datadog"
  echo "              Metrics query + Series submit API"
  echo "              Auth: DD_API_KEY + DD_APP_KEY env vars"
  echo "              Requires: curl, jq"
  echo ""
  echo -e "  ${BLUE}[compat]${NC}    cloudwatch"
  echo "              AWS CloudWatch get/put metric data"
  echo "              Auth: AWS credentials (CLI config or env)"
  echo "              Requires: aws CLI, jq"
  echo ""
  echo -e "  ${BLUE}[compat]${NC}    file"
  echo "              Local JSON file read/write (no network required)"
  echo "              For: CI, demos, audits, pre-handoff validation, offline testing"
  echo "              Path: .signalos/mock-metrics/<metric>.json"
  echo "              Requires: jq"
  echo ""

  echo -e "${BLUE}Tool availability:${NC}"
  for cmd in curl jq aws python3 bc; do
    if command -v "$cmd" &>/dev/null; then
      echo -e "  ${GREEN}✓${NC} $cmd"
    else
      echo -e "  ${RED}✗${NC} $cmd  (not installed)"
    fi
  done
}

# ============================================================================
# Main
# ============================================================================

resolve_paths() {
  # Must run AFTER parse_args so --repo-root is honoured.
  AUDIT_LOG="${REPO_ROOT}/.signalos/AUDIT_TRAIL.jsonl"
}

main() {
  parse_args "$@"
  resolve_paths

  case "$COMMAND" in
    read)           cmd_read ;;
    push)           cmd_push ;;
    check)          cmd_check ;;
    poll)           cmd_poll ;;
    list-backends)  cmd_list_backends ;;
    "")             usage 0 ;;
    *)              log_error "Unknown command: $COMMAND"; usage 1 ;;
  esac
}

main "$@"
