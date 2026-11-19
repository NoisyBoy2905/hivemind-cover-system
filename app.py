import csv
import io
import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, flash, g, abort
from werkzeug.security import check_password_hash, generate_password_hash

from import_tt import TIMETABLE_CSV, import_timetable
from setup_db import DATABASE, create_database, subject_code

app = Flask(__name__)


def load_secret_key():
    """Keep the same secret key between restarts, so people stay signed in.

    When hosted, set the SECRET_KEY environment variable. Locally, a random key
    is made once and saved in .secret_key, which is in .gitignore.
    """
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".secret_key")
    if not os.path.exists(key_file):
        with open(key_file, "w") as f:
            f.write(secrets.token_hex(32))
    with open(key_file) as f:
        return f.read().strip()


app.secret_key = load_secret_key()
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # uploads up to 2 MB

if not os.path.exists(DATABASE):
    create_database()

PERIODS = {
    1: ("08:50", "09:50"),
    2: ("09:50", "10:50"),
    3: ("11:10", "12:10"),
    4: ("12:10", "13:10"),
    5: ("13:40", "14:40"),
    6: ("14:40", "15:40"),
}

SUBJECTS = ["Maths", "Science", "History", "English"]

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]

STATUSES = ["Pending", "Covered", "Declined", "Cancelled"]

# Roles an account can have, and how they're shown on the page
ROLES = {
    "teacher": "Teacher",
    "cover_supervisor": "Cover supervisor",
    "slt": "SLT",
    "admin": "Admin",
}

# Every period is one hour (see PERIODS), so one lesson covered = one hour
LESSON_HOURS = 1

# Demo figure for estimating what cover costs. Not real pay data.
COVER_RATE = 25.00


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.before_request
def load_user():
    db = get_db()
    g.user = None
    user_id = session.get("user_id")
    if user_id is not None:
        g.user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    # A disabled account is logged out straight away, even mid-session
    if g.user is not None and not g.user["active"]:
        session.clear()
        g.user = None
    if g.user is None and request.endpoint not in ("signin", "static", "personal_link"):
        return redirect(url_for("signin", next=request.path ))
    # After an admin resets someone's password, they must choose their own first
    if (g.user is not None and g.user["must_change_password"]
            and request.endpoint not in ("change_password", "signout", "static")):
        return redirect(url_for("change_password"))


