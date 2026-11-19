import os
import re
import sqlite3
from collections import Counter

from werkzeug.security import generate_password_hash

from import_tt import TIMETABLE_CSV, import_timetable

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "hivemind.db")
SCHEMA = os.path.join(BASE_DIR, "schema.sql")

DEMO_PASSWORD = "password123"

STAFF = [
    ("rpatel", "Ms R. Patel", "English", "teacher"),
    ("jokafor", "Mr J. Okafor", "Science", "teacher"),
    ("ahughes", "Mrs A. Hughes", "Maths", "teacher"),
    ("lchen", "Ms L. Chen", "English", "teacher"),
    ("dmorgan", "Mr D. Morgan", "History", "teacher"),
    ("skhan", "Ms S. Khan", "Science", "teacher"),
    ("tbrooks", "Mr T. Brooks", "Maths", "teacher"),
    ("ewilson", "Mrs E. Wilson", "History", "teacher"),
    ("kfoster", "Mrs K. Foster", "Science", "teacher"),
    ("nward", "Mr N. Ward", "PE", "teacher"),
    ("gadams", "Ms G. Adams", "Art", "teacher"),
    ("mevans", "Mr M. Evans", "Maths", "teacher"),
    ("jreid", "Mrs J. Reid", "Cover", "cover_supervisor"),
    ("pgrant", "Mr P. Grant", "Leadership", "slt"),
    ("mtrew", "Mr M. Trew", "Leadership", "admin"),
]

ABSENCES = [
    ("jokafor", 0, 0, "Illness", "Approved"),
    ("ahughes", 0, 0, "Training", "Approved"),
    ("dmorgan", 0, 0, "Appointment", "Approved"),
    ("kfoster", 0, 2, "Illness", "Approved"),
    ("rpatel", 1, 1, "Training", "Approved"),
    ("rpatel", 4, 4, "Appointment", "Approved"),
    ("nward", 6, 7, "School trip", "Approved"),
    ("ewilson", -3, -2, "Illness", "Approved"),
    ("tbrooks", -8, -8, "Training", "Approved"),
    ("lchen", 3, 3, "Appointment", "Pending"),
    ("skhan", 7, 8, "Training", "Pending"),
]

# Which timetable subjects count as each department, for linking accounts to staff codes
DEPARTMENT_SUBJECTS = {
    "English": ["En"],
    "Maths": ["Ma"],
    "Science": ["Sc", "Ch", "Bi", "Ph"],
    "History": ["Hi"],
    "PE": ["PE"],
    "Art": ["Ar"],
}

COVER_REQUESTS = [
    ("science-10b", "jokafor", "rpatel", "10B Science", "S14", "Science", 0, 2,
     "Students complete the forces worksheet, questions 1-8, working in silence. "
     "Question 9 is the extension. Collect the sheets at the end and leave them "
     "in Mr Okafor's tray in the science office.",
     "Forces worksheet.pdf", "Textbooks are on the shelf by the window in S14.", "Covered"),
    ("maths-9c", "ahughes", "rpatel", "9C Maths", "M3", "Maths", 0, 5,
     "Students log in to Sparx on the laptops and complete this week's tasks. "
     "Anyone who finishes early can do the times tables challenge. "
     "Please sign the laptop log sheet.",
     "9C seating plan.pdf", "Laptop trolley is in M3. The code is on the door.", "Covered"),
    ("history-8b", "dmorgan", "ewilson", "8B History", "H2", "History", 0, 1,
     "Read pages 34-37 and complete the source questions.",
     None, "Textbooks are in the cupboard.", "Covered"),
    ("history-9a", "dmorgan", None, "9A History", "H2", "History", 0, 3,
     "Finish the World War One poster from last lesson.",
     None, "Coloured pens are in the drawer.", "Pending"),
    ("science-7c", "kfoster", "skhan", "7C Science", "S3", "Science", 0, 2,
     "Watch the video on cells and fill in the worksheet.",
     "Cells worksheet.pdf", None, "Covered"),
    ("science-11b", "kfoster", None, "11B Science", "S3", "Science", 0, 4,
     "Exam practice: paper 1, questions 1-5.",
     "Paper 1.pdf", None, "Pending"),
    ("science-10a", "kfoster", None, "10A Science", "S3", "Science", 0, 6,
     "Revision quiz on the periodic table.",
     None, None, "Pending"),
    ("english-11a", "rpatel", "lchen", "11A English", "E4", "English", 1, 1,
     "Read chapter 4 of An Inspector Calls and answer the questions on the board.",
     None, "Copies of the play are in the box under the desk.", "Covered"),
    ("english-12b", "rpatel", None, "12B Literature", "E2", "English", 1, 3,
     "Essay planning: compare the two poems in the anthology handout.",
     None, None, "Pending"),
    ("history-8a", "jokafor", "rpatel", "8A History", "H1", "History", 1, 6,
     "Timeline activity on the Norman Conquest.",
     None, None, "Cancelled"),
    ("science-8c", "kfoster", None, "8C Science", "S3", "Science", 1, 2,
     "Complete the states of matter booklet.",
     None, None, "Pending"),
    ("english-7d", "rpatel", None, "7D English", "E4", "English", 4, 4,
     "Spelling test then silent reading.",
     None, None, "Declined"),
    ("history-8d", "ewilson", "dmorgan", "8D History", "H3", "History", -3, 2,
     "Castle design task.", None, None, "Covered"),
    ("history-7a", "ewilson", "tbrooks", "7A History", "H3", "History", -3, 5,
     "Read the Battle of Hastings article and answer questions 1-6.", None, None, "Covered"),
    ("maths-10d", "tbrooks", "mevans", "10D Maths", "M1", "Maths", -8, 3,
     "Algebra worksheet.", None, None, "Covered"),
]

