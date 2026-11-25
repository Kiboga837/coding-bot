"""
Self-Coding Bot (Streamlit)
Describe what you want -> the bot plans the build, then writes/updates code
step by step every N seconds (default 7), saving to disk and checking it with
your local Python interpreter.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import anthropic
import streamlit as st

st.set_page_config(page_title="Self-Coding Bot", page_icon="🤖", layout="wide")

# ---------- helpers ----------
def get_client(api_key: str):
    return anthropic.Anthropic(api_key=api_key)


def ask(client, model: str, system: str, prompt: str, max_tokens: int = 4000) -> str:
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


def extract_code(text: str) -> str:
    m = re.search(r"```(?:python)?\n(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip() + "\n"


def extract_json_list(text: str):
    m = re.search(r"\[.*\]", text, re.S)
    try:
        data = json.loads(m.group(0)) if m else []
        return [str(x) for x in data]
    except Exception:
        return []


def run_python(py: str, args: list, cwd: Path, timeout: int):
    try:
        p = subprocess.run(
            [py, *args], cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 0, f"(timed out after {timeout}s - treated as running OK)"
    except Exception as e:
        return 1, f"Failed to launch interpreter: {e}"


def countdown(seconds: int, slot):
    for s in range(seconds, 0, -1):
        slot.info(f"⏳ Next code write in {s}s ...")
        time.sleep(1)
    slot.empty()


# ---------- state ----------
st.session_state.setdefault("running", False)
st.session_state.setdefault("final_code", "")
st.session_state.setdefault("log", [])

# ---------- sidebar ----------
with st.sidebar:
    st.header("⚙️ Settings")
    api_key = st.text_input(
        "Anthropic API key",
        type="password",
        value=st.secrets.get("ANTHROPIC_API_KEY", "") if hasattr(st, "secrets") else "",
    )
    model = st.text_input("Model", value="claude-sonnet-4-6")
    interval = st.number_input("Write interval (seconds)", 1, 120, 7)
    max_steps = st.slider("Max build steps", 2, 15, 6)

    st.subheader("Local Python")
    py_path = st.text_input("Python interpreter", value=sys.executable)
    if st.button("Test interpreter"):
        code, out = run_python(py_path, ["--version"], Path("."), 10)
        (st.success if code == 0 else st.error)(out or "No output")

    workdir = Path(st.text_input("Workspace folder", value="bot_workspace"))
    filename = st.text_input("Output file", value="generated_app.py")
    auto_run = st.checkbox(
        "Run the code after each write",
        value=False,
        help="Executes generated code on this machine. Off = syntax check only.",
    )
    run_timeout = st.number_input("Run timeout (s)", 1, 120, 10)
    st.warning("Generated code can do anything Python can. Review before running.")

# ---------- main ----------
st.title("🤖 Self-Coding Bot")
description = st.text_area(
    "What should I build?",
    placeholder="e.g. A CLI to-do manager that stores tasks in JSON with add/list/done commands",
    height=120,
)

c1, c2, _ = st.columns([1, 1, 6])
start = c1.button("▶ Start", type="primary", disabled=st.session_state.running)
stop = c2.button("⏹ Stop")

if stop:
    st.session_state.running = False
    st.info("Stopped.")

plan_box = st.container()
status = st.empty()
code_col, log_col = st.columns([3, 2])
code_slot = code_col.empty()
log_slot = log_col.container()

if start:
    if not api_key:
        st.error("Enter your Anthropic API key in the sidebar.")
    elif not description.strip():
        st.error("Describe what you want built.")
    else:
        st.session_state.running = True
        st.session_state.log = []
        workdir.mkdir(parents=True, exist_ok=True)
        target = workdir / filename
        client = get_client(api_key)

        try:
            # 1) Plan
            status.info("🧠 Planning ...")
            plan_raw = ask(
                client, model,
                "You are a senior Python engineer. Reply ONLY with a JSON array of strings.",
                f"Break this project into at most {max_steps} small, incremental coding steps. "
                f"Each step should extend one working Python file.\nProject: {description}",
                1000,
            )
            steps = extract_json_list(plan_raw)[:max_steps] or [description]
            with plan_box:
                st.subheader("Plan")
                for i, s in enumerate(steps, 1):
                    st.write(f"{i}. {s}")

            # 2) Write code at intervals
            code = ""
            for i, step in enumerate(steps, 1):
                if not st.session_state.running:
                    break
                status.info(f"✍️ Step {i}/{len(steps)}: {step}")
                prompt = (
                    f"Project goal: {description}\n\n"
                    f"Current file contents:\n```python\n{code or '# (empty)'}\n```\n\n"
                    f"Last check output:\n{st.session_state.log[-1]['output'] if st.session_state.log else 'none'}\n\n"
                    f"Now implement step {i}: {step}\n"
                    "Fix any errors from the last check. Return the COMPLETE updated file "
                    "in a single ```python block, with no commentary."
                )
                code = extract_code(
                    ask(client, model,
                        "You write clean, working, single-file Python. Output only code.",
                        prompt)
                )
                target.write_text(code, encoding="utf-8")
                st.session_state.final_code = code
                code_slot.code(code, language="python", line_numbers=True)

                # 3) Check with local Python
                rc, out = run_python(py_path, ["-m", "py_compile", filename], workdir, 20)
                label = "syntax OK" if rc == 0 else "syntax error"
                if rc == 0 and auto_run:
                    rc, out = run_python(py_path, [filename], workdir, int(run_timeout))
                    label = "ran OK" if rc == 0 else "runtime error"
                st.session_state.log.append({"step": i, "ok": rc == 0, "output": out})
                with log_slot:
                    (st.success if rc == 0 else st.error)(f"Step {i}: {label}")
                    if out:
                        st.code(out)

                if i < len(steps) and st.session_state.running:
                    countdown(int(interval), status)

            status.success(f"✅ Done. Saved to {target.resolve()}")
        except Exception as e:
            status.error(f"Error: {e}")
        finally:
            st.session_state.running = False

if st.session_state.final_code and not st.session_state.running:
    st.download_button(
        "⬇ Download code", st.session_state.final_code, file_name=filename
    )