def roles_required(*roles):
    """Only let users with one of these roles open the page."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def is_teacher():
    return g.user["role"] == "teacher"

@app.context_processor
def inject_periods():
    return {"periods": PERIODS}


@app.template_filter("initials")
def initials(display_name):
    words = display_name.replace(".", "").split()[1:]
    return "".join(word[0] for word in words).upper()


@app.template_filter("short_name")
def short_name(display_name):
    words = display_name.split()
    return f"{words[0]} {words[-1]}"


@app.template_filter("day")
def day(iso_date):
    lesson_day = date.fromisoformat(iso_date)
    return f"{lesson_day:%a} {lesson_day.day} {lesson_day:%b}"


@app.template_filter("time_ago")
def time_ago(timestamp):
    then = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    minutes = int((datetime.now(timezone.utc) - then).total_seconds() // 60)
    if minutes < 1:
        return "Just now"
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 60 * 24:
        hours = minutes // 60
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    if minutes < 60 * 48:
        return "Yesterday"
    return f"{minutes // (60 * 24)} days ago"


def greeting():
    hour = datetime.now().hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


@app.route("/")
def home():
    if g.user["role"] == "admin":
        return redirect(url_for("admin"))
    if g.user["role"] == "cover_supervisor":
        return redirect(url_for("cover_desk"))
    db = get_db()
    today = date.today().isoformat()

    my_cover = db.execute(
        """SELECT c.*, u.display_name AS absent_teacher
           FROM cover_requests c
           JOIN users u ON u.id = c.requested_by
           WHERE c.covered_by = ? AND c.lesson_date = ? AND c.status = 'Covered'
           ORDER BY c.period""",
        (g.user["id"], today),
    ).fetchall()

    my_requests = db.execute(
        """SELECT c.*, u.display_name AS cover_teacher
           FROM cover_requests c
           LEFT JOIN users u ON u.id = c.covered_by
           WHERE c.requested_by = ? AND c.lesson_date >= ? AND c.status != 'Cancelled'
           ORDER BY c.lesson_date, c.period""",
        (g.user["id"], today),
    ).fetchall()

    changes = db.execute(
        """SELECT * FROM changes
           WHERE user_id = ? AND created_at > ?
           ORDER BY created_at DESC""",
        (g.user["id"], session.get("since", "")),
    ).fetchall()

    stats = {
        "total_staff": db.execute("SELECT COUNT(*) FROM users WHERE role != 'admin'").fetchone()[0],
        "absent_today": db.execute(
            """SELECT COUNT(DISTINCT staff_id) FROM absences
               WHERE status = 'Approved' AND ? BETWEEN start_date AND end_date""",
            (today,),
        ).fetchone()[0],
        "needs_cover": db.execute(
            "SELECT COUNT(*) FROM cover_requests WHERE lesson_date = ? AND status = 'Pending'",
            (today,),
        ).fetchone()[0],
        "covered": db.execute(
            "SELECT COUNT(*) FROM cover_requests WHERE lesson_date = ? AND status = 'Covered'",
            (today,),
        ).fetchone()[0],
    }

    return render_template(
        "index.html",
        greeting=greeting(),
        today=date.today(),
        stats=stats,
        my_cover=my_cover,
        my_requests=my_requests,
        changes=changes,
    )


@app.route("/staff")
@roles_required("cover_supervisor", "slt")
def staff():
    db = get_db()
    today = date.today().isoformat()
    departments = [row["department"] for row in db.execute(
        "SELECT DISTINCT department FROM users WHERE role != 'admin' ORDER BY department"
    )]

    department = request.args.get("department", "")
    if department not in departments:
        department = ""

    staff_list = db.execute(
        """SELECT u.id, u.display_name, u.department,
                  MAX(a.reason) AS absence_reason, MAX(a.end_date) AS back_after
           FROM users u
           LEFT JOIN absences a
                  ON a.staff_id = u.id AND a.status = 'Approved'
                 AND ? BETWEEN a.start_date AND a.end_date
           WHERE u.role != 'admin' AND (? = '' OR u.department = ?)
           GROUP BY u.id
           ORDER BY u.department, u.display_name""",
        (today, department, department),
    ).fetchall()

    absent_count = sum(1 for person in staff_list if person["absence_reason"])

    return render_template(
        "staff.html",
        staff_list=staff_list,
        departments=departments,
        department=department,
        absent_count=absent_count,
    )


@app.route("/absences")
def absences():
    today = date.today().isoformat()

    # Teachers only see their own absences
    where = ""
    params = []
    if is_teacher():
        where = "WHERE a.staff_id = ?"
        params.append(g.user["id"])

    rows = get_db().execute(
        f"""SELECT a.*, u.display_name, u.department,
                  (SELECT COUNT(*) FROM cover_requests c
                   WHERE c.requested_by = a.staff_id
                     AND c.lesson_date BETWEEN a.start_date AND a.end_date
                     AND c.status != 'Cancelled') AS lessons
           FROM absences a
           JOIN users u ON u.id = a.staff_id
           {where}
           ORDER BY a.start_date, u.display_name""",
        params,
    ).fetchall()

    current = [row for row in rows if row["start_date"] <= today <= row["end_date"]]
    upcoming = [row for row in rows if row["start_date"] > today]
    past = [row for row in rows if row["end_date"] < today][::-1]

    return render_template("absences.html", current=current, upcoming=upcoming, past=past)


@app.route("/cover-arrangements")
def cover_arrangements():
    today = date.today().isoformat()

    status = request.args.get("status", "")
    if status not in STATUSES:
        status = ""

    when = request.args.get("when", "upcoming")
    date_rules = {
        "today": ("c.lesson_date = ?", "c.lesson_date, c.period"),
        "upcoming": ("c.lesson_date >= ?", "c.lesson_date, c.period"),
        "past": ("c.lesson_date < ?", "c.lesson_date DESC, c.period"),
    }
    if when not in date_rules:
        when = "upcoming"
    date_rule, order = date_rules[when]

    # Teachers only see lessons they asked cover for, or are covering
    mine = ""
    params = [today, status, status]
    if is_teacher():
        mine = "AND (c.requested_by = ? OR c.covered_by = ?)"
        params += [g.user["id"], g.user["id"]]

    arrangements = get_db().execute(
        f"""SELECT c.*, absent.display_name AS absent_teacher, cover.display_name AS cover_teacher
            FROM cover_requests c
            JOIN users absent ON absent.id = c.requested_by
            LEFT JOIN users cover ON cover.id = c.covered_by
            WHERE {date_rule} AND (? = '' OR c.status = ?) {mine}
            ORDER BY {order}""",
        params,
    ).fetchall()

    return render_template(
        "cover_arrangements.html",
        arrangements=arrangements,
        statuses=STATUSES,
        status=status,
        when=when,
    )


@app.route("/reports")
@roles_required("slt")
def reports():
    db = get_db()

    covered_lessons = db.execute(
        """SELECT COUNT(*) AS all_time,
                  SUM(lesson_date >= date('now', 'localtime', 'start of month')) AS this_month
           FROM cover_requests
           WHERE status = 'Covered'""",
    ).fetchone()
    lessons_all_time = covered_lessons["all_time"] or 0
    lessons_this_month = covered_lessons["this_month"] or 0
    cover_totals = {
        "lessons": lessons_all_time,
        "hours": lessons_all_time * LESSON_HOURS,
        "cost": lessons_all_time * LESSON_HOURS * COVER_RATE,
        "lessons_month": lessons_this_month,
        "hours_month": lessons_this_month * LESSON_HOURS,
        "cost_month": lessons_this_month * LESSON_HOURS * COVER_RATE,
    }

    by_subject = db.execute(
        """SELECT subject,
                  COUNT(*) AS total,
                  SUM(status = 'Covered') AS covered,
                  SUM(status = 'Pending') AS pending,
                  SUM(status = 'Declined') AS declined
           FROM cover_requests
           WHERE status != 'Cancelled'
           GROUP BY subject
           ORDER BY total DESC, subject""",
    ).fetchall()

    by_teacher = db.execute(
        """SELECT u.display_name, u.department, COUNT(c.id) AS lessons
           FROM users u
           LEFT JOIN cover_requests c ON c.covered_by = u.id AND c.status = 'Covered'
           WHERE u.role not in ('slt', 'admin')
           GROUP BY u.id
           ORDER BY lessons DESC, u.display_name""",
    ).fetchall()

    by_reason = db.execute(
        """SELECT reason,
                  COUNT(*) AS absences,
                  SUM(julianday(end_date) - julianday(start_date) + 1) AS days
           FROM absences
           WHERE status = 'Approved'
           GROUP BY reason
           ORDER BY days DESC, reason""",
    ).fetchall()

    total = sum(row["total"] for row in by_subject)
    covered = sum(row["covered"] for row in by_subject)
    cover_rate = round(covered / total * 100) if total else 0

    return render_template(
        "reports.html",
        by_subject=by_subject,
        by_teacher=by_teacher,
        by_reason=by_reason,
        total=total,
        covered=covered,
        cover_rate=cover_rate,
        cover_totals=cover_totals,
        lesson_hours=LESSON_HOURS,
        rate=COVER_RATE,
        max_lessons=max([row["lessons"] for row in by_teacher] + [1]),
        max_days=max([int(row["days"]) for row in by_reason] + [1]),
    )


@app.route("/timetable")
def timetable():
    db = get_db()
    teachers = db.execute("SELECT * FROM teachers ORDER BY staff_code").fetchall()
    codes = [teacher["staff_code"] for teacher in teachers]

    if is_teacher():
        # Teachers can only see their own timetable
        staff_code = g.user["staff_code"] or ""
    else:
        staff_code = request.args.get("staff", "")
        if staff_code not in codes:
            staff_code = codes[0] if codes else ""

    # Slots that exist in the school's timetable (Wednesday has no period 6)
    school_slots = {
        (row["day"], row["period"])
        for row in db.execute("SELECT day, period FROM timetable UNION SELECT day, period FROM duties")
    }

    grid = {}
    teacher = None
    if staff_code:
        teacher = db.execute("SELECT * FROM teachers WHERE staff_code = ?", (staff_code,)).fetchone()

    # New staff can have a code that isn't in the imported timetable yet
    if teacher:
        lessons = db.execute(
            """SELECT t.day, t.period, t.room, t.subject, c.class_name
               FROM timetable t
               JOIN classes c ON c.class_id = t.class_id
               WHERE t.teacher_id = ?""",
            (teacher["teacher_id"],),
        ).fetchall()
        for lesson in lessons:
            grid[(lesson["day"], lesson["period"])] = {"lesson": lesson}

        duties = db.execute(
            "SELECT day, period, duty FROM duties WHERE teacher_id = ?",
            (teacher["teacher_id"],),
        ).fetchall()
        for duty in duties:
            grid.setdefault((duty["day"], duty["period"]), {"duty": duty["duty"]})

    return render_template(
        "timetable.html",
        codes=codes,
        staff_code=staff_code,
        teacher=teacher,
        grid=grid,
        school_slots=school_slots,
        days=["Mon", "Tue", "Wed", "Thu", "Fri"],
    )


@app.route("/cover", methods=["GET", "POST"])
def cover():
    error = None
    form = request.form

    if request.method == "POST":
        class_name = form.get("class_name", "").strip()
        room = form.get("room", "").strip()
        subject = form.get("subject", "")
        lesson_date = form.get("lesson_date", "")
        period = form.get("period", "")
        set_work = form.get("set_work", "").strip()
        resources = form.get("resources", "").strip() or None

        try:
            valid_date = date.fromisoformat(lesson_date) >= date.today()
        except ValueError:
            valid_date = False

        if not (class_name and room and set_work):
            error = "Please fill in every required field."
        elif subject not in SUBJECTS:
            error = "Please choose a subject from the list."
        elif not period.isdigit() or int(period) not in PERIODS:
            error = "Please choose a period from the list."
        elif not valid_date:
            error = "The lesson date must be today or later."
        else:
            db = get_db()
            db.execute(
                """INSERT INTO cover_requests
                   (requested_by, class_name, room, subject, lesson_date, period, set_work, resources)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (g.user["id"], class_name, room, subject, lesson_date, int(period), set_work, resources),
            )
            db.commit()
            flash(f"Cover requested for {class_name}, period {period} on {day(lesson_date)}.")
            return redirect(url_for("cover_arrangements"))

    return render_template(
        "cover.html",
        error=error,
        form=form,
        subjects=SUBJECTS,
        today=date.today().isoformat(),
    )


