# How to undo things

Until now this project had no way to go back. When a change was bad, the only
option was to describe what it used to look like and rebuild it by hand — which
is how a stylesheet got destroyed earlier in the build and recovered by luck
from a stray file in a temp folder.

That is fixed. There is now a git repository in the project root.

---

## Switching between the two colour schemes

Two tags exist:

```
git checkout blue-accent   -- site/     # steel blue (current)
git checkout violet-accent -- site/     # the original violet
```

Then restart: `python run.py`, and hard-refresh the browser with
**Ctrl+Shift+R** — the stylesheet is cached and you will otherwise see the old
colours and think it failed.

The `-- site/` at the end matters. It changes only the site folder and leaves
everything else alone.

---

## Undoing the last change

```
git checkout -- .          # throw away all uncommitted edits
git reset --hard HEAD~1    # undo the last commit entirely
```

`--hard` deletes work permanently. Use the first form unless you are certain.

---

## Before I change anything

Commit first. It takes five seconds and it is the entire safety net:

```
git add -A
git commit -m "before <whatever we are about to try>"
```

If the result is wrong:

```
git reset --hard HEAD
```

and you are back exactly where you were, every file, no discussion about what
it used to look like.

---

## Naming a version you might want back

```
git tag matte-experiment
```

Then `git checkout matte-experiment -- site/` returns to it at any point. Tags
cost nothing and are worth adding before any large visual change.

---

## Seeing what actually changed

```
git status                 # which files differ
git diff                   # the exact lines, before committing
git diff --stat HEAD~1     # what the last commit touched
git log --oneline          # the history
```

`git diff` is the useful one. If a change is larger than expected, that is
where it shows up — before it is committed rather than after something breaks.

---

## What is deliberately not tracked

`.gitignore` excludes the database, `.secret_key`, `.env`, `__pycache__` and
the virtualenv. Committing a secret key to a repository is difficult to undo
properly, so it is excluded from the start rather than removed later.

Everything else is tracked: 87 files.

---

## The one thing this does not protect against

The repository lives inside the project folder. Delete the folder and the
history goes with it.

Once you are ready, push to a private GitHub repo — that turns this from an
undo button into an actual backup. Until then, the zip in your downloads folder
is the only copy that survives losing the machine.
