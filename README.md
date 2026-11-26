# flag-janitor

An agent that hunts stale Flagsmith feature flags and cleans them up **with a
human approving every change**. Built for Toggle Wars (WeMakeDevs × Flagsmith),
Track 02: AI Agents and Feature Flags.

Stale flags are quiet tech debt: a flag that has been on for everyone for months
still costs a branch on every read, and a flag that is off everywhere leaves a
dead code path nobody dares to delete. flag-janitor finds both, shows you exactly
what it wants to change, and only acts after you say yes.

## What it does

1. **scan** (read-only) — lists every flag across every environment via the
   Flagsmith Management API and classifies each one:
   - `bake_in` — enabled everywhere, fully rolled out. Bake it in, drop the branch.
   - `removal` — disabled everywhere, dead code. Remove it.
   - `active` — anything in between. The janitor leaves these alone.
2. **plan** (read-only) — additionally greps your repo for the stale flag keys and
   writes `PLAN.md` + `plan.json`: every code site it found and the Flagsmith
   action it proposes.
3. **you review** — edit `plan.json`: `approved: true`, your name in `approved_by`.
   Change or delete anything you disagree with first.
4. **apply** — pushes a cleanup branch, opens a PR with the safe automatic edits
   (ambiguous spots are listed for you instead), and archives the flags in
   Flagsmith. Full trail in `trace.jsonl`.

## Why an agent and not a script

The judgement is the point: deciding which flags are *actually* done (vs. mid-
rollout), matching them to real code, and knowing where to stop. It stops early
and often — see [SAFETY.md](SAFETY.md). A human approves every change before it
happens, and everything it does lands in a reviewable PR and an append-only
trace file.

## Quick start

```bash
cp .env.example .env   # fill in your keys
set -a; . ./.env; set +a

python3 janitor/janitor.py scan                      # what do my flags look like?
python3 janitor/janitor.py plan --repo path/to/app   # what would it change?
# review PLAN.md, edit plan.json -> approved: true, approved_by: "you"
python3 janitor/janitor.py apply --repo path/to/app  # PR opened, flags archived
```

No dependencies beyond Python 3.9+ (stdlib only) and git. For the demo flags and
app see `examples/demo-app/`.

## Demo

`examples/demo-app/` is a tiny shop page with three flags in the demo Flagsmith
project: one fully rolled out, one dead, one mid-experiment. The demo video shows
scan → plan → human approval → PR, and the mid-experiment flag being correctly
left alone.
