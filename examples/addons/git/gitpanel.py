#!/usr/bin/env python3
"""Kachel »Git« für devc (Art panel, SPEC 8.13).

devc ruft dieses Skript im Container auf und gibt ihm auf stdin eine JSON-Zeile: eine Ansicht
({"view": …, "args": …}) oder eine Aktion ({"action": …, "args": …, "values": …}). Die Antwort ist ein
JSON-Objekt auf stdout. Das Skript liefert nur Daten; gezeichnet wird in devc.

Braucht nur Python 3 und git.
"""

import argparse
import json
import os
import re
import subprocess
import sys

SECRET = "/run/devc/secrets/git.token"
TOKEN_USER = "x-access-token"  # mit einem Token ist der Benutzername bei GitHub und GitLab beliebig, nur nicht leer
SKIP = {"node_modules", "venv", "__pycache__", "site-packages", "dist", "build", "target", "vendor"}
MAX_REPOS = 60
MAX_TEXT = 60000
LOG_COUNT = 30
HASH = re.compile(r"[0-9a-f]{7,40}")
STATES = {"M": "geändert", "A": "neu", "D": "gelöscht", "R": "umbenannt", "C": "kopiert", "T": "Typ geändert", "U": "Konflikt"}


class Problem(Exception):
    """Fehler, den der Nutzer als Meldung sieht."""


def hide(text: str) -> str:
    """Zugangsdaten in Adressen unkenntlich machen."""
    return re.sub(r"(://)[^/@\s]+@", r"\1***@", text)


def cut(text: str) -> str:
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "\n… (gekürzt)"


def git(repo: str, *args: str, timeout: int = 30, env: dict | None = None, text: str | None = None):
    full = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", LC_ALL="C.UTF-8")
    full.update(env or {})
    try:
        return subprocess.run(["git", "-C", repo, "-c", "core.quotepath=off", "-c", "color.ui=false", *args], capture_output=True,
                              encoding="utf-8", errors="replace", timeout=timeout, env=full, input=text,
                              stdin=subprocess.DEVNULL if text is None else None)
    except subprocess.TimeoutExpired:
        raise Problem(f"git {args[0]} hat nach {timeout} Sekunden nicht geantwortet") from None
    except OSError as error:
        raise Problem(f"git lässt sich nicht starten: {error.strerror}") from None


def said(result) -> str:
    return hide((result.stdout + result.stderr).strip())


# --- Repositories finden ---------------------------------------------------------


def find_repos(roots: list[str], depth: int) -> list[str]:
    found = []
    for root in roots:
        root = os.path.realpath(root)
        if not os.path.isdir(root):
            continue
        for current, dirs, _files in os.walk(root):
            level = 0 if current == root else current[len(root):].count(os.sep)
            if os.path.exists(os.path.join(current, ".git")):
                if current not in found:
                    found.append(current)
                dirs[:] = []  # Repositories in Repositories (Submodule) zeigt die Liste nicht
                continue
            if level >= depth:
                dirs[:] = []
                continue
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP and not os.path.islink(os.path.join(current, d)))
            if len(found) >= MAX_REPOS:
                return found
    return found


def need_repo(options, args: dict) -> str:
    """Der Ordner aus der Anfrage MUSS ein Repository unter einem der Suchordner sein."""
    repo = os.path.realpath(args.get("repo") or "")
    roots = [os.path.realpath(r) for r in options.roots]
    inside = any(repo == root or repo.startswith(root.rstrip(os.sep) + os.sep) for root in roots)
    if not inside or not os.path.exists(os.path.join(repo, ".git")):
        raise Problem(f"Kein Repository: {args.get('repo') or '(leer)'}")
    return repo


def need_file(args: dict) -> str:
    file = args.get("file") or ""
    if not file or file.startswith("/") or ".." in file.split("/"):
        raise Problem("Datei fehlt oder ist ungültig")
    return file


def need_hash(args: dict) -> str:
    commit = args.get("commit") or ""
    if not HASH.fullmatch(commit):
        raise Problem("Commit fehlt oder ist ungültig")
    return commit


# --- Stand eines Repositorys -------------------------------------------------------


