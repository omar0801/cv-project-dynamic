import re
import os
import sys
import shutil
import subprocess
import platform
import threading
import tkinter as tk
import tkinter.font as tkFont
from tkinter import ttk, messagebox
from pathlib import Path
import json

# 3rd party dark theme (pip install sv-ttk)
import sv_ttk

import subprocess, platform

def _win_no_window_flags():
    if platform.system() == "Windows":
        return 0x08000000  # CREATE_NO_WINDOW
    return 0



# === RESOURCE PATHS (dev + packaged) ===
# During dev, this file lives in src/, so project root is parent.
DEV_ROOT = Path(__file__).resolve().parent.parent  # .../cv-project
# When packaged (PyInstaller), resources live in _MEIPASS.
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", str(DEV_ROOT)))

def RPATH(*parts) -> Path:
    """Resolve a resource path inside base/ or modules/ whether dev or packaged."""
    return (RESOURCE_ROOT / Path(*parts)).resolve()

# === CONFIG: templates (versioned with fallback) ===
JOB_TYPES = {
    "EE": [RPATH('base', 'template_ee_v1.tex'), RPATH('base', 'template_ee.tex')],
    "Data": [RPATH('base', 'template_data_v1.tex'), RPATH('base', 'template_data.tex')],
}

COVER_LETTER_TEMPLATES = {
    # Map job type -> list of candidate cover letter templates (first existing wins)
    # Provide a generic fallback too
    "EE": [RPATH('base', 'cover_letter_ee_v1.tex'), RPATH('base', 'cover_letter_ee.tex')],
    "Data": [RPATH('base', 'cover_letter_data_v1.tex'), RPATH('base', 'cover_letter_data.tex')],
    "*": [RPATH('base', 'cover_letter_v1.tex'), RPATH('base', 'cover_letter.tex')],
}

def resolve_template(job_type_code: str) -> Path | None:
    for candidate in JOB_TYPES.get(job_type_code, []):
        if Path(candidate).exists():
            return Path(candidate)
    return None


def resolve_cover_template(job_type_code: str) -> Path | None:
    # Prefer job-type specific, then generic fallbacks
    for candidate in COVER_LETTER_TEMPLATES.get(job_type_code, []):
        if Path(candidate).exists():
            return Path(candidate)
    for candidate in COVER_LETTER_TEMPLATES.get("*", []):
        if Path(candidate).exists():
            return Path(candidate)
    return None

# === NAME / FOLDER HELPERS ===

def sanitize_name(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9 _-]+", "", s)
    s = re.sub(r"\s+", "_", s)
    return s or "untitled"


def ensure_unique_folder(base: Path) -> Path:
    """Return base if free, else base_1, base_2, ..."""
    if not base.exists():
        return base
    n = 1
    while True:
        cand = base.parent / f"{base.name}_{n}"
        if not cand.exists():
            return cand
        n += 1

# === TEMPLATE PATCHERS ===

def ensure_local_resume_class(cv_path: str):
    """Ensure the template uses \\documentclass{resume} (found via TEXINPUTS)."""
    try:
        text = Path(cv_path).read_text(encoding='utf-8')
        text2 = re.sub(r'\\documentclass\s*\{[^\}]*resume[^\}]*\}', r'\\documentclass{resume}', text)
        if text2 != text:
            Path(cv_path).write_text(text2, encoding='utf-8')
    except Exception:
        pass


def rewrite_module_inputs_to_absolute(tex_path: str):
    """
    Turn \\input{../../modules/...} and \\includegraphics{...modules/...}
    into absolute paths so LaTeX can find them when running from jobs/<company>/<role>/.
    Works for both CV and cover letter templates.
    """
    def absify(rel: str) -> str:
        rel_path = Path(rel).as_posix()
        # strip leading ../ and ./ to anchor at project root (RESOURCE_ROOT)
        while rel_path.startswith('../'):
            rel_path = rel_path[3:]
        if rel_path.startswith('./'):
            rel_path = rel_path[2:]
        abs_p = RPATH(*Path(rel_path).parts)
        return Path(abs_p).as_posix()

    try:
        text = Path(tex_path).read_text(encoding='utf-8')

        # \input{...modules/...}
        text = re.sub(r'\\input\{([^\}]*/?modules/[^\}]*)\}',
              lambda m: '\\input{' + absify(m.group(1)) + '}', text)

        # \includegraphics[<opts>]{...modules/...}
        text = re.sub(r'(\\includegraphics(?:\[[^\]]*\])?\{)([^\}]*modules/[^\}]*)(\})',
                      lambda m: m.group(1) + absify(m.group(2)) + m.group(3), text)

        Path(tex_path).write_text(text, encoding='utf-8')
    except Exception:
        pass

