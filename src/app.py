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
import sv_ttk

# =========================
# USER-CONFIGURABLE: change this and run the app
# =========================
CANDIDATE_NAME = "Your Name"  # The name to write into \name{...} and filenames

# Optional preview deps (install: pip install pymupdf pillow)
try:
    import fitz  # PyMuPDF
    from PIL import Image, ImageTk
except Exception:
    fitz = None
    Image = ImageTk = None

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES  # type: ignore
except Exception:
    TkinterDnD = None
    DND_FILES = None

"""
CV/CL Builder GUI (single-template version)
- Creates per-job folders: jobs/<company>/<role[_n]>/
- Copies LaTeX templates and injects summary + selected projects
- (Optional) compiles and shows a right‑side PDF preview
- Drag-and-drop .tex project files into the Projects list to add them on the fly.

Main sections
1) Paths & template resolution
2) LaTeX helpers (escape, patchers, compile)
3) Project loader
4) Preview panel
5) GUI wiring
"""

# =========================
# 1) PATHS & TEMPLATE RESOLUTION
# =========================

def _win_no_window_flags():
    if platform.system() == "Windows":
        return 0x08000000  # CREATE_NO_WINDOW
    return 0

# During dev, this file lives in src/, so project root is parent.
DEV_ROOT = Path(__file__).resolve().parent.parent
# When packaged (PyInstaller), resources live in _MEIPASS.
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", str(DEV_ROOT)))

def RPATH(*parts) -> Path:
    """Resolve a resource path inside base/ or modules/ (dev or packaged)."""
    return (RESOURCE_ROOT / Path(*parts)).resolve()

# --- Single templates ---
CV_TEMPLATE = RPATH('base', 'cv.tex')
COVER_LETTER_TEMPLATE = RPATH('base', 'cover_letter.tex')

def resolve_template() -> Path | None:
    return CV_TEMPLATE if CV_TEMPLATE.exists() else None

def resolve_cover_template() -> Path | None:
    return COVER_LETTER_TEMPLATE if COVER_LETTER_TEMPLATE.exists() else None

# Simple name/slug utilities

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


def filename_prefix_from_name(full_name: str) -> str:
    """Return something like Last_First (letters only) for filenames."""
    parts = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", full_name)
    if not parts:
        return "Candidate"
    first = parts[0].title()
    last = parts[-1].title()
    return f"{last}_{first}"

# =========================
# 2) LaTeX HELPERS (escape, patchers, compile)
# =========================

def rewrite_module_inputs_to_absolute(tex_path: str):
    r"""Rewrite \input{...modules/...} and \includegraphics{...modules/...} to absolute paths.
    Keeps LaTeX happy when compiling from jobs/<company>/<role/>.
    """
    def absify(rel: str) -> str:
        rel_path = Path(rel).as_posix()
        while rel_path.startswith('../'):
            rel_path = rel_path[3:]
        if rel_path.startswith('./'):
            rel_path = rel_path[2:]
        abs_p = RPATH(*Path(rel_path).parts)
        return Path(abs_p).as_posix()

    try:
        text = Path(tex_path).read_text(encoding='utf-8')
        text = re.sub(r'\\input\{([^\}]*/?modules/[^\}]*)\}',
                        lambda m: '\\input{' + absify(m.group(1)) + '}', text)
        text = re.sub(r'(\\includegraphics(?:\[[^\]]*\])?\{)([^\}]*modules/[^\}]*)(\})',
                        lambda m: m.group(1) + absify(m.group(2)) + m.group(3), text)
        Path(tex_path).write_text(text, encoding='utf-8')
    except Exception:
        pass

# LaTeX escaping
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

def patch_cv_candidate_name(cv_path: str, full_name: str):
    """Replace the first \name{...} occurrence with the candidate's name.
    Uses a callable replacement so Python doesn't interpret backslashes
    like "\n" as a newline.
    """
    try:
        s = Path(cv_path).read_text(encoding='utf-8')
    except Exception:
        return
    escaped = latex_escape(full_name.strip()) if full_name else "Candidate"
    pattern = r"\\name\s*\{[^{}]*\}"

    def _repl(_m):
        return chr(92) + "name{" + escaped + "}"

    new_s, n = re.subn(pattern, _repl, s, count=1)
    if n:
        try:
            Path(cv_path).write_text(new_s, encoding='utf-8')
        except Exception:
            pass

