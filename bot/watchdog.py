"""
Watchdog Module - Ensures only 1 bot instance runs and auto-restarts on crash
"""
import os
import sys
import time
import traceback
import subprocess

# Get the directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOCK_FILE = os.path.join(SCRIPT_DIR, "bot.lock")

# Cross-platform Python command
if sys.platform == "win32":
    PYTHON_CMD = "python"
else:
    PYTHON_CMD = "python3"

BOT_COMMAND = [PYTHON_CMD, os.path.join(SCRIPT_DIR, "main.py")]
RESTART_DELAY = 10  # sekundės


def acquire_lock():
    """Acquire lock file to ensure only one instance runs"""
    if os.path.exists(LOCK_FILE):
        # Check if the process in lock file is still running
        try:
            with open(LOCK_FILE, "r") as f:
                old_pid = int(f.read().strip())
            
            # Check if process is still running (cross-platform)
            if sys.platform == "win32":
                # Windows: use tasklist
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {old_pid}"],
                    capture_output=True,
                    text=True
                )
                if str(old_pid) in result.stdout:
                    print(f"❌ Bot already running (PID: {old_pid}). Lock file exists.")
                    sys.exit(1)
            else:
                # Unix/Linux: use os.kill with signal 0 (doesn't kill, just checks)
                try:
                    os.kill(old_pid, 0)
                    print(f"❌ Bot already running (PID: {old_pid}). Lock file exists.")
                    sys.exit(1)
                except ProcessLookupError:
                    # Process doesn't exist, remove stale lock
                    print(f"⚠️ Removing stale lock file (PID: {old_pid} not found)")
                    os.remove(LOCK_FILE)
        except (ValueError, FileNotFoundError):
            # Lock file corrupted or removed, continue
            pass

    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))

    print("🔒 Lock acquired")


def release_lock():
    """Release lock file on exit"""
    if os.path.exists(LOCK_FILE):
        os.remove(LOCK_FILE)
        print("🔓 Lock released")


def watchdog_loop():
    """Main watchdog loop - monitors bot and restarts on crash"""
    while True:
        try:
            print("🚀 Starting bot process...")
            # Change to bot directory for proper imports
            process = subprocess.Popen(
                BOT_COMMAND,
                cwd=SCRIPT_DIR
            )
            exit_code = process.wait()

            if exit_code == 0:
                print("✅ Bot exited normally")
                break
            else:
                print(f"⚠️ Bot stopped unexpectedly (exit code: {exit_code}). Restarting in {RESTART_DELAY}s...")

        except KeyboardInterrupt:
            print("🛑 Manual stop detected")
            break

        except Exception as e:
            print("❌ Watchdog error:", e)
            traceback.print_exc()

        time.sleep(RESTART_DELAY)


if __name__ == "__main__":
    try:
        acquire_lock()
        watchdog_loop()
    finally:
        release_lock()

