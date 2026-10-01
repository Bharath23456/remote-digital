@echo off
echo ========================================================
echo Starting Cloudflare Tunnel for GitHub Webhook...
echo ========================================================
.\cloudflared.exe tunnel --url http://127.0.0.1:5000
pause
