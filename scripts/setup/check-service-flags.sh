#!/bin/sh
# Check that every flag a server unit passes is one that server's own `--help` lists, and that the server is built.
#
# A flag the installed build does not know makes the service crash-loop at start (found on the Pi: `--mlock` was renamed in this llama.cpp), and
# only the Pi's own build can say. install-services.sh runs this first, so a bad flag stops the install instead of breaking a service.
#
#   scripts/setup/check-service-flags.sh                 the three server units in scripts/setup/systemd/
#   scripts/setup/check-service-flags.sh FILE.service ...   these unit files
set -eu

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if [ "$#" -eq 0 ]; then
    set -- "$ROOT/scripts/setup/systemd/pom-whisper.service" "$ROOT/scripts/setup/systemd/pom-scorer.service" "$ROOT/scripts/setup/systemd/pom-oracle.service"
fi

problems=0
for unit_file in "$@"; do
    name="$(basename "$unit_file")"
    exec_line="$(sed -n 's/^ExecStart=//p' "$unit_file")"
    set -- $exec_line                          # the program, then its words (the units hold no quotes)
    binary="$1"
    shift
    if [ ! -x "$binary" ]; then
        echo "$name: $binary is not built (see scripts/setup/setup-whisper.sh and setup-llama.sh)" >&2
        problems=$((problems + 1))
        continue
    fi
    help_text="$("$binary" --help 2>&1 || true)"
    for word in "$@"; do
        case "$word" in
            -[A-Za-z]*|--*)
                if ! printf '%s\n' "$help_text" | grep -Eq -- "(^|[[:space:],])$word([[:space:],=]|\$)"; then
                    echo "$name: $binary does not list the flag $word in --help" >&2
                    problems=$((problems + 1))
                fi
                ;;
        esac
    done
done
[ "$problems" -eq 0 ] && echo "service flags ok"
[ "$problems" -eq 0 ]
