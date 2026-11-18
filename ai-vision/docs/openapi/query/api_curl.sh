#!/usr/bin/env bash

# Run all AI Vision OpenAPI curl examples against AI Vision OpenAPI.
# Default BASE_URL uses localhost; override BASE_URL for another deployment
# before invoking this script, for example:
#   BASE_URL=http://localhost:5174 ./docs/openapi/query/api_curl.sh

set -uo pipefail

BASE_URL="${BASE_URL:-http://localhost:5174}"
OPENAPI_PREFIX="${OPENAPI_PREFIX:-/openapi/v1}"
API_KEY="${API_KEY:-sk-aiv-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx}"
SPACE_ID="${SPACE_ID:-961005a6-6cf4-41e2-ab6b-58006b2e378b}"
AGENT_PRODUCT="${AGENT_PRODUCT:-ClaudeCode}"
TRACE_ID="${TRACE_ID:-6f3c089b27fc65f0fe0743fedf5623b2}"
SPAN_ID="${SPAN_ID:-e78938ad5d401b8a}"
SESSION_ID="${SESSION_ID:-fb5ba3c2-d7ad-46d8-a479-a0d1038acf82}"
IO_SESSION_ID="${IO_SESSION_ID:-${SESSION_ID}}"
FROM_TIME="${FROM_TIME:-2026-06-29T00:00:00+08:00}"
TO_TIME="${TO_TIME:-2026-06-30T23:59:59+08:00}"
ENV_VALUE="${ENV_VALUE:-Default}"
RESULT_DIR="${RESULT_DIR:-api_curl_results/$(date +%Y%m%d_%H%M%S)}"

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required but not found" >&2
  exit 127
fi

mkdir -p "${RESULT_DIR}"
SUMMARY_FILE="${RESULT_DIR}/summary.tsv"
printf 'name\tmethod\turl\tstatus\tresponse_file\tcommand_file\terror_file\n' > "${SUMMARY_FILE}"

AUTH_HEADER="Authorization: Bearer ${API_KEY}"
BASE_OPENAPI_URL="${BASE_URL}${OPENAPI_PREFIX}/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}"

shell_quote() {
  printf '%q' "$1"
}

print_config() {
  cat <<EOF
Running AI Vision OpenAPI requests
  BASE_URL=${BASE_URL}
  OPENAPI_PREFIX=${OPENAPI_PREFIX}
  SPACE_ID=${SPACE_ID}
  AGENT_PRODUCT=${AGENT_PRODUCT}
  TRACE_ID=${TRACE_ID}
  SPAN_ID=${SPAN_ID}
  SESSION_ID=${SESSION_ID}
  IO_SESSION_ID=${IO_SESSION_ID}
  FROM_TIME=${FROM_TIME}
  TO_TIME=${TO_TIME}
  ENV_VALUE=${ENV_VALUE}
  RESULT_DIR=${RESULT_DIR}
EOF
}

effective_io_session_id() {
  if [[ -n "${IO_SESSION_ID}" ]]; then
    printf '%s' "${IO_SESSION_ID}"
    return
  fi
  printf '%s' "${SESSION_ID}"
}

write_result() {
  local name="$1"
  local method="$2"
  local url="$3"
  local status="$4"
  local outfile="$5"
  local command_file="$6"
  local errfile="$7"

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${name}" "${method}" "${url}" "${status}" "${outfile}" "${command_file}" "${errfile}" \
    >> "${SUMMARY_FILE}"
  printf '[%s] %s %s -> %s\n' "${name}" "${method}" "${url}" "${status}"
}

finalize_err_file() {
  local errfile="$1"
  if [[ -s "${errfile}" ]]; then
    printf '%s' "${errfile}"
    return
  fi

  rm -f "${errfile}"
  printf ''
}

write_post_command() {
  local command_file="$1"
  local url="$2"
  local outfile="$3"
  local errfile="$4"
  local body="$5"

  {
    echo '#!/usr/bin/env bash'
    echo 'set -uo pipefail'
    echo
    printf '%s \\\n' \
      "status=\$(curl -sS -o $(shell_quote "${outfile}") -w '%{http_code}' -X POST $(shell_quote "${url}")"
    printf '%s \\\n' "  -H $(shell_quote "${AUTH_HEADER}")"
    printf '%s \\\n' "  -H $(shell_quote 'Content-Type: application/json')"
    printf '%s \\\n' "  --data @-"
    printf "%s <<'JSON'\n" "  2>$(shell_quote "${errfile}")"
    printf '%s\n' "${body}"
    echo 'JSON'
    echo ')'
    echo 'curl_exit=$?'
    echo 'if [[ ${curl_exit} -ne 0 ]]; then status="curl_error_${curl_exit}"; fi'
    printf 'if [[ ! -s %s ]]; then rm -f %s; fi\n' \
      "$(shell_quote "${errfile}")" \
      "$(shell_quote "${errfile}")"
    echo 'printf "%s\n" "${status}"'
  } > "${command_file}"
  chmod +x "${command_file}"
}

