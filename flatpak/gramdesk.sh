#!/bin/sh
export PYTHONPATH=/app/lib/gramdesk${PYTHONPATH:+:$PYTHONPATH}
exec python3 -m gramdesk "$@"
