#!/usr/bin/env bash
# Authenticated loopback JSON-RPC helpers for the producer's aria2c process.

call_aria2_rpc() {
    curl -sSf \
        --connect-timeout 1 \
        --max-time 2 \
        -H "Content-Type: application/json" \
        --data-binary "$1" \
        "$ARIA2_RPC_URL" 2>/dev/null
}

query_aria2_status() {
    local response
    local parsed
    local -a fields

    if ! response=$(call_aria2_rpc "$ARIA2_STATUS_REQUEST"); then
        return 1
    fi
    if ! parsed=$(jq -er '
        if .error then
            error(.error.message)
        else
            .result
            | [
                .status,
                .totalLength,
                .completedLength,
                .downloadSpeed,
                (.errorCode // ""),
                ((.errorMessage // "") | gsub("[\\r\\n\\t]"; " "))
            ]
            | .[]
        end
    ' <<<"$response" 2>/dev/null); then
        return 1
    fi

    readarray -t fields <<<"$parsed"
    if [ "${#fields[@]}" -lt 4 ] || \
       ! [[ "${fields[1]}" =~ ^[0-9]+$ ]] || \
       ! [[ "${fields[2]}" =~ ^[0-9]+$ ]] || \
       ! [[ "${fields[3]}" =~ ^[0-9]+$ ]]; then
        return 1
    fi
    ARIA2_STATUS="${fields[0]}"
    ARIA2_TOTAL_LENGTH="${fields[1]}"
    ARIA2_COMPLETED_LENGTH="${fields[2]}"
    ARIA2_DOWNLOAD_SPEED="${fields[3]}"
    ARIA2_ERROR_CODE="${fields[4]:-}"
    ARIA2_ERROR_MESSAGE="${fields[5]:-}"
}

shutdown_aria2() {
    local response

    for _ in 1 2 3; do
        if response=$(call_aria2_rpc "$ARIA2_SHUTDOWN_REQUEST") && \
           jq -e '.result == "OK"' >/dev/null <<<"$response"; then
            return 0
        fi
        sleep 1
    done
    return 1
}