write_get_command() {
  local command_file="$1"
  local url="$2"
  local outfile="$3"
  local errfile="$4"
  shift 4

  {
    echo '#!/usr/bin/env bash'
    echo 'set -uo pipefail'
    echo
    printf '%s \\\n' \
      "status=\$(curl -sS -G -o $(shell_quote "${outfile}") -w '%{http_code}' $(shell_quote "${url}")"
    printf '%s \\\n' "  -H $(shell_quote "${AUTH_HEADER}")"
    local param
    for param in "$@"; do
      printf '%s \\\n' "  --data-urlencode $(shell_quote "${param}")"
    done
    printf '%s)\n' "  2>$(shell_quote "${errfile}")"
    echo 'curl_exit=$?'
    echo 'if [[ ${curl_exit} -ne 0 ]]; then status="curl_error_${curl_exit}"; fi'
    printf 'if [[ ! -s %s ]]; then rm -f %s; fi\n' \
      "$(shell_quote "${errfile}")" \
      "$(shell_quote "${errfile}")"
    echo 'printf "%s\n" "${status}"'
  } > "${command_file}"
  chmod +x "${command_file}"
}

run_post_json() {
  local name="$1"
  local url="$2"
  local body="$3"
  local outfile="${RESULT_DIR}/${name}.json"
  local errfile="${RESULT_DIR}/${name}.err"
  local command_file="${RESULT_DIR}/${name}.sh"
  local status
  local recorded_errfile

  write_post_command "${command_file}" "${url}" "${outfile}" "${errfile}" "${body}"
  status="$("${command_file}")"
  recorded_errfile="$(finalize_err_file "${errfile}")"

  write_result \
    "${name}" "POST" "${url}" "${status}" "${outfile}" "${command_file}" "${recorded_errfile}"
}

run_get() {
  local name="$1"
  local url="$2"
  shift 2
  local outfile="${RESULT_DIR}/${name}.json"
  local errfile="${RESULT_DIR}/${name}.err"
  local command_file="${RESULT_DIR}/${name}.sh"
  local status
  local recorded_errfile

  write_get_command "${command_file}" "${url}" "${outfile}" "${errfile}" "$@"
  status="$("${command_file}")"
  recorded_errfile="$(finalize_err_file "${errfile}")"

  write_result \
    "${name}" "GET" "${url}" "${status}" "${outfile}" "${command_file}" "${recorded_errfile}"
}

trace_list_body() {
  cat <<JSON
{
  "page": 1,
  "limit": 20,
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}",
  "orderBy": "start_time_unix_nano desc",
  "filters": [
    {
      "field": "resource.attributes.ant.agent.env",
      "op": "=",
      "value": "${ENV_VALUE}"
    }
  ],
  "keyword": "",
  "entry_only": false
}
JSON
}

span_batch_body() {
  cat <<JSON
{
  "spanIds": [
    "${SPAN_ID}"
  ],
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}"
}
JSON
}

session_list_body() {
  cat <<JSON
{
  "page": 1,
  "limit": 20,
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}",
  "orderBy": "start_time desc",
  "filters": [
    {
      "field": "resource.attributes.ant.agent.env",
      "op": "=",
      "value": "${ENV_VALUE}"
    }
  ],
  "keyword": ""
}
JSON
}

session_ids_body() {
  local session_id
  session_id="$(effective_io_session_id)"
  cat <<JSON
{
  "sessionIds": [
    "${session_id}"
  ],
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}"
}
JSON
}

session_io_body() {
  cat <<JSON
{
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}"
}
JSON
}

event_list_body() {
  cat <<JSON
{
  "mode": "fieldsJson",
  "page": 1,
  "limit": 50,
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}",
  "fields": ["time", "event_name", "trace_id", "session_id", "username"],
  "filters": {
    "env": "${ENV_VALUE}"
  },
  "orderBy": "-time"
}
JSON
}