def create_cover_for_absence(db, absence_id):
    """Make a Pending cover request for every timetabled lesson the absent
    teacher misses. Call this when an absence is approved.

    Returns how many cover requests were made. Lessons that already have a
    cover request are skipped, so running it twice doesn't make duplicates.
    """
    absence = db.execute(
        """SELECT a.*, u.staff_code, u.display_name
           FROM absences a JOIN users u ON u.id = a.staff_id
           WHERE a.id = ?""",
        (absence_id,),
    ).fetchone()
    if absence is None or not absence["staff_code"]:
        return 0

    start = date.fromisoformat(absence["start_date"])
    end = date.fromisoformat(absence["end_date"])
    made = 0

    lesson_day = start
    while lesson_day <= end:
        if lesson_day.weekday() < 5:  # Monday to Friday
            day_name = ["Mon", "Tue", "Wed", "Thu", "Fri"][lesson_day.weekday()]
            lessons = db.execute(
                """SELECT t.period, t.room, t.subject, c.class_name
                   FROM timetable t
                   JOIN teachers te ON te.teacher_id = t.teacher_id
                   JOIN classes c ON c.class_id = t.class_id
                   WHERE te.staff_code = ? AND t.day = ?""",
                (absence["staff_code"], day_name),
            ).fetchall()

            for lesson in lessons:
                period = int(lesson["period"].lstrip("P"))  # "P3" -> 3
                already = db.execute(
                    """SELECT 1 FROM cover_requests
                       WHERE requested_by = ? AND lesson_date = ? AND period = ?
                         AND status != 'Cancelled'""",
                    (absence["staff_id"], lesson_day.isoformat(), period),
                ).fetchone()
                if already:
                    continue

                db.execute(
                    """INSERT INTO cover_requests
                       (requested_by, class_name, room, subject, lesson_date, period, set_work)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        absence["staff_id"],
                        lesson["class_name"],
                        lesson["room"],
                        lesson["subject"],
                        lesson_day.isoformat(),
                        period,
                        f"No set work yet. {absence['display_name']} or their department will add it.",
                    ),
                )
                made += 1
        lesson_day += timedelta(days=1)

    return made


@app.route("/cover/<int:cover_id>/cancel", methods=["POST"])
def cancel_cover(cover_id):
    db = get_db()
    request_row = db.execute(
        "SELECT * FROM cover_requests WHERE id = ? AND requested_by = ?",
        (cover_id, g.user["id"]),
    ).fetchone()

    # Only your own requests, and only ones that haven't happened yet
    if request_row is None:
        abort(404)
    if request_row["status"] not in ("Pending", "Covered") or request_row["lesson_date"] < date.today().isoformat():
        flash("That cover request can't be cancelled.")
        return redirect(url_for("home"))

    db.execute("UPDATE cover_requests SET status = 'Cancelled' WHERE id = ?", (cover_id,))

    # Tell the person who was covering it
    if request_row["covered_by"]:
        db.execute(
            """INSERT INTO changes (user_id, cover_id, type, message)
               VALUES (?, ?, 'Cancelled', ?)""",
            (
                request_row["covered_by"],
                cover_id,
                f"Your cover for {request_row['class_name']} (period {request_row['period']}, "
                f"{day(request_row['lesson_date'])}) is no longer needed",
            ),
        )
    db.commit()
    flash(f"Cover request for {request_row['class_name']}, period {request_row['period']} cancelled.")
    return redirect(url_for("home"))


# ---------- Cover desk (cover supervisor) ----------

# Subject codes used in the school timetable, grouped into faculties.
# Used to find the best person to cover a lesson.
SUBJECT_GROUPS = {
    "Science": ["Sc", "Ph", "Ch", "Bi"],
    "Maths": ["Ma", "EnMa"],
    "English": ["En", "ResitEn"],
    "Humanities": ["Hi", "Ge", "Gg", "So", "PS"],
    "Computing": ["TL", "Cs", "Cm"],
    "Engineering": ["Eg", "EngBTEC", "TLEng", "EPH", "El", "DT", "Fo"],
    "Languages": ["Gm"],
    "Creative": ["Ar", "Md"],
    "PE": ["PE", "HF"],
    "Business": ["Bs", "TLM"],
}

# If nobody from the same faculty is free, these are the next best fit
RELATED_GROUPS = {
    "Science": ["Maths"],
    "Maths": ["Science", "Computing", "Engineering"],
    "Computing": ["Maths", "Engineering"],
    "Engineering": ["Maths", "Science"],
    "English": ["Humanities"],
    "Humanities": ["English"],
    "Business": ["Maths", "Computing"],
    "Languages": ["English"],
    "Creative": [],
    "PE": [],
}

# Department names used on the Request cover form and for demo accounts
DEPARTMENT_GROUPS = {
    "Science": "Science",
    "Maths": "Maths",
    "English": "English",
    "History": "Humanities",
    "PE": "PE",
    "Art": "Creative",
}

SUBJECT_NAMES = {
    "Sc": "Science", "Ph": "Physics", "Ch": "Chemistry", "Bi": "Biology",
    "Ma": "Maths", "En": "English", "Hi": "History", "Ge": "Geography",
    "Gg": "Geography", "Gm": "German", "Ar": "Art", "PE": "PE",
    "Bs": "Business", "Cs": "Computer science", "DT": "Design and technology",
    "Fo": "Food", "So": "Sociology", "PS": "Psychology", "Md": "Media",
}


def subject_group(code):
    for group, codes in SUBJECT_GROUPS.items():
        if code in codes:
            return group
    return None


def lesson_subject(subject):
    """Return (subject code, faculty) for a cover request's subject.

    Requests from the form use department names ("Science"), so there's no exact
    subject code. Requests from the timetable use codes ("Ph A1" -> "Ph").
    """
    if subject in DEPARTMENT_GROUPS:
        return None, DEPARTMENT_GROUPS[subject]
    code = subject_code(subject)
    return code, subject_group(code)


def subject_fit(lesson_code, lesson_group, taught_codes, department):
    """How well someone fits a lesson: 3 best, 0 no match. Returns (score, reason)."""
    taught_groups = {subject_group(code) for code in taught_codes}
    taught_groups.add(DEPARTMENT_GROUPS.get(department))
    taught_groups.discard(None)

    if lesson_code and lesson_code in taught_codes:
        return 3, f"Teaches {SUBJECT_NAMES.get(lesson_code, lesson_code)}"
    if lesson_group and lesson_group in taught_groups:
        return 2, f"{lesson_group} teacher"
    related = [group for group in RELATED_GROUPS.get(lesson_group, []) if group in taught_groups]
    if related:
        return 1, f"Related subject ({', '.join(related)})"
    return 0, None


def timetable_day(iso_date):
    """Turn "2026-11-19" into "Thu". Weekends have no timetable, so they give None."""
    weekday = date.fromisoformat(iso_date).weekday()
    return WEEKDAYS[weekday] if weekday < 5 else None


def busy_staff_codes(db, day_name, period):
    """Staff codes with a lesson or a duty in this slot."""
    slot = f"P{period}"
    rows = db.execute(
        """SELECT te.staff_code FROM timetable t
           JOIN teachers te ON te.teacher_id = t.teacher_id
           WHERE t.day = ? AND t.period = ?
           UNION
           SELECT te.staff_code FROM duties d
           JOIN teachers te ON te.teacher_id = d.teacher_id
           WHERE d.day = ? AND d.period = ?""",
        (day_name, slot, day_name, slot),
    ).fetchall()
    return {row[0] for row in rows}


def absent_user_ids(db, iso_date):
    rows = db.execute(
        """SELECT staff_id FROM absences
           WHERE status = 'Approved' AND ? BETWEEN start_date AND end_date""",
        (iso_date,),
    ).fetchall()
    return {row[0] for row in rows}


def suggest_cover(db, lesson_date, period, subject=None, requested_by=None):
    """Staff who could cover this lesson, best first.

    Left out: anyone absent, already covering this period, or with a lesson or
    duty now (from the timetable), and the teacher who asked for cover.
    Order:
      1. Subject fit: teaches that subject, then same faculty, then a related
         subject (e.g. a Maths teacher for Physics), then anyone else
      2. Hours of cover already done: fewest first
      3. Fairness on the day: fewest lessons and duties that day first
    """
    day_name = timetable_day(lesson_date)
    busy = busy_staff_codes(db, day_name, period) if day_name else set()
    absent = absent_user_ids(db, lesson_date)
    covering = {
        row[0] for row in db.execute(
            """SELECT covered_by FROM cover_requests
               WHERE lesson_date = ? AND period = ? AND status = 'Covered'""",
            (lesson_date, period),
        )
    }
    covered_counts = dict(db.execute(
        """SELECT covered_by, COUNT(*) FROM cover_requests
           WHERE status = 'Covered' AND covered_by IS NOT NULL
           GROUP BY covered_by"""
    ).fetchall())
    lesson_code, lesson_group = lesson_subject(subject) if subject else (None, None)

    suggestions = []
    for person in db.execute(
        """SELECT * FROM users
           WHERE role IN ('teacher', 'cover_supervisor')
           ORDER BY display_name"""
    ):
        if person["id"] == requested_by or person["id"] in absent or person["id"] in covering:
            continue
        if person["staff_code"] and person["staff_code"] in busy:
            continue

        taught_codes = set()
        periods_today = 0
        if person["staff_code"]:
            taught_codes = {
                subject_code(row[0]) for row in db.execute(
                    """SELECT DISTINCT t.subject FROM timetable t
                       JOIN teachers te ON te.teacher_id = t.teacher_id
                       WHERE te.staff_code = ?""",
                    (person["staff_code"],),
                )
            }
            if day_name:
                periods_today = db.execute(
                    """SELECT COUNT(*) FROM (
                           SELECT t.period FROM timetable t
                           JOIN teachers te ON te.teacher_id = t.teacher_id
                           WHERE te.staff_code = ? AND t.day = ?
                           UNION
                           SELECT d.period FROM duties d
                           JOIN teachers te ON te.teacher_id = d.teacher_id
                           WHERE te.staff_code = ? AND d.day = ?)""",
                    (person["staff_code"], day_name, person["staff_code"], day_name),
                ).fetchone()[0]

        fit, fit_reason = subject_fit(lesson_code, lesson_group, taught_codes, person["department"])
        lessons_covered = covered_counts.get(person["id"], 0)
        hours_covered = lessons_covered * LESSON_HOURS

        reasons = []
        if fit_reason:
            reasons.append(fit_reason)
        if person["role"] == "cover_supervisor":
            reasons.append("Cover supervisor")
        reasons.append(f"{hours_covered} hour{'' if hours_covered == 1 else 's'} of cover so far")
        if not person["staff_code"]:
            reasons.append("No timetable linked, so check they're free")
        elif day_name is None:
            reasons.append("Weekend, no timetable")
        else:
            reasons.append(f"Busy {periods_today} of {len(PERIODS)} periods today")

        suggestions.append({
            "id": person["id"],
            "display_name": person["display_name"],
            "department": person["department"],
            "fit": fit,
            "lessons_covered": lessons_covered,
            "hours_covered": hours_covered,
            "reasons": reasons,
            "sort_key": (
                -fit,
                hours_covered,
                periods_today,
                not person["staff_code"],
                person["display_name"],
            ),
        })

    suggestions.sort(key=lambda person: person["sort_key"])
    return suggestions


def desk_date():
    """The date the cover desk is showing: ?date=YYYY-MM-DD, or today."""
    try:
        return date.fromisoformat(request.args.get("date", ""))
    except ValueError:
        return date.today()


@app.route("/cover-desk")
@roles_required("cover_supervisor", "slt")
def cover_desk():
    db = get_db()
    shown = desk_date()
    shown_iso = shown.isoformat()

    requests_for_day = db.execute(
        """SELECT c.*, absent.display_name AS absent_teacher, cover.display_name AS cover_teacher
           FROM cover_requests c
           JOIN users absent ON absent.id = c.requested_by
           LEFT JOIN users cover ON cover.id = c.covered_by
           WHERE c.lesson_date = ? AND c.status != 'Cancelled'
           ORDER BY c.period, c.class_name""",
        (shown_iso,),
    ).fetchall()

    pending = [row for row in requests_for_day if row["status"] == "Pending"]
    covered = [row for row in requests_for_day if row["status"] == "Covered"]
    declined = [row for row in requests_for_day if row["status"] == "Declined"]

    suggestions = {
        row["id"]: suggest_cover(db, shown_iso, row["period"], row["subject"], row["requested_by"])
        for row in pending
    }

    # Staff absent on this day, and how many of their timetabled lessons have no cover request yet
    absent_staff = []
    day_name = timetable_day(shown_iso)
    for row in db.execute(
        """SELECT a.id AS absence_id, a.reason, a.end_date, u.id, u.display_name, u.staff_code
           FROM absences a JOIN users u ON u.id = a.staff_id
           WHERE a.status = 'Approved' AND ? BETWEEN a.start_date AND a.end_date
           ORDER BY u.display_name""",
        (shown_iso,),
    ):
        missing = 0
        if day_name and row["staff_code"]:
            missing = db.execute(
                """SELECT COUNT(*) FROM timetable t
                   JOIN teachers te ON te.teacher_id = t.teacher_id
                   WHERE te.staff_code = ? AND t.day = ?
                     AND NOT EXISTS (
                         SELECT 1 FROM cover_requests c
                         WHERE c.requested_by = ? AND c.lesson_date = ?
                           AND c.period = CAST(SUBSTR(t.period, 2) AS INTEGER)
                           AND c.status != 'Cancelled')""",
                (row["staff_code"], day_name, row["id"], shown_iso),
            ).fetchone()[0]
        absent_staff.append({**dict(row), "missing": missing})

    # Who's free each period, for the "Free staff" panel
    free_by_period = {period: suggest_cover(db, shown_iso, period) for period in PERIODS}

    return render_template(
        "cover_desk.html",
        shown=shown,
        shown_iso=shown_iso,
        previous_day=(shown - timedelta(days=1)).isoformat(),
        next_day=(shown + timedelta(days=1)).isoformat(),
        is_today=shown == date.today(),
        is_weekend=day_name is None,
        pending=pending,
        covered=covered,
        declined=declined,
        suggestions=suggestions,
        absent_staff=absent_staff,
        free_by_period=free_by_period,
    )


def cover_request_or_404(db, cover_id):
    row = db.execute("SELECT * FROM cover_requests WHERE id = ?", (cover_id,)).fetchone()
    if row is None:
        abort(404)
    return row


@app.route("/cover-desk/<int:cover_id>/assign", methods=["POST"])
@roles_required("cover_supervisor", "slt")
def assign_cover(cover_id):
    db = get_db()
    row = cover_request_or_404(db, cover_id)
    back = url_for("cover_desk", date=row["lesson_date"])

    if row["status"] != "Pending":
        flash("That lesson isn't waiting for cover any more.")
        return redirect(back)

    # Check again that they're still free: someone may have been given this period since the page loaded
    user_id = request.form.get("user_id", type=int)
    free_ids = {s["id"] for s in suggest_cover(db, row["lesson_date"], row["period"], row["subject"], row["requested_by"])}
    if user_id not in free_ids:
        flash("That person isn't free for this lesson any more. Please choose someone else.")
        return redirect(back)

    person = db.execute("SELECT display_name FROM users WHERE id = ?", (user_id,)).fetchone()
    db.execute(
        "UPDATE cover_requests SET status = 'Covered', covered_by = ? WHERE id = ?",
        (user_id, cover_id),
    )
    db.execute(
        "INSERT INTO changes (user_id, cover_id, type, message) VALUES (?, ?, 'New', ?)",
        (user_id, cover_id,
         f"You have been asked to cover {row['class_name']}, period {row['period']} "
         f"on {day(row['lesson_date'])} in {row['room']}"),
    )
    db.commit()
    flash(f"{person['display_name']} is covering {row['class_name']}, period {row['period']}.")
    return redirect(back)


@app.route("/cover-desk/<int:cover_id>/unassign", methods=["POST"])
@roles_required("cover_supervisor", "slt")
def unassign_cover(cover_id):
    db = get_db()
    row = cover_request_or_404(db, cover_id)
    back = url_for("cover_desk", date=row["lesson_date"])

    if row["status"] not in ("Covered", "Declined"):
        return redirect(back)

    if row["covered_by"]:
        db.execute(
            "INSERT INTO changes (user_id, cover_id, type, message) VALUES (?, ?, 'Cancelled', ?)",
            (row["covered_by"], cover_id,
             f"Your cover for {row['class_name']} (period {row['period']}, "
             f"{day(row['lesson_date'])}) is no longer needed"),
        )
    db.execute(
        "UPDATE cover_requests SET status = 'Pending', covered_by = NULL WHERE id = ?",
        (cover_id,),
    )
    db.commit()
    flash(f"{row['class_name']}, period {row['period']} needs cover again.")
    return redirect(back)


@app.route("/cover-desk/<int:cover_id>/decline", methods=["POST"])
@roles_required("cover_supervisor", "slt")
def decline_cover(cover_id):
    db = get_db()
    row = cover_request_or_404(db, cover_id)
    if row["status"] == "Pending":
        db.execute("UPDATE cover_requests SET status = 'Declined' WHERE id = ?", (cover_id,))
        db.commit()
        flash(f"{row['class_name']}, period {row['period']} marked as no staff free.")
    return redirect(url_for("cover_desk", date=row["lesson_date"]))


@app.route("/cover-desk/absence/<int:absence_id>/create-cover", methods=["POST"])
@roles_required("cover_supervisor", "slt")
def create_cover_from_absence(absence_id):
    db = get_db()
    made = create_cover_for_absence(db, absence_id)
    db.commit()
    if made:
        flash(f"Made {made} cover request{'' if made == 1 else 's'} from the timetable.")
    else:
        flash("Every timetabled lesson for that absence already has a cover request.")
    return redirect(url_for("cover_desk", date=request.form.get("date", "")))


def page_for_role(user):
    """Where someone's personal link takes them."""
    if user["role"] == "teacher":
        return url_for("timetable")
    if user["role"] == "cover_supervisor":
        return url_for("cover_desk")
    if user["role"] == "admin":
        return url_for("admin")
    return url_for("home")


@app.route("/u/<name>")
def personal_link(name):
    """Each person's own link. It never signs anyone in by itself.

    Not signed in, or signed in as someone else: go to sign in, with their username filled in.
    Signed in as the right person: go to their page.
    """
    name = name.lower()
    if g.user is None or g.user["name"] != name:
        if g.user is not None:
            session.clear()
            flash("That link is for someone else. Sign in as them to use it.")
        return redirect(url_for("signin", next=request.path))
    return redirect(page_for_role(g.user))


@app.route("/signin", methods=["GET", "POST"])
def signin():
    error = None

    if request.method == "POST":
        name = request.form.get("name", "").strip().lower()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()

        if user is None or not check_password_hash(user["password_hash"], password):
            error = "That name or password is wrong."
        elif not user["active"]:
            error = "Your account is inactive. Please contact the administrator."
        else:
            session.clear()
            session["user_id"] = user["id"]
            session["since"] = user["last_seen"] or ""
            db.execute("UPDATE users SET last_seen = datetime('now') WHERE id = ?", (user["id"],))
            db.commit()
            next_page = request.args.get("next", "")
            if next_page.startswith("/") and not next_page.startswith("//"):
                return redirect(next_page)
            return redirect(url_for("home"))

    next_page = request.args.get("next", "")
    username = request.form.get("name", "")
    if not username and next_page.startswith("/u/"):
        username = next_page[len("/u/"):]
    return render_template("signin.html", error=error, username=username)


@app.route("/account/password", methods=["GET", "POST"])
def change_password():
    error = None

    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        if not check_password_hash(g.user["password_hash"], current):
            error = "Your current password is wrong."
        elif len(new) < 8:
            error = "Your new password must be at least 8 characters."
        elif new != confirm:
            error = "The new passwords don't match."
        else:
            db = get_db()
            db.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
                (generate_password_hash(new), g.user["id"]),
            )
            db.commit()
            flash("Your password has been changed.")
            return redirect(url_for("home"))

    return render_template("change_password.html", error=error)