# File IO

def read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.readlines()

def write_file(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)

# Clean aux files left by LaTeX

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

# Compilation pipeline: latexmk -> pdflatex x2 fallback

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
                creationflags=_win_no_window_flags(),
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
            creationflags=_win_no_window_flags(),
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

# ---- Small viewer for LaTeX log tail ----

def show_log_excerpt(log_path: str, parent):
    try:
        txt = Path(log_path).read_text(encoding="utf-8", errors="ignore")
        tail = "\n".join(txt.splitlines()[-150:])
    except Exception as e:
        tail = f"Could not read log: {e}"
    win = tk.Toplevel(parent)
    win.title("LaTeX log")
    win.geometry("900x600")
    t = tk.Text(win, wrap="none")
    t.pack(fill="both", expand=True)
    t.insert("1.0", tail)
    t.configure(state="disabled")
    ttk.Button(win, text="Open full log", command=lambda: open_pdf_viewer(log_path)).pack(anchor="e", padx=8, pady=8)


def compile_latex(tex_file_path, open_pdf=False, clean_files=False):
    tex_dir = os.path.dirname(tex_file_path)
    base_name = os.path.splitext(os.path.basename(tex_file_path))[0]
    pdf_path = os.path.join(tex_dir, base_name + ".pdf")
    log_path = os.path.join(tex_dir, base_name + ".log")

    env = os.environ.copy()
    env['TEXINPUTS'] = str(RPATH('base')) + os.pathsep + env.get('TEXINPUTS', '')

    try:
        ok = try_latexmk(tex_dir, base_name, env)
        if not ok:
            rc = compile_with_pdflatex(tex_dir, base_name, env)
            if rc != 0:
                raise RuntimeError("pdflatex failed")

        if clean_files:
            clean_latex_junk(tex_file_path, keep_log=False)
        if open_pdf:
            open_pdf_viewer(pdf_path)
        return True, None, log_path
    except Exception as e:
        if clean_files:
            clean_latex_junk(tex_file_path, keep_log=True)
        hint = f"{e} — check log: {log_path}"
        return False, hint, log_path


# =========================
# 3) PROJECT LOADER & CORE ACTIONS
# =========================

def load_projects():
    """Collect projects from base/projects.json plus modules/projects/*.tex."""
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

# Create job folder + copy CV template

def create_cv_for(role_title, company_name, job_link):
    template_path = resolve_template()
    if not template_path:
        messagebox.showerror("Error", "No CV template found at base/cv.tex.")
        return None, None

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        project_root = exe_dir.parent
    else:
        project_root = DEV_ROOT

    jobs_dir = project_root / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)

    company_slug = sanitize_name(company_name)
    company_dir = jobs_dir / company_slug
    company_dir.mkdir(parents=True, exist_ok=True)

    role_slug = sanitize_name(role_title or "general")
    base_folder = company_dir / role_slug
    job_folder = ensure_unique_folder(base_folder)
    job_folder.mkdir(parents=True, exist_ok=True)

    prefix = filename_prefix_from_name(CANDIDATE_NAME)
    destination_file = job_folder / f"{prefix}_CV.tex"

    try:
        shutil.copy(str(template_path), str(destination_file))
        rewrite_module_inputs_to_absolute(str(destination_file))
        patch_cv_candidate_name(str(destination_file), CANDIDATE_NAME)
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

# Fill summary + projects, optionally compile

def customise_cv_content(job_folder, cv_path, summary, selected_ids, compile_opt, clean_opt, open_opt, id_to_path, is_raw_summary: bool, ui_parent=None):
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

    raw = summary.strip()
    escaped_summary = raw if is_raw_summary else latex_escape(raw)

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
        ok, err, log_path = compile_latex(cv_path, open_pdf=False, clean_files=clean_opt)
        if not ok:
            if messagebox.askyesno("Compile failed", f"LaTeX compilation failed.\n\n{err}\n\nOpen log?"):
                parent = ui_parent or tk._get_default_root()
                show_log_excerpt(log_path, parent)
            return False

    return True

# Cover letter creation + fill + optional compile