# === PROJECT LOADER ===

def load_projects():
    projects = []
    json_file = RPATH('base', 'projects.json')
    if json_file.exists():
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for item in data:
            rel_path = item.get('path', '')
            abs_path = RPATH(*Path(rel_path).parts)
            name = item.get('name') or Path(rel_path).stem.replace('_', ' ').title()
            pid = str(item.get('id') or len(projects) + 1)
            projects.append({'id': pid, 'name': name, 'path': str(abs_path)})

    listed = {Path(p['path']).resolve() for p in projects}
    proj_dir = RPATH('modules', 'projects')
    if proj_dir.exists():
        for tex in sorted(proj_dir.glob('*.tex')):
            if tex.resolve() not in listed:
                pid = str(len(projects) + 1)
                name = tex.stem.replace('_', ' ').title()
                projects.append({'id': pid, 'name': name, 'path': str(tex.resolve())})

    id_to_path = {p['id']: p['path'] for p in projects}
    return projects, id_to_path

# === SMALL UTILS ===

def which(cmd):
    from shutil import which as _which
    return _which(cmd)


def try_latexmk(tex_dir, base_name, env) -> bool:
    if which("latexmk"):
        try:
            subprocess.run(
                ["latexmk", "-pdf", "-silent", f"{base_name}.tex"],
                cwd=tex_dir,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_win_no_window_flags(),  # ← hide console
                check=True,
            )
            return True
        except Exception:
            return False
    return False



def compile_with_pdflatex(tex_dir, base_name, env) -> int:
    rc = 0
    for _ in range(2):
        proc = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", f"{base_name}.tex"],
            cwd=tex_dir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_win_no_window_flags(),  # ← hide console
        )
        rc = proc.returncode
    return rc



def open_pdf_viewer(path):
    try:
        if platform.system() == "Windows":
            os.startfile(path)
        elif platform.system() == "Darwin":
            subprocess.run(["open", path])
        else:
            subprocess.run(["xdg-open", path])
    except Exception:
        pass


def open_folder(path):
    try:
        if platform.system() == "Windows":
            os.startfile(path)
        elif platform.system() == "Darwin":
            subprocess.run(["open", path])
        else:
            subprocess.run(["xdg-open", path])
    except Exception:
        pass

# === LaTeX escaping ===
LATEX_SPECIALS = {
    "#": r"\#",
    "$": r"\$",
    "%": r"\%",
    "&": r"\&",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\\textasciitilde{}",
    "^": r"\\textasciicircum{}",
    "\\": r"\\textbackslash{}",
}


def latex_escape(text):
    return "".join(LATEX_SPECIALS.get(ch, ch) for ch in text)


def read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.readlines()


def write_file(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)

# === CLEANUP ===

def clean_latex_junk(tex_file_path: str, keep_log: bool = True):
    tex_dir = os.path.dirname(tex_file_path)
    base_name = os.path.splitext(os.path.basename(tex_file_path))[0]
    exts = [".aux", ".out", ".toc", ".fls", ".fdb_latexmk", ".synctex.gz"]
    if not keep_log:
        exts.append(".log")
    for ext in exts:
        p = os.path.join(tex_dir, base_name + ext)
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

# === COMPILATION ===

def compile_latex(tex_file_path, open_pdf=False, clean_files=False):
    tex_dir = os.path.dirname(tex_file_path)
    base_name = os.path.splitext(os.path.basename(tex_file_path))[0]
    pdf_path = os.path.join(tex_dir, base_name + ".pdf")
    log_path = os.path.join(tex_dir, base_name + ".log")

    env = os.environ.copy()
    env['TEXINPUTS'] = str(RPATH('base')) + os.pathsep + env.get('TEXINPUTS', '')

    try:
        # Try latexmk first; if not present/fails, fall back to pdflatex
        ok = try_latexmk(tex_dir, base_name, env)
        if not ok:
            rc = compile_with_pdflatex(tex_dir, base_name, env)
            if rc != 0:
                raise RuntimeError("pdflatex failed")

        if clean_files:
            clean_latex_junk(tex_file_path, keep_log=False)
        if open_pdf:
            open_pdf_viewer(pdf_path)
        return True, None
    except Exception as e:
        # Clean aux files but KEEP the .log for debugging
        if clean_files:
            clean_latex_junk(tex_file_path, keep_log=True)
        hint = f"{e} — check log: {log_path}"
        return False, hint

