import ast
import os
import sys
import subprocess
import tempfile
import shutil
import base64
import json
import queue
import threading
import hashlib
import time
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

# ============================================================
# FINGERPRINTER SETUP
# GitHub-backed Windows installation wizard
# ============================================================

OWNER = "Riskingfire"
REPO = "fingerprinter"
BRANCH = "main"

# Installer self-update & Asset repository details (using fingerprinter)
INSTALLER_OWNER = "Riskingfire"
INSTALLER_REPO = "fingerprinter"
INSTALLER_BRANCH = "main"
INSTALLER_PATH = "installers"

API_ROOT = f"https://api.github.com/repos/{OWNER}/{REPO}"
RAW_ROOT = f"https://raw.githubusercontent.com/{OWNER}/{REPO}/{BRANCH}"

INSTALLER_API_ROOT = f"https://api.github.com/repos/{INSTALLER_OWNER}/{INSTALLER_REPO}/contents/{INSTALLER_PATH}?ref={INSTALLER_BRANCH}"
INSTALLER_RAW_ROOT = f"https://raw.githubusercontent.com/{INSTALLER_OWNER}/{INSTALLER_REPO}/{INSTALLER_BRANCH}/{INSTALLER_PATH}"
ASSET_RAW_ROOT = f"https://raw.githubusercontent.com/{INSTALLER_OWNER}/{INSTALLER_REPO}/{INSTALLER_BRANCH}"

VERSIONS_DIR = "main/versions"
BASES_DIR = "bases"
STANDALONES_DIR = "standalones"
FP_DIR = "fp"              # required core package  -> <install>/fp/
MODULES_FOLDER = "modules"  # optional modules       -> <install>/modules/
NO_STANDALONE = "(select a file)"

# standalones/<language>/<file>   - language folder names -> canonical language
LANGUAGE_FOLDERS = {"python": "python", "py": "python", "cpp": "cpp", "c++": "cpp", "cplusplus": "cpp"}
# used when standalones/ has no language folders: guess the language from the extension
LANGUAGE_EXTENSIONS = {
    ".py": "python", ".pyw": "python",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".c": "cpp",
}
PYTHON_EXTENSIONS = (".py", ".pyw")
SOURCE_EXTENSIONS = (".py", ".pyw", ".c", ".cc", ".cxx", ".cpp", ".h", ".hpp")  # deleted with the cache after a build
CPP_COMPILABLE = (".cpp", ".cc", ".cxx", ".c")

DEFAULT_INSTALL = os.path.join(
    os.environ.get("LOCALAPPDATA", str(Path.home())),
    "Programs",
    "Fingerprinter",
)

BG = "#FFFFFF"
SIDEBAR = "#FFFFFF"
PANEL = "#FFFFFF"
FIELD = "#FFFFFF"
BORDER = "#B7B7B7"
TEXT = "#000000"
MUTED = "#333333"
ACCENT = "#E1E1E1"
ACCENT_HOVER = "#D5D5D5"
SUCCESS = "#000000"


class InstallerError(Exception):
    pass


# ============================================================
# GitHub helpers
# ============================================================

def github_get_json(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Fingerprinter-Installer",
            "Accept": "application/vnd.github+json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise InstallerError(
            f"GitHub returned HTTP {exc.code}.\n\n"
            f"URL: {url}\n\n"
            "Check the repository name, branch, and access permissions."
        ) from exc
    except Exception as exc:
        raise InstallerError(
            f"Could not contact GitHub.\n\n{exc}"
        ) from exc


