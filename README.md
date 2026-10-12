# HiveMind Ed — Cover System (Flask practice)

Written by me as practice for my T Level project.

## What it does
- **Home** and **Cover** pages that share one base template, styled with my own CSS
- A **cover-request form** (teacher, class, room, subject) that uses GET to show the form and POST to submit it. It then shows a confirmation message.
- A **Sign In** page layout with a show/hide password button (JavaScript). It doesn't log anyone in yet.
- `schema.sql` and `setup_db.py`, which create a SQLite database with `users` and `cover_requests` tables. The app doesn't use the database yet.

## How to run it
```
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS/Linux
pip install -r requirements.txt
python setup_db.py              # optional – creates hivemind.db
python app.py
```
Then open http://127.0.0.1:5000

## What I learned
- Flask routes
- Jinja template inheritance with `extends` and blocks
- `url_for`
- Forms and `request.form`
- CSS flexbox
- Sticky footer
- Accessible form labels
- Colour contrast

## Next steps
- Save cover requests to SQLite (the tables exist, but the app doesn't write to them yet)
- Make the Sign In page work
