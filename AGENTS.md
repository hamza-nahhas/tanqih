# Working on a recording with a coding agent

This file is for coding agents (Claude Code, Codex, Gemini CLI, ...) that a user asks to review the
cuts of a recording. The user reviews every cut by ear in the interface afterwards, so the job is to
put each cut in the right place and explain why. Do not make the decision for them.

## Commands

Run from the repository root. Use `uv run python -m tanqih ...`, or `.venv/Scripts/python` on
Windows and `.venv/bin/python` on macOS. Times are seconds or `h:mm:ss(.s)`.

```
python -m tanqih items  <file>                 # items: id, status, current range, label, reviewer's time
python -m tanqih peek   <file> <from> <to>     # timed transcript lines, silent gaps, loud sounds
python -m tanqih words  <file> <from> <to>     # one word per line: start end probability word
python -m tanqih prompt <file> [--all]         # the same text the interface gives to chat assistants
python -m tanqih apply  <file> proposals.json [--force]
python -m tanqih check  <file>                 # approved cuts: edge inside a word, speech repeated after the join, ...
python -m tanqih.worker transcribe <file> <from> <to>   # transcribe a range that has no transcript yet
python -m tanqih prepare <file>                # analyse + transcribe around every item (if not done)
```

`<file>` is the recording itself, for example `D:\Lectures\Lesson 05.mp3`. Its working files live in
`.tanqih/<file name>/` next to it: `state.json` (items and decisions), `transcript.json`,
`analysis.json` (pauses, loudness). Do not edit `state.json` by hand. Use `apply`.

## proposals.json

```json
[
  {"id": "L3a9f21", "start": "1:02:03.4", "end": "1:02:30.1", "confidence": "high",
   "reason": "Last kept words: «...». First kept words after the cut: «...»."},
  {"id": "L77d0e2", "status": "not_found", "reason": "What was found around the given time."},
  {"id": "new-1", "label": "كلام جانبي", "start": "1:10:05.2", "end": "1:10:19.8",
   "confidence": "medium", "reason": "..."}
]
```

- `confidence`: `high` when the place and both edges are clear, `medium` when one edge is uncertain,
  and `low` otherwise.
- Write `reason` in the language of the recording. Quote the last words kept before the cut and the
  first words kept after it.
- `apply` moves each edge into the nearest pause within 0.6 s. It leaves items the user already
  edited alone, unless you pass `--force`, which you should do only when the user asks.
- If the interface is open, the user must reload the page to see the new proposals.

## How to place a cut

1. Treat each reviewer's time as a search window, not a cut point. Reviewers can be up to two
   minutes off, and some write only the moment of the interruption.
2. Some reviewers write the remaining time from a countdown player. The parser already converts lines
   marked «متبقي» or «معكوس». If an item's times look mirrored (the described event is not near the
   given time but appears near `duration - time`), say so in `reason`.
3. Start the cut right after the last word that stays and end it right before the first word that
   stays. Never cut inside a word. Use `words` to find the exact edges.
4. The join must read naturally. No half sentence may be left hanging.
5. After an interruption, the reader or the teacher often re-reads the sentence that was cut off.
   Extend the cut from the first, interrupted reading to just before the re-reading, so that nothing
   is said twice after the cut.
6. Items with action `decide` (benefit, admonition, question): set the edges only. The status stays
   `pending` for the user.
7. The transcript sometimes skips whole stretches of speech. In `peek`, a long gap with high
   "النشاط الصوتي" (activity) probably holds speech. Transcribe that range again with
   `python -m tanqih.worker transcribe <file> <from> <to>` before concluding.
8. Check suspected typos in a reviewer's times before "correcting" them. The written time has more
   often been right.
9. If something clearly unwanted lies inside a window you read but is not in the list, propose it
   as `new-...`.
10. After the user approves cuts, run `check`. A "repeat" warning nearly always means rule 5 was
    missed.

## Changing the code

- There is no build step. The UI is plain HTML, CSS and JS in `tanqih/ui/`, served by
  `tanqih/server.py` (standard library only). Long jobs run as subprocesses of `tanqih/worker.py`
  and report through `PROGRESS <pct> <message>` lines.
- Interface text is Arabic and right-to-left.
- Tests: `uv run python -m unittest discover tests` (set `TANQIH_TEST_WHISPER=1` to also load the
  tiny speech model). GitHub Actions runs them on Windows, macOS (Apple Silicon and Intel) and
  Linux, and checks that the launchers open the interface.
- Releases: bump `__version__` in `tanqih/__init__.py` and `pyproject.toml`, run `uv lock`, then
  tag `vX.Y.Z` and publish a GitHub release. The interface tells users about it.
- Keep it working on Windows and macOS: use pathlib, pass ffmpeg arguments as lists, and do not
  rely on shell features.
