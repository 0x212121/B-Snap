import subprocess
import sys
import json

EXCLUDED = {"bcrypt"}

def get_outdated_packages():
    # Jalankan pip list --outdated --format=json
    result = subprocess.run(
        [sys.executable, "-m", "pip", "list", "--outdated", "--format=json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        print("❌ Failed to list outdated packages:")
        print(result.stderr)
        sys.exit(1)

    try:
        packages_json = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print("❌ Failed to parse pip output:", e)
        sys.exit(1)

    packages = []
    for pkg in packages_json:
        name = pkg["name"]
        if name.lower() not in {e.lower() for e in EXCLUDED}:
            packages.append(name)

    return packages

def upgrade_package(pkg_name):
    print(f"⬆️  Upgrading {pkg_name}...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", pkg_name]
    )
    if result.returncode != 0:
        print(f"❌ Failed to upgrade {pkg_name}")

def freeze_requirements():
    print("💾 Updating requirements.txt...")
    with open("requirements.txt", "w") as f:
        subprocess.run([sys.executable, "-m", "pip", "freeze"], stdout=f)

if __name__ == "__main__":
    print("🔍 Checking for outdated packages (excluding bcrypt)...")

    outdated = get_outdated_packages()

    if not outdated:
        print("✅ All packages are up to date.")
    else:
        for pkg in outdated:
            upgrade_package(pkg)

    freeze_requirements()
    print("✅ Done.")
