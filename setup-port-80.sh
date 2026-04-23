#!/bin/bash
# Redirect port 80 → 8080 so design-tools-live can run unprivileged.
# Run once as root after setting up the service.
set -e

iptables -t nat -A PREROUTING  -p tcp --dport 80 -j REDIRECT --to-port 8080
iptables -t nat -A OUTPUT      -p tcp --dport 80 -j REDIRECT --to-port 8080

# Persist across reboots (requires iptables-persistent)
#   sudo apt install iptables-persistent
netfilter-persistent save

echo "Port 80 → 8080 redirect active and saved."
