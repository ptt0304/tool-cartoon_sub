# Cartoon_Sub Development Rules

## Goal

Develop this repository with minimal context/token usage while preserving
correctness and existing architecture.

## Repository navigation

Do NOT scan the whole repository by default.

Start from:
1. files explicitly named in the task
2. relevant src module
3. relevant tests
4. only then follow dependencies if necessary

Documentation index:
docs/INDEX.md

Read only documentation relevant to the current task.

Do NOT read these by default:
- .venv/
- images/screenshots
- docs/phase5_check/
- generated outputs
- caches
- media files
- unrelated phase documents

## Architecture

Preserve existing architecture.

Do not:
- redesign unrelated modules
- refactor working code without need
- implement future phases
- duplicate existing functionality
- rewrite entire files when a small patch is sufficient

Prefer:
small incremental patches.

## Context efficiency

Do not repeatedly reopen unchanged large files unless required.

Use targeted searches for:
- class names
- function names
- model fields
- config keys

Avoid repository-wide searches when the task already identifies the module.

## Tests

Run targeted tests first.

Examples:

translation change:
tests related to translation only

segmentation change:
segmentation tests only

mask change:
mask/render tests only

Run the full test suite only when:
- a shared/core model changes
- serialization changes
- targeted tests show possible cross-module regression

## Responses

After implementing, do NOT paste source code into chat.

Final response should contain only:
- changed files
- tests run and result
- important compatibility issue if any
- unresolved issue if any

Keep it concise.

## Stop condition

When the requested task is implemented and relevant tests pass:
STOP.

Do not continue into the next phase automatically.