def status(repo: str) -> dict:
    """Zweig, Stand zum Server und geänderte Dateien (git status --porcelain=v2)."""
    result = git(repo, "status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all")
    if result.returncode != 0:
        raise Problem(said(result) or "git status schlug fehl")
    info = {"branch": "", "upstream": "", "ahead": 0, "behind": 0, "files": [], "empty": False}
    records = result.stdout.split("\0")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if record.startswith("# branch.head "):
            info["branch"] = record[14:]
        elif record.startswith("# branch.oid "):
            info["empty"] = record[13:] == "(initial)"
        elif record.startswith("# branch.upstream "):
            info["upstream"] = record[18:]
        elif record.startswith("# branch.ab "):
            ahead, behind = record[12:].split()
            info["ahead"], info["behind"] = int(ahead), -int(behind)
        elif record.startswith("1 "):
            parts = record.split(" ", 8)
            info["files"].append({"path": parts[8], "staged": parts[1][0], "work": parts[1][1]})
        elif record.startswith("2 "):
            parts = record.split(" ", 9)
            info["files"].append({"path": parts[9], "staged": parts[1][0], "work": parts[1][1]})
            index += 1  # der alte Name steht im nächsten Eintrag
        elif record.startswith("u "):
            parts = record.split(" ", 10)
            info["files"].append({"path": parts[10], "staged": "U", "work": "U"})
        elif record.startswith("? "):
            info["files"].append({"path": record[2:], "staged": ".", "work": "?"})
    if info["branch"] == "(detached)":
        info["branch"] = "(kein Zweig)"
    return info


def server_text(info: dict) -> str:
    if not info["upstream"]:
        return "kein Gegenstück auf dem Server"
    parts = []
    if info["ahead"]:
        parts.append(f"{info['ahead']} zu pushen")
    if info["behind"]:
        parts.append(f"{info['behind']} zu pullen")
    return ", ".join(parts) or "gleich"


def remote(repo: str) -> tuple[str, str]:
    """(Name, Adresse) des Servers; `origin`, sonst der erste."""
    names = git(repo, "remote").stdout.split()
    if not names:
        return "", ""
    name = "origin" if "origin" in names else names[0]
    return name, git(repo, "remote", "get-url", name).stdout.strip()


def file_state(entry: dict) -> str:
    if entry["work"] == "?":
        return "neu, noch nicht in git"
    code = entry["work"] if entry["work"] != "." else entry["staged"]
    return STATES.get(code, code)


# --- Ansichten ---------------------------------------------------------------------


def view_repos(options, args: dict) -> dict:
    rows = []
    for repo in find_repos(options.roots, options.depth):
        try:
            info = status(repo)
            last = git(repo, "log", "-1", "--pretty=format:%ad · %s", "--date=format:%Y-%m-%d %H:%M").stdout.strip()
            cells = [repo, info["branch"], str(len(info["files"])) if info["files"] else "keine", server_text(info), last or "noch kein Commit"]
        except Problem as problem:
            cells = [repo, "?", "?", "?", str(problem)]
        rows.append({"cells": cells, "actions": [{"label": "Öffnen", "view": "repo", "args": {"repo": repo}, "style": "primary"}]})
    return {"note": "Gesucht in " + ", ".join(options.roots) + f", {options.depth} Ebenen tief.",
            "blocks": [{"type": "table", "columns": ["Repository", "Zweig", "Änderungen", "Server", "Letzter Commit"], "rows": rows,
                        "empty": "Kein Repository gefunden. Lege eins an mit: git init"}]}