CHANGES = [
    ("rpatel", "maths-9c", "New", "You have been asked to cover 9C Maths, period 5 today", "-10 minutes"),
    ("rpatel", "science-10b", "Room change", "10B Science (period 2) has moved from S12 to S14", "-2 hours"),
    ("rpatel", "history-8a", "Cancelled", "Your cover for 8A History tomorrow is no longer needed", "-1 day"),
]


def create_database():
    connection = sqlite3.connect(DATABASE)
    connection.execute("PRAGMA foreign_keys = ON")

    with open(SCHEMA) as f:
        connection.executescript(f.read())

    password_hash = generate_password_hash(DEMO_PASSWORD)
    staff_ids = {}
    for name, display_name, department, role in STAFF:
        cursor = connection.execute(
            "INSERT INTO users (name, display_name, department, password_hash, role) VALUES (?, ?, ?, ?, ?)",
            (name, display_name, department, password_hash, role),
        )
        staff_ids[name] = cursor.lastrowid

    for name, start, end, reason, status in ABSENCES:
        connection.execute(
            """INSERT INTO absences (staff_id, start_date, end_date, reason, status)
               VALUES (?, date('now', 'localtime', ?), date('now', 'localtime', ?), ?, ?)""",
            (staff_ids[name], f"{start:+d} days", f"{end:+d} days", reason, status),
        )

    cover_ids = {}
    for (key, requested_by, covered_by, class_name, room, subject, days_ahead, period,
         set_work, attachment, resources, status) in COVER_REQUESTS:
        cursor = connection.execute(
            """INSERT INTO cover_requests
               (requested_by, covered_by, class_name, room, subject, lesson_date,
                period, set_work, attachment, resources, status)
               VALUES (?, ?, ?, ?, ?, date('now', 'localtime', ?), ?, ?, ?, ?, ?)""",
            (staff_ids[requested_by], staff_ids.get(covered_by), class_name, room, subject,
             f"{days_ahead:+d} days", period, set_work, attachment, resources, status),
        )
        cover_ids[key] = cursor.lastrowid

    for name, cover_key, change_type, message, offset in CHANGES:
        connection.execute(
            """INSERT INTO changes (user_id, cover_id, type, message, created_at)
               VALUES (?, ?, ?, ?, datetime('now', ?))""",
            (staff_ids[name], cover_ids[cover_key], change_type, message, offset),
        )

    connection.commit()

    if os.path.exists(TIMETABLE_CSV):
        counts = import_timetable(connection)
        print(f"Timetable imported: {counts['lessons']} lessons, {counts['duties']} duties, {counts['staff']} staff codes")
        link_staff_codes(connection)

    connection.close()


def subject_code(subject):
    # "IB En2" -> "En", "Ch A1" -> "Ch", "PE C1" -> "PE"
    match = re.match(r"(?:IB )?([A-Za-z]+)", subject)
    return match.group(1) if match else ""


def link_staff_codes(connection):
    """Give each demo account a staff code from the imported timetable.

    The codes are picked here rather than typed into this file, so no real
    staff codes are written in the code that goes on GitHub.
    """
    used = set()

    def take(code, name):
        used.add(code)
        connection.execute("UPDATE users SET staff_code = ? WHERE name = ?", (code, name))

    # SLT: the staff code with the most SLT duties
    slt_codes = connection.execute(
        """SELECT te.staff_code FROM duties d JOIN teachers te ON te.teacher_id = d.teacher_id
           WHERE d.duty LIKE 'SLT%' GROUP BY te.staff_code ORDER BY COUNT(*) DESC, te.staff_code"""
    ).fetchall()
    # Cover supervisor: the non-teaching staff code with the most on-call duties
    cover_codes = connection.execute(
        """SELECT te.staff_code FROM duties d JOIN teachers te ON te.teacher_id = d.teacher_id
           WHERE te.staff_type = 'NON_TEACHING' AND d.duty LIKE 'OnCall%'
           GROUP BY te.staff_code ORDER BY COUNT(*) DESC, te.staff_code"""
    ).fetchall()

    # How many lessons of each subject every teaching staff code has
    subject_counts = {}
    for row in connection.execute(
        """SELECT te.staff_code, t.subject FROM timetable t
           JOIN teachers te ON te.teacher_id = t.teacher_id
           WHERE te.staff_type = 'TEACHING' ORDER BY te.staff_code"""
    ):
        counts = subject_counts.setdefault(row[0], Counter())
        counts[subject_code(row[1])] += 1

    users = connection.execute("SELECT name, department, role FROM users ORDER BY id").fetchall()
    for name, department, role in users:
        if role == "admin":
            continue 
        elif role == "slt":
            choices = [row[0] for row in slt_codes]
        elif role == "cover_supervisor":
            choices = [row[0] for row in cover_codes]
        else:
            wanted = DEPARTMENT_SUBJECTS.get(department, [])
            # Best match first: the code with the most lessons in this department's subjects
            choices = sorted(
                subject_counts,
                key=lambda code: -sum(subject_counts[code][s] for s in wanted),
            )
        for code in choices:
            if code not in used:
                take(code, name)
                break

    connection.commit()


if __name__ == "__main__":
    create_database()
    print("Database created with demo data.")
    print(f"Demo accounts all use the password {DEMO_PASSWORD}:")
    print("  rpatel (teacher), jreid (cover supervisor), pgrant (SLT), mtrew (admin)")