# === Core (CV) ===

def create_cv_for(job_type_code, role_title, company_name, job_link):
    template_path = resolve_template(job_type_code)
    if not template_path:
        messagebox.showerror("Error", f"No template found for job type '{job_type_code}'.")
        return None, None

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        project_root = exe_dir.parent
    else:
        project_root = DEV_ROOT

    jobs_dir = project_root / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)

    # NEW ORDER: jobs/<company>/<role> (auto _1, _2 on the role folder)
    company_slug = sanitize_name(company_name)
    company_dir = jobs_dir / company_slug
    company_dir.mkdir(parents=True, exist_ok=True)

    role_slug = sanitize_name(role_title or "general")
    base_folder = company_dir / role_slug
    job_folder = ensure_unique_folder(base_folder)
    job_folder.mkdir(parents=True, exist_ok=True)

    destination_file = job_folder / "Barouni_Omar_CV.tex"

    try:
        shutil.copy(str(template_path), str(destination_file))
        ensure_local_resume_class(str(destination_file))
        rewrite_module_inputs_to_absolute(str(destination_file))
    except Exception as e:
        messagebox.showerror("Error", f"Could not prepare CV files:\n{e}")
        return None, None

    notes_file = job_folder / "job-notes.md"
    try:
        with open(notes_file, "w", encoding="utf-8") as f:
            f.write(f"# Job Application Notes - {company_name}\n\n")
            f.write(f"**Role:** {role_title}\n")
            f.write(f"**Job Link:** {job_link}\n")
    except Exception:
        pass

    return str(job_folder), str(destination_file)


def customise_cv_content(job_folder, cv_path, summary, selected_ids, compile_opt, clean_opt, open_opt, id_to_path):
    try:
        lines = read_file(cv_path)
    except FileNotFoundError:
        messagebox.showerror("Error", "CV file not found.")
        return False

    has_summary_marker = any("% PASTE SUMMARY HERE" in ln for ln in lines)
    has_projects_marker = any("% PROJECT PATHS HERE" in ln for ln in lines)
    if not (has_summary_marker and has_projects_marker):
        messagebox.showwarning(
            "Warning",
            "Template is missing one or both markers:\n% PASTE SUMMARY HERE\n% PROJECT PATHS HERE"
        )

    missing = []
    project_inputs = []
    for pid in selected_ids:
        path = id_to_path.get(pid)
        if not path or not os.path.exists(path):
            missing.append(pid)
        else:
            project_inputs.append(f"\\input{{{Path(path).as_posix()}}}\n")
    if missing:
        messagebox.showerror("Missing project files", f"Missing entries: {', '.join(missing)}")
        return False

    escaped_summary = latex_escape(summary.strip())

    new_lines = []
    for line in lines:
        if "% PASTE SUMMARY HERE" in line:
            new_lines.append(escaped_summary + "\n")
        elif "% PROJECT PATHS HERE" in line:
            new_lines += project_inputs
        else:
            new_lines.append(line)

    write_file(cv_path, new_lines)

    if compile_opt:
        ok, err = compile_latex(cv_path, open_pdf=open_opt, clean_files=clean_opt)
        if not ok:
            messagebox.showerror("Compile failed", f"LaTeX compilation failed:\n{err or 'Unknown error'}")
            return False

    return True

# === Core (Cover Letter) ===

def create_cover_letter_for(job_type_code, job_folder: str):
    tpl = resolve_cover_template(job_type_code)
    if not tpl:
        messagebox.showerror("Error", f"No cover letter template found for job type '{job_type_code}'.")
        return None

    dest = Path(job_folder) / "Barouni_Omar_Cover_Letter.tex"
    try:
        shutil.copy(str(tpl), str(dest))
        # Apply the same absolute path rewriting to ensure modules/ work in CL too
        rewrite_module_inputs_to_absolute(str(dest))
        return str(dest)
    except Exception as e:
        messagebox.showerror("Error", f"Could not prepare cover letter file:\n{e}")
        return None


