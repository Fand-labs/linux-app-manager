
#!/usr/bin/env python3

import csv
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, Gio


APP_ID = "io.github.fandlabs.linuxappmanager"
APP_NAME = "Linux App Manager"
BATCH_SIZE = 40

LOG_DIR = os.path.expanduser("~/.local/state/linux-app-manager")
LOG_FILE = os.path.join(LOG_DIR, "linux-app-manager.log")


def setup_logger():
    os.makedirs(LOG_DIR, exist_ok=True)

    logger = logging.getLogger("linux-app-manager")
    logger.setLevel(logging.ERROR)
    logger.propagate = False

    if not logger.handlers:
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(threadName)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        file_handler.setLevel(logging.ERROR)
        file_handler.setFormatter(formatter)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.ERROR)
        console_handler.setFormatter(formatter)

        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

    return logger


log = setup_logger()


def authorize_root():
    pkexec = shutil.which("pkexec")

    if not pkexec:
        print("Erro: pkexec não encontrado. Instale o Polkit.", file=sys.stderr)
        return False

    try:
        result = subprocess.run(
            [pkexec, "/usr/bin/true"],
            stdin=subprocess.DEVNULL,
            timeout=180
        )

        if result.returncode != 0:
            print("Autenticação administrativa cancelada ou recusada.", file=sys.stderr)
            return False

        return True

    except subprocess.TimeoutExpired:
        print("Tempo limite da autenticação excedido.", file=sys.stderr)
        return False

    except Exception:
        log.exception("Erro na autenticação administrativa")
        return False


def execute(command, timeout=120):
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout
        )

        return result.stdout.strip().splitlines()

    except subprocess.TimeoutExpired:
        log.error("Timeout executando comando: %s", command)

    except subprocess.CalledProcessError as error:
        log.error(
            "Comando falhou | código=%s | comando=%s | erro=%s",
            error.returncode,
            command,
            error.stderr
        )

    except FileNotFoundError:
        log.error("Executável não encontrado: %s", command[0])

    except Exception:
        log.exception("Erro executando comando: %s", command)

    return []


def list_apt():
    programs = []

    lines = execute([
        "dpkg-query",
        "-W",
        "-f=${db:Status-Status}\t${binary:Package}\n"
    ])

    for line in lines:
        parts = line.split("\t", 1)

        if len(parts) == 2 and parts[0] == "installed":
            programs.append({
                "source": "APT",
                "name": parts[1]
            })

    return programs


def list_flatpak():
    if not shutil.which("flatpak"):
        return []

    programs = []

    lines = execute([
        "flatpak",
        "list",
        "--app",
        "--columns=application"
    ])

    for line in lines:
        name = line.strip()

        if name:
            programs.append({
                "source": "Flatpak",
                "name": name
            })

    return programs


def list_snap():
    if not shutil.which("snap"):
        return []

    programs = []

    lines = execute(["snap", "list"])

    for line in lines[1:]:
        parts = line.split()

        if parts:
            programs.append({
                "source": "Snap",
                "name": parts[0]
            })

    return programs


def collect_programs():
    programs = list_apt() + list_flatpak() + list_snap()

    unique = {}

    for program in programs:
        key = (
            program["source"],
            program["name"].lower()
        )

        unique[key] = program

    programs = list(unique.values())
    programs.sort(key=lambda item: item["name"].lower())

    return programs


def program_key(program):
    return (
        program["source"],
        program["name"].lower()
    )


def desktop_escape(value):
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\r", "")
    )


def desktop_quote_argument(argument):
    argument = str(argument)

    if not argument:
        return '""'

    if re.search(r'[\s"\\`$]', argument):
        argument = (
            argument
            .replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("`", "\\`")
            .replace("$", "\\$")
        )

        return f'"{argument}"'

    return argument


