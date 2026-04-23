#!/bin/bash
# Redirect port 80 → 8080 so design-tools-live can run unprivileged.
# Run once as root after setting up the service.
set -e

iptables -t nat -A PREROUTING  -p tcp --dport 80 -j REDIRECT --to-port 8082
iptables -t nat -A OUTPUT      -p tcp --dport 80 -j REDIRECT --to-port 8082

# Persist across reboots via a systemd one-shot service
cat > /etc/systemd/system/iptables-port80.service << 'EOF'
[Unit]
Description=Redirect port 80 to 8082
After=network.target

[Service]
Type=oneshot
ExecStart=/sbin/iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 8082
ExecStart=/sbin/iptables -t nat -A OUTPUT -p tcp --dport 80 -j REDIRECT --to-port 8082
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable iptables-port80

echo "Port 80 → 8082 redirect active and will persist across reboots."