@app.route("/signout", methods=["POST"])
def signout():
    session.clear()
    return redirect(url_for("signin"))

@app.route("/admin")
@roles_required("admin")
def admin():
    user = get_db().execute(
        """SELECT id, name, display_name, role, department, staff_code, active, last_seen
           FROM users
           ORDER BY active DESC, role, display_name"""
    ).fetchall()
    return render_template("admin.html", users=user)


def free_staff_codes(db, keep=None):
    """Timetable staff codes that no account uses yet, for suggestions on the form.

    keep: a code to include anyway (the account's own code, when editing).
    """
    codes = [row[0] for row in db.execute(
        """SELECT staff_code FROM teachers
           WHERE staff_code NOT IN (SELECT staff_code FROM users WHERE staff_code IS NOT NULL)
           ORDER BY staff_code"""
    )]
    if keep and keep not in codes:
        codes = sorted(codes + [keep])
    return codes


def read_account_form(db, form, user_id=None):
    """Read and check the add/edit account form.

    user_id: the account being edited, or None when adding one.
    Returns (values, error). error is None when everything is fine.
    """
    values = {
        "display_name": form.get("display_name", "").strip(),
        "name": form.get("name", "").strip().lower(),
        "role": form.get("role", ""),
        "department": form.get("department", "").strip(),
        "staff_code": form.get("staff_code", "").strip().upper() or None,  # empty means no timetable
    }
    staff_code = values["staff_code"]

    if not (values["display_name"] and values["name"] and values["department"]):
        return values, "Please fill in every field."
    if not values["name"].isalnum():
        return values, "Usernames can only use letters and numbers."
    if values["role"] not in ROLES:
        return values, "Please choose a role from the list."
    if staff_code is not None and not (len(staff_code) == 3 and staff_code.isalpha()):
        return values, "Staff codes are 3 letters, like XYZ."
    if staff_code is not None and db.execute(
        "SELECT 1 FROM users WHERE staff_code = ? AND id IS NOT ?", (staff_code, user_id)
    ).fetchone():
        return values, f"{staff_code} is already linked to another account."
    return values, None


