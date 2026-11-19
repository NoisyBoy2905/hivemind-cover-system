DROP TABLE IF EXISTS duties;
DROP TABLE IF EXISTS timetable;
DROP TABLE IF EXISTS classes;
DROP TABLE IF EXISTS teachers;
DROP TABLE IF EXISTS changes;
DROP TABLE IF EXISTS cover_requests;
DROP TABLE IF EXISTS absences;
DROP TABLE IF EXISTS users;

CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    department TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'teacher'
        CHECK (role IN ('teacher', 'cover_supervisor', 'slt', 'admin')),
    staff_code TEXT UNIQUE,
    last_seen TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE absences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    staff_id INTEGER NOT NULL REFERENCES users(id),
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Pending'
        CHECK (status IN ('Pending', 'Approved', 'Declined')),
    reason TEXT NOT NULL
        CHECK (reason IN ('Illness', 'Training', 'Appointment', 'School trip')),
    reviewed_by INTEGER REFERENCES users(id),
    CHECK (end_date >= start_date)
);

CREATE TABLE cover_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requested_by INTEGER NOT NULL REFERENCES users(id),
    covered_by INTEGER REFERENCES users(id),
    class_name TEXT NOT NULL,
    room TEXT NOT NULL,
    subject TEXT NOT NULL,
    lesson_date TEXT NOT NULL,
    period INTEGER NOT NULL,
    set_work TEXT NOT NULL,
    attachment TEXT,
    resources TEXT,
    status TEXT NOT NULL DEFAULT 'Pending'
        CHECK (status IN ('Pending', 'Covered', 'Declined', 'Cancelled')),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    cover_id INTEGER REFERENCES cover_requests(id),
    type TEXT NOT NULL CHECK (type IN ('New', 'Room change', 'Cancelled')),
    message TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- School timetable, filled from timetable.csv by import_tt.py
CREATE TABLE teachers (
    teacher_id INTEGER PRIMARY KEY AUTOINCREMENT,
    staff_code TEXT NOT NULL UNIQUE,
    staff_type TEXT NOT NULL
);

CREATE TABLE classes (
    class_id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_name TEXT NOT NULL UNIQUE
);

CREATE TABLE timetable (
    timetable_id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL,
    teacher_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    period TEXT NOT NULL,
    room TEXT NOT NULL,
    subject TEXT NOT NULL,
    FOREIGN KEY (class_id) REFERENCES classes(class_id) ON DELETE CASCADE,
    FOREIGN KEY (teacher_id) REFERENCES teachers(teacher_id) ON DELETE CASCADE,
    UNIQUE (teacher_id, day, period)
);

CREATE TABLE duties (
    duty_id INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    period TEXT NOT NULL,
    duty TEXT NOT NULL,
    FOREIGN KEY (teacher_id) REFERENCES teachers(teacher_id) ON DELETE CASCADE
);
