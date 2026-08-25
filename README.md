# Docházka timeline demo

Run the interactive one-day attendance editor with:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

The project dependencies belong in `.venv`; do not install them into your
user-wide Python environment. After the environment has been created, activate
it and run `python app.py` on later sessions.

Click anywhere on the 24-hour timeline to set the red insertion cursor. Insert
an 8-hour work block to make room at the cursor, splitting an interval when the
cursor is inside it. Insert a four-hour half-day vacation block to overwrite
the existing intervals in that period. Blocks can be moved or resized to the
nearest minute.
