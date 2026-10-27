#!/usr/bin/env python3
"""Maintains the milestone 1 spec folder.

  python3 spec_tool.py build                       regenerate single-file/ from staged/ (shared files and rule text)
  python3 spec_tool.py lint                        syntax, README coverage and rule-id checks for both layouts
  python3 spec_tool.py stage <single-file|staged> <both|A|B>
                                                   put one layout loose in milestone-1-specs/ for a run
  python3 spec_tool.py verify                      while staged: confirm the loose files still match the master copy
  python3 spec_tool.py restore                     after the run: remove the loose copy, put both layouts back
  python3 spec_tool.py status                      show what milestone-1-specs/ holds right now

staged/ is the source of truth for rules and check code; single-file/ keeps its own README.md,
check entry points and the hand-written header above the marker line in roles.md and channels.md.
"""
import hashlib
import importlib
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SPECS = os.path.normpath(os.path.join(HERE, "..", "course-staff-files", "final_task", "environment",
                                      "milestone-specs", "milestone-1-specs"))
# Master copy of both layouts while a run is staged. It lives outside the iCloud-synced Desktop because
# iCloud deleted files that were moved out of and back into the synced tree; only copies are made now.
HOLD = os.path.expanduser("~/.cache/milestone1-spec-master")
STAGED = os.path.join(HOLD, "STAGED.txt")
READMES = os.path.join(HERE, "run-readmes")
LINK = os.path.join(HERE, "spec")
LINK_TARGET = "../course-staff-files/final_task/environment/milestone-specs/milestone-1-specs"
LAYOUTS = ("single-file", "staged")
COMPONENTS = {"A": "roles", "B": "channels"}
MARKER = "<!-- Everything below this line is generated"
SHARED = ["common/architecture.md", "common/checklib.py", "common/restart.sh",
          "roles/checks/roles_rules.py", "channels/checks/channels_rules.py"]
TAIL = "Every request must answer within 5 seconds."
RULE_ID = re.compile(r"^([AB]\d\.\d+) \*\*", re.M)
NO_CACHE = shutil.ignore_patterns("__pycache__", "*.pyc")


def die(msg):
    print(f"error: {msg}")
    sys.exit(1)


def layout_dir(layout):
    """The layout's current home: inside milestone-1-specs/, or the master copy while a run is staged."""
    for base in ((HOLD, SPECS) if os.path.exists(STAGED) else (SPECS, HOLD)):
        if os.path.isdir(os.path.join(base, layout)):
            return os.path.join(base, layout)
    die(f"cannot find the {layout} layout in {SPECS} or {HOLD}")


def staged_rules(staged, comp):
    """Concatenated '## Rules' sections of staged/<comp>/<comp>-N.md, in step order."""
    files = sorted((f for f in os.listdir(os.path.join(staged, comp)) if re.fullmatch(rf"{comp}-\d+\.md", f)),
                   key=lambda f: int(re.search(r"(\d+)", f).group(1)))
    parts = []
    for f in files:
        text = open(os.path.join(staged, comp, f)).read()
        if "\n## Rules\n" not in text:
            die(f"{comp}/{f} has no '## Rules' heading")
        parts.append(text.split("\n## Rules\n", 1)[1].replace(TAIL, "").strip())
    return "\n\n".join(parts) + f"\n\n{TAIL}\n"


def generated_md(single, staged, comp):
    path = os.path.join(single, comp, f"{comp}.md")
    text = open(path).read()
    if MARKER not in text:
        die(f"{path} lost its marker line ({MARKER} ...)")
    end_of_marker = text.index("\n", text.index(MARKER)) + 1
    return text[:end_of_marker] + "\n" + staged_rules(staged, comp)


def cmd_build():
    if os.path.exists(STAGED):
        die("a run is staged; run 'restore' first")
    single, staged = os.path.join(SPECS, "single-file"), os.path.join(SPECS, "staged")
    for rel in SHARED:
        os.makedirs(os.path.dirname(os.path.join(single, rel)), exist_ok=True)
        shutil.copy2(os.path.join(staged, rel), os.path.join(single, rel))
    for comp in COMPONENTS.values():
        md = generated_md(single, staged, comp)
        open(os.path.join(single, comp, f"{comp}.md"), "w").write(md)
    clean_caches(SPECS)
    print("single-file/ rebuilt from staged/")
    cmd_lint()


def clean_caches(root):
    for dirpath, dirnames, _ in os.walk(root):
        for d in [d for d in dirnames if d == "__pycache__"]:
            shutil.rmtree(os.path.join(dirpath, d))


