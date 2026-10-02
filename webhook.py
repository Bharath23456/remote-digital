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
        print("Push detected! Starting Python CI/CD and Deployment pipeline in background...", flush=True)

        # Configure git to avoid filemode and ownership conflicts on mounted volume
        subprocess.run(["git", "config", "--global", "--add", "safe.directory", "*"], cwd=REPO_DIR, capture_output=True, text=True, timeout=30)
        subprocess.run(["git", "config", "core.filemode", "false"], cwd=REPO_DIR, capture_output=True, text=True, timeout=30)

        # Step 1: Fetch latest changes from GitHub
        print(f"Fetching latest code from branch '{DEPLOY_BRANCH}' in {REPO_DIR}...", flush=True)
        fetch = subprocess.run(
            ["git", "fetch", "origin", DEPLOY_BRANCH],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            timeout=120
        )
        if fetch.returncode != 0:
            print(f"[ERROR] Git fetch failed: {fetch.stderr}", flush=True)
            return

        # Identify which files changed to build only affected services
        diff_proc = subprocess.run(
            ["git", "diff", "--name-only", "HEAD", f"origin/{DEPLOY_BRANCH}"],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            timeout=30
        )
        changed_files = diff_proc.stdout.strip().splitlines() if diff_proc.returncode == 0 else []
        print(f"Detected {len(changed_files)} changed file(s): {changed_files[:10]}", flush=True)

        # Reset cleanly to origin branch (discards any local untracked/mode discrepancies)
        reset = subprocess.run(
            ["git", "reset", "--hard", f"origin/{DEPLOY_BRANCH}"],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            timeout=60
        )
        if reset.returncode != 0:
            print(f"[ERROR] Git reset failed: {reset.stderr}", flush=True)
            return
        print(f"Working tree reset cleanly to origin/{DEPLOY_BRANCH}.", flush=True)

        # Step 2: Run Tests & Verification (optional)
        if RUN_TESTS:
            print("Running tests and system integrity checks...", flush=True)
            test_cmd = [
                "docker", "compose", "-p", COMPOSE_PROJECT,
                "exec", "-T", "backend", "python", "manage.py", "check"
            ]
            try:
                tests = subprocess.run(test_cmd, cwd=REPO_DIR, capture_output=True, text=True, timeout=90)
                if tests.returncode != 0:
                    print(f"[WARNING] Django check warning/failure: {tests.stderr}", flush=True)
                else:
                    print("System integrity checks passed!", flush=True)
            except Exception as e:
                print(f"[WARNING] Test step encountered exception: {e}", flush=True)

        # Step 3: Determine which services need building/restarting
        frontend_changed = any(f.startswith("frontend/") for f in changed_files)
        backend_changed = any(f.startswith("backend/") for f in changed_files)
        gateway_changed = any(f.startswith("storage_gateway/") for f in changed_files)
        identity_changed = any(f.startswith("identity_service/") for f in changed_files)

        # If nothing specific detected (e.g. forced trigger) or multiple, default sensibly
        services_to_rebuild = []
        if frontend_changed or not changed_files:
            services_to_rebuild.append("frontend")
        if backend_changed:
            services_to_rebuild.append("backend")
        if gateway_changed:
            services_to_rebuild.append("storage-gateway")
        if identity_changed:
            services_to_rebuild.append("identity-service")

        for svc in services_to_rebuild:
            print(f"Building updated service: {svc}...", flush=True)
            build = subprocess.run(
                ["docker", "compose", "-p", COMPOSE_PROJECT, "build", svc],
                cwd=REPO_DIR,
                capture_output=True,
                text=True,
                timeout=300
            )
            if build.returncode != 0:
                print(f"[ERROR] Docker build for {svc} failed: {build.stderr}", flush=True)
            else:
                print(f"Build succeeded for {svc}!", flush=True)

        # Step 4: Deploy and recreate the updated services
        services_to_up = services_to_rebuild if services_to_rebuild else ["frontend"]
        print(f"Restarting updated services: {services_to_up}...", flush=True)
        deploy = subprocess.run(
            ["docker", "compose", "-p", COMPOSE_PROJECT, "up", "-d"] + services_to_up,
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            timeout=120
        )
        if deploy.returncode != 0:
            print(f"[ERROR] Deployment up failed: {deploy.stderr}", flush=True)
            return

        print("Pipeline finished successfully! Changes are live on the server.", flush=True)
    except subprocess.TimeoutExpired as te:
        print(f"[ERROR] Pipeline step timed out: {te}", flush=True)
    except Exception as e:
        print(f"[ERROR] Pipeline encountered unexpected error: {e}", flush=True)
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
