# CV/CL Builder — README

A tiny app that assembles a tailored CV (and optional cover letter) from LaTeX templates. It inserts your summary and selected project snippets, compiles to PDF, and stores each application in its own folder.

---

## Quick start (Windows)

1. **Install Python 3.10+**

2. **Install MiKTeX (full + auto-packages)**

   * Download MiKTeX: [miktex.org/download](https://miktex.org/download)
   * During install, set **“Install missing packages on-the-fly” → *Yes***.
   * Open **MiKTeX Console → Updates → Check for updates** and apply all.
   * Verify in a new PowerShell:
     ```ps1
     Get-Command pdflatex
     pdflatex --version
     ```



3. **Create & activate a virtual environment**

   ```bat
   python -m venv .venv
   .venv\Scripts\activate
   python -m pip install --upgrade pip
   ```

4. **Install Python dependencies (from `requirements.txt`)**

   ```bat
   pip install -r requirements.txt
   ```

   > The provided `requirements.txt` includes optional preview/drag‑and‑drop packages. If you prefer a minimal setup, remove `pymupdf`, `pillow`, or `tkinterdnd2`.

5. **Set your name once**

   * Open the script (e.g. `src/app.py`).
   * At the very top, set:

     ```python
     CANDIDATE_NAME = "Your Name"
     ```

6. **Run the app**

   ```bat
   python -m src.app
   ```

---

## Folder layout

```
base/
  cv.tex              # main CV template (contains the markers below)
  cover_letter.tex    # cover letter template (contains “% PASTE HERE”)
  resume.cls          # class used by cv.tex
  projects.json       # (optional) project list for the UI
modules/
  education.tex       # user-editable section (generic by default)
  skills.tex          # user-editable section (generic by default)
  experience.tex      # user-editable section (generic by default)
  projects/
    template_project.tex  # example project snippet (.tex)
src/
  app.py              # the GUI app (this repo’s main script)
jobs/                 # auto-created per application (output)
```

---

## The two markers the app needs

Your `base/cv.tex` **must** contain these exact comments so the app knows where to inject:

* `%% PASTE SUMMARY HERE` — your summary text goes here
* `%% PROJECT PATHS HERE` — `\input{...}` lines for the chosen projects go here

> Keep these markers inside the body (after `\begin{document}`) to avoid LaTeX errors.

---

## Using the app

1. **Company** — name of the company you’re applying to.
2. **Role Title** — title of the job.
3. **Job Link** — just saved into `job-notes.md` for your records.
4. **Summary** — paste or type your tailored summary. Toggle *Insert raw LaTeX in summary* if you want to include LaTeX markup.
5. **Projects** — select 1–4 relevant items from the list.
6. Options:

   * **Compile CV to PDF** — build a PDF via `latexmk` (or `pdflatex` fallback).
   * **Clean LaTeX junk files** — removes `.aux/.out/.toc/...` after compile.
   * **Include cover letter** — paste body text; the app fills and compiles `cover_letter.tex`.
7. Click **Generate**.

**Output** (example):

```
jobs/<company>/<role>/
  Last_First_CV.tex
  Last_First_CV.pdf
  Last_First_Cover_Letter.tex   # if selected
  Last_First_Cover_Letter.pdf   # if selected
  job-notes.md
```

---

## Adding projects

You have two ways to populate the **Projects** list:

### A) Put `.tex` files in `modules/projects/`

Any `*.tex` you place here will be picked up automatically.

### B) List them in `base/projects.json` (optional)

Provide a stable list with IDs and friendly names:

```json
[
  {"id": 1, "name": "Data Pipeline on AWS", "path": "modules/projects/data_pipeline.tex"},
  {"id": 2, "name": "FPGA Traffic Light Controller", "path": "modules/projects/fpga_traffic_light.tex"}
]
```

> The app also lets you drag‑and‑drop `.tex` files (requires `tkinterdnd2`). Dropped files are copied into `modules/projects/` and added to the list.

Each project snippet should be a self-contained LaTeX chunk, e.g.:

```tex
\begin{rSubsection}{Project Title}{Jan. 2024 – Apr. 2024}{Role / Course}{ }
  \item Built X using Y; achieved Z (\\textbf{metrics}).
  \item Deployed on <cloud/board>; automated with CI/CD; tests at N%\,coverage.
\end{rSubsection}
```

---

## Personalising the generic sections

Edit the files in `modules/` to reflect your background:

* `education.tex` — keep it degree‑agnostic if you plan to share the template, or fill in your real institution, degree, and highlights.
* `skills.tex` — group by **Languages**, **Tools**, **Platforms/Cloud**, **Data/ML**, **Hardware** — whatever fits you. Keep it consistent and brief.
* `experience.tex` — duplicate the example `rSubsection` blocks and tailor for your roles.

> The idea: the template ships with **anonymous, reusable** sections so anyone can adopt it. Each user then edits `modules/*.tex` once, and only tailors the Summary and chosen Projects per application.

