#!/bin/sh
[ -f /tmp/tunnel-disabled ] && exit 0
exec curl -fsS -o /dev/null http://127.0.0.1:2000/ready