def named_in(readme_text):
    """Every file path a README names, with 'x_1.py` to `x_4.py' ranges expanded."""
    names = set(re.findall(r"[\w./-]+\.(?:py|sh|md)", readme_text))
    for prefix, stem, lo, hi in re.findall(r"([\w/]*?)(\w+?)_(\d+)\.py` to `\w+?_(\d+)\.py", readme_text):
        names |= {f"{prefix}{stem}_{n}.py" for n in range(int(lo), int(hi) + 1)}
    return names


def files_of(root, comps):
    out = []
    for top in ["common"] + comps:
        for dirpath, dirnames, files in os.walk(os.path.join(root, top)):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            out += [os.path.relpath(os.path.join(dirpath, f), root) for f in files if not f.endswith(".pyc")]
    return sorted(out)


def rule_ids_in_checks(root, comp):
    sys.path[:0] = [os.path.join(root, "common"), os.path.join(root, comp, "checks")]
    sys.modules.pop("checklib", None)
    mod = importlib.import_module(f"{comp}_rules")
    ids = [rid for g in mod.GROUPS for rid in g[2]]
    sys.modules.pop(f"{comp}_rules")
    del sys.path[:2]
    return ids


def lint_readme(label, text, root, comps, problems):
    named = named_in(text)
    for rel in files_of(root, comps):
        if rel not in named and os.path.basename(rel) not in named:
            problems.append(f"{label}: does not name {rel}")
    for name in sorted(named):
        if "/" in name and not name.startswith("/") and not os.path.exists(os.path.join(root, name)):
            problems.append(f"{label}: names {name}, which does not exist")


def sync_conflicts():
    """Copies like 'checks 2' that iCloud Drive leaves behind when folders move quickly."""
    found = []
    for root in (SPECS, HERE):
        for dirpath, dirnames, files in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "jobs")]
            found += [os.path.join(dirpath, n) for n in dirnames + files if re.search(r" \d+(\.\w+)?$", n)]
    return found


def cmd_lint():
    problems = [f"sync-conflict copy (delete it after checking): {p}" for p in sync_conflicts()]
    single, staged = layout_dir("single-file"), layout_dir("staged")
    for layout, root in (("single-file", single), ("staged", staged)):
        for rel in files_of(root, list(COMPONENTS.values())):
            path = os.path.join(root, rel)
            if rel.endswith(".py"):
                try:
                    compile(open(path).read(), path, "exec")
                except SyntaxError as e:
                    problems.append(f"{layout}/{rel}: {e}")
                if os.path.basename(rel).startswith("check_") and not os.access(path, os.X_OK):
                    problems.append(f"{layout}/{rel}: not executable")
            elif rel.endswith(".sh"):
                r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
                if r.returncode:
                    problems.append(f"{layout}/{rel}: {r.stderr.strip()}")
        lint_readme(f"{layout}/README.md", open(os.path.join(root, "README.md")).read(), root,
                    list(COMPONENTS.values()), problems)
        for comp in COMPONENTS.values():
            md_ids = RULE_ID.findall("".join(open(os.path.join(root, comp, f)).read()
                                             for f in sorted(os.listdir(os.path.join(root, comp))) if f.endswith(".md")))
            check_ids = rule_ids_in_checks(root, comp)
            if sorted(md_ids) != sorted(check_ids) or len(set(md_ids)) != len(md_ids):
                problems.append(f"{layout}/{comp}: spec rule ids {sorted(set(md_ids) ^ set(check_ids)) or 'duplicated'} "
                                f"do not match the check's rule ids")
    for rel in SHARED:
        if open(os.path.join(single, rel), "rb").read() != open(os.path.join(staged, rel), "rb").read():
            problems.append(f"single-file/{rel} differs from staged/{rel}; run 'build'")
    for comp in COMPONENTS.values():
        if open(os.path.join(single, comp, f"{comp}.md")).read() != generated_md(single, staged, comp):
            problems.append(f"single-file/{comp}/{comp}.md is out of date with staged/{comp}/*.md; run 'build'")
    for layout in LAYOUTS:
        for key, comp in COMPONENTS.items():
            path = os.path.join(READMES, f"{layout}-{key}.md")
            if not os.path.isfile(path):
                problems.append(f"missing run README {path}")
                continue
            lint_readme(f"run-readmes/{layout}-{key}.md", open(path).read(), layout_dir(layout), [comp], problems)
    clean_caches(single)
    clean_caches(staged)
    for p in problems:
        print("PROBLEM", p)
    print(f"lint: {len(problems)} problem(s)")
    if problems:
        sys.exit(1)


def loose_entries():
    return [e for e in os.listdir(SPECS) if e not in (".gitkeep",) + LAYOUTS]


def digests(root):
    """{relative path: sha256} for every file under root, ignoring caches."""
    out = {}
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for f in files:
            if not f.endswith(".pyc"):
                path = os.path.join(dirpath, f)
                out[os.path.relpath(path, root)] = hashlib.sha256(open(path, "rb").read()).hexdigest()
    return out


