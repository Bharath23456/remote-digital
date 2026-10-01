import os
import sys
import subprocess
import shutil
from flask import Flask, request, jsonify

app = Flask(__name__)

# Configuration
REPO_DIR = os.environ.get("REPO_DIR", os.path.dirname(os.path.abspath(__file__)))
DEPLOY_BRANCH = os.environ.get("DEPLOY_BRANCH", "main")
COMPOSE_PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "remote-digital")
HOST_PORT = int(os.environ.get("WEBHOOK_PORT", 5000))
RUN_TESTS = os.environ.get("RUN_TESTS", "true").lower() in ("true", "1", "yes")

@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "service": "ADMIEZO CI/CD Webhook Listener",
        "repo_dir": REPO_DIR,
        "branch": DEPLOY_BRANCH
    }), 200

@app.route("/webhook", methods=["POST"])
def github_webhook():
    if request.method == "POST":
        print("Push detected! Starting Python CI/CD and Deployment pipeline...")

        # Step 1: Pull the latest code
        print(f"Pulling latest code from branch '{DEPLOY_BRANCH}' in {REPO_DIR}...")
        pull = subprocess.run(
            ["git", "pull", "origin", DEPLOY_BRANCH],
            cwd=REPO_DIR,
            capture_output=True,
            text=True
        )
        if pull.returncode != 0:
            print(f"Git pull failed: {pull.stderr}")
            return jsonify({
                "status": "error",
                "message": "Git pull failed",
                "logs": pull.stderr or pull.stdout
            }), 500

        # Step 2: Run Tests & Verification
        if RUN_TESTS:
            print("Running tests and system integrity checks...")
            # Check backend Django system integrity
            test_cmd = [
                "docker", "compose", "-p", COMPOSE_PROJECT,
                "exec", "-T", "backend", "python", "manage.py", "check"
            ]
            tests = subprocess.run(test_cmd, cwd=REPO_DIR, capture_output=True, text=True)
            if tests.returncode != 0:
                print("Tests failed! Aborting build and deployment.")
                return jsonify({
                    "status": "failed",
                    "message": "Tests / System checks failed",
                    "logs": tests.stderr or tests.stdout
                }), 400
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
            print(f"Docker build failed: {build.stderr}")
            return jsonify({
                "status": "error",
                "message": "Docker build failed",
                "logs": build.stderr or build.stdout
            }), 500

        # Step 4: Deploy to Live Server
        print("Deploying updated services to live server...")
        # Check if Windows host has scripts/deploy_live.ps1 available
        live_script = os.path.join(REPO_DIR, "scripts", "deploy_live.ps1")
        if sys.platform == "win32" and os.path.exists(live_script) and shutil.which("powershell"):
            deploy = subprocess.run(
                ["powershell", "-ExecutionPolicy", "Bypass", "-File", live_script, "-DeployPath", REPO_DIR],
                cwd=REPO_DIR,
                capture_output=True,
                text=True
            )
        else:
            # Multi-container deployment via Docker Compose
            deploy = subprocess.run(
                ["docker", "compose", "-p", COMPOSE_PROJECT, "up", "-d"],
                cwd=REPO_DIR,
                capture_output=True,
                text=True
            )

        if deploy.returncode != 0:
            print(f"Deployment failed: {deploy.stderr}")
            return jsonify({
                "status": "error",
                "message": "Deployment failed",
                "logs": deploy.stderr or deploy.stdout
            }), 500

        print("Pipeline finished! New code is running on the live server.")
        return jsonify({
            "status": "success",
            "message": "Code tested, built, and deployed successfully!"
        }), 200

if __name__ == "__main__":
    print(f"🚀 Starting ADMIEZO Webhook Deployment Server on port {HOST_PORT}...")
    app.run(host="0.0.0.0", port=HOST_PORT)