class LinuxAppManager(Adw.Application):

    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS
        )

        self.programs = []
        self.filtered = []

        self.window = None
        self.list_box = None
        self.search_entry = None
        self.counter_label = None
        self.refresh_button = None
        self.export_button = None
        self.status_label = None
        self.select_all_button = None
        self.scrolled = None

        self.select_all_handler_id = None

        self.desktop_apps = {}

        self.render_generation = 0
        self.render_index = 0

        self.loading = False
        self.busy_operations = 0

        self.processes = {}
        self.process_lock = threading.Lock()

        self.selected_keys = set()

        self.uninstall_queue = []
        self.uninstall_mode = None
        self.uninstall_current = None
        self.uninstall_success = 0
        self.uninstall_failed = 0
        self.uninstall_total = 0
        self.queue_running = False

        self.mass_buttons = []

    def do_activate(self):
        if self.window:
            self.window.present()
            return

        self.build_interface()
        self.window.present()
        self.refresh_programs()

    def build_desktop_index(self):
        self.desktop_apps.clear()

        for app in Gio.AppInfo.get_all():
            try:
                if not app.should_show():
                    continue

                desktop_id = app.get_id()

                if desktop_id:
                    key = desktop_id.removesuffix(".desktop").lower()
                    self.desktop_apps[key] = app

                executable = app.get_executable()

                if executable:
                    executable = os.path.basename(
                        executable.split()[0]
                    ).lower()

                    self.desktop_apps.setdefault(executable, app)

            except Exception:
                log.exception("Erro indexando aplicativo desktop")

    def find_desktop_app(self, program):
        name = program["name"].lower()

        candidates = [
            name,
            name.split(":")[0],
            name.replace("_", "-")
        ]

        for candidate in candidates:
            app = self.desktop_apps.get(candidate)

            if app:
                return app

        return None

    def get_desktop_directory(self):
        desktop_dir = GLib.get_user_special_dir(
            GLib.UserDirectory.DIRECTORY_DESKTOP
        )

        if not desktop_dir:
            desktop_dir = os.path.expanduser("~/Desktop")

        os.makedirs(desktop_dir, exist_ok=True)

        return desktop_dir

    def get_launch_command(self, program):
        source = program["source"]
        name = program["name"]

        if source == "Flatpak":
            return ["flatpak", "run", name]

        if source == "Snap":
            return ["snap", "run", name]

        app = self.find_desktop_app(program)

        if not app:
            raise RuntimeError(
                "Atalho de inicialização não encontrado."
            )

        commandline = app.get_commandline()

        if not commandline:
            executable = app.get_executable()

            if not executable:
                raise RuntimeError(
                    "Executável do aplicativo não encontrado."
                )

            return [executable]

        success, command = GLib.shell_parse_argv(commandline)

        if not success or not command:
            raise RuntimeError(
                "Comando de inicialização inválido."
            )

        command = [
            argument
            for argument in command
            if "%" not in argument
        ]

        if not command:
            raise RuntimeError(
                "Comando de inicialização inválido."
            )

        return command

    def get_desktop_icon(self, program):
        app = self.find_desktop_app(program)

        if app:
            try:
                icon = app.get_icon()

                if isinstance(icon, Gio.ThemedIcon):
                    names = icon.get_names()

                    if names:
                        return names[0]

                if isinstance(icon, Gio.FileIcon):
                    file = icon.get_file()
                    path = file.get_path()

                    if path:
                        return path

            except Exception:
                log.exception(
                    "Erro obtendo ícone para atalho: %s",
                    program["name"]
                )

        return "application-x-executable"

    def create_desktop_shortcut(self, button, program):
        try:
            desktop_dir = self.get_desktop_directory()
            command = self.get_launch_command(program)

            app = self.find_desktop_app(program)

            display_name = program["name"]

            if app:
                display_name = (
                    app.get_display_name()
                    or program["name"]
                )

            icon = self.get_desktop_icon(program)

            safe_name = re.sub(
                r'[\\/:*?"<>|]',
                "_",
                display_name
            ).strip()

            if not safe_name:
                safe_name = program["name"]

            filename = f"{safe_name}.desktop"
            desktop_path = os.path.join(desktop_dir, filename)

            counter = 2

            while os.path.exists(desktop_path):
                filename = f"{safe_name} ({counter}).desktop"
                desktop_path = os.path.join(desktop_dir, filename)
                counter += 1

            exec_line = " ".join(
                desktop_quote_argument(argument)
                for argument in command
            )

            desktop_content = (
                "[Desktop Entry]\n"
                "Version=1.0\n"
                "Type=Application\n"
                f"Name={desktop_escape(display_name)}\n"
                f"Exec={exec_line}\n"
                f"Icon={desktop_escape(icon)}\n"
                "Terminal=false\n"
                "StartupNotify=true\n"
            )

            with open(
                desktop_path,
                "w",
                encoding="utf-8",
                newline="\n"
            ) as desktop_file:
                desktop_file.write(desktop_content)

            os.chmod(desktop_path, 0o755)

            try:
                desktop_file = Gio.File.new_for_path(desktop_path)

                desktop_file.set_attribute_string(
                    "metadata::trusted",
                    "true",
                    Gio.FileQueryInfoFlags.NONE,
                    None
                )

            except Exception:
                log.exception(
                    "Não foi possível marcar o atalho como confiável"
                )

            self.status_label.set_text(
                f"Atalho criado: {display_name}"
            )

        except Exception as error:
            log.exception(
                "Erro criando atalho para %s",
                program["name"]
            )

            self.show_error(
                "Erro ao criar atalho",
                str(error)
            )

    def build_interface(self):
        self.build_desktop_index()

        self.window = Adw.ApplicationWindow(
            application=self,
            title=APP_NAME,
            default_width=900,
            default_height=720
        )

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()

        title = Gtk.Label(label=APP_NAME)
        title.add_css_class("heading")
        header.set_title_widget(title)

        self.refresh_button = Gtk.Button.new_from_icon_name(
            "view-refresh-symbolic"
        )
        self.refresh_button.set_tooltip_text("Atualizar aplicativos")
        self.refresh_button.connect(
            "clicked",
            lambda button: self.refresh_programs()
        )

        self.export_button = Gtk.Button(label="Exportar CSV")
        self.export_button.connect("clicked", self.export_csv)

        self.log_button = Gtk.Button.new_from_icon_name(
            "document-open-symbolic"
        )
        self.log_button.set_tooltip_text("Abrir log")
        self.log_button.connect("clicked", self.open_log)

        header.pack_end(self.log_button)
        header.pack_end(self.export_button)
        header.pack_end(self.refresh_button)

        toolbar.add_top_bar(header)

        main = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=14
        )

        main.set_margin_top(20)
        main.set_margin_bottom(20)
        main.set_margin_start(20)
        main.set_margin_end(20)

        heading = Gtk.Label(label="Aplicativos instalados")
        heading.add_css_class("title-1")
        heading.set_halign(Gtk.Align.START)
        main.append(heading)

        self.search_entry = Gtk.SearchEntry(
            placeholder_text="Buscar aplicativos..."
        )
        self.search_entry.set_hexpand(True)
        self.search_entry.connect(
            "search-changed",
            lambda entry: self.apply_filter()
        )
        main.append(self.search_entry)

        stats = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        stats.add_css_class("card")

        stats_content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4
        )

        stats_content.set_margin_top(12)
        stats_content.set_margin_bottom(12)
        stats_content.set_margin_start(16)
        stats_content.set_margin_end(16)

        self.counter_label = Gtk.Label(
            label="0 aplicativos encontrados"
        )
        self.counter_label.add_css_class("title-2")
        self.counter_label.set_halign(Gtk.Align.START)

        stats_content.append(self.counter_label)
        stats.append(stats_content)
        main.append(stats)

        selection_bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8
        )

        self.select_all_button = Gtk.CheckButton(
            label="Selecionar todos"
        )

        self.select_all_handler_id = self.select_all_button.connect(
            "toggled",
            self.on_select_all
        )

        selection_bar.append(self.select_all_button)

        selection_spacer = Gtk.Box()
        selection_spacer.set_hexpand(True)
        selection_bar.append(selection_spacer)

        self.selected_label = Gtk.Label(label="0 selecionados")
        self.selected_label.add_css_class("dim-label")
        selection_bar.append(self.selected_label)

        main.append(selection_bar)

        action_bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8
        )

        self.uninstall_selected_button = Gtk.Button(
            label="Desinstalar marcados"
        )
        self.uninstall_selected_button.add_css_class(
            "destructive-action"
        )
        self.uninstall_selected_button.connect(
            "clicked",
            self.uninstall_selected
        )

        self.uninstall_all_button = Gtk.Button(
            label="Desinstalar tudo"
        )
        self.uninstall_all_button.add_css_class(
            "destructive-action"
        )
        self.uninstall_all_button.connect(
            "clicked",
            self.uninstall_all
        )

        action_bar.append(self.uninstall_selected_button)
        action_bar.append(self.uninstall_all_button)

        main.append(action_bar)

        list_header = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12
        )

        list_title = Gtk.Label(label="Aplicativos")
        list_title.add_css_class("heading")
        list_title.set_halign(Gtk.Align.START)
        list_title.set_hexpand(True)

        self.status_label = Gtk.Label(label="Carregando...")
        self.status_label.add_css_class("dim-label")

        list_header.append(list_title)
        list_header.append(self.status_label)
        main.append(list_header)

        main.append(
            Gtk.Separator(
                orientation=Gtk.Orientation.HORIZONTAL
            )
        )

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_vexpand(True)
        self.scrolled.set_policy(
            Gtk.PolicyType.NEVER,
            Gtk.PolicyType.AUTOMATIC
        )

        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list_box.add_css_class("boxed-list")

        self.scrolled.set_child(self.list_box)
        main.append(self.scrolled)

        toolbar.set_content(main)
        self.window.set_content(toolbar)

        self.mass_buttons = [
            self.uninstall_selected_button,
            self.uninstall_all_button
        ]

    def set_interface_busy(self, busy):
        self.refresh_button.set_sensitive(not busy and not self.loading)
        self.search_entry.set_sensitive(not busy)

        for button in self.mass_buttons:
            button.set_sensitive(not busy)

        self.export_button.set_sensitive(
            not busy and bool(self.filtered)
        )

    def refresh_programs(self):
        if self.loading or self.queue_running:
            return

        self.loading = True

        self.refresh_button.set_sensitive(False)
        self.export_button.set_sensitive(False)
        self.status_label.set_text("Atualizando...")

        thread = threading.Thread(
            target=self.load_programs,
            daemon=True,
            name="PackageScanner"
        )
        thread.start()

    def load_programs(self):
        try:
            programs = collect_programs()
            GLib.idle_add(self.update_interface, programs)

        except Exception:
            log.exception("Falha na descoberta de aplicativos")
            GLib.idle_add(self.load_failed)

    def load_failed(self):
        self.loading = False
        self.refresh_button.set_sensitive(True)

        self.status_label.set_text("Erro ao carregar aplicativos")

        self.show_error(
            "Erro",
            "Não foi possível carregar os aplicativos. Consulte o log."
        )

        return GLib.SOURCE_REMOVE

    def update_interface(self, programs):
        self.programs = [
            program
            for program in programs
            if self.find_desktop_app(program) is not None
        ]

        self.loading = False
        self.refresh_button.set_sensitive(True)

        self.apply_filter()
        self.status_label.set_text("Atualizado")

        return GLib.SOURCE_REMOVE

    def clear_list(self):
        child = self.list_box.get_first_child()

        while child:
            next_child = child.get_next_sibling()
            self.list_box.remove(child)
            child = next_child

    def apply_filter(self):
        if not self.list_box:
            return GLib.SOURCE_REMOVE

        search = self.search_entry.get_text().strip().lower()

        self.filtered = [
            program
            for program in self.programs
            if search in program["name"].lower()
        ]

        self.counter_label.set_text(
            f"{len(self.filtered)} aplicativos encontrados"
        )

        self.export_button.set_sensitive(
            bool(self.filtered) and not self.queue_running
        )

        self.render_generation += 1
        generation = self.render_generation

        self.clear_list()
        self.render_index = 0

        self.select_all_button.handler_block(
            self.select_all_handler_id
        )

        self.select_all_button.set_active(
            bool(self.filtered)
            and all(
                program_key(program) in self.selected_keys
                for program in self.filtered
            )
        )

        self.select_all_button.handler_unblock(
            self.select_all_handler_id
        )

        self.update_selection_counter()

        if not self.filtered:
            self.status_label.set_text("Nenhum aplicativo encontrado")
            return GLib.SOURCE_REMOVE

        self.status_label.set_text("Carregando lista...")

        GLib.idle_add(self.render_batch, generation)

        return GLib.SOURCE_REMOVE

    def render_batch(self, generation):
        if generation != self.render_generation:
            return GLib.SOURCE_REMOVE

        start = self.render_index
        end = min(start + BATCH_SIZE, len(self.filtered))

        for index in range(start, end):
            try:
                self.add_program_row(self.filtered[index])

            except Exception:
                log.exception(
                    "Erro renderizando aplicativo: %s",
                    self.filtered[index]["name"]
                )

        self.render_index = end

        if end < len(self.filtered):
            return GLib.SOURCE_CONTINUE

        self.status_label.set_text(
            f"{len(self.filtered)} encontrados"
        )

        return GLib.SOURCE_REMOVE

    def add_program_row(self, program):
        row = Gtk.ListBoxRow()
        key = program_key(program)

        row.program_key = key

        content = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10
        )

        content.set_margin_top(9)
        content.set_margin_bottom(9)
        content.set_margin_start(10)
        content.set_margin_end(10)

        check = Gtk.CheckButton()
        check.set_active(key in self.selected_keys)
        check.connect(
            "toggled",
            self.on_program_toggled,
            program
        )

        app = self.find_desktop_app(program)

        icon = None
        display_name = program["name"]

        if app:
            try:
                app_icon = app.get_icon()

                if app_icon is not None:
                    icon = Gtk.Image.new_from_gicon(app_icon)

                display_name = (
                    app.get_display_name()
                    or program["name"]
                )

            except Exception:
                log.exception(
                    "Erro obtendo ícone: %s",
                    program["name"]
                )

        if icon is None:
            icon = Gtk.Image.new_from_icon_name(
                "application-x-executable-symbolic"
            )

        icon.set_pixel_size(30)
        icon.set_valign(Gtk.Align.CENTER)

        name = Gtk.Label(label=display_name)
        name.set_halign(Gtk.Align.START)
        name.set_hexpand(True)
        name.set_ellipsize(3)

        source = Gtk.Label(label=program["source"])
        source.add_css_class("dim-label")

        run_button = Gtk.Button(label="Executar")
        run_button.add_css_class("suggested-action")
        run_button.set_valign(Gtk.Align.CENTER)
        run_button.connect(
            "clicked",
            self.run_program,
            program
        )

        stop_button = Gtk.Button(label="Parar")
        stop_button.set_valign(Gtk.Align.CENTER)
        stop_button.set_sensitive(
            self.has_running_process(program)
        )
        stop_button.connect(
            "clicked",
            self.stop_program,
            program
        )

        shortcut_button = Gtk.Button(label="Criar atalho")
        shortcut_button.set_tooltip_text(
            "Criar atalho na área de trabalho"
        )
        shortcut_button.set_valign(Gtk.Align.CENTER)
        shortcut_button.connect(
            "clicked",
            self.create_desktop_shortcut,
            program
        )

        remove_button = Gtk.Button(label="Desinstalar")
        remove_button.add_css_class("destructive-action")
        remove_button.set_valign(Gtk.Align.CENTER)
        remove_button.connect(
            "clicked",
            self.confirm_uninstall,
            program
        )

        row.check_button = check
        row.run_button = run_button
        row.stop_button = stop_button
        row.shortcut_button = shortcut_button
        row.remove_button = remove_button

        content.append(check)
        content.append(icon)
        content.append(name)
        content.append(source)
        content.append(run_button)
        content.append(stop_button)
        content.append(shortcut_button)
        content.append(remove_button)

        row.set_child(content)
        self.list_box.append(row)

    def on_program_toggled(self, check, program):
        key = program_key(program)

        if check.get_active():
            self.selected_keys.add(key)
        else:
            self.selected_keys.discard(key)

        self.update_selection_counter()

    def on_select_all(self, check):
        active = check.get_active()

        for program in self.filtered:
            key = program_key(program)

            if active:
                self.selected_keys.add(key)
            else:
                self.selected_keys.discard(key)

        child = self.list_box.get_first_child()

        while child:
            if hasattr(child, "check_button"):
                child.check_button.set_active(active)

            child = child.get_next_sibling()

        self.update_selection_counter()

    def update_selection_counter(self):
        count = sum(
            1
            for program in self.filtered
            if program_key(program) in self.selected_keys
        )

        self.selected_label.set_text(
            f"{count} selecionados"
        )

    def run_program(self, button, program):
        try:
            command = self.get_launch_command(program)
            self.launch_process(program, command)

        except Exception as error:
            log.exception(
                "Falha executando aplicativo: %s",
                program["name"]
            )
            self.show_error("Erro ao executar", str(error))

    def launch_process(self, program, command):
        key = program_key(program)

        with self.process_lock:
            existing = self.processes.get(key)

            if existing and self.process_group_exists(existing.pid):
                GLib.idle_add(
                    self.update_process_buttons,
                    program
                )
                return

            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True
            )

            self.processes[key] = process

        GLib.idle_add(self.update_process_buttons, program)

        threading.Thread(
            target=self.monitor_process,
            args=(program, process, key),
            daemon=True,
            name=f"Monitor-{process.pid}"
        ).start()

    @staticmethod
    def process_group_exists(pid):
        try:
            os.killpg(pid, 0)
            return True

        except ProcessLookupError:
            return False

        except PermissionError:
            return True

        except OSError:
            return False

    def monitor_process(self, program, process, key):
        try:
            process.wait()

            while self.process_group_exists(process.pid):
                time.sleep(0.5)

        except Exception:
            log.exception(
                "Erro monitorando processo: %s",
                program["name"]
            )

        finally:
            with self.process_lock:
                if self.processes.get(key) is process:
                    self.processes.pop(key, None)

            GLib.idle_add(
                self.update_process_buttons,
                program
            )

    def has_running_process(self, program):
        key = program_key(program)

        with self.process_lock:
            process = self.processes.get(key)

            return (
                process is not None
                and self.process_group_exists(process.pid)
            )

    def update_process_buttons(self, program):
        if not self.list_box:
            return GLib.SOURCE_REMOVE

        key = program_key(program)
        running = self.has_running_process(program)

        child = self.list_box.get_first_child()

        while child:
            if getattr(child, "program_key", None) == key:
                child.run_button.set_sensitive(not running)
                child.stop_button.set_sensitive(running)
                break

            child = child.get_next_sibling()

        return GLib.SOURCE_REMOVE

    def stop_process_group(self, process, timeout=3):
        pid = process.pid

        if not self.process_group_exists(pid):
            return True

        try:
            os.killpg(pid, signal.SIGTERM)

        except ProcessLookupError:
            return True

        except OSError:
            log.exception("Erro enviando SIGTERM ao grupo %s", pid)

        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            if not self.process_group_exists(pid):
                return True

            time.sleep(0.1)

        if self.process_group_exists(pid):
            try:
                os.killpg(pid, signal.SIGKILL)

            except ProcessLookupError:
                return True

            except OSError:
                log.exception("Erro enviando SIGKILL ao grupo %s", pid)
                return False

        deadline = time.monotonic() + 2

        while time.monotonic() < deadline:
            if not self.process_group_exists(pid):
                return True

            time.sleep(0.1)

        return not self.process_group_exists(pid)

    def stop_program(self, button, program):
        key = program_key(program)

        with self.process_lock:
            process = self.processes.get(key)

        if not process or not self.process_group_exists(process.pid):
            button.set_sensitive(False)
            return

        button.set_sensitive(False)

        threading.Thread(
            target=self.stop_worker,
            args=(process, program),
            daemon=True,
            name=f"Stop-{process.pid}"
        ).start()

    def stop_worker(self, process, program):
        success = self.stop_process_group(process)

        with self.process_lock:
            key = program_key(program)

            if self.processes.get(key) is process:
                if not self.process_group_exists(process.pid):
                    self.processes.pop(key, None)

        GLib.idle_add(
            self.on_process_stopped,
            program,
            success
        )

    def on_process_stopped(self, program, success):
        self.update_process_buttons(program)

        if not success:
            self.show_error(
                "Erro ao parar",
                f"Não foi possível encerrar completamente {program['name']}."
            )

        return GLib.SOURCE_REMOVE

    def stop_program_before_uninstall(self, program):
        key = program_key(program)

        with self.process_lock:
            process = self.processes.get(key)

        if not process:
            return True

        if not self.process_group_exists(process.pid):
            with self.process_lock:
                if self.processes.get(key) is process:
                    self.processes.pop(key, None)

            return True

        success = self.stop_process_group(process)

        with self.process_lock:
            if self.processes.get(key) is process:
                if success or not self.process_group_exists(process.pid):
                    self.processes.pop(key, None)

        if not success:
            log.error(
                "Não foi possível encerrar o processo antes de desinstalar: %s",
                program["name"]
            )

        return success

    def confirm_uninstall(self, button, program):
        if self.queue_running or self.loading:
            return

        self.uninstall_mode = "individual"
        self.uninstall_total = 1
        self.uninstall_success = 0
        self.uninstall_failed = 0
        self.uninstall_current = program
        self.queue_running = True

        self.set_interface_busy(True)
        button.set_sensitive(False)

        self.status_label.set_text(
            f"Encerrando e desinstalando {program['name']}..."
        )

        self.execute_uninstall(program)

    def uninstall_selected(self, button):
        selected = [
            program
            for program in self.programs
            if program_key(program) in self.selected_keys
        ]

        if not selected:
            self.show_error(
                "Nenhum aplicativo selecionado",
                "Marque pelo menos um aplicativo para desinstalar."
            )
            return

        self.start_uninstall_queue(selected)

    def uninstall_all(self, button):
        if not self.programs:
            return

        self.start_uninstall_queue(list(self.programs))

    def start_uninstall_queue(self, programs):
        if self.queue_running or self.loading:
            return

        unique = {}

        for program in programs:
            unique[program_key(program)] = program

        self.uninstall_queue = list(unique.values())

        if not self.uninstall_queue:
            return

        self.uninstall_total = len(self.uninstall_queue)
        self.uninstall_success = 0
        self.uninstall_failed = 0
        self.uninstall_current = None
        self.queue_running = False

        dialog = Adw.MessageDialog.new(
            self.window,
            "Modo de desinstalação",
            (
                f"{self.uninstall_total} aplicativo(s) na fila.\n\n"
                "Escolha como deseja processar a fila."
            )
        )

        dialog.add_response("cancel", "Cancelar")
        dialog.add_response("yes", "Sim")
        dialog.add_response("confirm", "Confirmar")
        dialog.add_response("auto", "Auto")

        dialog.set_response_appearance(
            "yes",
            Adw.ResponseAppearance.DESTRUCTIVE
        )
        dialog.set_response_appearance(
            "auto",
            Adw.ResponseAppearance.DESTRUCTIVE
        )

        dialog.set_default_response("auto")
        dialog.set_close_response("cancel")

        dialog.connect(
            "response",
            self.on_queue_mode_response
        )

        dialog.present()

    def on_queue_mode_response(self, dialog, response):
        if response == "cancel":
            self.uninstall_queue.clear()
            return

        if response not in ("yes", "confirm", "auto"):
            self.uninstall_queue.clear()
            return

        self.uninstall_mode = response
        self.queue_running = True

        self.set_interface_busy(True)

        if response == "yes":
            self.status_label.set_text(
                "Fila autorizada. Iniciando desinstalação..."
            )
            self.uninstall_next()

        elif response == "confirm":
            self.status_label.set_text(
                "Aguardando confirmação individual..."
            )
            self.uninstall_next()

        elif response == "auto":
            self.status_label.set_text(
                "Modo automático: processando fila..."
            )
            self.uninstall_next()

    def uninstall_next(self):
        if not self.uninstall_queue:
            self.finish_uninstall_queue()
            return

        program = self.uninstall_queue.pop(0)
        self.uninstall_current = program

        if self.uninstall_mode == "confirm":
            self.ask_confirmation_for_next(program)
            return

        self.execute_uninstall(program)

    def ask_confirmation_for_next(self, program):
        remaining = len(self.uninstall_queue)

        dialog = Adw.MessageDialog.new(
            self.window,
            "Confirmar desinstalação",
            (
                f"Deseja desinstalar {program['name']}?\n\n"
                f"Restantes na fila: {remaining + 1}"
            )
        )

        dialog.add_response("skip", "Pular")
        dialog.add_response("remove", "Desinstalar")

        dialog.set_response_appearance(
            "remove",
            Adw.ResponseAppearance.DESTRUCTIVE
        )

        dialog.set_default_response("remove")
        dialog.set_close_response("skip")

        dialog.connect(
            "response",
            self.on_individual_confirmation,
            program
        )

        dialog.present()

    def on_individual_confirmation(self, dialog, response, program):
        if response == "remove":
            self.execute_uninstall(program)
        else:
            self.status_label.set_text(
                f"{program['name']} ignorado"
            )
            self.uninstall_next()

    def execute_uninstall(self, program):
        self.uninstall_current = program

        self.status_label.set_text(
            f"Encerrando {program['name']} antes de desinstalar..."
        )

        thread = threading.Thread(
            target=self.uninstall_worker,
            args=(program,),
            daemon=True,
            name="PackageUninstaller"
        )

        thread.start()

    def uninstall_worker(self, program):
        source = program["source"]
        name = program["name"]

        try:
            stopped = self.stop_program_before_uninstall(program)

            if not stopped:
                raise RuntimeError(
                    "Não foi possível encerrar o aplicativo. "
                    "A desinstalação foi cancelada para evitar conflitos."
                )

            GLib.idle_add(
                self.status_label.set_text,
                f"Desinstalando {name}..."
            )

            if source == "APT":
                command = [
                    "pkexec",
                    "apt-get",
                    "remove",
                    "-y",
                    "--",
                    name
                ]

            elif source == "Flatpak":
                command = [
                    "flatpak",
                    "uninstall",
                    "--noninteractive",
                    name
                ]

            elif source == "Snap":
                command = [
                    "pkexec",
                    "snap",
                    "remove",
                    name
                ]

            else:
                raise RuntimeError("Origem desconhecida.")

            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True,
                timeout=600
            )

            if result.stderr.strip():
                log.error(
                    "Saída de erro na desinstalação: %s",
                    result.stderr.strip()
                )

            success = True
            error = ""

        except subprocess.CalledProcessError as exception:
            success = False

            error = (
                exception.stderr.strip()
                or exception.stdout.strip()
                or "A desinstalação foi cancelada ou falhou."
            )

            log.error(
                "Falha na desinstalação | código=%s | pacote=%s | erro=%s",
                exception.returncode,
                name,
                error
            )

        except subprocess.TimeoutExpired:
            success = False
            error = "Tempo limite de desinstalação excedido."
            log.error("Timeout desinstalando: %s", name)

        except FileNotFoundError:
            success = False
            error = "Gerenciador de pacotes não encontrado."
            log.exception("Gerenciador não encontrado")

        except Exception as exception:
            success = False
            error = str(exception)
            log.exception("Erro desinstalando: %s", name)

        GLib.idle_add(
            self.uninstall_finished,
            program,
            success,
            error
        )

    def remove_program_from_interface(self, program):
        key = program_key(program)

        adjustment = self.scrolled.get_vadjustment()
        scroll_position = adjustment.get_value()

        self.programs = [
            item
            for item in self.programs
            if program_key(item) != key
        ]

        self.filtered = [
            item
            for item in self.filtered
            if program_key(item) != key
        ]

        self.selected_keys.discard(key)

        child = self.list_box.get_first_child()

        while child:
            next_child = child.get_next_sibling()

            if getattr(child, "program_key", None) == key:
                self.list_box.remove(child)
                break

            child = next_child

        self.counter_label.set_text(
            f"{len(self.filtered)} aplicativos encontrados"
        )

        self.export_button.set_sensitive(
            bool(self.filtered) and not self.queue_running
        )

        self.update_selection_counter()

        self.select_all_button.handler_block(
            self.select_all_handler_id
        )

        self.select_all_button.set_active(
            bool(self.filtered)
            and all(
                program_key(item) in self.selected_keys
                for item in self.filtered
            )
        )

        self.select_all_button.handler_unblock(
            self.select_all_handler_id
        )

        def restore_scroll():
            upper = adjustment.get_upper()
            page_size = adjustment.get_page_size()

            adjustment.set_value(
                min(
                    scroll_position,
                    max(0, upper - page_size)
                )
            )

            return GLib.SOURCE_REMOVE

        GLib.idle_add(restore_scroll)

    def uninstall_finished(self, program, success, error):
        individual = self.uninstall_mode == "individual"

        if success:
            self.uninstall_success += 1
            self.remove_program_from_interface(program)

            self.status_label.set_text(
                f"{program['name']} desinstalado "
                f"({self.uninstall_success}/{self.uninstall_total})"
            )

        else:
            self.uninstall_failed += 1

            log.error(
                "Não foi possível desinstalar %s: %s",
                program["name"],
                error
            )

            self.status_label.set_text(
                f"Falha: {program['name']} "
                f"({self.uninstall_success + self.uninstall_failed}"
                f"/{self.uninstall_total})"
            )

        self.uninstall_current = None

        if individual:
            self.queue_running = False
            self.uninstall_mode = None
            self.set_interface_busy(False)

            self.refresh_button.set_sensitive(not self.loading)
            self.export_button.set_sensitive(bool(self.filtered))

            if not success:
                self.show_error(
                    "Erro ao desinstalar",
                    f"{program['name']}\n\n{error}"
                )

            return GLib.SOURCE_REMOVE

        if not success and self.uninstall_mode == "confirm":
            self.show_error(
                "Erro ao desinstalar",
                f"{program['name']}\n\n{error}"
            )

        GLib.idle_add(self.uninstall_next)

        return GLib.SOURCE_REMOVE

    def finish_uninstall_queue(self):
        self.queue_running = False
        self.uninstall_current = None

        self.set_interface_busy(False)

        self.refresh_button.set_sensitive(not self.loading)
        self.export_button.set_sensitive(bool(self.filtered))

        self.status_label.set_text(
            "Fila concluída: "
            f"{self.uninstall_success} desinstalado(s), "
            f"{self.uninstall_failed} falha(s)."
        )

        summary = (
            f"Processamento concluído.\n\n"
            f"Total na fila: {self.uninstall_total}\n"
            f"Desinstalados: {self.uninstall_success}\n"
            f"Falhas: {self.uninstall_failed}"
        )

        dialog = Adw.MessageDialog.new(
            self.window,
            "Fila concluída",
            summary
        )

        dialog.add_response("ok", "Fechar")
        dialog.set_default_response("ok")
        dialog.present()

        self.uninstall_queue.clear()
        self.uninstall_mode = None

    def show_error(self, heading, message):
        log.error("%s: %s", heading, message)

        dialog = Adw.MessageDialog.new(
            self.window,
            heading,
            message
        )

        dialog.add_response("ok", "Fechar")
        dialog.set_default_response("ok")
        dialog.set_close_response("ok")
        dialog.present()

    def open_log(self, button):
        try:
            Gio.AppInfo.launch_default_for_uri(
                Gio.File.new_for_path(LOG_FILE).get_uri(),
                None
            )

        except Exception:
            log.exception("Erro abrindo arquivo de log")

            self.show_error(
                "Erro",
                f"Não foi possível abrir o log:\n{LOG_FILE}"
            )

    def export_csv(self, button):
        dialog = Gtk.FileChooserNative(
            title="Exportar aplicativos",
            transient_for=self.window,
            action=Gtk.FileChooserAction.SAVE,
            accept_label="Salvar",
            cancel_label="Cancelar"
        )

        dialog.set_current_name("aplicativos_instalados.csv")

        dialog.connect("response", self.save_csv)
        dialog.show()

    def save_csv(self, dialog, response):
        if response != Gtk.ResponseType.ACCEPT:
            dialog.destroy()
            return

        file = dialog.get_file()
        path = file.get_path() if file else None

        if not path:
            log.error("Caminho de exportação inválido")
            dialog.destroy()
            return

        try:
            with open(
                path,
                "w",
                newline="",
                encoding="utf-8-sig"
            ) as csvfile:

                writer = csv.DictWriter(
                    csvfile,
                    fieldnames=["name", "source"],
                    delimiter=";"
                )

                writer.writeheader()

                writer.writerows([
                    {
                        "name": program["name"],
                        "source": program["source"]
                    }
                    for program in self.filtered
                ])

            self.status_label.set_text(
                "Arquivo exportado com sucesso"
            )

        except OSError:
            log.exception("Erro exportando CSV")

            self.show_error(
                "Erro ao exportar",
                "Não foi possível salvar o arquivo."
            )

        dialog.destroy()


if __name__ == "__main__":
    if not authorize_root():
        sys.exit(1)

    try:
        app = LinuxAppManager()
        sys.exit(app.run())

    except Exception:
        log.exception("Falha fatal na aplicação")
        raise