def same_files(a, b, label):
    da, db = digests(a), digests(b)
    if da != db:
        diff = sorted(set(da) ^ set(db)) or sorted(k for k in da if da[k] != db[k])
        die(f"{label}: files differ from the master copy: {diff[:6]}")


def sweep_empty_loose():
    """Removes loose folders holding no files: iCloud re-creates empty skeletons of folders it saw deleted."""
    for e in loose_entries():
        path = os.path.join(SPECS, e)
        if os.path.isdir(path) and not any(files for _, _, files in os.walk(path)):
            shutil.rmtree(path)
            print(f"removed empty leftover folder milestone-1-specs/{e}")


def cmd_stage(layout, which):
    if layout not in LAYOUTS or which not in ("both", "A", "B"):
        die("usage: stage <single-file|staged> <both|A|B>")
    if not os.path.exists(STAGED):
        sweep_empty_loose()
    if os.path.exists(STAGED) or loose_entries():
        die("milestone-1-specs/ already holds a loose layout; run 'restore' first")
    cmd_lint()
    shutil.rmtree(HOLD, ignore_errors=True)
    os.makedirs(HOLD)
    for lay in LAYOUTS:
        shutil.copytree(os.path.join(SPECS, lay), os.path.join(HOLD, lay), ignore=NO_CACHE)
        same_files(os.path.join(SPECS, lay), os.path.join(HOLD, lay), f"master copy of {lay}")
    open(STAGED, "w").write(f"{layout} {which}\n")
    for lay in LAYOUTS:
        shutil.rmtree(os.path.join(SPECS, lay))
    src = os.path.join(HOLD, layout)
    comps = list(COMPONENTS.values()) if which == "both" else [COMPONENTS[which]]
    for top in ["common"] + comps:
        shutil.copytree(os.path.join(src, top), os.path.join(SPECS, top), ignore=NO_CACHE)
        same_files(os.path.join(SPECS, top), os.path.join(src, top), f"staged {top}/")
    readme = os.path.join(src, "README.md") if which == "both" else os.path.join(READMES, f"{layout}-{which}.md")
    shutil.copy2(readme, os.path.join(SPECS, "README.md"))
    if os.path.realpath(LINK) != SPECS:
        if os.path.lexists(LINK):
            os.remove(LINK)
        os.symlink(LINK_TARGET, LINK)
        print(f"created link student-files/spec -> {LINK_TARGET}")
    print(f"staged {layout} ({which}). milestone-1-specs/ now holds: {' '.join(sorted(loose_entries()))}")
    print("next: ./run_student_openai.sh 1    then: python3 spec_tool.py restore")


def cmd_verify():
    """While staged: the loose files still match the master. Otherwise: nothing to compare."""
    if not os.path.exists(STAGED):
        print("not staged; nothing to verify")
        return
    layout, which = open(STAGED).read().split()
    comps = list(COMPONENTS.values()) if which == "both" else [COMPONENTS[which]]
    for top in ["common"] + comps:
        same_files(os.path.join(SPECS, top), os.path.join(HOLD, layout, top), f"staged {top}/")
    print(f"verified: loose {layout} ({which}) files match the master copy")


def cmd_restore():
    if not os.path.exists(STAGED) or not all(os.path.isdir(os.path.join(HOLD, lay)) for lay in LAYOUTS):
        die(f"nothing to restore: {HOLD} does not hold a staged master copy")
    for e in loose_entries():
        path = os.path.join(SPECS, e)
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
    for lay in LAYOUTS:
        shutil.copytree(os.path.join(HOLD, lay), os.path.join(SPECS, lay), ignore=NO_CACHE)
        same_files(os.path.join(SPECS, lay), os.path.join(HOLD, lay), f"restored {lay}")
    os.remove(STAGED)
    sweep_empty_loose()
    print("restored: milestone-1-specs/ holds single-file/ and staged/ again (verified against the master copy)")


def cmd_status():
    if os.path.exists(STAGED):
        print(f"staged for a run: {open(STAGED).read().strip()}")
        print(f"loose in milestone-1-specs/: {' '.join(sorted(loose_entries()))}")
    else:
        print(f"submission form: milestone-1-specs/ holds {' '.join(sorted(os.listdir(SPECS)))}")
    print(f"student-files/spec -> {os.readlink(LINK) if os.path.islink(LINK) else 'MISSING'}")


if __name__ == "__main__":
    args = sys.argv[1:]
    commands = {"build": cmd_build, "lint": cmd_lint, "stage": cmd_stage, "restore": cmd_restore,
                "status": cmd_status, "verify": cmd_verify}
    if not args or args[0] not in commands:
        print(__doc__)
        sys.exit(2)
    commands[args[0]](*args[1:])