def create_cover_letter_for(job_folder: str):
    tpl = resolve_cover_template()
    if not tpl:
        messagebox.showerror("Error", "No cover letter template found at base/cover_letter.tex.")
        return None

    prefix = filename_prefix_from_name(CANDIDATE_NAME)
    dest = Path(job_folder) / f"{prefix}_Cover_Letter.tex"
    try:
        shutil.copy(str(tpl), str(dest))
        rewrite_module_inputs_to_absolute(str(dest))
        patch_cv_candidate_name(str(dest), CANDIDATE_NAME)
        return str(dest)
    except Exception as e:
        messagebox.showerror("Error", f"Could not prepare cover letter file:\n{e}")
        return None

def customise_cover_letter_content(cl_tex_path: str, body_text: str, compile_opt: bool, clean_opt: bool, open_opt: bool, ui_parent=None):
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
        ok, err, log_path = compile_latex(cl_tex_path, open_pdf=False, clean_files=clean_opt)
        if not ok:
            if messagebox.askyesno("Compile failed", f"Cover letter LaTeX compilation failed.\n\n{err}\n\nOpen log?"):
                parent = ui_parent or tk._get_default_root()
                show_log_excerpt(log_path, parent)
            return False

    return True


# =========================
# 4) RIGHT‑SIDE PREVIEW PANEL
# =========================

def build_side_preview(root):
    """Right-side PDF preview with CV/CL selector, zoom (Ctrl+Wheel),
    and vertical/horizontal scrolling."""
    root.columnconfigure(3, weight=0, minsize=780)

    panel = ttk.Labelframe(root, text="Preview", padding=8)
    panel.grid(row=1, column=3, rowspan=10, sticky="nsew", padx=(4, 8), pady=8)

    # Top controls
    sel_var = tk.StringVar(value="cv")
    controls = ttk.Frame(panel)
    controls.pack(side="top", fill="x")
    ttk.Radiobutton(controls, text="CV", value="cv", variable=sel_var, command=lambda: do_refresh()).pack(side="left")
    ttk.Radiobutton(controls, text="Cover letter", value="cl", variable=sel_var, command=lambda: do_refresh()).pack(side="left", padx=(8, 0))

    # Scrollable canvas area
    outer = ttk.Frame(panel)
    outer.pack(side="top", fill="both", expand=True, pady=(6, 0))

    canvas = tk.Canvas(outer, highlightthickness=0)
    vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    hsb = ttk.Scrollbar(outer, orient="horizontal", command=canvas.xview)
    canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

    outer.columnconfigure(0, weight=1)
    outer.rowconfigure(0, weight=1)
    canvas.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    hsb.grid(row=1, column=0, sticky="ew")

    inner = ttk.Frame(canvas)
    canvas.create_window((0, 0), window=inner, anchor="nw")

    # State
    panel._img_refs = []
    panel._current_zoom = 1.2
    panel._min_zoom = 0.6
    panel._max_zoom = 3.0
    panel._zoom_step = 0.1
    panel._current_path = None

    # --- Scroll speed knobs ---
    panel._scroll_units_v = 2 
    panel._scroll_units_h = 2

    def _update_scroll_region(_evt=None):
        bbox = canvas.bbox("all")
        if bbox:
            canvas.configure(scrollregion=bbox)

    inner.bind("<Configure>", _update_scroll_region)
    panel.bind("<Configure>", _update_scroll_region)

    def render_pdf(path: str, zoom: float | None = None):
        for w in inner.winfo_children():
            w.destroy()
        panel._img_refs.clear()

        if not path or not os.path.exists(path):
            ttk.Label(inner, text="No PDF to show yet.").pack(side="top", anchor="w", padx=6, pady=(6, 6))
            _update_scroll_region()
            return

        if zoom is not None:
            panel._current_zoom = max(panel._min_zoom, min(panel._max_zoom, zoom))

        if not fitz or not Image or not ImageTk:
            ttk.Label(inner, text="Install 'pymupdf' and 'pillow' for in-app preview.").pack(pady=12)
            _update_scroll_region()
            return

        try:
            doc = fitz.open(path)
        except Exception as e:
            ttk.Label(inner, text=f"Could not open PDF: {e}").pack(pady=12)
            _update_scroll_region()
            return

        mat = fitz.Matrix(panel._current_zoom, panel._current_zoom)
        for i, page in enumerate(doc):
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            tkimg = ImageTk.PhotoImage(img)
            panel._img_refs.append(tkimg)
            lbl = tk.Label(inner, image=tkimg, anchor="nw")
            lbl.pack(padx=6, pady=(6 if i == 0 else 0, 6), anchor="nw")
        _update_scroll_region()

    def do_refresh():
        choice = sel_var.get()
        path = getattr(root, "last_cv_pdf", None) if choice == "cv" else getattr(root, "last_cl_pdf", None)
        panel._current_path = path
        render_pdf(path)
    
    def _is_over_preview():
        x, y = root.winfo_pointerx(), root.winfo_pointery()
        w = root.winfo_containing(x, y)
        while w is not None:
            if w is panel or w is canvas or w is inner:
                return True
            w = getattr(w, "master", None)
        return False

    # Interactions
    def _on_ctrl_wheel(event):
        if not _is_over_preview():
            return  # let the focused widget use Ctrl+Wheel
        direction = 1 if event.delta > 0 else -1
        new_zoom = panel._current_zoom + direction * panel._zoom_step
        new_zoom = max(panel._min_zoom, min(panel._max_zoom, new_zoom))
        if panel._current_path:
            render_pdf(panel._current_path, new_zoom)
        return "break"

    def _normalize_ticks(delta: int) -> float:
        # Windows/Linux: multiples of 120; macOS: ±1 typically
        if delta == 0:
            return 0.0
        return (delta / 120.0) if abs(delta) >= 120 else float(delta)

    def _on_wheel(event):
        if event.state & 0x0004:
            return  # Ctrl+Wheel is handled above
        if not _is_over_preview():
            return  # do not scroll preview when pointer is elsewhere
        ticks = _normalize_ticks(event.delta)
        units = int(-ticks * panel._scroll_units_v)
        canvas.yview_scroll(units, "units")
        return "break"

    def _on_shift_wheel(event):
        if not _is_over_preview():
            return
        ticks = _normalize_ticks(event.delta)
        units = int(-ticks * panel._scroll_units_h)
        canvas.xview_scroll(units, "units")
        return "break"

    canvas.bind_all("<Control-MouseWheel>", _on_ctrl_wheel)
    canvas.bind_all("<MouseWheel>", _on_wheel)
    canvas.bind_all("<Shift-MouseWheel>", _on_shift_wheel)


    def _reset_zoom(_evt=None):
        if panel._current_path:
            render_pdf(panel._current_path, 1.0)
    canvas.bind_all("<Control-Key-0>", _reset_zoom)

    return {
        "panel": panel,
        "select_var": sel_var,
        "refresh": do_refresh,
        "render": render_pdf,
        "get_zoom": lambda: panel._current_zoom,
        "set_zoom": lambda z: render_pdf(panel._current_path, z),
    }


