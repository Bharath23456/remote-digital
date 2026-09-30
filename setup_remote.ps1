# ADMIEZO Remote Host Setup Script for Windows
param(
    [string]$TargetIP = ""
)

Write-Host "=== ADMIEZO Remote Server Setup ===" -ForegroundColor Cyan

# 1. Detect IP if not provided
if (-not $TargetIP) {
    $TargetIP = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { 
        $_.InterfaceAlias -notmatch "vEthernet|Loopback|WSL|Virtual|Docker" -and $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" 
    } | Select-Object -First 1).IPAddress
}

if (-not $TargetIP) {
    Write-Host "Error: Could not auto-detect IPv4 address. Please run with: .\setup_remote.ps1 <Your-IP>" -ForegroundColor Red
    exit 1
}

Write-Host "Detected Target IP: $TargetIP" -ForegroundColor Green

# 2. Update or create .env
$envFile = ".env"
$envContent = @"
DJANGO_ALLOWED_HOSTS=*
PLATFORM_HOSTS=localhost,127.0.0.1,platform.localhost,$TargetIP,$TargetIP.sslip.io,platform.$TargetIP.sslip.io,$TargetIP.nip.io,platform.$TargetIP.nip.io
CSRF_TRUSTED_ORIGINS=http://localhost:3000,http://*.localhost:3000,http://127.0.0.1:3000,http://${TargetIP}:3000,http://*.${TargetIP}.sslip.io:3000,http://${TargetIP}.sslip.io:3000,http://*.${TargetIP}.nip.io:3000,http://${TargetIP}.nip.io:3000,https://localhost:7383,https://*.localhost:7383,https://127.0.0.1:7383,https://${TargetIP}:7383,https://*.${TargetIP}.sslip.io:7383,https://${TargetIP}.sslip.io:7383,https://*.${TargetIP}.nip.io:7383,https://${TargetIP}.nip.io:7383,https://*.ngrok-free.app
TENANT_BASE_DOMAIN=${TargetIP}.sslip.io
"@

Set-Content -Path $envFile -Value $envContent
Write-Host "Configured .env for domain $TargetIP.sslip.io" -ForegroundColor Green

# 3. Create certs directory
if (-not (Test-Path "certs")) {
    New-Item -ItemType Directory -Path "certs" | Out-Null
}

# 4. Generate SSL Certificates with SANs
Write-Host "Generating SSL certificates for $TargetIP..." -ForegroundColor Yellow
$san = "subjectAltName=IP:$TargetIP,IP:127.0.0.1,DNS:localhost,DNS:$TargetIP.sslip.io,DNS:*.$TargetIP.sslip.io,DNS:$TargetIP.nip.io,DNS:*.$TargetIP.nip.io"
docker compose run --rm backend sh -c "openssl req -x509 -newkey rsa:2048 -nodes -keyout /tmp/k.pem -out /tmp/c.pem -days 825 -subj '/CN=$TargetIP' -addext '$san'; cat /tmp/k.pem > /app/k.pem; cat /tmp/c.pem > /app/c.pem"
docker compose cp backend:/app/k.pem ./certs/key.pem
docker compose cp backend:/app/c.pem ./certs/cert.pem
docker compose exec -T backend rm -f /app/k.pem /app/c.pem /tmp/k.pem /tmp/c.pem

# 5. Start Docker services
Write-Host "Starting Docker containers..." -ForegroundColor Yellow
docker compose up --build -d

# 6. Register tenant domain in database
Write-Host "Registering tenant subdomain in database..." -ForegroundColor Yellow
Start-Sleep -Seconds 5
docker compose exec -T backend python manage.py shell -c "from apps.tenancy.models import TenantAccount, TenantDomain; acc = TenantAccount.objects.filter(slug='northbridge').first(); (TenantDomain.objects.get_or_create(tenant_account=acc, hostname='northbridge.$TargetIP.sslip.io', defaults={'kind': 'managed', 'status': 'active', 'is_primary': True}) if acc else None)"

# 7. Add Windows Firewall rule for ports 3000 and 7383
Write-Host "To allow incoming LAN connections, run PowerShell as Administrator:" -ForegroundColor Magenta
Write-Host "  netsh advfirewall firewall add rule name='ADMIEZO Ports' dir=in action=allow protocol=TCP localport=3000,7383" -ForegroundColor Cyan

Write-Host "`n=== ADMIEZO IS READY! ===" -ForegroundColor Green
Write-Host "Platform Super Admin (HTTPS): https://${TargetIP}:7383" -ForegroundColor White
Write-Host "University Subdomain (HTTPS): https://northbridge.${TargetIP}.sslip.io:7383" -ForegroundColor White
Write-Host "University Subdomain (HTTP):  http://northbridge.${TargetIP}.sslip.io:3000" -ForegroundColor White
Write-Host "`nSeed Logins:" -ForegroundColor Gray
Write-Host "  Administrator: admin@admiezo.local / ChangeMe123!" -ForegroundColor Gray
Write-Host "  Super Admin:   platform@admiezo.local / ChangeMe123!" -ForegroundColor Gray
