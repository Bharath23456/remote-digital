import os
import sys
import subprocess
import shutil
import threading
from flask import Flask, request, jsonify

app = Flask(__name__)

# Configuration
REPO_DIR = os.environ.get("REPO_DIR", os.path.dirname(os.path.abspath(__file__)))
DEPLOY_BRANCH = os.environ.get("DEPLOY_BRANCH", "main")
COMPOSE_PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "remote-digital")
HOST_PORT = int(os.environ.get("WEBHOOK_PORT", 5000))
RUN_TESTS = os.environ.get("RUN_TESTS", "true").lower() in ("true", "1", "yes")

# Application services to update (excludes webhook so it won't interrupt its own execution)
APP_SERVICES = [
    "backend", "frontend", "db", "identity-service", "storage-gateway",
    "https-proxy", "outbox-worker", "integrity-worker",
    "ai-evaluation-worker", "secure-session-worker", "photocopy-expiry-worker"
]

is_deploying = False
deploy_lock = threading.Lock()

def execute_pipeline():
    global is_deploying
    with deploy_lock:
        if is_deploying:
            print("[WEBHOOK] Another deployment is already in progress. Skipping.")
            return
        is_deploying = True

    try:
        print("Push detected! Starting Python CI/CD and Deployment pipeline in background...")

        # Step 1: Pull the latest code
        print(f"Pulling latest code from branch '{DEPLOY_BRANCH}' in {REPO_DIR}...")
        pull = subprocess.run(
            ["git", "pull", "origin", DEPLOY_BRANCH],
            cwd=REPO_DIR,
            capture_output=True,
            text=True
        )
        if pull.returncode != 0:
            print(f"[ERROR] Git pull failed: {pull.stderr}")
            return

        # Step 2: Run Tests & Verification
        if RUN_TESTS:
            print("Running tests and system integrity checks...")
            test_cmd = [
                "docker", "compose", "-p", COMPOSE_PROJECT,
                "exec", "-T", "backend", "python", "manage.py", "check"
            ]
            tests = subprocess.run(test_cmd, cwd=REPO_DIR, capture_output=True, text=True)
            if tests.returncode != 0:
                print(f"[ERROR] Tests failed! Aborting build and deployment: {tests.stderr}")
                return
            print("Tests and system checks passed!")

        # Step 3: Build Docker Images
        print("Building Docker images for the stack...")
        build = subprocess.run(
            ["docker", "compose", "-p", COMPOSE_PROJECT, "build"],
            cwd=REPO_DIR,
            capture_output=True,
            text=True
        )
        if build.returncode != 0:
            print(f"[ERROR] Docker build failed: {build.stderr}")
            return

        # Step 4: Deploy to Live Server
        print("Deploying updated services to live server...")
        live_script = os.path.join(REPO_DIR, "scripts", "deploy_live.ps1")
        if sys.platform == "win32" and os.path.exists(live_script) and shutil.which("powershell"):
            deploy = subprocess.run(
                ["powershell", "-ExecutionPolicy", "Bypass", "-File", live_script, "-DeployPath", REPO_DIR],
                cwd=REPO_DIR,
                capture_output=True,
                text=True
            )
        else:
            # Multi-container deployment for application services
            deploy = subprocess.run(
                ["docker", "compose", "-p", COMPOSE_PROJECT, "up", "-d"] + APP_SERVICES,
                cwd=REPO_DIR,
                capture_output=True,
                text=True
            )

        if deploy.returncode != 0:
            print(f"[ERROR] Deployment failed: {deploy.stderr}")
            return

        print("Pipeline finished! New code is running on the live server.")
    finally:
        is_deploying = False

@app.route("/", methods=["GET", "POST"])
@app.route("/health", methods=["GET"])
@app.route("/webhook", methods=["GET", "POST"])
def github_webhook():
    # If a GET request is sent (e.g. from a browser or ping probe), return status 200 OK
    if request.method == "GET":
        return jsonify({
            "status": "ready",
            "service": "ADMIEZO CI/CD Webhook Listener",
            "message": "Webhook endpoint is active and listening. Push to GitHub to trigger deployment.",
            "repo_dir": REPO_DIR,
            "branch": DEPLOY_BRANCH
        }), 200

    # Handle POST request: Launch the pipeline asynchronously in background thread
    thread = threading.Thread(target=execute_pipeline, daemon=True)
    thread.start()

    # Immediately respond to GitHub with 200 OK so GitHub/Cloudflare never times out!
    return jsonify({
        "status": "success",
        "message": "Deployment pipeline triggered successfully! Processing in background."
    }), 200

if __name__ == "__main__":
    print(f"🚀 Starting ADMIEZO Webhook Deployment Server on port {HOST_PORT}...")
    app.run(host="0.0.0.0", port=HOST_PORT)