event_facets_body() {
  cat <<JSON
{
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}",
  "dimensions": ["event_name", "env", "username"],
  "filters": {
    "env": "${ENV_VALUE}"
  },
  "limit": 20
}
JSON
}

event_raw_sql_body() {
  cat <<JSON
{
  "from": "${FROM_TIME}",
  "to": "${TO_TIME}",
  "rawSql": "SELECT `period` AS time, count(*) AS value FROM {{table}} WHERE {{time_filter}} AND {{scope_filter}} AND {{agent_product_filter}} GROUP BY `period` ORDER BY `period` ASC LIMIT 1000",
  "format": "table",
  "refId": "A"
}
JSON
}

main() {
  print_config

  run_post_json "01_trace_list" \
    "${BASE_OPENAPI_URL}/traces/list" \
    "$(trace_list_body)"

  run_post_json "02_trace_list_io_usage" \
    "${BASE_OPENAPI_URL}/traces/list?io=true&usage=true" \
    "$(trace_list_body)"

  run_get "03_trace_detail" \
    "${BASE_OPENAPI_URL}/trace/${TRACE_ID}/detail" \
    "from=${FROM_TIME}" \
    "to=${TO_TIME}"

  run_get "04_trace_detail_io" \
    "${BASE_OPENAPI_URL}/trace/${TRACE_ID}/detail" \
    "io=true" \
    "from=${FROM_TIME}" \
    "to=${TO_TIME}"

  run_post_json "05_trace_spans_batch" \
    "${BASE_OPENAPI_URL}/trace/${TRACE_ID}/spans" \
    "$(span_batch_body)"

  run_get "06_span_detail" \
    "${BASE_OPENAPI_URL}/trace/${TRACE_ID}/span/${SPAN_ID}" \
    "from=${FROM_TIME}" \
    "to=${TO_TIME}"

  run_post_json "07_session_list" \
    "${BASE_OPENAPI_URL}/sessions/list" \
    "$(session_list_body)"

  run_post_json "08_session_list_io_usage" \
    "${BASE_OPENAPI_URL}/sessions/list?io=true&usage=true" \
    "$(session_list_body)"


  run_post_json "09_sessions_io_batch" \
    "${BASE_OPENAPI_URL}/sessions/io" \
    "$(session_ids_body)"

  run_post_json "10_sessions_usage_batch" \
    "${BASE_OPENAPI_URL}/sessions/usage" \
    "$(session_ids_body)"

  run_get "11_session_detail" \
    "${BASE_OPENAPI_URL}/session/${SESSION_ID}/detail" \
    "from=${FROM_TIME}" \
    "to=${TO_TIME}" \
    "include_spans=false"

  run_get "12_session_detail_io" \
    "${BASE_OPENAPI_URL}/session/${SESSION_ID}/detail" \
    "io=true" \
    "include_spans=true" \
    "from=${FROM_TIME}" \
    "to=${TO_TIME}"

  run_post_json "13_session_io" \
    "${BASE_OPENAPI_URL}/session/$(effective_io_session_id)/io" \
    "$(session_io_body)"

  run_get "14_metadata_events" \
    "${BASE_OPENAPI_URL}/metadata/events"

  run_get "15_metadata_traces" \
    "${BASE_OPENAPI_URL}/metadata/traces"

  run_post_json "16_events_list" \
    "${BASE_OPENAPI_URL}/events/list" \
    "$(event_list_body)"

  run_post_json "17_events_query_fields_json" \
    "${BASE_OPENAPI_URL}/events/query" \
    "$(event_list_body)"

  run_post_json "18_events_facets" \
    "${BASE_OPENAPI_URL}/events/facets" \
    "$(event_facets_body)"

  if [ "${RUN_RAWSQL_EXAMPLES:-false}" = "true" ]; then
    run_post_json "19_grafana_query_rawsql" \
      "${BASE_URL}${OPENAPI_PREFIX}/grafana/spaces/${SPACE_ID}/agents/${AGENT_PRODUCT}/query" \
      "$(event_raw_sql_body)"
  else
    echo "[skip] 19_grafana_query_rawsql (set RUN_RAWSQL_EXAMPLES=true; rawSql uses the same Space API Key auth)"
  fi

  echo
  echo "Done. Summary: ${SUMMARY_FILE}"
  echo "Responses saved in: ${RESULT_DIR}"
}

main "$@"