def customise_cover_letter_content(cl_tex_path: str, body_text: str, compile_opt: bool, clean_opt: bool, open_opt: bool):
    try:
        lines = read_file(cl_tex_path)
    except FileNotFoundError:
        messagebox.showerror("Error", "Cover letter file not found.")
        return False

    has_marker = any("% PASTE HERE" in ln for ln in lines)
    if not has_marker:
        messagebox.showwarning("Warning", "Cover letter template missing '% PASTE HERE' marker.")

    escaped_body = latex_escape(body_text.strip())

    new_lines = []
    inserted = False
    for line in lines:
        if "% PASTE HERE" in line and not inserted:
            new_lines.append(escaped_body + "\n")
            inserted = True
        else:
            new_lines.append(line)

    write_file(cl_tex_path, new_lines)

    if compile_opt:
        ok, err = compile_latex(cl_tex_path, open_pdf=open_opt, clean_files=clean_opt)
        if not ok:
            messagebox.showerror("Compile failed", f"Cover letter LaTeX compilation failed:\n{err or 'Unknown error'}")
            return False

    return True

# === GUI ===

def run_app():
    root = tk.Tk()
    root.title("")

    sv_ttk.set_theme("dark")

    style = ttk.Style()
    style.configure(".", font=("Segoe UI", 10))
    style.configure("TCombobox", font=("Segoe UI", 11)) 
    root.option_add('*TCombobox*Listbox.font', ('Segoe UI', 11))
    style.configure("TLabel", font=("Segoe UI", 11))
    style.configure("TEntry", font=("Segoe UI", 11))
    style.configure("TCheckbutton", font=("Segoe UI", 11))
    style.configure("TCombobox", font=("Segoe UI", 11)) 

    projects, id_to_path = load_projects()
    if not projects:
        messagebox.showerror("Error", "No projects found. Add entries to base/projects.json or put .tex files in modules/projects/")
        return

    
    for c in (0, 1, 2):
        root.columnconfigure(c, weight=1 if c in (1, 2) else 0)
    
    root.rowconfigure(1, weight=1)

    def set_busy(flag=True):
        root.config(cursor="watch" if flag else "")
        root.update_idletasks()

    # =========================
    # CV inputs inside a 2‑column form frame (tight label column)
    # =========================
    form = ttk.Frame(root)
    form.grid(row=1, column=0, columnspan=3, sticky="nsew", padx=8, pady=(8, 4))

    # labels column does NOT expand, inputs do
    form.columnconfigure(0, weight=0)   # fixed narrow label column
    form.columnconfigure(1, weight=1)                # inputs expand
    # make summary + projects rows stretchy inside the form
    form.rowconfigure(4, weight=1)  # summary row
    form.rowconfigure(5, weight=1)  # projects row

    # Row 0: Job Type
    job_type_var = tk.StringVar(value="EE")
    job_type_menu = ttk.Combobox(
    form,
    textvariable=job_type_var,
    values=list(JOB_TYPES.keys()),
    state="readonly",
    width=5
)
    job_type_menu.grid(row=0, column=1, sticky="w", pady=4)



    # Row 1: Company
    ttk.Label(form, text="Company:").grid(row=1, column=0, sticky="e", padx=(0, 8), pady=4)
    company_entry = ttk.Entry(form, width=40)
    company_entry.grid(row=1, column=1, sticky="we", pady=4)

    # Row 2: Role
    ttk.Label(form, text="Role Title:").grid(row=2, column=0, sticky="e", padx=(0, 8), pady=4)
    role_entry = ttk.Entry(form, width=40)
    role_entry.grid(row=2, column=1, sticky="we", pady=4)

    # Row 3: Link
    ttk.Label(form, text="Job Link:").grid(row=3, column=0, sticky="e", padx=(0, 8), pady=4)
    job_link_entry = ttk.Entry(form, width=40)
    job_link_entry.grid(row=3, column=1, sticky="we", pady=4)

    # Row 4: Summary (text + counter stacked)
    ttk.Label(form, text="Summary:").grid(row=4, column=0, sticky="ne", padx=(0, 8), pady=4)
    summary_outer = ttk.Frame(form)
    summary_outer.grid(row=4, column=1, sticky="nsew", pady=4)

    summary_frame = ttk.Frame(summary_outer)
    summary_frame.pack(side="top", fill="both", expand=True)
    summary_text = tk.Text(summary_frame, width=80, height=8, wrap="word")
    summary_text.pack(side="left", fill="both", expand=True)
    summary_scroll = ttk.Scrollbar(summary_frame, command=summary_text.yview)
    summary_scroll.pack(side="right", fill="y")
    summary_text.config(font=("Segoe UI", 11))

    counter_var = tk.StringVar(value="0 chars")
    counter_label = ttk.Label(summary_outer, textvariable=counter_var, anchor="e")
    counter_label.pack(side="bottom", anchor="e", pady=(2, 0))
    
    def update_count(_evt=None):
        n = len(summary_text.get("1.0", "end-1c"))
        counter_var.set(f"{n} chars")

    # Row 5: Projects
    ttk.Label(form, text="Projects:").grid(row=5, column=0, sticky="ne", padx=(0, 8), pady=4)
    proj_frame = ttk.Frame(form)
    proj_frame.grid(row=5, column=1, sticky="nsew", pady=4)

    project_listbox = tk.Listbox(proj_frame, selectmode=tk.MULTIPLE, width=28, height=7)
    project_listbox.pack(side="left", fill="both", expand=True)
    project_listbox.configure(font=("Segoe UI", 11))
    proj_scroll = ttk.Scrollbar(proj_frame, command=project_listbox.yview)
    proj_scroll.pack(side="right", fill="y")
    project_listbox.config(yscrollcommand=proj_scroll.set)

    for p in projects:
        project_listbox.insert(tk.END, f"{p['id']} - {p['name']}")

    # =========================
    # CV options row
    # =========================
    compile_var = tk.BooleanVar(value=True)
    clean_var = tk.BooleanVar(value=True)
    open_var = tk.BooleanVar(value=True)

    cv_opts = ttk.Frame(root)
    cv_opts.grid(row=7, column=0, columnspan=3, sticky="w", padx=8, pady=(2, 2))
    ttk.Checkbutton(cv_opts, text="Compile CV to PDF", variable=compile_var).pack(side="left", padx=(0, 15))
    ttk.Checkbutton(cv_opts, text="Clean LaTeX junk files", variable=clean_var).pack(side="left", padx=(0, 15))
    ttk.Checkbutton(cv_opts, text="Open compiled PDF", variable=open_var).pack(side="left")

    # =========================
    # Cover Letter toggle + content (hidden until checked)
    # =========================
    include_cl_var = tk.BooleanVar(value=False)
    include_cl_chk = ttk.Checkbutton(root, text="Include cover letter", variable=include_cl_var)
    include_cl_chk.grid(row=8, column=0, sticky="w", padx=8, pady=(8, 0))

    cl_frame = ttk.Labelframe(root, text="Cover letter", labelanchor="nw")
    style.configure("TLabelframe.Label", font=("Segoe UI", 11))

    cl_text_frame = ttk.Frame(cl_frame)
    cl_text_frame.grid(row=0, column=0, columnspan=3, sticky="nsew", padx=8, pady=(8, 8))
    cl_text = tk.Text(cl_text_frame, width=80, height=10, wrap="word")
    cl_text.pack(side="left", fill="both", expand=True)
    cl_scroll = ttk.Scrollbar(cl_text_frame, command=cl_text.yview)
    cl_scroll.pack(side="right", fill="y")
    cl_text.config(font=("Segoe UI", 11))

    cl_counter_var = tk.StringVar(value="0 chars")
    cl_counter_label = ttk.Label(cl_frame, textvariable=cl_counter_var)
    cl_counter_label.grid(row=1, column=2, sticky="e", padx=8, pady=(0, 8))

    def update_cl_count(_evt=None):
        n = len(cl_text.get("1.0", "end-1c"))
        cl_counter_var.set(f"{n} chars")
    cl_text.bind("<KeyRelease>", update_cl_count)
    summary_text.bind("<KeyRelease>", update_count)

    cl_compile_var = tk.BooleanVar(value=True)
    cl_open_var = tk.BooleanVar(value=True)

    cl_opts = ttk.Frame(cl_frame)
    cl_opts.grid(row=2, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 8))
    ttk.Checkbutton(cl_opts, text="Compile CL to PDF", variable=cl_compile_var).pack(side="left", padx=(0, 15))
    ttk.Checkbutton(cl_opts, text="Open compiled PDF", variable=cl_open_var).pack(side="left")

    for c in (0, 1, 2):
        cl_frame.columnconfigure(c, weight=1 if c != 0 else 0)
    cl_frame.rowconfigure(0, weight=1)

    def toggle_cover():
        if include_cl_var.get():
            cl_frame.grid(row=9, column=0, columnspan=3, sticky="nsew", padx=8, pady=(6, 4))
        else:
            cl_frame.grid_remove()

    include_cl_chk.config(command=toggle_cover)
    toggle_cover()  # start hidden

    # =========================
    # Status + Buttons
    # =========================
    status_var = tk.StringVar(value="")
    status_label = ttk.Label(root, textvariable=status_var, anchor="w")
    status_label.grid(row=10, column=0, columnspan=2, sticky="we", padx=8, pady=(4, 0))

    open_folder_btn = ttk.Button(root, text="Open job folder", state="disabled")
    open_folder_btn.grid(row=11, column=0, sticky="w", padx=8, pady=8)

    generate_btn = ttk.Button(root, text="Generate")
    generate_btn.grid(row=11, column=2, sticky="e", padx=8, pady=8)  # far right


    # Clear + Generate handlers
    def clear_form():
        role_entry.delete(0, tk.END)
        company_entry.delete(0, tk.END)
        job_link_entry.delete(0, tk.END)
        summary_text.delete("1.0", tk.END)
        project_listbox.selection_clear(0, tk.END)
        include_cl_var.set(False)
        toggle_cover()
        cl_text.delete("1.0", tk.END)
        update_count()
        update_cl_count()

    def on_generate(_evt=None):
        job_type = job_type_var.get().strip()
        role_title = role_entry.get().strip()
        company = company_entry.get().strip()
        job_link = job_link_entry.get().strip()
        summary = summary_text.get("1.0", "end-1c").strip()
        want_cl = include_cl_var.get()
        cl_body = cl_text.get("1.0", "end-1c").strip()

        selected_indices = project_listbox.curselection()
        selected_ids = []
        for idx in selected_indices:
            row = project_listbox.get(idx)
            pid = row.split(' - ', 1)[0].strip()
            selected_ids.append(pid)

        if not (job_type and role_title and company and job_link and summary and selected_ids):
            messagebox.showwarning("Missing Info", "Please complete all CV fields.")
            return

        if want_cl and not cl_body:
            messagebox.showwarning("Missing Info", "Please add cover letter body text or untick 'Include cover letter'.")
            return

        def work():
            try:
                root.config(cursor="watch")
                status_var.set("Creating files...")
                generate_btn.config(state="disabled")
                job_folder, cv_path = create_cv_for(job_type, role_title, company, job_link)
                if not job_folder:
                    messagebox.showerror("Error", "Failed to create files.")
                    return

                status_var.set("Customising CV template...")
                ok = customise_cv_content(
                    job_folder, cv_path, summary, selected_ids,
                    compile_var.get(), clean_var.get(), open_var.get(), id_to_path
                )
                if not ok:
                    return

                if want_cl:
                    status_var.set("Preparing cover letter...")
                    cl_path = create_cover_letter_for(job_type, job_folder)
                    if not cl_path:
                        return
                    ok2 = customise_cover_letter_content(
                        cl_path, cl_body, cl_compile_var.get(), clean_var.get(), cl_open_var.get(),
                    )
                    if not ok2:
                        return

                messagebox.showinfo("Success", "Documents created successfully.")
                open_folder_btn.config(state="normal", command=lambda p=job_folder: open_folder(p))
                status_var.set("Done.")
                clear_form()
            finally:
                root.config(cursor="")
                generate_btn.config(state="normal")

        threading.Thread(target=work, daemon=True).start()

    generate_btn.config(command=on_generate)
    root.bind("<Control-Return>", on_generate)

    root.mainloop()



if __name__ == "__main__":
    run_app()
