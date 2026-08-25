# Repository Guidelines

## Project Structure & Module Organization

This repository is an initial Python scaffold. Keep application code in a clear
top-level package directory (for example, `dochazka/`) and place automated tests
in `tests/`. Put reusable fixtures in `tests/conftest.py`; keep sample input or
non-code assets in a dedicated `assets/` or `tests/fixtures/` directory. Avoid
committing generated files, virtual environments, or local secrets.

When adding a feature, keep its related modules together and use small,
single-purpose files. For example, use `dochazka/services/attendance.py` rather
than collecting unrelated business logic in one module.

## Build, Test, and Development Commands

No build or test tooling is committed yet. Once dependencies are introduced,
create and activate a virtual environment, then install the declared
dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pytest
```

Use `python -m pytest` to run the full test suite. Add any project-specific run,
format, or lint commands to this file when their configuration is committed.

## Coding Style & Naming Conventions

Target modern Python and follow PEP 8: four spaces for indentation, UTF-8 text,
and descriptive names. Use `snake_case` for modules, functions, variables, and
test files; `PascalCase` for classes; and `UPPER_SNAKE_CASE` for constants. Add
type hints to public functions and concise docstrings where behavior is not
obvious. Prefer standard-library solutions before adding dependencies.

If formatting or linting tools such as Ruff or Black are added, commit their
configuration and run them before opening a pull request.

## Testing Guidelines

Use `pytest` for new tests. Name files `test_*.py` and test functions
`test_<behavior>()`, e.g. `test_rejects_missing_employee_id`. Cover normal
behavior, validation failures, and important edge cases. Keep tests isolated:
do not depend on machine-specific paths, clocks, network services, or existing
local data.

## Commit & Pull Request Guidelines

The existing history contains only the initial commit, so no established commit
format is available. Use short, imperative subjects such as `Add attendance
import validation`; keep commits focused on one logical change. Pull requests
should summarize the change, note test commands run, link relevant issues, and
include screenshots or example output when a user-facing workflow changes.
