import csv
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "hivemind.db")
TIMETABLE_CSV = os.path.join(BASE_DIR, "timetable.csv")


def get_teacher_id(cursor, staff_code, staff_type):
    cursor.execute("INSERT or IGNORE INTO teachers (staff_code, staff_type) VALUES (?, ?)", (staff_code, staff_type))
    cursor.execute("SELECT teacher_id FROM teachers WHERE staff_code = ?", (staff_code,))
    return cursor.fetchone()[0]

def get_class_id(cursor, class_name):
    cursor.execute("INSERT or IGNORE INTO classes (class_name) VALUES (?)", (class_name,))
    cursor.execute("SELECT class_id FROM classes WHERE class_name = ?", (class_name,))
    return cursor.fetchone()[0]

def import_timetable(conn, csv_file=None):
    """Replace the timetable with the one in the CSV.

    csv_file: an open text file (e.g. an upload). If it's left out, timetable.csv is used.
    If anything in the file is wrong, nothing is changed and a ValueError explains why.
    Returns how many lessons, duties and staff codes there are afterwards.
    """
    if csv_file is None:
        with open(TIMETABLE_CSV, newline="", encoding="utf-8-sig") as f:
            return import_timetable(conn, f)

    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM timetable")
        cursor.execute("DELETE FROM duties")

        reader = csv.DictReader(csv_file)
        headers = reader.fieldnames or []
        slots = [h for h in headers if "_P" in h and not h.endswith("_Room")]
        if "staff_code" not in headers or "staff_type" not in headers or not slots:
            raise ValueError("This doesn't look like the staff timetable export: it needs staff_code, "
                             "staff_type and columns like Mon_P1 and Mon_P1_Room.")
        missing_rooms = [slot for slot in slots if slot + "_Room" not in headers]
        if missing_rooms:
            raise ValueError(f"These columns have no matching room column: {', '.join(missing_rooms)}.")

        for line, row in enumerate(reader, start=2):
            staff_code = (row["staff_code"] or "").strip()
            if not staff_code:
                continue
            teacher_id = get_teacher_id(cursor, staff_code, (row["staff_type"] or "").strip())
            for slot in slots:
                cell = (row[slot] or "").strip()
                room = (row[slot + "_Room"] or "").strip()
                if not cell:
                    continue
                day, period = slot.split("_")

                if room:
                    if "/" not in cell:
                        raise ValueError(f"Row {line} ({staff_code}), {slot}: the lesson \"{cell}\" "
                                         "should look like class/subject, e.g. 10X/Ma1.")
                    class_name, subject = cell.split("/", 1)
                    class_id = get_class_id(cursor, class_name.strip())
                    cursor.execute("INSERT INTO timetable (class_id, teacher_id, day, period, room, subject) VALUES (?, ?, ?, ?, ?, ?)", (class_id, teacher_id, day, period, room, subject.strip()))
                else:
                    cursor.execute("INSERT INTO duties (teacher_id, day, period, duty) VALUES (?, ?, ?, ?)", (teacher_id, day, period, cell))
    except Exception:
        conn.rollback()  # leave the old timetable exactly as it was
        raise

    conn.commit()
    return {
        "lessons": cursor.execute("SELECT COUNT(*) FROM timetable").fetchone()[0],
        "duties": cursor.execute("SELECT COUNT(*) FROM duties").fetchone()[0],
        "staff": cursor.execute("SELECT COUNT(*) FROM teachers").fetchone()[0],
    }


if __name__ == "__main__":
    conn = sqlite3.connect(DATABASE)
    conn.execute('PRAGMA foreign_keys = ON')
    counts = import_timetable(conn)
    conn.close()
    print("Lessons:", counts["lessons"])
    print("Duties:", counts["duties"])
    print("Staff:", counts["staff"])
