# todoist_add.py
# Create Todoist tasks from the *latest downloaded* CSV (by creation time) or an explicit --csv path.
# CSV expects at least a CONTENT column; optional: DUE_DATE (YYYY-MM-DD), DUE_TIME (HH:MM 24h), PRIORITY (P1..P4 or 1..4), DESCRIPTION.
# CONTENT supports inline tags (@tag) and section (/WeekX or /Week_X), e.g.:
#   ENGG2112 Coding Quiz @course @ENGG2112 /Week7

import os, csv, glob, sys, datetime, re, argparse
from pathlib import Path
from typing import List
from zoneinfo import ZoneInfo
import requests

API = "https://api.todoist.com/rest/v2"

# ---- Adjust if needed ----
PROJECT_NAME = "Course"           # Target project
LOCAL_TZ = "Australia/Sydney"     # For timed tasks
# --------------------------

# ---------- Token handling ----------
def load_token(cli_token: str | None) -> str:
    # 1) CLI --token
    if cli_token:
        return cli_token.strip()

    # 2) Environment variable
    env = os.getenv("TODOIST_TOKEN")
    if env:
        return env.strip()

    # 3) (Optional) hardcode fallback — put your token string here if you *really* want*
    HARDCODED = ""  # e.g., "6f76aeb8b1076ee8462f15d192ed54b03770cb30"
    if HARDCODED:
        return HARDCODED.strip()

    print("ERROR: Set your token (use --token or export TODOIST_TOKEN=...)")
    sys.exit(1)

HEADERS = None  # will be set in main() after token is loaded

# ---------- CSV discovery (latest by creation time / “Date Added”) ----------
def _candidate_csvs(paths: List[Path]) -> List[str]:
    files = []
    for p in paths:
        p = p.expanduser()
        files += glob.glob(str(p / "*.csv"))
        files += glob.glob(str(p / "*.CSV"))
    # dedupe while preserving order
    return list(dict.fromkeys(files))

def _file_created(path: str) -> float:
    st = os.stat(path)
    # macOS/APFS: creation time (birthtime); fallback to mtime if not available
    return getattr(st, "st_birthtime", None) or st.st_mtime

def latest_downloaded_csv(explicit: str | None = None) -> str:
    # 1) Command-line override
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_file():
            print(f"DEBUG: Using explicit CSV: {p}")
            return str(p)
        if p.is_dir():
            cands = _candidate_csvs([p])
            if not cands:
                raise FileNotFoundError(f"No CSV files found in {p}")
            cands.sort(key=_file_created, reverse=True)
            print(f"DEBUG: Using newest in dir {p}: {cands[0]}")
            return cands[0]
        raise FileNotFoundError(f"--csv path not found: {p}")

    # 2) Search common Downloads locations (hardcode first)
    candidates = [
        Path("/Users/rajdipshah/Downloads"),
        Path("~/Downloads"),
        Path("~/Library/Mobile Documents/com~apple~CloudDocs/Downloads"),  # iCloud Downloads
    ]
    csvs = _candidate_csvs(candidates)
    if not csvs:
        raise FileNotFoundError("No CSV files found in any Downloads folder.")
    csvs.sort(key=_file_created, reverse=True)
    print("DEBUG: Selected (latest by creation time):", csvs[0])
    return csvs[0]

# ---------- Todoist helpers ----------
def get_project_id(name: str, headers: dict) -> int:
    r = requests.get(f"{API}/projects", headers=HEADERS)
    r.raise_for_status()
    for p in r.json():
        if p["name"] == name:
            return p["id"]
    raise ValueError(f'Project "{name}" not found.')

def get_sections_map(project_id: int) -> dict:
    r = requests.get(f"{API}/sections", headers=HEADERS, params={"project_id": project_id})
    r.raise_for_status()
    return {s["name"]: s["id"] for s in r.json()}

def to_priority(val) -> int:
    """Accept P1..P4 or 1..4. Todoist API: 4=P1 (highest) … 1=P4 (lowest)."""
    if val is None:
        return 4
    s = str(val).strip().upper().lstrip("P")
    if s not in {"1","2","3","4"}:
        return 4
    return {1:4, 2:3, 3:2, 4:1}[int(s)]