# =========================
# 5) GUI WIRING (with drag-and-drop for .tex projects)
# =========================

def _parse_dnd_file_list(data: str) -> list[str]:
    """Parse DND_FILES payload into a list of paths (handles braces/quotes)."""
    if not data:
        return []
    parts = []
    buf = ''
    in_brace = False
    for ch in data:
        if ch == '{':
            in_brace = True
            buf = ''
        elif ch == '}':
            in_brace = False
            parts.append(buf)
            buf = ''
        elif ch == ' ' and not in_brace:
            if buf:
                parts.append(buf)
                buf = ''
        else:
            buf += ch
    if buf:
        parts.append(buf)
    return parts

# ---- ensure we can write into modules/projects and copy files there ----

def _ensure_projects_dir_writable() -> Path | None:
    dest = RPATH('modules', 'projects')
    try:
        dest.mkdir(parents=True, exist_ok=True)
        test = dest / '.__write_test__'
        test.write_text('ok', encoding='utf-8')
        test.unlink(missing_ok=True)
        return dest
    except Exception as e:
        messagebox.showerror("Projects folder not writable",
                             f"Cannot write to {dest}.\nReason: {e}\n\nIf you're running a packaged build, run from source or choose a writable location.")
        return None

def _copy_tex_into_projects(src: Path) -> Path | None:
    dest_dir = _ensure_projects_dir_writable()
    if not dest_dir:
        return None
    base = sanitize_name(src.stem) or 'project'
    dest = dest_dir / f"{base}.tex"
    n = 1
    while dest.exists():
        dest = dest_dir / f"{base}_{n}.tex"
        n += 1
    try:
        shutil.copy2(src, dest)
        return dest
    except Exception as e:
        messagebox.showerror("Copy failed", f"Could not copy {src} -> {dest}:\n{e}")
        return None

