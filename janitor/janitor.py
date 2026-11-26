#!/usr/bin/env python3
"""
flag-janitor - an AI-agent-ready cleanup agent for stale Flagsmith feature flags.

Commands:
  scan    List flags across all environments and classify staleness.
  plan    Scan + locate code references in a target repo, write PLAN.md and plan.json.
  apply   Execute an APPROVED plan: open a cleanup PR on the target repo and archive
          the flags in Flagsmith. Refuses to run without human approval recorded
          in the plan file.

The agent NEVER changes code or flags on its own. `apply` requires plan.json to
contain "approved": true and a non-empty "approved_by". See SAFETY.md.

Stdlib only. Configuration via environment variables (see .env.example).
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error

FS_API = "https://api.flagsmith.com/api/v1"
GH_API = "https://api.github.com"
TRACE_FILE = "trace.jsonl"


def trace(action, **details):
    entry = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "action": action, **details}
    with open(TRACE_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"[trace] {action}: {json.dumps(details)[:200]}")


def http(method, url, token, body=None, auth_style="bearer"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if auth_style == "bearer":
        req.add_header("Authorization", f"Token {token}")
    else:
        req.add_header("Authorization", f"Api-Key {token}")
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HTTP {e.code} {method} {url}: {e.read()[:300]}")


def fs(method, path, key, body=None):
    return http(method, FS_API + path, key, body, auth_style="apikey")


def gh(method, path, pat, body=None):
    return http(method, GH_API + path, pat, body, auth_style="bearer")


def get_environments(project_id, key):
    return fs("GET", f"/projects/{project_id}/environments/", key)


def get_features(project_id, key):
    out = []
    url = f"/projects/{project_id}/features/"
    while url:
        page = fs("GET", url, key)
        if isinstance(page, dict) and "results" in page:
            out.extend(page["results"])
            url = page.get("next")
            if url:
                url = url.replace(FS_API, "")
        else:
            out.extend(page)
            url = None
    return out


def get_feature_states(env_api_key, key):
    out = []
    url = f"/environments/{env_api_key}/featurestates/"
    while url:
        page = fs("GET", url, key)
        if isinstance(page, dict) and "results" in page:
            out.extend(page["results"])
            url = page.get("next")
            if url:
                url = url.replace(FS_API, "")
        else:
            out.extend(page)
            url = None
    return out


def get_audit_log(project_id, key, limit=200):
    try:
        page = fs("GET", f"/audit/?project={project_id}&page_size={limit}", key)
        return page.get("results", page) if isinstance(page, dict) else page
    except SystemExit:
        return []


def classify(features, states_by_env, audit, min_age_days):
    """Classify each flag. Returns list of dicts with a `verdict`:
    bake_in (enabled everywhere), removal (disabled everywhere), active (skip)."""
    last_change = {}
    for row in audit:
        msg = (row.get("log") or "")
        when = row.get("created_date")
        m = re.search(r"(?:Flag|Feature)\s+(.+?)\s+(?:updated|created|changed|deleted)", msg, re.I)
        if m and when:
            name = m.group(1).strip().strip('"')
            if name not in last_change or when > last_change[name]:
                last_change[name] = when
    now = dt.datetime.now(dt.timezone.utc)
    results = []
    for feat in features:
        name = feat["name"]
        env_states = []
        for env_name, states in states_by_env.items():
            st = next((s for s in states if s.get("feature") == feat["id"]), None)
            env_states.append({"env": env_name, "enabled": bool(st and st.get("enabled")),
                               "value": st.get("feature_state_value") if st else None})
        enabled_everywhere = all(s["enabled"] for s in env_states) and env_states
        disabled_everywhere = not any(s["enabled"] for s in env_states) and env_states
        changed = last_change.get(name)
        age_days = None
        if changed:
            try:
                age_days = (now - dt.datetime.fromisoformat(changed.replace("Z", "+00:00"))).days
            except ValueError:
                pass
        old_enough = (age_days is None) or (age_days >= min_age_days)
        if enabled_everywhere and old_enough:
            verdict = "bake_in"
        elif disabled_everywhere and old_enough:
            verdict = "removal"
        else:
            verdict = "active"
        results.append({"flag": name, "id": feat["id"], "verdict": verdict,
                        "last_change": changed, "age_days": age_days, "env_states": env_states})
    return results


def cmd_scan(args):
    key = os.environ["FLAGSMITH_API_KEY"]
    envs = get_environments(args.project, key)
    states_by_env = {e["name"]: get_feature_states(e["api_key"], key) for e in envs}
    feats = get_features(args.project, key)
    audit = get_audit_log(args.project, key)
    classified = classify(feats, states_by_env, audit, args.min_age_days)
    print(json.dumps(classified, indent=2))
    trace("scan", flags=len(classified),
          stale=[c["flag"] for c in classified if c["verdict"] != "active"])
    return classified


CODE_PATTERNS = [
    # JS/TS: flagsmith.getValue("flag") / flagsmith.hasFeature("flag") / useFlags(['flag'])
    (re.compile(r'flagsmith\.(?:getValue|hasFeature|isFeatureEnabled)\(\s*[\'"](?P<flag>[a-zA-Z0-9_\-]+)[\'"]\s*\)'), "js"),
    (re.compile(r'[\'"](?P<flag>[a-zA-Z0-9_\-]+)[\'"]\s*\)\s*/[/*]\s*flagsmith'), "js"),
]


def find_code_refs(repo_path, flag_names):
    """Locate flag-key string literals in source files."""
    refs = {}
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "dist", "build")]
        for fn in files:
            if not fn.endswith((".js", ".jsx", ".ts", ".tsx", ".py", ".html")):
                continue
            path = os.path.join(root, fn)
            try:
                with open(path, encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                for flag in flag_names:
                    if re.search(r'[\'"`]' + re.escape(flag) + r'[\'"`]', line):
                        refs.setdefault(flag, []).append(
                            {"file": os.path.relpath(path, repo_path), "line": i,
                             "text": line.strip()[:160]})
    return refs


def cmd_plan(args):
    classified = cmd_scan(args)
    stale = [c for c in classified if c["verdict"] in ("bake_in", "removal")]
    refs = find_code_refs(args.repo, [c["flag"] for c in stale])
    actions = []
    for c in stale:
        for ref in refs.get(c["flag"], []):
            actions.append({"flag": c["flag"], "verdict": c["verdict"], **ref})
    plan = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "project": args.project,
        "approved": False,
        "approved_by": "",
        "stale_flags": stale,
        "code_actions": actions,
        "flag_actions": [{"flag": c["flag"], "action": "archive_in_flagsmith"} for c in stale],
    }
    with open("plan.json", "w") as f:
        json.dump(plan, f, indent=2)
    with open("PLAN.md", "w") as f:
        f.write("# Flag Janitor cleanup plan\n\n")
        f.write("Review every line. To approve, set `approved: true` and your name in "
                "`approved_by` in plan.json, then run `apply`.\n\n")
        for c in stale:
            f.write(f"## {c['flag']} -> {c['verdict']}\n")
            for s in c["env_states"]:
                f.write(f"- {s['env']}: {'enabled' if s['enabled'] else 'disabled'}\n")
            for ref in refs.get(c["flag"], []):
                f.write(f"- code: `{ref['file']}:{ref['line']}` {ref['text']}\n")
            f.write("- flagsmith: archive flag after PR merges\n\n")
    trace("plan", stale=len(stale), code_actions=len(actions))
    print("Wrote PLAN.md and plan.json (approved=false). Review, approve, then apply.")


def replace_flag_in_line(line, flag, verdict):
    """Bake the flag outcome into a flagsmith SDK call. Conservative: only rewrites
    direct SDK calls; anything else is left for the human (listed in the PR body)."""
    baked = "true" if verdict == "bake_in" else "false"
    new = re.sub(r'flagsmith\.(?:getValue|hasFeature|isFeatureEnabled)\(\s*[\'"]'
                 + re.escape(flag) + r'[\'"]\s*\)', baked, line)
    if new != line:
        return new, True
    return line, False


def cmd_apply(args):
    with open("plan.json") as f:
        plan = json.load(f)
    if not plan.get("approved") or not plan.get("approved_by"):
        raise SystemExit("REFUSED: plan.json is not approved. A human must set "
                         "approved=true and approved_by. See SAFETY.md.")
    approver = plan["approved_by"]
    key = os.environ["FLAGSMITH_API_KEY"]
    pat = os.environ["GITHUB_TOKEN"]
    repo = os.environ["GITHUB_REPO"]  # owner/name
    trace("apply_start", approved_by=approver)

    # 1. code edits on a new branch
    branch = "flag-janitor/cleanup-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    subprocess.run(["git", "-C", args.repo, "checkout", "main"], check=True)
    subprocess.run(["git", "-C", args.repo, "checkout", "-b", branch], check=True)
    changed_files = []
    unhandled = []
    for act in plan["code_actions"]:
        path = os.path.join(args.repo, act["file"])
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        i = act["line"] - 1
        if i >= len(lines):
            unhandled.append(act)
            continue
        new_line, ok = replace_flag_in_line(lines[i], act["flag"], act["verdict"])
        if ok:
            lines[i] = new_line
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            if act["file"] not in changed_files:
                changed_files.append(act["file"])
        else:
            unhandled.append(act)
    if not changed_files:
        raise SystemExit("No automatic code edits were safe to make. See plan; aborting before PR.")
    subprocess.run(["git", "-C", args.repo, "add"] + changed_files, check=True)
    msg = f"chore(flags): bake in/remove stale flags ({', '.join(sorted({a['flag'] for a in plan['code_actions']}))})\n\nProposed by flag-janitor, approved by {approver}."
    subprocess.run(["git", "-C", args.repo, "commit", "-m", msg], check=True)
    subprocess.run(["git", "-C", args.repo, "push", "-u", "origin", branch], check=True)

    # 2. open the PR
    flags_txt = "\n".join(f"- `{c['flag']}` -> {c['verdict']}" for c in plan["stale_flags"])
    unhandled_txt = ("\n\nLeft for a human (no safe automatic rewrite):\n"
                     + "\n".join(f"- `{a['file']}:{a['line']}` ({a['flag']})" for a in unhandled)
                     if unhandled else "")
    pr = gh("POST", f"/repos/{repo}/pulls", pat, {
        "title": "flag-janitor: stale flag cleanup",
        "head": branch,
        "base": args.base,
        "body": (f"Stale Flagsmith flags, proposed by flag-janitor and approved by {approver}.\n\n"
                 f"{flags_txt}\n\nFlags are archived in Flagsmith after this PR is opened."
                 f"{unhandled_txt}\n\nFull decision trail in trace.jsonl."),
    })
    trace("pr_opened", url=pr["html_url"])

    # 3. archive flags in Flagsmith
    for c in plan["stale_flags"]:
        fs("PATCH", f"/projects/{plan['project']}/features/{c['id']}/", key,
           {"is_archived": True})
        trace("flag_archived", flag=c["flag"])
    print(f"PR: {pr['html_url']}")
    trace("apply_done")


def main():
    p = argparse.ArgumentParser(prog="flag-janitor")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("scan", "plan"):
        sp = sub.add_parser(name)
        sp.add_argument("--project", type=int, default=int(os.environ.get("FLAGSMITH_PROJECT_ID", "0")))
        sp.add_argument("--repo", default=".")
        sp.add_argument("--min-age-days", type=int, default=14)
    sp = sub.add_parser("apply")
    sp.add_argument("--repo", default=".")
    sp.add_argument("--base", default="main")
    args = p.parse_args()
    {"scan": cmd_scan, "plan": cmd_plan, "apply": cmd_apply}[args.cmd](args)


if __name__ == "__main__":
    main()