def view_repo(options, args: dict) -> dict:
    repo = need_repo(options, args)
    info = status(repo)
    remote_name, url = remote(repo)
    here = {"repo": repo}
    facts = [f"| Ordner | `{repo}` |", f"| Zweig | `{info['branch']}` |",
             "| Server | " + (f"`{hide(url)}`" if url else "keiner eingetragen") + " |"]
    if url:
        facts.append(f"| Stand zum Server | {server_text(info)} |")
    blocks = [{"type": "actions", "actions": [{"label": "Zurück zur Liste", "view": "", "args": {}}]},
              {"type": "text", "text": "| Was | Wert |\n|---|---|\n" + "\n".join(facts)}]

    staged = [f for f in info["files"] if f["staged"] not in (".", "?")]
    rows = []
    for entry in info["files"]:
        target = {"repo": repo, "file": entry["path"]}
        is_staged = entry["staged"] != "."
        pending = entry["work"] != "."
        buttons = [{"label": "Unterschied", "view": "diff", "args": target}]
        if pending:
            buttons.append({"label": "Vormerken", "action": "stage", "args": target})
        if is_staged:
            buttons.append({"label": "Nicht vormerken", "action": "unstage", "args": target})
        rows.append({"cells": [entry["path"], file_state(entry), "teilweise" if is_staged and pending else "ja" if is_staged else "nein"], "actions": buttons})
    blocks.append({"type": "table", "title": "Änderungen", "columns": ["Datei", "Stand", "Vorgemerkt"], "rows": rows, "empty": "Keine Änderungen. Alles ist committet."})

    if info["files"]:
        blocks.append({"type": "form", "title": "Commit erstellen",
                       "fields": [{"name": "message", "label": "Beschreibung", "type": "multiline", "required": True,
                                   "hint": "Erste Zeile kurz, danach bei Bedarf eine Leerzeile und mehr Text."},
                                  {"name": "all", "label": "Alle Änderungen aufnehmen, auch neue Dateien", "type": "bool", "value": not staged,
                                   "hint": "Aus: nur die vorgemerkten Dateien."}],
                       "submit": {"label": "Commit erstellen", "action": "commit", "args": here, "style": "primary"}})

    if url:
        server = []
        server.append({"label": "Push" + (f" ({info['ahead']})" if info["ahead"] else ""), "action": "push", "args": here, "style": "primary"})
        server.append({"label": "Pull" + (f" ({info['behind']})" if info["behind"] else ""), "action": "pull", "args": here})
        blocks.append({"type": "actions", "title": "Server", "actions": server})
    if not url:
        blocks.append({"type": "text", "text": "Für einen Push fehlt die Adresse des Servers. Im Terminal: `git -C " + repo + " remote add origin <adresse>`"})

    if not info["empty"]:
        unpushed = set(git(repo, "rev-list", "@{u}..HEAD").stdout.split()) if info["upstream"] else set()
        log = git(repo, "log", "-n", str(LOG_COUNT), "--pretty=format:%H%x1f%h%x1f%ad%x1f%an%x1f%s%x1e", "--date=format:%Y-%m-%d %H:%M")
        rows = []
        for line in log.stdout.split("\x1e"):
            parts = line.strip("\n").split("\x1f")
            if len(parts) != 5:
                continue
            full, short, date, author, subject = parts
            target = {"repo": repo, "commit": full}
            where = "–" if not info["upstream"] else "noch nicht gepusht" if full in unpushed else "auf dem Server"
            rows.append({"cells": [short, date, author, subject, where], "actions": [
                {"label": "Ansehen", "view": "commit", "args": target},
                {"label": "Rückgängig", "action": "revert", "args": target,
                 "confirm": f"Commit {short} »{subject}« rückgängig machen? Es entsteht ein neuer Commit, der diese Änderung zurücknimmt. Nichts geht verloren."}]})
        blocks.append({"type": "table", "title": f"Letzte Commits (höchstens {LOG_COUNT})", "columns": ["Commit", "Datum", "Von", "Beschreibung", "Server"], "rows": rows})
    return {"title": os.path.basename(repo) or repo, "blocks": blocks}


def view_diff(options, args: dict) -> dict:
    repo, file = need_repo(options, args), need_file(args)
    tracked = git(repo, "ls-files", "--error-unmatch", "--", file).returncode == 0
    if tracked:
        has_head = git(repo, "rev-parse", "--verify", "-q", "HEAD").returncode == 0
        text = git(repo, "diff", *(["HEAD"] if has_head else ["--cached"]), "--", file).stdout or "(kein Unterschied)"
    else:
        try:
            with open(os.path.join(repo, file), encoding="utf-8", errors="replace") as handle:
                text = handle.read(MAX_TEXT + 1)
        except OSError as error:
            raise Problem(f"{file}: {error.strerror}") from None
    return {"title": file, "blocks": [
        {"type": "actions", "actions": [{"label": "Zurück zum Repository", "view": "repo", "args": {"repo": repo}}]},
        {"type": "code", "title": "Unterschied zum letzten Commit" if tracked else "Neue Datei", "text": cut(text)}]}


def view_commit(options, args: dict) -> dict:
    repo, commit = need_repo(options, args), need_hash(args)
    result = git(repo, "show", "--stat", "--patch", "--format=fuller", "--date=format:%Y-%m-%d %H:%M", commit)
    if result.returncode != 0:
        raise Problem(said(result) or "Commit nicht gefunden")
    return {"title": "Commit " + commit[:8], "blocks": [
        {"type": "actions", "actions": [{"label": "Zurück zum Repository", "view": "repo", "args": {"repo": repo}}]},
        {"type": "code", "text": cut(result.stdout)}]}