def run_app():
    # Use TkinterDnD if available for native drag-and-drop
    if TkinterDnD is not None:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    root.title("")

    # Dark theme + base typography
    sv_ttk.set_theme("dark")
    style = ttk.Style()
    style.configure(".", font=("Segoe UI", 10))
    for k in ("TCombobox", "TLabel", "TEntry", "TCheckbutton"):
        style.configure(k, font=("Segoe UI", 11))
    root.option_add('*TCombobox*Listbox.font', ('Segoe UI', 11))

    projects, id_to_path = load_projects()
    if not projects:
        messagebox.showerror("Error", "No projects found. Add base/projects.json or put .tex files in modules/projects/")
        return

    # Main grid: columns 0..2 form, 3 preview
    for c in (0, 1, 2, 3):
        root.columnconfigure(c, weight=1 if c in (1, 2) else 0)
    root.rowconfigure(1, weight=1)

    # Preview panel (right)
    preview = build_side_preview(root)
    root._preview = preview

    # ----- Left form (labels column compact; inputs expand)
    form = ttk.Frame(root)
    form.grid(row=1, column=0, columnspan=3, sticky="nsew", padx=8, pady=(8, 4))
    form.columnconfigure(0, weight=0)
    form.columnconfigure(1, weight=1)
    form.rowconfigure(4, weight=1)  # summary
    form.rowconfigure(5, weight=1)  # projects

    # Company (row 0)
    ttk.Label(form, text="Company:").grid(row=0, column=0, sticky="e", padx=(0, 8), pady=4)
    company_entry = ttk.Entry(form, width=40)
    company_entry.grid(row=0, column=1, sticky="we", pady=4)

    # Role (row 1)
    ttk.Label(form, text="Role Title:").grid(row=1, column=0, sticky="e", padx=(0, 8), pady=4)
    role_entry = ttk.Entry(form, width=40)
    role_entry.grid(row=1, column=1, sticky="we", pady=4)

    # Link (row 2)
    ttk.Label(form, text="Job Link:").grid(row=2, column=0, sticky="e", padx=(0, 8), pady=4)
    job_link_entry = ttk.Entry(form, width=40)
    job_link_entry.grid(row=2, column=1, sticky="we", pady=4)

    # --- Raw LaTeX toggle (above Summary) ---
    raw_summary_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        form,
        text="Insert raw LaTeX in summary",
        variable=raw_summary_var
    ).grid(row=3, column=1, sticky="w", pady=(0, 0))

    # Summary (row 4)
    ttk.Label(form, text="Summary:").grid(row=4, column=0, sticky="ne", padx=(0, 8), pady=4)
    summary_outer = ttk.Frame(form)
    summary_outer.grid(row=4, column=1, sticky="nsew", pady=4)

    summary_frame = ttk.Frame(summary_outer)
    summary_frame.pack(side="top", fill="both", expand=True)
    summary_text = tk.Text(summary_frame, width=80, height=6, wrap="word")
    summary_text.pack(side="left", fill="both", expand=True)
    summary_scroll = ttk.Scrollbar(summary_frame, command=summary_text.yview)
    summary_scroll.pack(side="right", fill="y")
    summary_text.config(font=("Segoe UI", 11), yscrollcommand=summary_scroll.set, exportselection=False)

    counter_var = tk.StringVar(value="0 chars")
    counter_label = ttk.Label(summary_outer, textvariable=counter_var, anchor="e")
    counter_label.pack(side="bottom", anchor="e", pady=(2, 0))

    def update_count(_evt=None):
        n = len(summary_text.get("1.0", "end-1c"))
        counter_var.set(f"{n} chars")

    # Projects list (row 5)
    ttk.Label(form, text="Projects:").grid(row=5, column=0, sticky="ne", padx=(0, 8), pady=4)

    # Outer container with grid so the button can sit below the list
    proj_outer = ttk.Frame(form)
    proj_outer.grid(row=5, column=1, sticky="nsew", pady=4)
    proj_outer.columnconfigure(0, weight=1)
    proj_outer.rowconfigure(0, weight=1)

    # Drop zone header (top)
    drop_hdr = ttk.Label(proj_outer, text=("Drag .tex files here (will copy to modules/projects)" if TkinterDnD else "Add .tex with button (DnD addon not installed)"), anchor="w")
    drop_hdr.grid(row=0, column=0, sticky="we", pady=(0, 4))

    # List+scrollbar frame
    list_frame = ttk.Frame(proj_outer)
    list_frame.grid(row=1, column=0, sticky="nsew")
    list_frame.columnconfigure(0, weight=1)

    project_listbox = tk.Listbox(list_frame, selectmode=tk.MULTIPLE, width=28, height=5, exportselection=False)
    project_listbox.pack(side="left", fill="both", expand=True)
    proj_scroll = ttk.Scrollbar(list_frame, command=project_listbox.yview)
    proj_scroll.pack(side="right", fill="y")
    project_listbox.config(yscrollcommand=proj_scroll.set, font=("Segoe UI", 11))

    # Populate list
    for p in projects:
        project_listbox.insert(tk.END, f"{p['id']} - {p['name']}")

    # Helper to add new project paths dynamically (COPY into modules/projects)
    def add_project_paths(paths: list[str]):
        nonlocal projects, id_to_path
        dest_dir = _ensure_projects_dir_writable()
        if not dest_dir:
            return
        added = 0
        for pth in paths:
            src = Path(pth).resolve()
            if not src.exists() or src.suffix.lower() != '.tex':
                continue
            dest = _copy_tex_into_projects(src)
            if not dest:
                continue
            # Avoid duplicates by absolute path
            if any(Path(x['path']).resolve() == dest.resolve() for x in projects):
                continue
            pid = str(len(projects) + 1)
            name = dest.stem.replace('_', ' ').title()
            rec = {'id': pid, 'name': name, 'path': str(dest.resolve())}
            projects.append(rec)
            id_to_path[pid] = str(dest.resolve())
            project_listbox.insert(tk.END, f"{pid} - {name}")
            added += 1
        if added:
            messagebox.showinfo("Projects", f"Copied & added {added} project(s) to modules/projects.")

    # Fallback: add files button
    def _add_files_dialog():
        from tkinter import filedialog
        files = filedialog.askopenfilenames(title="Select .tex project files", filetypes=[("TeX files", "*.tex")])
        add_project_paths(list(files))

    # Button below list (row 2)
    add_btn = ttk.Button(proj_outer, text="Add .tex files…", command=_add_files_dialog)
    add_btn.grid(row=2, column=0, sticky="w", pady=(6, 0))

    # DnD bindings if available
    if TkinterDnD and DND_FILES:
        def _on_enter(_e):
            project_listbox.configure(highlightthickness=2, highlightbackground="#5cb85c")
        def _on_leave(_e):
            project_listbox.configure(highlightthickness=0)
        project_listbox.drop_target_register(DND_FILES)
        project_listbox.dnd_bind('<<DropEnter>>', _on_enter)
        project_listbox.dnd_bind('<<DropLeave>>', _on_leave)
        def _on_drop(e):
            project_listbox.configure(highlightthickness=0)
            paths = _parse_dnd_file_list(e.data)
            add_project_paths(paths)
            return 'break'
        project_listbox.dnd_bind('<<Drop>>', _on_drop)

    # CV options
    compile_var = tk.BooleanVar(value=True)
    clean_var = tk.BooleanVar(value=True)

    cv_opts = ttk.Frame(root)
    cv_opts.grid(row=7, column=0, columnspan=3, sticky="w", padx=8, pady=(2, 2))
    ttk.Checkbutton(cv_opts, text="Compile CV to PDF", variable=compile_var).pack(side="left", padx=(0, 15))
    ttk.Checkbutton(cv_opts, text="Clean LaTeX junk files", variable=clean_var).pack(side="left")

    # Cover letter toggle + editor
    include_cl_var = tk.BooleanVar(value=False)
    include_cl_chk = ttk.Checkbutton(root, text="Include cover letter", variable=include_cl_var)
    include_cl_chk.grid(row=8, column=0, sticky="w", padx=8, pady=(8, 0))

    cl_frame = ttk.Labelframe(root, text="Cover letter", labelanchor="nw")
    ttk.Style().configure("TLabelframe.Label", font=("Segoe UI", 11))

    cl_text_frame = ttk.Frame(cl_frame)
    cl_text_frame.grid(row=0, column=0, columnspan=3, sticky="nsew", padx=8, pady=(8, 8))
    cl_text = tk.Text(cl_text_frame, width=80, height=10, wrap="word")
    cl_text.pack(side="left", fill="both", expand=True)
    cl_scroll = ttk.Scrollbar(cl_text_frame, command=cl_text.yview)
    cl_scroll.pack(side="right", fill="y")
    cl_text.config(font=("Segoe UI", 11), yscrollcommand=cl_scroll.set)

    cl_counter_var = tk.StringVar(value="0 chars")
    cl_counter_label = ttk.Label(cl_frame, textvariable=cl_counter_var)
    cl_counter_label.grid(row=1, column=2, sticky="e", padx=8, pady=(0, 8))

    def update_cl_count(_evt=None):
        n = len(cl_text.get("1.0", "end-1c"))
        cl_counter_var.set(f"{n} chars")

    cl_text.bind("<KeyRelease>", update_cl_count)
    summary_text.bind("<KeyRelease>", update_count)

    cl_compile_var = tk.BooleanVar(value=True)
    cl_opts = ttk.Frame(cl_frame)
    cl_opts.grid(row=2, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 8))
    ttk.Checkbutton(cl_opts, text="Compile CL to PDF", variable=cl_compile_var).pack(side="left")

    for c in (0, 1, 2):
        cl_frame.columnconfigure(c, weight=1 if c != 0 else 0)
    cl_frame.rowconfigure(0, weight=1)

    def toggle_cover():
        if include_cl_var.get():
            cl_frame.grid(row=9, column=0, columnspan=3, sticky="nsew", padx=8, pady=(6, 4))
        else:
            cl_frame.grid_remove()

    include_cl_chk.config(command=toggle_cover)
    toggle_cover()

    # Status + actions
    status_var = tk.StringVar(value="")
    status_label = ttk.Label(root, textvariable=status_var, anchor="w")
    status_label.grid(row=10, column=0, columnspan=2, sticky="we", padx=8, pady=(4, 0))

    open_folder_btn = ttk.Button(root, text="Open job folder", state="disabled")
    open_folder_btn.grid(row=11, column=0, sticky="w", padx=8, pady=8)

    generate_btn = ttk.Button(root, text="Generate")
    generate_btn.grid(row=11, column=3, sticky="e", padx=8, pady=8)

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
        raw_summary_var.set(False)

    def on_generate(_evt=None):
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

        if not (role_title and company and job_link and summary and selected_ids):
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
                job_folder, cv_path = create_cv_for(role_title, company, job_link)
                if not job_folder:
                    messagebox.showerror("Error", "Failed to create files.")
                    return

                status_var.set("Customising CV template...")
                ok = customise_cv_content(
                    job_folder, cv_path, summary, selected_ids,
                    compile_var.get(), clean_var.get(), False,
                    id_to_path,
                    raw_summary_var.get(),
                    ui_parent=root
                )
                if not ok:
                    return

                cl_path = None
                if want_cl:
                    status_var.set("Preparing cover letter...")
                    cl_path = create_cover_letter_for(job_folder)
                    if not cl_path:
                        return
                    ok2 = customise_cover_letter_content(
                        cl_path, cl_body, cl_compile_var.get(), clean_var.get(), False,
                        ui_parent=root
                    )
                    if not ok2:
                        return

                open_folder_btn.config(state="normal", command=lambda p=job_folder: open_folder(p))
                status_var.set("Done.")

                # Update preview sources
                cv_pdf = cv_path[:-4] + ".pdf"
                root.last_cv_pdf = cv_pdf if os.path.exists(cv_pdf) else None
                root.last_cl_pdf = None
                if want_cl and cl_path:
                    cl_pdf = str(Path(cl_path).with_suffix(".pdf"))
                    root.last_cl_pdf = cl_pdf if os.path.exists(cl_pdf) else None

                # Always show preview (CV first)
                root._preview["select_var"].set("cv")
                root._preview["refresh"]()

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