def download_bytes(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Fingerprinter-Installer"},
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except Exception as exc:
        raise InstallerError(
            f"Download failed:\n{url}\n\n{exc}"
        ) from exc


def get_file_sha256(path):
    sha256_hash = hashlib.sha256()
    with open(path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def safe_relative_path(path):
    path = path.replace("\\", "/").strip("/")
    parts = path.split("/")

    if (
        not path
        or any(part in ("", ".", "..") for part in parts)
        or ":" in parts[0]
    ):
        raise InstallerError(f"Unsafe repository path: {path}")

    return Path(*parts)


def is_downloadable(path):
    lowered = path.lower()
    parts = lowered.split("/")

    if lowered.endswith((".pyc", ".pyo")):
        return False
    if "__pycache__" in parts:
        return False
    if lowered.endswith(".ds_store"):
        return False

    return True


def version_sort_key(value):
    import re

    parts = re.findall(r"\d+|[^\d]+", value.lower())
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in parts
    )


# ============================================================
# Shortcut and EXE Creation Helpers
# ============================================================

def create_desktop_shortcut(target_path, shortcut_name="Fingerprinter", icon_path=None):
    """Creates a Windows desktop shortcut using a VBS script fallback."""
    try:
        desktop = Path(os.path.join(os.path.expanduser("~"), "Desktop"))
        shortcut_path = desktop / f"{shortcut_name}.lnk"
        
        icon_line = f'oLink.IconLocation = "{icon_path}, 0"' if icon_path and Path(icon_path).exists() else ""
        vbs_script = f"""
        Set oWS = WScript.CreateObject("WScript.Shell")
        sLinkFile = "{shortcut_path}"
        Set oLink = oWS.CreateShortcut(sLinkFile)
        oLink.TargetPath = "{target_path}"
        oLink.WorkingDirectory = "{Path(target_path).parent}"
        {icon_line}
        oLink.Save
        """
        vbs_path = desktop / "create_shortcut.vbs"
        vbs_path.write_text(vbs_script)
        subprocess.run(["cscript", "//nologo", str(vbs_path)], check=True)
        if vbs_path.exists():
            vbs_path.unlink()
        return True
    except Exception as e:
        print(f"Failed to create shortcut: {e}")
        return False


def find_python():
    """Interpreter used to run PyInstaller."""
    if not getattr(sys, "frozen", False):
        return sys.executable

    for name in ("python", "py"):
        found = shutil.which(name)
        if found and _run([found, "-c", "import sys"]).returncode == 0:
            return found

    raise InstallerError(
        "Could not find a Python interpreter on this PC.\n\n"
        "Install Python from python.org (tick 'Add python.exe to PATH') "
        "or run this installer as a .py file instead of an .exe."
    )


def _run(cmd, cwd=None):
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        errors="replace",
        cwd=cwd,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _can_import(py, name):
    return _run([py, "-c", f"import {name}"]).returncode == 0


def _pip_install(py, package):
    result = _run([py, "-m", "pip", "install", package])
    if result.returncode != 0:
        result = _run([py, "-m", "pip", "install", "--user", package])
    return result


def _imports_used(version_dir):
    own = {"fp", "fingerprinter", "fp_modules"}
    own |= {p.stem for p in Path(version_dir).rglob("*.py")}
    found = set()

    for path in Path(version_dir).rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                if name.split(".")[0] not in own:
                    found.add(name)
    return sorted(found)


def compile_to_exe(entry_script, version_dir, log=lambda message: None, exe_name="Fingerprinter", output_dir=None, full_app=True, icon_path=None):
    version_dir = Path(version_dir)
    output_dir = Path(output_dir) if output_dir else version_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    py = find_python()
    log(f"Using Python: {py}")

    if not _can_import(py, "PyInstaller"):
        log("Installing PyInstaller...")
        result = _pip_install(py, "pyinstaller")
        if result.returncode != 0 or not _can_import(py, "PyInstaller"):
            raise InstallerError(
                f"PyInstaller could not be installed for:\n{py}\n\n"
                f"Try manually:\n\"{py}\" -m pip install pyinstaller\n\n"
                f"{(result.stderr or result.stdout)[-1500:]}"
            )

    for package in (("numpy", "pynput") if full_app else ()):
        if not _can_import(py, package):
            log(f"Installing {package}...")
            result = _pip_install(py, package)
            if result.returncode != 0 or not _can_import(py, package):
                raise InstallerError(
                    f"'{package}' is not installed for:\n{py}\n\n"
                    f"Install it with:\n\"{py}\" -m pip install {package}"
                )
    if full_app and not _can_import(py, "psutil"):
        _pip_install(py, "psutil")

    work = Path(tempfile.mkdtemp(prefix="fp_build_"))
    cmd = [
        py, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--noconsole", "--onefile",
        "--name", exe_name,
        "--distpath", str(output_dir),
        "--workpath", str(work / "build"),
        "--specpath", str(work),
        "--paths", str(version_dir),
    ]
    if icon_path and Path(icon_path).exists():
        cmd += ["--icon", str(icon_path)]

    if full_app:
        cmd += ["--collect-submodules", "pynput"]

    modules_dir = version_dir / "modules"
    if modules_dir.is_dir():
        cmd += ["--add-data", f"{modules_dir}{os.pathsep}modules"]

    for name in _imports_used(version_dir):
        cmd += ["--hidden-import", name]

    cmd.append(str(entry_script))

    try:
        result = _run(cmd, cwd=str(version_dir))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    exe = output_dir / f"{exe_name}.exe"
    if result.returncode != 0 or not exe.exists():
        raise InstallerError(
            "Compilation failed:\n\n" + (result.stderr or result.stdout)[-2000:]
        )
    return exe


# ============================================================
# Installer application
# ============================================================

class FingerprinterInstaller:
    def __init__(self, root):
        self.root = root
        self.root.title("Fingerprinter Setup")
        
        # Download app.ico for UI and taskbar usage
        self.icon_cache_path = Path(tempfile.gettempdir()) / "app_fingerprinter.ico"
        try:
            ico_data = download_bytes(f"{ASSET_RAW_ROOT}/app.ico")
            self.icon_cache_path.write_bytes(ico_data)
            
            # Set window icon and taskbar icon
            self.root.iconbitmap(str(self.icon_cache_path))
        except Exception as e:
            print(f"Could not load app.ico: {e}")
            self.icon_cache_path = None

        self.root.geometry("940x680")
        self.root.minsize(820, 600)
        self.root.configure(bg=BG)

        self.events = queue.Queue()
        self.busy = False
        self.cancel_requested = False

        self.tree = []
        self.tree_by_path = {}
        self.versions = []
        self.bases = []
        self.manifest = {"features": [], "modules": []}

        self.version_var = tk.StringVar()
        self.base_var = tk.StringVar()
        self.standalone_file_var = tk.StringVar(value=NO_STANDALONE)
        self.standalone_open = False
        self.standalones = {}
        self.install_targets = {}
        self.install_standalone = None
        self.destination_var = tk.StringVar(value=DEFAULT_INSTALL)
        self.standalone_language = tk.StringVar(value="python")
        self.create_shortcut_var = tk.BooleanVar(value=True)
        self.compile_exe_var = tk.BooleanVar(value=False)
        self.keep_source_var = tk.BooleanVar(value=False)

        self.feature_vars = {}
        self.standalone_vars = {}
        self.extra_open = False

        self.current_step = 0
        self.steps = [
            "Welcome",
            "Location",
            "Version",
            "Components",
            "Ready to install",
            "Installing",
            "Finished",
        ]

        self.style = ttk.Style()
        self.style.theme_use("clam")

        self.style.configure(
            "FP.Horizontal.TProgressbar",
            troughcolor="#303644",
            background=ACCENT,
            bordercolor="#303644",
            lightcolor=ACCENT,
            darkcolor=ACCENT,
        )
        self.style.configure(
            "FP.TCombobox",
            fieldbackground=FIELD,
            background=PANEL,
            foreground=TEXT,
            arrowcolor=TEXT,
            bordercolor=BORDER,
        )
        self.style.map(
            "FP.TCombobox",
            fieldbackground=[("readonly", FIELD)],
            foreground=[("readonly", TEXT)],
            selectbackground=[("readonly", FIELD)],
            selectforeground=[("readonly", TEXT)],
        )

        self._build_shell()
        self._show_step(0)

        self.root.after(100, self._poll_events)
        
        # Check for installer updates first, then load repository tree
        threading.Thread(target=self._check_and_perform_installer_update, daemon=True).start()
        self._start_background(self._load_repository, "Connecting to GitHub...")

    def _check_and_perform_installer_update(self):
        """Checks the single installer file in Riskingfire/fingerprinter/installers."""
        try:
            current_file = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
            local_hash = get_file_sha256(current_file)

            contents = github_get_json(INSTALLER_API_ROOT)
            if not isinstance(contents, list) or not contents:
                return

            remote_file_info = None
            for item in contents:
                if item.get("type") == "file" and item.get("name", "").endswith((".py", ".exe")):
                    remote_file_info = item
                    break

            if not remote_file_info:
                return

            remote_filename = remote_file_info["name"]
            remote_download_url = remote_file_info["download_url"]
            
            script_bytes = download_bytes(remote_download_url)
            remote_hash = hashlib.sha256(script_bytes).hexdigest()

            if remote_hash != local_hash:
                self.events.put(("status", "New installer version found!"))
                
                should_update = tk.BooleanVar(value=False)
                update_event = threading.Event()

                def prompt_user():
                    should_update.set(messagebox.askyesno(
                        "Update Available",
                        f"A newer installer version ({remote_filename}) has been found on GitHub.\nWould you like to update now?",
                        parent=self.root
                    ))
                    update_event.set()

                self.root.after(0, prompt_user)
                update_event.wait()

                if should_update.get():
                    self.events.put(("status", "Downloading updated installer to cache..."))
                    cache_dir = Path(tempfile.mkdtemp(prefix="fp_inst_cache_"))
                    try:
                        script_path = cache_dir / remote_filename
                        script_path.write_bytes(script_bytes)

                        self.events.put(("status", "Compiling new installer..."))
                        log_func = lambda m: print(m)
                        new_exe_path = compile_to_exe(
                            script_path,
                            cache_dir,
                            log=log_func,
                            exe_name=Path(remote_filename).stem,
                            output_dir=current_file.parent,
                            full_app=False,
                            icon_path=self.icon_cache_path
                        )

                        if new_exe_path and new_exe_path.exists():
                            self.events.put(("status", "Launching updated installer..."))
                            subprocess.Popen([str(new_exe_path), "--delete-old", str(current_file)])
                            self.root.after(100, self.root.destroy)
                    finally:
                        shutil.rmtree(cache_dir, ignore_errors=True)
        except Exception as e:
            print(f"Installer update check failed: {e}")

    # --------------------------------------------------------
    # Window shell
    # --------------------------------------------------------

    def _build_shell(self):
        self.root.configure(bg="#FFFFFF")
        header = tk.Frame(self.root, bg="#FFFFFF", height=82)
        header.pack(fill="x", padx=18, pady=(12, 0))
        header.pack_propagate(False)

        tk.Label(
            header, text="Welcome to the Fingerprinter Setup Wizard",
            bg="#FFFFFF", fg="#000000", font=("Segoe UI", 16),
        ).pack(anchor="w", pady=(10, 0))
        tk.Label(
            header,
            text="This wizard will guide you through the installation of Fingerprinter.",
            bg="#FFFFFF", fg="#333333", font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(5, 0))
        tk.Frame(self.root, bg="#B7B7B7", height=1).pack(fill="x", pady=(8, 0))

        self.main = tk.Frame(self.root, bg="#FFFFFF")
        self.main.pack(fill="both", expand=True, padx=18, pady=12)
        self.step_labels = []

        self.content = tk.Frame(self.main, bg="#FFFFFF")
        self.content.pack(fill="both", expand=True)
        self.page_area = tk.Frame(self.content, bg="#FFFFFF")
        self.page_area.pack(fill="both", expand=True, padx=10, pady=8)

        self.footer = tk.Frame(self.root, bg="#F0F0F0", height=58)
        self.footer.pack(fill="x", side="bottom")
        self.footer.pack_propagate(False)
        tk.Frame(self.footer, bg="#B7B7B7", height=1).pack(fill="x")

        self.footer_status = tk.Label(
            self.footer, text="Connecting to GitHub...",
            bg="#F0F0F0", fg="#333333", font=("Segoe UI", 9),
        )
        self.footer_status.pack(side="left", padx=18, pady=15)

        self.cancel_button = self._button(self.footer, "Cancel", self.cancel, secondary=True)
        self.cancel_button.pack(side="right", padx=(6, 18), pady=10)
        self.next_button = self._button(self.footer, "Next >", self.next_step)
        self.next_button.pack(side="right", padx=6, pady=10)
        self.back_button = self._button(self.footer, "< Back", self.back_step, secondary=True)
        self.back_button.pack(side="right", padx=6, pady=10)

    def _button(self, parent, text, command, secondary=False):
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg="#E1E1E1",
            fg=TEXT,
            activebackground=(
                "#41495B" if secondary else ACCENT_HOVER
            ),
            activeforeground="#FFFFFF",
            relief="flat",
            bd=0,
            padx=17,
            pady=9,
            font=("Segoe UI", 9, "bold"),
            cursor="arrow",
            disabledforeground="#788195",
        )

    def _clear_page(self):
        for child in self.page_area.winfo_children():
            child.destroy()

    def _heading(self, title, subtitle):
        tk.Label(
            self.page_area,
            text=title,
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 21, "bold"),
            anchor="w",
        ).pack(fill="x")

        tk.Label(
            self.page_area,
            text=subtitle,
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 10),
            justify="left",
            wraplength=590,
            anchor="w",
        ).pack(fill="x", pady=(8, 22))

    def _card(self, parent=None):
        return tk.Frame(
            parent or self.page_area,
            bg=FIELD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )

    # --------------------------------------------------------
    # Wizard navigation
    # --------------------------------------------------------

    def _show_step(self, step):
        self.current_step = step
        self._clear_page()

        self.back_button.configure(
            state="normal" if 0 < step < 5 else "disabled"
        )
        self.cancel_button.configure(
            state="normal" if step < 5 else "disabled"
        )

        if step == 0:
            self._page_welcome()
        elif step == 1:
            self._page_location()
        elif step == 2:
            self._page_version()
        elif step == 3:
            self._page_components()
        elif step == 4:
            self._page_ready()
        elif step == 5:
            self._page_installing()
        elif step == 6:
            self._page_finished()

        if step == 4:
            self.next_button.configure(
                text="Install",
                command=self.begin_install,
                state="normal",
            )
        elif step == 5:
            self.next_button.configure(
                text="Installing...",
                state="disabled",
            )
        elif step == 6:
            self.next_button.configure(
                text="Close",
                command=self.root.destroy,
                state="normal",
            )
        else:
            self.next_button.configure(
                text="Next >",
                command=self.next_step,
                state="normal",
            )

    def next_step(self):
        if self.busy:
            return

        if self.current_step == 0:
            self._show_step(1)

        elif self.current_step == 1:
            if not self.destination_var.get().strip():
                messagebox.showerror(
                    "Missing location",
                    "Choose an installation folder.",
                    parent=self.root,
                )
                return
            self._show_step(2)

        elif self.current_step == 2:
            if not self.version_var.get():
                messagebox.showerror(
                    "Missing version",
                    "Wait for GitHub versions to load.",
                    parent=self.root,
                )
                return

            if self._selected_standalone():
                self._show_step(3)
                return

            if not self.manifest.get("features"):
                messagebox.showerror(
                    "Manifest not loaded",
                    "Wait for the component manifest to load.",
                    parent=self.root,
                )
                return

            self._show_step(3)

        elif self.current_step == 3:
            self._show_step(4)

    def back_step(self):
        if not self.busy and self.current_step > 0:
            self._show_step(self.current_step - 1)

    # --------------------------------------------------------
    # Welcome page
    # --------------------------------------------------------

    def _page_welcome(self):
        self._heading(
            "Welcome to Fingerprinter",
            "This wizard will install Fingerprinter from your GitHub repository.",
        )

        hero = self._card()
        hero.pack(fill="x", pady=(0, 18))

        inner = tk.Frame(hero, bg=FIELD)
        inner.pack(fill="x", padx=20, pady=22)

        tk.Label(
            inner,
            text="Your installation, your choice.",
            bg=FIELD,
            fg=TEXT,
            font=("Segoe UI", 15, "bold"),
        ).pack(anchor="w", pady=(8, 6))

        tk.Label(
            inner,
            text=(
                "Choose a release, select components, configure options like EXE compilation "
                "or desktop shortcuts, and let the installer organize everything into versions/."
            ),
            bg=FIELD,
            fg=MUTED,
            font=("Segoe UI", 10),
            justify="left",
            wraplength=530,
        ).pack(anchor="w")

    # --------------------------------------------------------
    # Location page
    # --------------------------------------------------------

    def _page_location(self):
        self._heading(
            "Choose installation location",
            "Choose the folder where the downloaded files will be saved.",
        )

        tk.Label(
            self.page_area,
            text="Destination folder",
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w")

        row = tk.Frame(self.page_area, bg=PANEL)
        row.pack(fill="x", pady=(9, 18))

        tk.Entry(
            row,
            textvariable=self.destination_var,
            bg=FIELD,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            highlightbackground=BORDER,
            highlightthickness=1,
            font=("Segoe UI", 10),
        ).pack(
            side="left",
            fill="x",
            expand=True,
            ipady=10,
            padx=(0, 10),
        )

        self._button(
            row, "Browse...", self.browse_destination, secondary=True
        ).pack(side="right")

    def browse_destination(self):
        folder = filedialog.askdirectory(
            title="Choose installation folder",
            initialdir=self.destination_var.get() or str(Path.home()),
            mustexist=False,
        )
        if folder:
            self.destination_var.set(folder)

    # --------------------------------------------------------
    # Version page
    # --------------------------------------------------------

    def _page_version(self):
        self._heading(
            "Choose your version",
            "Select a version folder and its base package.",
        )

        tk.Label(
            self.page_area,
            text="Available version",
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w")

        self.version_combo = ttk.Combobox(
            self.page_area,
            textvariable=self.version_var,
            state="readonly",
            style="FP.TCombobox",
            font=("Segoe UI", 10),
        )
        self.version_combo.pack(fill="x", pady=(8, 18), ipady=3)
        if self.versions:
            self.version_combo["values"] = self.versions
            if not self.version_var.get():
                self.version_var.set(self.versions[0])
        self.version_combo.bind(
            "<<ComboboxSelected>>", self._version_changed
        )

        tk.Label(
            self.page_area,
            text="Base package",
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w")

        self.base_combo = ttk.Combobox(
            self.page_area,
            textvariable=self.base_var,
            state="readonly",
            style="FP.TCombobox",
            font=("Segoe UI", 10),
        )
        self.base_combo.pack(fill="x", pady=(8, 18), ipady=6)

        self.standalone_toggle = tk.Button(
            self.page_area,
            text="",
            command=self._toggle_standalone,
            bg=PANEL,
            fg=MUTED,
            activebackground=PANEL,
            relief="flat",
            bd=0,
            font=("Segoe UI", 9, "underline"),
            cursor="arrow",
        )
        self.standalone_toggle.pack(anchor="w", pady=(0, 6))

        self.standalone_frame = tk.Frame(self.page_area, bg=PANEL)

        tk.Label(
            self.standalone_frame,
            text="Language",
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w")

        self.standalone_lang_combo = ttk.Combobox(
            self.standalone_frame,
            textvariable=self.standalone_language,
            state="readonly",
            style="FP.TCombobox",
            font=("Segoe UI", 10),
            values=sorted(self.standalones),
        )
        self.standalone_lang_combo.pack(fill="x", pady=(6, 10), ipady=3)
        self.standalone_lang_combo.bind(
            "<<ComboboxSelected>>", self._standalone_language_changed
        )

        tk.Label(
            self.standalone_frame,
            text="File (only this one file is downloaded - no base, fp or modules)",
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w")

        self.standalone_file_combo = ttk.Combobox(
            self.standalone_frame,
            textvariable=self.standalone_file_var,
            state="readonly",
            style="FP.TCombobox",
            font=("Segoe UI", 10),
            values=[NO_STANDALONE],
        )
        self.standalone_file_combo.pack(fill="x", pady=(6, 12), ipady=3)
        self.standalone_file_combo.bind(
            "<<ComboboxSelected>>", self._standalone_changed
        )

        self.version_details = tk.Label(
            self.page_area,
            text="Loading available versions from GitHub...",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9),
            justify="left",
            wraplength=580,
        )
        self.version_details.pack(anchor="w", pady=(4, 0))
        self._update_standalone_ui()

        if self.versions:
            self.version_combo["values"] = self.versions
            if not self.version_var.get():
                self.version_var.set(self.versions[0])
            self._version_changed()

    def _version_changed(self, _event=None):
        version = self.version_var.get()
        if not version:
            return

        prefix = f"{VERSIONS_DIR}/{version}/{BASES_DIR}/"
        bases = set()

        for item in self.tree:
            path = item.get("path", "")
            if path.startswith(prefix):
                remainder = path[len(prefix):]
                if remainder:
                    bases.add(remainder.split("/")[0])

        self.bases = sorted(bases, key=str.lower)
        self.base_combo["values"] = self.bases

        if self.bases:
            if self.base_var.get() not in self.bases:
                self.base_var.set(self.bases[0])
        else:
            self.base_var.set("")

        self._refresh_standalones(version)

        self.manifest = {"features": [], "modules": []}
        self.feature_vars = {}

        self.version_details.configure(
            text=f"Loading component manifest for {version}..."
        )

        self._start_background(
            lambda: self._load_manifest(version),
            f"Loading {version} manifest...",
        )

    def _find_standalones(self, version):
        for prefix in (
            f"{VERSIONS_DIR}/{version}/{STANDALONES_DIR}/",
            f"{STANDALONES_DIR}/",
        ):
            found = {}
            for item in self.tree:
                path = item.get("path", "")
                if (
                    item.get("type") != "blob"
                    or not path.startswith(prefix)
                    or not is_downloadable(path)
                ):
                    continue

                remainder = path[len(prefix):]
                first, _, rest = remainder.partition("/")
                language = LANGUAGE_FOLDERS.get(first.lower())

                if language and rest:
                    found.setdefault(language, {})[rest] = path
                else:
                    language = LANGUAGE_EXTENSIONS.get(Path(remainder).suffix.lower())
                    if language:
                        found.setdefault(language, {})[remainder] = path
            if found:
                return found
        return {}

    def _refresh_standalones(self, version):
        self.standalones = self._find_standalones(version)
        languages = sorted(self.standalones)

        combo = getattr(self, "standalone_lang_combo", None)
        if combo is not None and combo.winfo_exists():
            combo["values"] = languages

        if self.standalone_language.get() not in self.standalones:
            self.standalone_language.set(languages[0] if languages else "")
        self._standalone_language_changed()

    def _standalone_language_changed(self, _event=None):
        files = sorted(
            self.standalones.get(self.standalone_language.get(), {}), key=str.lower
        )

        combo = getattr(self, "standalone_file_combo", None)
        if combo is not None and combo.winfo_exists():
            combo["values"] = [NO_STANDALONE] + files

        if self.standalone_file_var.get() not in files:
            self.standalone_file_var.set(NO_STANDALONE)
        self._standalone_changed()

    def _toggle_standalone(self):
        self.standalone_open = not self.standalone_open
        if not self.standalone_open:
            self.standalone_file_var.set(NO_STANDALONE)
        self._update_standalone_ui()
        self._standalone_changed()

        if self.standalone_open and not self.standalones:
            self.version_details.configure(
                text=f"No standalones were found for {self.version_var.get()}."
            )

    def _update_standalone_ui(self):
        if not hasattr(self, "standalone_toggle") or not self.standalone_toggle.winfo_exists():
            return
        if self.standalone_open:
            self.standalone_toggle.configure(text="Standalone \u25BE")
            self.standalone_frame.pack(fill="x", before=self.version_details)
        else:
            self.standalone_toggle.configure(text="Standalone \u25B8")
            self.standalone_frame.pack_forget()

    def _selected_standalone(self):
        language = self.standalone_language.get()
        name = self.standalone_file_var.get()
        repo_path = self.standalones.get(language, {}).get(name)
        if not repo_path:
            return None
        return {"language": language, "name": name, "repo_path": repo_path}

    def _standalone_changed(self, _event=None):
        combo = getattr(self, "base_combo", None)
        if combo is not None and combo.winfo_exists():
            combo.configure(
                state="disabled" if self._selected_standalone() else "readonly"
            )

    # --------------------------------------------------------
    # Component selection page
    # --------------------------------------------------------

    def _page_components(self):
        self._heading(
            "Choose components & options",
            "Select components and specify whether to build an .exe or create shortcuts.",
        )

        self.feature_frame = tk.Frame(self.page_area, bg=PANEL)
        self.feature_frame.pack(fill="x")

        self._render_features()

        tk.Frame(
            self.page_area,
            bg=BORDER,
            height=1,
        ).pack(fill="x", pady=(15, 13))

        options_card = self._card()
        options_card.pack(fill="x", pady=(5, 10))
        
        opt_inner = tk.Frame(options_card, bg=FIELD)
        opt_inner.pack(fill="x", padx=12, pady=10)

        tk.Checkbutton(
            opt_inner,
            text="Create Desktop Shortcut",
            variable=self.create_shortcut_var,
            bg=FIELD,
            fg=TEXT,
            activebackground=FIELD,
            activeforeground=TEXT,
            selectcolor="#FFFFFF",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", pady=2)

        tk.Checkbutton(
            opt_inner,
            text="Convert files to standalone .exe during installation",
            variable=self.compile_exe_var,
            bg=FIELD,
            fg=TEXT,
            activebackground=FIELD,
            activeforeground=TEXT,
            selectcolor="#FFFFFF",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", pady=2)

        tk.Label(
            opt_inner,
            text="When building an .exe, the Python files are cached temporarily and deleted afterwards.",
            bg=FIELD,
            fg=MUTED,
            font=("Segoe UI", 9),
        ).pack(anchor="w", padx=(25, 0))

        self.advanced_button = tk.Button(
            opt_inner,
            text="",
            command=self._toggle_advanced,
            bg=FIELD,
            fg=MUTED,
            activebackground=FIELD,
            relief="flat",
            bd=0,
            font=("Segoe UI", 9, "underline"),
            cursor="arrow",
        )
        self.advanced_button.pack(anchor="w", pady=(8, 0))

        self.advanced_frame = tk.Frame(opt_inner, bg=FIELD)
        tk.Checkbutton(
            self.advanced_frame,
            text="Keep the Python (.py) files next to the .exe",
            variable=self.keep_source_var,
            bg=FIELD,
            fg=TEXT,
            activebackground=FIELD,
            activeforeground=TEXT,
            selectcolor="#FFFFFF",
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=2)
        self._update_advanced()

    def _toggle_advanced(self):
        self.extra_open = not self.extra_open
        self._update_advanced()

    def _update_advanced(self):
        if self.extra_open:
            self.advanced_button.configure(text="Advanced \u25BE")
            self.advanced_frame.pack(anchor="w", padx=(10, 0))
        else:
            self.advanced_button.configure(text="Advanced \u25B8")
            self.advanced_frame.pack_forget()

    def _render_features(self):
        if not hasattr(self, "feature_frame"):
            return

        for child in self.feature_frame.winfo_children():
            child.destroy()

        if self._selected_standalone():
            tk.Label(
                self.feature_frame,
                text=(
                    f"Standalone file '{self._selected_standalone()['name']}' selected: only "
                    "that single file is downloaded, so there are no components to choose."
                ),
                bg=PANEL,
                fg=MUTED,
                font=("Segoe UI", 10),
                wraplength=560,
                justify="left",
            ).pack(anchor="w")
            return

        features = self.manifest.get("features", [])

        if not features:
            tk.Label(
                self.feature_frame,
                text="No components found in this version's manifest.",
                bg=PANEL,
                fg=MUTED,
                font=("Segoe UI", 10),
            ).pack(anchor="w")
            return

        for feature in features:
            fid = feature.get("id", "")
            name = feature.get("name", fid)
            description = feature.get("description", "")

            if fid not in self.feature_vars:
                self.feature_vars[fid] = tk.BooleanVar(
                    value=bool(feature.get("default", False))
                )

            card = self._card(self.feature_frame)
            card.pack(fill="x", pady=4)

            inner = tk.Frame(card, bg=FIELD)
            inner.pack(fill="x", padx=12, pady=10)

            tk.Checkbutton(
                inner,
                text=name,
                variable=self.feature_vars[fid],
                bg=FIELD,
                fg=TEXT,
                activebackground=FIELD,
                activeforeground=TEXT,
                selectcolor="#FFFFFF",
                font=("Segoe UI", 10, "bold"),
                anchor="w",
            ).pack(anchor="w")

            tk.Label(
                inner,
                text=description,
                bg=FIELD,
                fg=MUTED,
                font=("Segoe UI", 9),
                wraplength=550,
                justify="left",
            ).pack(anchor="w", padx=(25, 0), pady=(3, 0))

    # --------------------------------------------------------
    # Review page
    # --------------------------------------------------------

    def _page_ready(self):
        self._heading(
            "Ready to install",
            "Review your choices before downloading and organizing files.",
        )

        card = self._card()
        card.pack(fill="both", expand=True)

        inner = tk.Frame(card, bg=FIELD)
        inner.pack(fill="both", expand=True, padx=18, pady=18)

        def add_line(label, value):
            tk.Label(
                inner,
                text=label.upper(),
                bg=FIELD,
                fg=MUTED,
                font=("Segoe UI", 8, "bold"),
            ).pack(anchor="w", pady=(9, 3))

            tk.Label(
                inner,
                text=value or "(none)",
                bg=FIELD,
                fg=TEXT,
                font=("Segoe UI", 10),
                wraplength=560,
                justify="left",
            ).pack(anchor="w")

        add_line("Version", self.version_var.get())
        if self._selected_standalone():
            chosen = self._selected_standalone()
            add_line(
                "Standalone (only this single file is downloaded)",
                f"{chosen['language']}: {chosen['name']}",
            )
            add_line("Base package", "(not used)")
        else:
            add_line("Base package", self.base_var.get())
        add_line("Destination", self.destination_var.get())
        add_line("Compile to EXE", "Yes" if self.compile_exe_var.get() else "No")
        if self.compile_exe_var.get():
            add_line(
                "Python files after build",
                "Kept next to the .exe" if self.keep_source_var.get()
                else "Deleted (cached only while building)",
            )
        add_line("Desktop Shortcut", "Yes" if self.create_shortcut_var.get() else "No")

    # --------------------------------------------------------
    # Installing page
    # --------------------------------------------------------

    def _page_installing(self):
        self._heading(
            "Installing Fingerprinter",
            "Please wait while files are downloaded and configured.",
        )

        self.progress_title = tk.Label(
            self.page_area,
            text="Preparing installation...",
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 11, "bold"),
        )
        self.progress_title.pack(anchor="w", pady=(0, 12))

        self.progress = ttk.Progressbar(
            self.page_area,
            mode="determinate",
            maximum=100,
            style="FP.Horizontal.TProgressbar",
        )
        self.progress.pack(fill="x", pady=(0, 10))

        self.progress_percent = tk.Label(
            self.page_area,
            text="0%",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9),
        )
        self.progress_percent.pack(anchor="e")

        self.current_file = tk.Label(
            self.page_area,
            text="",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9),
            wraplength=580,
            justify="left",
        )
        self.current_file.pack(anchor="w", pady=(18, 8))

        self.install_log = tk.Text(
            self.page_area,
            bg=FIELD,
            fg="#CBD4E4",
            insertbackground=TEXT,
            relief="flat",
            height=9,
            font=("Consolas", 9),
            wrap="word",
            state="disabled",
        )
        self.install_log.pack(fill="both", expand=True, pady=(8, 0))

    # --------------------------------------------------------
    # Finished page
    # --------------------------------------------------------

    def _page_finished(self):
        self._heading(
            "Installation complete",
            "Fingerprinter has been successfully installed into the 'versions/' structure.",
        )

        card = self._card()
        card.pack(fill="x")

        inner = tk.Frame(card, bg=FIELD)
        inner.pack(fill="x", padx=20, pady=24)

        tk.Label(
            inner,
            text="You're all set.",
            bg=FIELD,
            fg=TEXT,
            font=("Segoe UI", 17, "bold"),
        ).pack(anchor="w", pady=(5, 8))

        tk.Label(
            inner,
            text=(
                f"Files saved to versions directory:\n"
                f"{self.destination_var.get()}"
            ),
            bg=FIELD,
            fg=MUTED,
            font=("Segoe UI", 10),
            wraplength=550,
            justify="left",
        ).pack(anchor="w")

        self._button(
            self.page_area,
            "Open installation folder",
            self.open_install_folder,
            secondary=True,
        ).pack(anchor="w", pady=(16, 0))

    # --------------------------------------------------------
    # Repository discovery
    # --------------------------------------------------------

    def _load_repository(self):
        self.events.put(("status", "Fetching repository file list..."))

        result = github_get_json(f"{API_ROOT}/git/trees/{BRANCH}?recursive=1")

        if result.get("truncated"):
            raise InstallerError(
                "GitHub returned an incomplete repository file list."
            )

        self.tree = [
            item for item in result.get("tree", [])
            if item.get("type") in ("blob", "tree")
        ]
        self.tree_by_path = {
            item["path"]: item for item in self.tree
        }

        prefix = VERSIONS_DIR.rstrip("/") + "/"
        versions = set()

        for item in self.tree:
            path = item.get("path", "")

            if path.startswith(prefix):
                remainder = path[len(prefix):]
                version = remainder.split("/", 1)[0]

                if version:
                    versions.add(version)

        self.versions = sorted(
            versions,
            key=version_sort_key,
            reverse=True,
        )

        if not self.versions:
            raise InstallerError(
                f"No version folders were found under:\n{VERSIONS_DIR}/\n\n"
                "Check that the repository contains folders like "
                "main/versions/V0.4/."
            )

        self.events.put(("repository", self.versions, self.tree))

    def _load_manifest(self, version):
        path = f"{VERSIONS_DIR}/{version}/modules/modules.json"
        url = f"{RAW_ROOT}/{path}"

        try:
            data = json.loads(download_bytes(url).decode("utf-8"))
        except Exception as exc:
            raise InstallerError(
                f"Could not load component manifest:\n{path}\n\n{exc}"
            ) from exc

        if not isinstance(data, dict):
            raise InstallerError("The component manifest must be a JSON object.")

        if not isinstance(data.get("features", []), list):
            raise InstallerError("Manifest 'features' must be a list.")

        if not isinstance(data.get("modules", []), list):
            raise InstallerError("Manifest 'modules' must be a list.")

        self.events.put(("manifest", version, data))

    # --------------------------------------------------------
    # Dependency resolution and install plan
    # --------------------------------------------------------

    def _selected_modules(self):
        modules = {
            module.get("id"): module
            for module in self.manifest.get("modules", [])
            if module.get("id")
        }

        selected = set()
        visiting = set()

        def include(module_id):
            if module_id in selected:
                return

            if module_id in visiting:
                raise InstallerError(
                    f"Circular module dependency: {module_id}"
                )

            if module_id not in modules:
                raise InstallerError(
                    f"Unknown module dependency: {module_id}"
                )

            visiting.add(module_id)
            module = modules[module_id]

            requirements = module.get("requires", [])
            if isinstance(requirements, str):
                requirements = [requirements]

            for requirement in requirements:
                include(requirement)

            visiting.remove(module_id)
            selected.add(module_id)

        for feature in self.manifest.get("features", []):
            fid = feature.get("id")
            variable = self.feature_vars.get(fid)

            if variable is not None and variable.get():
                included = feature.get("includes", [])
                if isinstance(included, str):
                    included = [included]

                for module_id in included:
                    include(module_id)

        return [modules[mid] for mid in sorted(selected)]

    def _module_target(self, module, repo_path, version):
        modules_prefix = f"{VERSIONS_DIR}/{version}/modules/"
        if repo_path.startswith(modules_prefix):
            remainder = repo_path[len(modules_prefix):]
        else:
            remainder = Path(repo_path).name

        if remainder.startswith((FP_DIR + "/", MODULES_FOLDER + "/")):
            return remainder

        explicit = str(
            module.get("dest") or module.get("folder") or module.get("target") or ""
        ).strip().strip("/\\")
        module_id = str(module.get("id", "")).lower()

        if explicit:
            folder = explicit
        elif module_id == FP_DIR or module_id.startswith(("fp_", "fp.", "fp-")):
            folder = FP_DIR
        else:
            folder = MODULES_FOLDER
        return f"{folder}/{remainder}"

    def _build_install_plan(self):
        version = self.version_var.get()
        base = self.base_var.get()
        destination = self.destination_var.get().strip()

        if not version:
            raise InstallerError("Choose a version.")

        if not destination:
            raise InstallerError("Choose an installation folder.")

        destination_path = Path(destination).expanduser().resolve()

        standalone = self._selected_standalone()
        if standalone:
            targets = {standalone["repo_path"]: Path(standalone["name"]).name}
            self.install_targets = targets
            self.install_standalone = standalone
            return list(targets), destination_path

        selected_features = [
            feature for feature in self.manifest.get("features", [])
            if self.feature_vars.get(feature.get("id")) is not None
            and self.feature_vars[feature.get("id")].get()
        ]

        if not selected_features:
            raise InstallerError("Select at least one component.")

        if not base:
            raise InstallerError("Choose a base package.")

        targets = {}

        base_prefix = f"{VERSIONS_DIR}/{version}/{BASES_DIR}/{base}/"
        for item in sorted(self.tree, key=lambda i: i.get("path", "").lower()):
            path = item.get("path", "")
            if (
                item.get("type") == "blob"
                and path.startswith(base_prefix)
                and is_downloadable(path)
            ):
                targets[path] = path[len(base_prefix):]

        fp_prefix = f"{VERSIONS_DIR}/{version}/{FP_DIR}/"
        for item in sorted(self.tree, key=lambda i: i.get("path", "").lower()):
            path = item.get("path", "")
            if (
                item.get("type") == "blob"
                and path.startswith(fp_prefix)
                and is_downloadable(path)
            ):
                targets[path] = f"{FP_DIR}/{path[len(fp_prefix):]}"

        for module in self._selected_modules():
            filename = module.get("file")

            if not filename:
                continue

            filename = filename.replace("\\", "/").lstrip("/")

            if filename.startswith(VERSIONS_DIR + "/"):
                path = filename
            else:
                path = f"{VERSIONS_DIR}/{version}/modules/{filename}"

            if path not in self.tree_by_path:
                raise InstallerError(
                    f"Module file was not found in the repository:\n{path}"
                )

            targets[path] = self._module_target(module, path, version)

        self.install_targets = targets
        self.install_standalone = None
        return list(targets), destination_path

    # --------------------------------------------------------
    # Download and install
    # --------------------------------------------------------

    def begin_install(self):
        try:
            files, destination = self._build_install_plan()
        except Exception as exc:
            messagebox.showerror(
                "Cannot install",
                str(exc),
                parent=self.root,
            )
            return

        if destination.exists() and not destination.is_dir():
            messagebox.showerror(
                "Invalid destination",
                "The destination exists but is not a folder.",
                parent=self.root,
            )
            return

        self.install_files = files
        self.install_destination = destination
        self.cancel_requested = False

        self._show_step(5)
        self.back_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")

        self._start_background(
            self._perform_install,
            "Downloading and setting up files...",
        )

    @staticmethod
    def _find_entry_script(folder):
        scripts = sorted(folder.glob("*.py"), key=lambda p: p.name.lower())
        wanted = ["fingerprinter.py"]
        for name in wanted:
            for script in scripts:
                if script.name.lower() == name.lower():
                    return script
        for script in scripts:
            if not script.name.startswith("_"):
                return script
        return None

    @staticmethod
    def _move_files(source_root, dest_root, skip_python):
        for path in sorted(source_root.rglob("*")):
            if path.is_dir() or "__pycache__" in path.parts:
                continue
            if skip_python and path.suffix.lower() in SOURCE_EXTENSIONS:
                continue
            target = dest_root / path.relative_to(source_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))

    def _perform_install(self):
        files = self.install_files
        targets = self.install_targets
        standalone = self.install_standalone
        destination = self.install_destination
        total = len(files)
        written = 0

        version = self.version_var.get()
        version_dir = destination / "versions" / version

        if standalone:
            target_dir = version_dir / STANDALONES_DIR / standalone["language"]
        else:
            target_dir = version_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        compile_requested = self.compile_exe_var.get()
        use_cache = compile_requested and not self.keep_source_var.get()
        cache_dir = Path(tempfile.mkdtemp(prefix="fp_cache_")) if use_cache else None
        work_dir = cache_dir if use_cache else target_dir
        work_root = work_dir.resolve()

        try:
            for index, repo_path in enumerate(files, start=1):
                if self.cancel_requested:
                    self.events.put(("cancelled",))
                    return

                self.events.put(("progress", index - 1, total, repo_path))

                relative = safe_relative_path(targets[repo_path])
                output_path = (work_dir / relative).resolve()

                try:
                    output_path.relative_to(work_root)
                except ValueError as exc:
                    raise InstallerError(
                        f"Refusing to write outside the target directory:\n{repo_path}"
                    ) from exc

                data = download_bytes(f"{RAW_ROOT}/{repo_path}")

                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(data)

                written += 1
                self.events.put(("log", f"Downloaded: {relative.as_posix()}"))
                self.events.put(("progress", index, total, repo_path))

            if standalone:
                entry = work_dir / safe_relative_path(targets[files[0]])
                language = standalone["language"]
                suffix = entry.suffix.lower()
                compilable = (
                    suffix in PYTHON_EXTENSIONS if language == "python"
                    else suffix in CPP_COMPILABLE
                )
                exe_name = entry.stem
            else:
                entry = self._find_entry_script(work_dir)
                language = "python"
                compilable = entry is not None
                exe_name = "Fingerprinter"
            main_script_path = None

            if compile_requested and entry and compilable:
                self.events.put(("log", "Compiling to .exe (this can take a few minutes)..."))
                log = lambda message: self.events.put(("log", message))
                try:
                    if language == "cpp":
                        main_script_path = compile_cpp_to_exe(
                            entry, target_dir, exe_name, log=log
                        )
                    else:
                        main_script_path = compile_to_exe(
                            entry,
                            work_dir,
                            log=log,
                            exe_name=exe_name,
                            output_dir=target_dir,
                            full_app=standalone is None,
                            icon_path=self.icon_cache_path
                        )
                except Exception:
                    if use_cache:
                        self._move_files(cache_dir, target_dir, skip_python=False)
                    raise
                self.events.put(("log", "EXE compilation complete."))

                if use_cache:
                    self.events.put(("log", "Removing cached Python files..."))
                    self._move_files(cache_dir, target_dir, skip_python=True)

            else:
                if compile_requested:
                    self.events.put(("log", "Nothing to compile for this file - skipped EXE compilation."))
                if use_cache:
                    self._move_files(cache_dir, target_dir, skip_python=False)
                if entry:
                    main_script_path = target_dir / entry.relative_to(work_dir)

            if (
                self.create_shortcut_var.get()
                and main_script_path
                and Path(main_script_path).suffix.lower() in (".exe", ".py", ".pyw")
            ):
                create_desktop_shortcut(str(main_script_path), shortcut_name=exe_name, icon_path=self.icon_cache_path)
                self.events.put(("log", "Created desktop shortcut."))

            self.events.put(("complete", written, str(target_dir)))

        finally:
            if cache_dir is not None:
                shutil.rmtree(cache_dir, ignore_errors=True)

    # --------------------------------------------------------
    # Background workers and UI event queue
    # --------------------------------------------------------

    def _start_background(self, function, status):
        if self.busy:
            return

        self.busy = True
        self.footer_status.configure(text=status)

        def worker():
            try:
                function()
            except Exception as exc:
                self.events.put(("error", str(exc)))
            finally:
                self.events.put(("worker_done",))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]

                if kind == "status":
                    self.footer_status.configure(text=event[1])

                elif kind == "repository":
                    _, versions, _tree = event
                    self.busy = False
                    self.versions = versions
                    if hasattr(self, "version_combo") and self.version_combo.winfo_exists():
                        self.version_combo["values"] = versions

                    if versions:
                        self.version_var.set(versions[0])
                        if hasattr(self, "version_combo") and self.version_combo.winfo_exists():
                            self._version_changed()

                    self.footer_status.configure(
                        text=f"Connected: {len(versions)} version(s) found"
                    )

                elif kind == "manifest":
                    _, version, manifest = event

                    if self.version_var.get() != version:
                        continue

                    self.manifest = manifest
                    self.feature_vars = {}

                    for feature in manifest.get("features", []):
                        fid = feature.get("id")
                        if fid:
                            self.feature_vars[fid] = tk.BooleanVar(
                                value=bool(feature.get("default", False))
                            )

                    if self.current_step == 3:
                        self._render_features()

                    self.version_details.configure(
                        text=(
                            f"{version}: "
                            f"{len(manifest.get('features', []))} component options"
                        )
                    )
                    self.footer_status.configure(
                        text=f"Manifest loaded for {version}"
                    )

                elif kind == "progress":
                    _, done, total, path = event

                    if self.current_step == 5:
                        percent = int(done * 100 / max(total, 1))
                        self.progress.configure(value=percent)
                        self.progress_percent.configure(text=f"{percent}%")
                        self.progress_title.configure(
                            text=f"Downloading file {done} of {total}"
                        )
                        self.current_file.configure(text=path)

                elif kind == "log":
                    if self.current_step == 5:
                        self.install_log.configure(state="normal")
                        self.install_log.insert("end", event[1] + "\n")
                        self.install_log.see("end")
                        self.install_log.configure(state="disabled")

                elif kind == "complete":
                    _, count, destination = event
                    self.completed_count = count
                    self.destination_var.set(destination)
                    self.busy = False
                    self._show_step(6)
                    self.footer_status.configure(
                        text=f"Successfully downloaded {count} file(s)"
                    )

                elif kind == "cancelled":
                    self.busy = False
                    self._show_step(3)
                    self.footer_status.configure(
                        text="Installation cancelled"
                    )
                    messagebox.showinfo(
                        "Installation cancelled",
                        "The installation was cancelled.",
                        parent=self.root,
                    )

                elif kind == "error":
                    self.busy = False
                    if self.current_step == 5:
                        self._show_step(4)

                    if hasattr(self, "version_details") and self.version_details.winfo_exists():
                        self.version_details.configure(
                            text=f"Could not load data: {event[1]}"
                        )
                    self.footer_status.configure(text="An error occurred")
                    messagebox.showerror(
                        "Installer error",
                        event[1],
                        parent=self.root,
                    )

                elif kind == "worker_done":
                    self.busy = False

        except queue.Empty:
            pass

        self.root.after(100, self._poll_events)

    def open_install_folder(self):
        try:
            os.startfile(self.destination_var.get())
        except Exception as exc:
            messagebox.showerror(
                "Could not open folder",
                str(exc),
                parent=self.root,
            )

    def cancel(self):
        if self.current_step == 5:
            if messagebox.askyesno(
                "Cancel installation?",
                "Stop downloading?",
                parent=self.root,
            ):
                self.cancel_requested = True
                self.footer_status.configure(text="Cancelling...")
            return

        if messagebox.askyesno(
            "Exit setup",
            "Are you sure you want to close setup?",
            parent=self.root,
        ):
            self.root.destroy()


def handle_old_installer_cleanup():
    """Deletes the old installer executable if passed via --delete-old."""
    if "--delete-old" in sys.argv:
        try:
            idx = sys.argv.index("--delete-old")
            if idx + 1 < len(sys.argv):
                old_path = Path(sys.argv[idx + 1])
                if old_path.exists():
                    time.sleep(1.5)
                    old_path.unlink()
        except Exception as e:
            print(f"Could not delete old installer: {e}")


def main():
    handle_old_installer_cleanup()
    root = tk.Tk()
    FingerprinterInstaller(root)
    root.mainloop()


if __name__ == "__main__":
    main()
