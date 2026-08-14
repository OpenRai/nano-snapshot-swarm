#!/usr/bin/env bash
# Shared runtime configuration validation for producer shell entrypoints.

parse_boolean_env() {
    local name="$1"
    local default_value="$2"
    local value="${!name:-}"
    local normalized

    if [[ -z "$value" ]]; then
        printf '%s\n' "$default_value"
        return 0
    fi

    normalized=$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')
    case "$normalized" in
        true|false)
            printf '%s\n' "$normalized"
            ;;
        *)
            printf 'ERROR: %s must be true or false (case-insensitive); got %s\n' \
                "$name" "$value" >&2
            return 2
            ;;
    esac
}
