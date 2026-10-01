#!/usr/bin/env bash
# ADMIEZO Remote Host Setup Script for Linux / macOS
set -e

echo "=== ADMIEZO Remote Server Setup ==="

TARGET_IP="$1"

# 1. Detect IP if not provided
if [ -z "$TARGET_IP" ]; then
    if command -v ip >/dev/null 2>&1; then
        TARGET_IP=$(ip route get 1.1.1.1 | awk '{print $7; exit}')
    elif command -v ifconfig >/dev/null 2>&1; then
        TARGET_IP=$(ifconfig | grep 'inet ' | grep -v '127.0.0.1' | awk '{print $2}' | head -n 1)
    fi
fi

if [ -z "$TARGET_IP" ]; then
    echo "Error: Could not auto-detect IP. Please run: ./setup_remote.sh <Your-IP>"
    exit 1
fi

echo "Detected Target IP: $TARGET_IP"

# 2. Update .env
cat <<EOF > .env
DJANGO_ALLOWED_HOSTS=*
PLATFORM_HOSTS=localhost,127.0.0.1,platform.localhost,$TARGET_IP,$TARGET_IP.sslip.io,platform.$TARGET_IP.sslip.io,$TARGET_IP.nip.io,platform.$TARGET_IP.nip.io
CSRF_TRUSTED_ORIGINS=http://localhost:3000,http://*.localhost:3000,http://127.0.0.1:3000,http://${TARGET_IP}:3000,http://*.${TARGET_IP}.sslip.io:3000,http://${TARGET_IP}.sslip.io:3000,http://*.${TARGET_IP}.nip.io:3000,http://${TARGET_IP}.nip.io:3000,https://localhost:7383,https://*.localhost:7383,https://127.0.0.1:7383,https://${TARGET_IP}:7383,https://*.${TARGET_IP}.sslip.io:7383,https://${TARGET_IP}.sslip.io:7383,https://*.${TARGET_IP}.nip.io:7383,https://${TARGET_IP}.nip.io:7383,https://*.ngrok-free.app
TENANT_BASE_DOMAIN=${TARGET_IP}.sslip.io
EOF

echo "Configured .env for domain $TARGET_IP.sslip.io"

# 3. Create certs directory
mkdir -p certs

# 4. Generate SSL Certificates
echo "Generating SSL certificates for $TARGET_IP..."
SAN="subjectAltName=IP:$TARGET_IP,IP:127.0.0.1,DNS:localhost,DNS:$TARGET_IP.sslip.io,DNS:*.$TARGET_IP.sslip.io,DNS:$TARGET_IP.nip.io,DNS:*.$TARGET_IP.nip.io"
docker compose run --rm backend sh -c "openssl req -x509 -newkey rsa:2048 -nodes -keyout /tmp/k.pem -out /tmp/c.pem -days 825 -subj '/CN=$TARGET_IP' -addext '$SAN' && cat /tmp/k.pem > /app/k.pem && cat /tmp/c.pem > /app/c.pem"
docker compose cp backend:/app/k.pem ./certs/key.pem
docker compose cp backend:/app/c.pem ./certs/cert.pem
docker compose exec -T backend rm -f /app/k.pem /app/c.pem /tmp/k.pem /tmp/c.pem || true

# 5. Start Docker containers
echo "Starting Docker containers..."
docker compose up --build -d

# 6. Register domain
echo "Registering tenant subdomain in database..."
sleep 5
docker compose exec -T backend python manage.py shell -c "from apps.tenancy.models import TenantAccount, TenantDomain; acc = TenantAccount.objects.filter(slug='northbridge').first(); (TenantDomain.objects.get_or_create(tenant_account=acc, hostname='northbridge.$TARGET_IP.sslip.io', defaults={'kind': 'managed', 'status': 'active', 'is_primary': True}) if acc else None)"

echo ""
echo "=== ADMIEZO IS READY! ==="
echo "Platform Super Admin (HTTPS): https://${TARGET_IP}:7383"
echo "University Subdomain (HTTPS): https://northbridge.${TARGET_IP}.sslip.io:7383"
echo "University Subdomain (HTTP):  http://northbridge.${TARGET_IP}.sslip.io:3000"
echo "CI/CD Webhook Listener:       http://${TARGET_IP}:5000/webhook"
echo ""
echo "Seed Logins:"
echo "  Administrator: admin@admiezo.local / ChangeMe123!"
echo "  Super Admin:   platform@admiezo.local / ChangeMe123!"