# Only build an ISO datetime if BOTH date & time are given
def to_iso(date_s: str | None, time_s: str | None) -> str | None:
    date_s = (date_s or "").strip()
    time_s = (time_s or "").strip()
    if not date_s or not time_s:
        return None
    dt = datetime.datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M")
    return dt.replace(tzinfo=ZoneInfo(LOCAL_TZ)).isoformat()

# Parse @tags and /Week section out of CONTENT
SECTION_PAT = re.compile(r"^/(week[_ ]?\d+)$", re.IGNORECASE)

def parse_content(content: str):
    """
    CONTENT example: "ENGG2112 Coding Quiz @course @ENGG2112 /Week7"
    Returns: (title:str, labels:list[str], section_name:str|None)
    - tags: tokens starting with '@'
    - section: '/Week7' or '/Week_7' -> normalized to 'Week7'
    """
    if not content or not content.strip():
        return "", [], None

    tokens = content.strip().split()
    labels, title_parts = [], []
    section_name = None

    for t in tokens:
        if t.startswith("@") and len(t) > 1:
            labels.append(t[1:])
            continue
        if t.startswith("/"):
            m = SECTION_PAT.match(t.lower())
            if m:
                normalized = m.group(1)                                  # e.g., 'week_7'
                normalized = normalized.replace("_", "").replace(" ", "")  # 'week7'
                section_name = "Week" + normalized[len("week"):]           # 'Week7'
                continue
        title_parts.append(t)

    title = " ".join(title_parts).strip()
    return title, labels, section_name

def create_task(project_id: int, sections: dict, title: str, labels: list[str],
                section_name: str | None, date_s: str | None, time_s: str | None,
                description: str, priority: int):
    if not title:
        print("Skipping row with empty title/CONTENT.")
        return None

    payload = {
        "content": title,
        "project_id": project_id,
        "labels": labels,
        "priority": priority,
    }
    if description:
        payload["description"] = description

    # Due handling: all-day if time missing; timed if both provided
    iso = to_iso(date_s, time_s)
    if date_s and not time_s:
        payload["due_date"] = date_s.strip()
    elif iso:
        payload["due_datetime"] = iso
        payload["due_lang"] = "en"

    if section_name:
        sid = sections.get(section_name)
        if not sid:
            raise ValueError(f'Section "{section_name}" not found in project "{PROJECT_NAME}".')
        payload["section_id"] = sid

    r = requests.post(f"{API}/tasks", headers=HEADERS, json=payload)
    try:
        r.raise_for_status()
    except requests.HTTPError:
        print("Failed to create task:", r.text)
        raise
    task = r.json()
    print(f'✓ {task["content"]} → {section_name or "(no section)"}  (id={task["id"]})')
    return task["id"]

def read_rows(csv_path: str):
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Normalize headers to UPPER for robustness
            yield { (k or "").strip().upper(): (v or "").strip() for k, v in row.items() }

# ---------- main ----------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=None, help="Path to csv file or folder (optional)")
    parser.add_argument("--token", default=None, help="Todoist API token (optional; env TODOIST_TOKEN also works)")
    args = parser.parse_args()

    token = load_token(args.token)
    global HEADERS
    HEADERS = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    csv_path = latest_downloaded_csv(args.csv)
    print(f"Using CSV: {csv_path}")

    project_id = get_project_id(PROJECT_NAME, HEADERS)
    sections_map = get_sections_map(project_id)

    count = 0
    for r in read_rows(csv_path):
        content = r.get("CONTENT", "")
        title, labels, section_name = parse_content(content)

        date_s = r.get("DUE_DATE", "")
        time_s = r.get("DUE_TIME", "")  # may be empty/absent for all-day
        priority = to_priority(r.get("PRIORITY"))
        description = r.get("DESCRIPTION", "")

        create_task(project_id, sections_map, title, labels, section_name,
                    date_s, time_s, description, priority)
        count += 1

    print(f"Done. Created {count} task(s).")

if __name__ == "__main__":
    main()
