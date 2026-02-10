import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from telegram import Bot


ERROR_PATTERNS = re.compile(
    r"(Traceback|ERROR|Exception|CRITICAL|authenticationError|ModuleNotFoundError|KeyError|ValueError)"
)
IGNORE_SUBSTRINGS = {
    "ERROR HANDLING ENABLED",
}


def get_cursor_terminals_dir(repo_path: Path) -> Path:
    custom_dir = os.getenv("CURSOR_TERMINALS_DIR")
    if custom_dir:
        return Path(custom_dir)

    drive = repo_path.drive.rstrip(":").lower()
    parts = [part for part in repo_path.parts if part not in (repo_path.drive, os.sep)]
    if repo_path.anchor and parts and parts[0] == repo_path.anchor:
        parts = parts[1:]
    slug = "-".join([drive] + parts)
    return Path.home() / ".cursor" / "projects" / slug / "terminals"


def load_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state), encoding="utf-8")


def get_latest_log_file(terminals_dir: Path) -> Path | None:
    if not terminals_dir.exists():
        return None
    candidates = list(terminals_dir.glob("*.txt"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def extract_errors(new_text: str) -> list[str]:
    lines = new_text.splitlines()
    error_lines = []
    for line in lines:
        if any(token in line for token in IGNORE_SUBSTRINGS):
            continue
        if ERROR_PATTERNS.search(line):
            error_lines.append(line)
    return error_lines


def main() -> int:
    load_dotenv()
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        print("Missing TELEGRAM_TOKEN or TELEGRAM_CHAT_ID.")
        return 1

    repo_path = Path(__file__).resolve().parent
    terminals_dir = get_cursor_terminals_dir(repo_path)
    log_file = get_latest_log_file(terminals_dir)
    if not log_file:
        print("No terminal logs found.")
        return 0

    state_path = Path(os.getenv("LOCALAPPDATA", Path.home())) / "futures-signals-bot" / "log_watcher_state.json"
    state = load_state(state_path)
    last_file = state.get("file_path")
    last_offset = int(state.get("offset", 0))

    if last_file != str(log_file):
        last_offset = 0

    content = log_file.read_text(encoding="utf-8", errors="ignore")
    if last_offset > len(content):
        last_offset = 0

    new_text = content[last_offset:]
    state["file_path"] = str(log_file)
    state["offset"] = len(content)
    save_state(state_path, state)

    if not new_text.strip():
        return 0

    error_lines = extract_errors(new_text)
    if not error_lines:
        return 0

    # Send a compact message with recent error lines
    tail = error_lines[-20:]
    message = "⚠️ Bot log error(s) detected:\n" + "\n".join(tail)

    bot = Bot(token=token)
    bot.send_message(chat_id=chat_id, text=message[:3500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