VIEWS = {"": view_repos, "repo": view_repo, "diff": view_diff, "commit": view_commit}

# --- Aktionen ----------------------------------------------------------------------


def done(result, good: str, bad: str) -> dict:
    ok = result.returncode == 0
    return {"ok": ok, "message": good if ok else bad, "output": said(result)}


def act_stage(options, args, values) -> dict:
    repo, file = need_repo(options, args), need_file(args)
    return done(git(repo, "add", "-A", "--", file), f"{file} ist vorgemerkt.", "Vormerken schlug fehl.")


def act_unstage(options, args, values) -> dict:
    repo, file = need_repo(options, args), need_file(args)
    if git(repo, "rev-parse", "--verify", "-q", "HEAD").returncode == 0:
        result = git(repo, "restore", "--staged", "--", file)
    else:
        result = git(repo, "rm", "--cached", "-q", "--", file)  # vor dem ersten Commit gibt es nichts zum Zurückholen
    return done(result, f"{file} ist nicht mehr vorgemerkt.", "Das schlug fehl.")


def act_commit(options, args, values) -> dict:
    repo = need_repo(options, args)
    message = (values.get("message") or "").strip()
    if not message:
        raise Problem("Die Beschreibung fehlt.")
    if values.get("all") is True:
        added = git(repo, "add", "-A")
        if added.returncode != 0:
            return done(added, "", "Die Änderungen ließen sich nicht aufnehmen.")
    if git(repo, "diff", "--cached", "--quiet").returncode == 0:
        raise Problem("Es ist nichts vorgemerkt. Merke Dateien vor oder schalte »Alle Änderungen aufnehmen« ein.")
    result = git(repo, "commit", "-F", "-", text=message + "\n", timeout=60)
    if result.returncode != 0 and "tell me who you are" in result.stderr:
        return {"ok": False, "message": "git kennt deinen Namen noch nicht. Trage beim Addon base die Werte git_name und git_email ein.", "output": ""}
    return done(result, "Commit erstellt.", "Der Commit schlug fehl.")


def act_revert(options, args, values) -> dict:
    repo, commit = need_repo(options, args), need_hash(args)
    parents = git(repo, "rev-list", "--parents", "-n", "1", commit).stdout.split()
    if not parents:
        raise Problem("Commit nicht gefunden")
    if len(parents) > 2:
        raise Problem("Das ist ein Merge-Commit. Den nimmst du im Terminal zurück: git revert -m 1 " + commit[:8])
    result = git(repo, "revert", "--no-edit", commit, timeout=60)
    if result.returncode != 0:
        output = said(result)
        git(repo, "revert", "--abort")  # nichts halb Fertiges zurücklassen
        return {"ok": False, "message": "Rückgängig ging nicht; das Repository ist unverändert. Meist stören nicht committete Änderungen oder spätere Commits an denselben Zeilen.", "output": output}
    return done(result, f"Commit {commit[:8]} ist rückgängig gemacht (neuer Commit).", "")


def server_env(options) -> tuple[list[str], dict]:
    """Zugangsdaten für https: git fragt dieses Skript (`--credential`), kein Wert steht in einer Prozessliste."""
    helper = "!python3 " + os.path.abspath(__file__) + " --credential"
    env = {"DEVC_GIT_USER": options.user}
    env.setdefault("GIT_SSH_COMMAND", os.environ.get("GIT_SSH_COMMAND") or "ssh -o BatchMode=yes")
    return ["-c", "credential.helper=", "-c", "credential.helper=" + helper], env


def server_hint(options, url: str, output: str) -> str:
    if url.startswith("http") and re.search(r"Authentication failed|could not read (Username|Password)|terminal prompts disabled|403", output):
        if not os.path.isfile(SECRET) or not os.path.getsize(SECRET):
            return " Für https fehlt das Token: unter Secrets git.token setzen."
        return " Der Server lehnt die Zugangsdaten ab. Prüfe das Secret git.token (es braucht Schreibrecht) und bei Servern mit Passwort den Wert username."
    if re.search(r"non-fast-forward|fetch first|rejected", output):
        return " Auf dem Server gibt es neuere Commits. Mache zuerst einen Pull."
    return ""


