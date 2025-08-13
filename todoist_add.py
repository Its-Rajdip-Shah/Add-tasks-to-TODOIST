# 6f76aeb8b1076ee8462f15d192ed54b03770cb30

import os, csv, glob, sys, datetime, re, argparse
from pathlib import Path
from typing import List
from zoneinfo import ZoneInfo
import requests

API = "https://api.todoist.com/rest/v2"
TOKEN = os.getenv("TODOIST_TOKEN")
if not TOKEN:
    print("ERROR: Set your token first: export TODOIST_TOKEN=YOUR_TODOIST_TOKEN")
    sys.exit(1)

HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# ---- Adjust if needed ----
PROJECT_NAME = "Course"           # target project name
LOCAL_TZ = "Australia/Sydney"     # for due_datetime
# --------------------------

# ---------- CSV discovery (latest by creation time / Date Added) ----------
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
    # macOS/APFS exposes creation time (birthtime)
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

    # 2) Search common Downloads locations (hardcode your user path first)
    candidates = [
        Path("/Users/rajdipshah/Downloads"),
        Path("~/Downloads"),
        Path("~/Library/Mobile Documents/com~apple~CloudDocs/Downloads"),  # iCloud Downloads
    ]
    csvs = _candidate_csvs(candidates)
    print("DEBUG: CSVs found:", csvs)
    if not csvs:
        raise FileNotFoundError("No CSV files found in any Downloads folder.")
    csvs.sort(key=_file_created, reverse=True)
    print("DEBUG: Selected (latest by creation time):", csvs[0])
    return csvs[0]

# ---------- Todoist helpers ----------
def get_project_id(name: str) -> int:
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

def to_iso(date_s: str | None, time_s: str | None) -> str | None:
    """Return ISO-8601 with local offset, or None if no date."""
    date_s = (date_s or "").strip()
    time_s = (time_s or "23:59").strip()
    if not date_s:
        return None
    dt = datetime.datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M")
    return dt.replace(tzinfo=ZoneInfo(LOCAL_TZ)).isoformat()

# Parse @tags and /Week section out of CONTENT
SECTION_PAT = re.compile(r"^/(week[_ ]?\d+)$", re.IGNORECASE)

def parse_content(content: str):
    """
    CONTENT example: "OOP ED Task submitted? @course @oop /Week2"
    Returns: (title:str, labels:list[str], section_name:str|None)
    - tags: tokens starting with '@'
    - section: '/Week2' or '/Week_2' (case-insensitive) -> normalized to 'Week2'
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
                normalized = m.group(1)                    # e.g. 'week_2'
                normalized = normalized.replace("_", "").replace(" ", "")  # 'week2'
                section_name = "Week" + normalized[len("week"):]           # 'Week2'
                continue
        title_parts.append(t)

    title = " ".join(title_parts).strip()
    return title, labels, section_name

def create_task(project_id: int, sections: dict, title: str, labels: list[str],
                section_name: str | None, due_iso: str | None,
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
    if due_iso:
        payload["due_datetime"] = due_iso
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
    args = parser.parse_args()

    csv_path = latest_downloaded_csv(args.csv)
    print(f"Using CSV: {csv_path}")

    project_id = get_project_id(PROJECT_NAME)
    sections_map = get_sections_map(project_id)

    count = 0
    for r in read_rows(csv_path):
        content = r.get("CONTENT", "")
        title, labels, section_name = parse_content(content)

        due_iso = to_iso(r.get("DUE_DATE"), r.get("DUE_TIME"))
        priority = to_priority(r.get("PRIORITY"))
        description = r.get("DESCRIPTION", "")

        create_task(project_id, sections_map, title, labels, section_name, due_iso, description, priority)
        count += 1

    print(f"Done. Created {count} task(s).")

if __name__ == "__main__":
    main()