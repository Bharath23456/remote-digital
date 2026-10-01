# Self-elevate to Administrator if not already running as Admin
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ">> ENABLING WI-FI / LAN ACCESS FOR ADMIEZO" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Add inbound firewall rule for project ports
netsh advfirewall firewall delete rule name="ADMIEZO Project Ports" 2>$null | Out-Null
netsh advfirewall firewall add rule name="ADMIEZO Project Ports" dir=in action=allow protocol=TCP localport=3000,5000,7383 | Out-Null
Write-Host "[OK] Windows Firewall rules added for ports 3000, 5000, and 7383." -ForegroundColor Green

# 2. Display access URLs
$wifiIP = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { 
    $_.InterfaceAlias -match "Wi-Fi|Wireless|Ethernet" -and $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" 
} | Select-Object -First 1).IPAddress

Write-Host "`n[READY] Connected Wi-Fi devices can now access:" -ForegroundColor Yellow
Write-Host "  - Web App (HTTP):       http://$($wifiIP):3000" -ForegroundColor White
Write-Host "  - Web App (HTTPS):      https://$($wifiIP):7383" -ForegroundColor White
Write-Host "  - University Subdomain: http://northbridge.$($wifiIP).sslip.io:3000" -ForegroundColor White
Write-Host "  - Webhook Listener:     http://$($wifiIP):5000/webhook" -ForegroundColor White

Write-Host "`nPress any key to close..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
