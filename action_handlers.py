"""
action_handlers.py — G.I.L.
Standalone handlers for actions that need multi-step logic beyond execute_action().
Each function is self-contained and receives everything it needs as parameters.
"""

from pathlib import Path


# ── Credential handlers ───────────────────────────────────────────────────────

def handle_save_credential(target: str, speak, window) -> None:
    from credentials import save_credential, initialize_credentials
    from ears import listen_once
    initialize_credentials()

    parts = [p.strip() for p in target.split("|")]
    if len(parts) == 3:
        service, email, password = parts
        save_credential(service, email, password)
    else:
        speak("Which service is this for?")
        window.set_state("listening")
        service = listen_once(timeout_secs=8) or ""

        speak("Email or username?")
        window.set_state("listening")
        email = listen_once(timeout_secs=10) or ""

        speak("Password?")
        window.set_state("listening")
        password = listen_once(timeout_secs=10) or ""

        if service and email and password:
            save_credential(service.strip(), email.strip(), password.strip())
        else:
            speak("Didn't catch all of that. Try again or use the settings panel.")


def handle_list_credentials(speak) -> None:
    from credentials import list_services, initialize_credentials
    initialize_credentials()
    services = list_services()
    if services:
        print(f"[G.I.L. VAULT] Stored services: {', '.join(services)}")
    else:
        print("[G.I.L. VAULT] No credentials stored.")


def handle_delete_credential(target: str, speak) -> None:
    from credentials import delete_credential, initialize_credentials
    initialize_credentials()
    if target:
        delete_credential(target)


# ── Task / project handlers ───────────────────────────────────────────────────

def handle_create_project(name: str, window) -> None:
    if not name:
        return
    from tasks import create_project
    create_project(name)
    window.refresh_tasks()
    window.after(0, window.show_window)


def handle_add_task(target: str, window) -> None:
    if not target:
        return
    from tasks import add_task
    parts   = [p.strip() for p in target.split("|")]
    text    = parts[0]
    project = parts[1].lower().replace(" ", "_") if len(parts) > 1 else ""
    add_task(text, project)
    window.refresh_tasks()
    window.after(0, window.show_window)


def handle_complete_task(target: str, window) -> None:
    if not target:
        return
    from tasks import complete_task
    complete_task(target)
    window.refresh_tasks()


# ── 3D / build / project handlers ────────────────────────────────────────────

def handle_create_3d(description: str, project_name: str = "") -> None:
    if not description:
        return
    try:
        from studio3d import open_studio
        open_studio(description, variant=1,
                    project_name=project_name or description.title())
    except Exception as exc:
        print(f"[G.I.L. STUDIO] Error: {exc}")


def handle_build(target: str) -> None:
    """Parse 'description | project-name' and spawn claude -p."""
    parts       = [p.strip() for p in target.split("|", 1)]
    description = parts[0]
    name        = parts[1] if len(parts) > 1 else ""
    if not description:
        return
    from actions import build_project
    ok = build_project(description, name)
    if not ok:
        print("[G.I.L. BUILD] Failed to open terminal — is 'claude' CLI installed?")


def handle_prompt_project(target: str) -> None:
    """Open an existing Desktop project in a Claude Code terminal."""
    import subprocess
    parts       = [p.strip() for p in target.split("|", 1)]
    folder_name = parts[0]
    prompt      = parts[1] if len(parts) > 1 else "Summarize the project and what I should work on next."
    if not folder_name:
        return

    desktop = Path.home() / "Desktop"
    project_dir = None
    for d in desktop.iterdir():
        if d.is_dir() and folder_name.lower() in d.name.lower():
            project_dir = d
            break

    if not project_dir:
        print(f"[G.I.L. PROJECT] Folder not found on Desktop: {folder_name}")
        return

    index_html = project_dir / "index.html"
    if index_html.exists():
        print(f"[G.I.L. PROJECT] '{project_dir.name}' has index.html — opening in browser.")
        try:
            from webgen import _open_file
            _open_file(index_html)
        except Exception:
            import webbrowser
            webbrowser.open(index_html.as_uri())
        return

    _WEB_WORDS = {"website", "webpage", "landing", "webapp", "frontend", "homepage"}
    if any(w in project_dir.name.lower() for w in _WEB_WORDS):
        print(f"[G.I.L. PROJECT] '{project_dir.name}' looks like a website — running webgen.")
        try:
            from webgen import generate_for_project
            generate_for_project(project_dir)
        except Exception as exc:
            print(f"[G.I.L. PROJECT] Webgen failed: {exc}")
        return

    dir_str  = str(project_dir)
    cmd_body = f'cd /d "{dir_str}" && echo {prompt} | claude -p --continue --dangerously-skip-permissions'

    for launcher in [
        ["wt", "-d", dir_str, "cmd", "/k", cmd_body],
        ["cmd", "/c", "start", "cmd", "/k", cmd_body],
    ]:
        try:
            subprocess.Popen(launcher)
            print(f"[G.I.L. PROJECT] Opened '{project_dir.name}' with Claude Code.")
            return
        except FileNotFoundError:
            continue