def not_in_timetable_note(db, staff_code):
    """Extra message when a staff code isn't in the imported timetable yet."""
    if staff_code and not db.execute(
        "SELECT 1 FROM teachers WHERE staff_code = ?", (staff_code,)
    ).fetchone():
        return f" {staff_code} isn't in the timetable yet, so it will link up when the next timetable is imported."
    return ""


def department_list(db):
    return [row[0] for row in db.execute("SELECT DISTINCT department FROM users ORDER BY department")]


@app.route("/admin/new", methods=["GET", "POST"])
@roles_required("admin")
def admin_new():
    db = get_db()
    error = None
    form = request.form

    if request.method == "POST":
        values, error = read_account_form(db, form)
        password = form.get("password", "")
        if error is None and len(password) < 8:
            error = "The password must be at least 8 characters."
        if error is None:
            try:
                db.execute(
                    """INSERT INTO users (name, display_name, department, role, staff_code, password_hash)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (values["name"], values["display_name"], values["department"], values["role"],
                     values["staff_code"], generate_password_hash(password)),
                )
                db.commit()
            except sqlite3.IntegrityError:
                error = "That username is already taken."
            else:
                flash(f"Account created for {values['display_name']}. Username: {values['name']}."
                      + not_in_timetable_note(db, values["staff_code"]))
                return redirect(url_for("admin"))

    return render_template(
        "admin_form.html",
        error=error,
        form=form,
        roles=ROLES,
        staff_codes=free_staff_codes(db),
        departments=department_list(db),
        user=None,
    )


def account_or_404(db, user_id):
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        abort(404)
    return user


@app.route("/admin/<int:user_id>/edit", methods=["GET", "POST"])
@roles_required("admin")
def admin_edit(user_id):
    db = get_db()
    user = account_or_404(db, user_id)
    error = None
    form = request.form

    if request.method == "POST":
        values, error = read_account_form(db, form, user_id)
        # Don't let an admin remove their own admin role, or there might be no admin left
        if error is None and user_id == g.user["id"] and values["role"] != "admin":
            error = "You can't remove your own admin role."
        if error is None:
            try:
                db.execute(
                    """UPDATE users
                       SET name = ?, display_name = ?, department = ?, role = ?, staff_code = ?
                       WHERE id = ?""",
                    (values["name"], values["display_name"], values["department"], values["role"],
                     values["staff_code"], user_id),
                )
                db.commit()
            except sqlite3.IntegrityError:
                error = "That username is already taken."
            else:
                flash(f"Saved changes to {values['display_name']}."
                      + not_in_timetable_note(db, values["staff_code"]))
                return redirect(url_for("admin"))

    return render_template(
        "admin_form.html",
        error=error,
        form=form,
        roles=ROLES,
        staff_codes=free_staff_codes(db, keep=user["staff_code"]),
        departments=department_list(db),
        user=user,
    )


@app.route("/admin/<int:user_id>/reset-password", methods=["POST"])
@roles_required("admin")
def admin_reset_password(user_id):
    db = get_db()
    user = account_or_404(db, user_id)

    # A random temporary password. They have to change it the next time they sign in.
    temporary = secrets.token_urlsafe(8)
    db.execute(
        "UPDATE users SET password_hash = ?, must_change_password = 1 WHERE id = ?",
        (generate_password_hash(temporary), user_id),
    )
    db.commit()
    flash(f"New temporary password for {user['display_name']} ({user['name']}): {temporary} "
          "Give it to them now: it won't be shown again. They'll choose their own when they sign in.")
    return redirect(url_for("admin_edit", user_id=user_id))


@app.route("/admin/<int:user_id>/toggle-active", methods=["POST"])
@roles_required("admin")
def admin_toggle_active(user_id):
    db = get_db()
    user = account_or_404(db, user_id)

    if user_id == g.user["id"]:
        flash("You can't disable your own account.")
        return redirect(url_for("admin_edit", user_id=user_id))

    db.execute("UPDATE users SET active = 1 - active WHERE id = ?", (user_id,))
    db.commit()
    if user["active"]:
        flash(f"{user['display_name']}'s account is disabled. They can't sign in, but their cover history is kept.")
    else:
        flash(f"{user['display_name']}'s account is active again.")
    return redirect(url_for("admin"))


@app.route("/admin/timetable", methods=["GET", "POST"])
@roles_required("admin")
def admin_timetable():
    db = get_db()
    error = None

    if request.method == "POST":
        upload = request.files.get("timetable")
        if upload is None or upload.filename == "":
            error = "Please choose a CSV file to upload."
        elif not upload.filename.lower().endswith(".csv"):
            error = "The file must be a .csv. In Excel, use File > Save As > CSV UTF-8."
        else:
            data = upload.read()
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = None
                error = "The file couldn't be read. In Excel, save it as CSV UTF-8 and try again."

            if text is not None:
                codes_before = {row[0] for row in db.execute("SELECT staff_code FROM teachers")}
                try:
                    counts = import_timetable(db, io.StringIO(text, newline=""))
                except (ValueError, KeyError, csv.Error) as problem:
                    error = f"The timetable wasn't changed. {problem}"
                else:
                    # Keep the uploaded file, so rebuilding the demo database uses the latest timetable
                    if os.path.exists(TIMETABLE_CSV):
                        os.replace(TIMETABLE_CSV, TIMETABLE_CSV.replace(".csv", ".previous.csv"))
                    with open(TIMETABLE_CSV, "wb") as f:
                        f.write(data)

                    newly_linked = db.execute(
                        """SELECT u.display_name, u.staff_code FROM users u
                           JOIN teachers te ON te.staff_code = u.staff_code
                           ORDER BY u.display_name"""
                    ).fetchall()
                    newly_linked = [row for row in newly_linked if row["staff_code"] not in codes_before]
                    message = (f"Timetable imported: {counts['lessons']} lessons, {counts['duties']} duties, "
                               f"{counts['staff']} staff codes.")
                    if newly_linked:
                        message += " Now linked: " + ", ".join(
                            f"{row['display_name']} ({row['staff_code']})" for row in newly_linked) + "."
                    flash(message)
                    return redirect(url_for("admin_timetable"))

    summary = {
        "lessons": db.execute("SELECT COUNT(*) FROM timetable").fetchone()[0],
        "duties": db.execute("SELECT COUNT(*) FROM duties").fetchone()[0],
        "staff": db.execute("SELECT COUNT(*) FROM teachers").fetchone()[0],
    }
    waiting = db.execute(
        """SELECT display_name, staff_code FROM users
           WHERE staff_code IS NOT NULL
             AND staff_code NOT IN (SELECT staff_code FROM teachers)
           ORDER BY display_name"""
    ).fetchall()
    no_code = db.execute(
        "SELECT display_name FROM users WHERE staff_code IS NULL AND role != 'admin' ORDER BY display_name"
    ).fetchall()
    return render_template(
        "admin_timetable.html", error=error, summary=summary, waiting=waiting, no_code=no_code,
    )


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