def act_push(options, args, values) -> dict:
    repo = need_repo(options, args)
    name, url = remote(repo)
    if not name:
        raise Problem("Für dieses Repository ist kein Server eingetragen.")
    info = status(repo)
    if info["branch"].startswith("("):
        raise Problem("Es ist kein Zweig ausgecheckt; so lässt sich nicht pushen.")
    config, env = server_env(options)
    target = [] if info["upstream"] else ["-u", name, info["branch"]]
    result = git(repo, *config, "push", *target, timeout=100, env=env)
    output = said(result)
    if result.returncode != 0:
        return {"ok": False, "message": "Der Push schlug fehl." + server_hint(options, url, output), "output": output}
    return {"ok": True, "message": "Push erledigt.", "output": output}


def act_pull(options, args, values) -> dict:
    """Pull ohne Zusammenführen (`--ff-only`): Entweder kommen die Commits des Servers sauber dazu, oder nichts ändert sich."""
    repo = need_repo(options, args)
    name, url = remote(repo)
    if not name:
        raise Problem("Für dieses Repository ist kein Server eingetragen.")
    info = status(repo)
    if info["branch"].startswith("("):
        raise Problem("Es ist kein Zweig ausgecheckt; so lässt sich nicht pullen.")
    config, env = server_env(options)
    target = [] if info["upstream"] else [name, info["branch"]]
    result = git(repo, *config, "pull", "--ff-only", *target, timeout=100, env=env)
    output = said(result)
    if result.returncode == 0:
        return {"ok": True, "message": "Pull erledigt." if "Already up to date" not in output else "Pull erledigt. Es gab nichts Neues.", "output": output}
    if re.search(r"Not possible to fast-forward|[Dd]iverg", output):
        hint = " Du und der Server habt beide neue Commits. Das Zusammenführen machst du im Terminal: git pull --rebase oder git pull --no-rebase."
    elif "would be overwritten" in output:
        hint = " Der Pull träfe Dateien mit nicht committeten Änderungen. Committe sie zuerst."
    elif re.search(r"couldn't find remote ref|no such ref", output):
        hint = " Diesen Zweig gibt es auf dem Server noch nicht. Mache zuerst einen Push."
    else:
        hint = server_hint(options, url, output)
    return {"ok": False, "message": "Der Pull schlug fehl; das Repository ist unverändert." + hint, "output": output}


ACTIONS = {"stage": act_stage, "unstage": act_unstage, "commit": act_commit, "revert": act_revert, "push": act_push, "pull": act_pull}

# --- Einstieg ----------------------------------------------------------------------


def credential(operation: str) -> int:
    """Antwort an git als »credential helper«: Benutzername aus der Umgebung, Passwort aus dem Secret."""
    if operation != "get":
        return 0
    user = os.environ.get("DEVC_GIT_USER", "") or TOKEN_USER
    try:
        with open(SECRET, encoding="utf-8") as handle:
            password = handle.read().strip("\r\n")
    except OSError:
        password = ""
    if password:
        sys.stdout.write(f"username={user}\npassword={password}\n")
    return 0


def answer(options, asked: dict) -> dict:
    args = asked.get("args") or {}
    try:
        if "action" in asked:
            handler = ACTIONS.get(asked["action"])
            if handler is None:
                raise Problem(f"Unbekannte Aktion: {asked['action']}")
            result = handler(options, args, asked.get("values") or {})
            if result["ok"] and asked["action"] in ("commit", "revert", "push", "pull"):
                result["view"] = {"view": "repo", "args": {"repo": args.get("repo", "")}}
            return result
        handler = VIEWS.get(asked.get("view") or "")
        if handler is None:
            raise Problem(f"Unbekannte Ansicht: {asked.get('view')}")
        return handler(options, args)
    except Problem as problem:
        if "action" in asked:
            return {"ok": False, "message": str(problem)}
        return {"blocks": [{"type": "text", "text": str(problem)},
                           {"type": "actions", "actions": [{"label": "Zurück zur Liste", "view": "", "args": {}}]}]}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Kachel »Git« für devc")
    parser.add_argument("--roots", default="/work /home/dev")
    parser.add_argument("--depth", type=int, default=4)
    parser.add_argument("--user", default="")
    parser.add_argument("--credential", metavar="<get|store|erase>")
    options = parser.parse_args(argv)
    if options.credential:
        return credential(options.credential)
    options.roots = options.roots.split() or ["/work"]
    try:
        asked = json.loads(sys.stdin.readline() or "{}")
    except ValueError:
        asked = {}
    if not isinstance(asked, dict):
        asked = {}
    json.dump(answer(options, asked), sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
