import os
import sys
import json
import time
import random
import signal
import atexit
import subprocess
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from threading import Thread, Event
from typing import Dict, List, Tuple, Iterator, Union

from PySide6.QtCore import Qt, QEvent, QTimer, QAbstractNativeEventFilter, QByteArray, QStandardPaths, QLockFile
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QPushButton, QCheckBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit,
    QFileDialog, QMessageBox, QTabWidget, QLabel, QSystemTrayIcon, QMenu, QStyle,
    QDialog, QDialogButtonBox,
)

# --------- App meta ---------
VERSION = "1.1.0"
APP_NAME = "Random Images • For Wallpaper Engine"
APP_ICON_FILE = "icon.ico"
WEBSITE = "https://rafaelneves.dev.br"

# --------- Opções  ---------
DETACH_FROM_CONSOLE = True  # Desanexa do console
SUPPRESS_UI_ON_SHUTDOWN = True  # Não abrir MessageBox

Item = Tuple[str, Union[str, float]]  # ("cmd", comando) ou ("sleep", segundos)

# ---------- Cache de diretórios ----------
_DIR_CACHE: Dict[Tuple[str, Tuple[str, ...]], List[str]] = {}

def _list_images_cached(pasta: Path, extensoes: Tuple[str, ...]) -> List[str]:
    key = (str(pasta.resolve()), tuple(sorted(e.lower() for e in extensoes)))
    if key in _DIR_CACHE:
        return _DIR_CACHE[key]
    if not pasta.exists() or not pasta.is_dir():
        raise FileNotFoundError(f"Invalid folder: {pasta}")
    imgs: List[Tuple[str, str]] = []
    with os.scandir(pasta) as it:
        for entry in it:
            if entry.is_file():
                _, ext = os.path.splitext(entry.name)
                if ext.lower() in key[1]:
                    imgs.append((entry.name, Path(entry.path).as_posix()))
    if not imgs:
        raise FileNotFoundError(f"No valid images found in: {pasta}")
    imgs.sort(key=lambda t: t[0])
    paths = [p for _, p in imgs]
    _DIR_CACHE[key] = paths
    return paths

# ---------- Núcleo ----------
def construir_script(
    exe_path: str,
    monitor: str,
    props: Dict[str, str],
    passo_fade: Decimal = Decimal("0.05"),
    intervalo_segundos: float = 60.0,
    extensoes: Tuple[str, ...] = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".mp4"),
    aleatorio: bool = False,
    fade: bool = True,
    fadename: str = "opaimg",
) -> Iterator[Item]:
    if passo_fade <= 0:
        raise ValueError("passo_fade deve ser > 0")
    if intervalo_segundos < 0:
        raise ValueError("intervalo_segundos deve ser >= 0")
    if fade and not fadename:
        raise ValueError("fadename deve ser informado quando fade=True")

    prefix = f'"{exe_path}" -control applyProperties -monitor {monitor} -properties '

    def raw_props(d: Dict) -> str:
        return f'RAW~({json.dumps(d, ensure_ascii=False, separators=(",", ":"))})~END'

    def raw_fade(v: Decimal) -> str:
        val = str(v.quantize(Decimal("0.00"), rounding=ROUND_HALF_UP))
        return f'RAW~({{"{fadename}":{val}}})~END'

    fade_out_cmds: List[Item] = []
    fade_in_cmds:  List[Item] = []
    if fade:
        v = Decimal("1.00")
        while v >= Decimal("0.00"):
            fade_out_cmds.append(("cmd", prefix + raw_fade(v)))
            v -= passo_fade
        if fade_out_cmds[-1][1] != prefix + raw_fade(Decimal("0.00")):
            fade_out_cmds.append(("cmd", prefix + raw_fade(Decimal("0.00"))))

        v = Decimal("0.00")
        while v <= Decimal("1.00"):
            fade_in_cmds.append(("cmd", prefix + raw_fade(v)))
            v += passo_fade
        if fade_in_cmds[-1][1] != prefix + raw_fade(Decimal("1.00")):
            fade_in_cmds.append(("cmd", prefix + raw_fade(Decimal("1.00"))))

    fixed: Dict[str, str] = {}
    folders: Dict[str, List[str]] = {}
    for k, p in props.items():
        path = Path(p)
        if path.is_dir():
            imgs = _list_images_cached(path, extensoes)
            folders[k] = imgs
        else:
            if not path.exists():
                raise FileNotFoundError(f"File not found: {p}")
            fixed[k] = str(path.as_posix())

    if not fixed and not folders:
        raise ValueError("Props do not contain any valid files or folders.")

    state = {}
    for k, imgs in folders.items():
        n = len(imgs)
        if aleatorio:
            ordem = list(range(n))
            random.shuffle(ordem)
        else:
            # ordena pelos nomes dos arquivos de forma case-insensitive
            ordem = sorted(range(n), key=lambda i: Path(imgs[i]).name.lower())
        state[k] = {"imgs": imgs, "ordem": ordem, "i": 0, "n": n, "aleatorio": aleatorio}

    while True:
        for it in fade_out_cmds:
            yield it

        rodada = dict(fixed)
        for k, st in state.items():
            idx = st["ordem"][st["i"]]
            rodada[k] = st["imgs"][idx]
            st["i"] += 1
            if st["i"] >= st["n"]:
                st["i"] = 0
                if st["aleatorio"]:
                    random.shuffle(st["ordem"])

        yield ("cmd", prefix + raw_props(rodada))

        for it in fade_in_cmds:
            yield it

        if intervalo_segundos > 0:
            yield ("sleep", float(intervalo_segundos))


def executar_script(itens: Iterator[Item], stop_event: Event | None = None) -> None:
    """
    Executor resiliente:
      - sleep cooperativo com stop_event.wait
      - shell=False sempre que possível
      - prioridade abaixo do normal no Windows
      - debounce para comandos repetidos
      - try/except
    """
    try:
        if os.name == "nt":
            CREATE_NO_WINDOW = 0x08000000
            BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
            creationflags = CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS
        else:
            creationflags = 0

        last_raw = None
        last_cmd = None

        for tipo, valor in itens:
            if stop_event and stop_event.is_set():
                break

            if tipo == "cmd":
                rcode = 0
                if isinstance(valor, tuple) and len(valor) >= 4 and valor[0] == "__raw__":
                    _, exe_path, monitor, raw = valor[:4]
                    if raw == last_raw:
                        continue
                    last_raw = raw
                    args = [
                        exe_path, "-control", "applyProperties",
                        "-monitor", str(monitor),
                        "-properties", raw
                    ]
                    r = subprocess.run(args, shell=False, creationflags=creationflags)
                    rcode = r.returncode
                else:
                    if valor == last_cmd:
                        continue
                    last_cmd = valor
                    if isinstance(valor, list):
                        r = subprocess.run(valor, shell=False, creationflags=creationflags)
                    else:
                        r = subprocess.run(str(valor), shell=False, creationflags=creationflags)
                    rcode = r.returncode

                if rcode != 0:
                    print("Comando falhou com código:", rcode)

            elif tipo == "sleep":
                timeout = float(valor)
                if stop_event:
                    if stop_event.wait(timeout):
                        return
                else:
                    time.sleep(timeout)

            else:
                raise ValueError(f"Item inválido: {tipo}")

    except Exception as e:
        # Log simples e retorno silencioso para não disparar popups
        print("Falha no executor:", repr(e))


def executar_multimonitor_com_stop(configs: List[dict], stop: Event) -> None:
    threads: List[Thread] = []
    try:
        for cfg in configs:
            seq = construir_script(
                exe_path=cfg["exe_path"],
                monitor=cfg["monitor"],
                props=cfg["props"],
                passo_fade=Decimal(str(cfg.get("passo_fade", "0.05"))),
                intervalo_segundos=float(cfg.get("intervalo_segundos", 60.0)),
                extensoes=tuple(cfg.get("extensoes", [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".mp4"])),
                aleatorio=bool(cfg.get("aleatorio", False)),
                fade=bool(cfg.get("fade", True)),
                fadename=str(cfg.get("fadename", "opaimg")),
            )
            t = Thread(target=executar_script, args=(seq, stop), daemon=True)
            t.start()
            threads.append(t)

        while any(t.is_alive() for t in threads) and not stop.is_set():
            time.sleep(0.25)
    except Exception as e:
        print("Erro na orquestração:", repr(e))
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=1.5)

# ---------- Utilitários de desligamento ----------
class WinSessionEndFilter(QAbstractNativeEventFilter):
    """Captura WM_QUERYENDSESSION e WM_ENDSESSION para encerrar limpo no Windows."""
    WM_QUERYENDSESSION = 0x0011
    WM_ENDSESSION = 0x0016

    def __init__(self, on_session_end):
        super().__init__()
        self.on_session_end = on_session_end

    def nativeEventFilter(self, eventType: QByteArray, message):
        try:
            if eventType == b"windows_generic_MSG":
                msg = int(message.message)
                if msg in (self.WM_QUERYENDSESSION, self.WM_ENDSESSION):
                    self.on_session_end()
        except Exception:
            pass
        return False, 0

def detach_from_console():
    """Remove vínculo com o console para evitar mensagens do CMD ao fechar."""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        GetConsoleWindow = kernel32.GetConsoleWindow
        FreeConsole = kernel32.FreeConsole
        if GetConsoleWindow() != 0:
            FreeConsole()
    except Exception:
        pass

def install_console_ctrl_handler(on_ctrl):
    """Trata CTRL_LOGOFF_EVENT/CTRL_SHUTDOWN_EVENT em apps com console."""
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        PHANDLER_ROUTINE = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
        SetConsoleCtrlHandler = ctypes.windll.kernel32.SetConsoleCtrlHandler

        def handler(ctrl_type):
            # 0: CTRL_C, 1: CTRL_BREAK, 5: CTRL_LOGOFF, 6: CTRL_SHUTDOWN
            if ctrl_type in (0, 1, 5, 6):
                try:
                    on_ctrl()
                finally:
                    return True
            return False

        SetConsoleCtrlHandler(PHANDLER_ROUTINE(handler), True)
    except Exception:
        pass

# ---------- GUI ----------
DEFAULT_EXTS = ".png,.jpg,.jpeg,.gif,.mp4"
CONFIG_FILE = "config_wallpaper.json"

def parse_props_text(text: str) -> Dict[str, str]:
    props: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k:
            props[k] = v
    return props

def props_to_text(props: Dict[str, str]) -> str:
    return "\n".join(f"{k}={v}" for k, v in props.items())

class MonitorTab(QWidget):
    def __init__(self, idx: int):
        super().__init__()
        self.idx = idx
        self.build_ui()

    def build_ui(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.exe_edit = QLineEdit()
        btn_exe = QPushButton("Browse...")
        btn_exe.clicked.connect(self.pick_exe)
        exeh = QHBoxLayout(); exeh.addWidget(self.exe_edit); exeh.addWidget(btn_exe)

        self.monitor_edit = QLineEdit()
        self.monitor_edit.setPlaceholderText("e.g., 1")

        self.intervalo = QSpinBox()
        self.intervalo.setRange(0, 86400)
        self.intervalo.setValue(1800)

        self.fade_chk = QCheckBox("Enable fade")
        self.fade_chk.setChecked(True)
        self.fadename = QLineEdit("opaimg")
        self.passo_fade = QDoubleSpinBox()
        self.passo_fade.setRange(0.01, 1.0)
        self.passo_fade.setSingleStep(0.01)
        self.passo_fade.setDecimals(2)
        self.passo_fade.setValue(0.20)

        self.aleatorio_chk = QCheckBox("Shuffle images")
        self.aleatorio_chk.setChecked(True)

        self.exts_edit = QLineEdit(DEFAULT_EXTS)

        self.props_edit = QPlainTextEdit()
        self.props_edit.setPlaceholderText("Examples:\n_11=C:/Users/YourUser/Downloads\n_169=C:/Users/YourUser/Downloads")

        form.addRow("Wallpaper Engine executable:", self._h(exeh))
        form.addRow("Monitor:", self.monitor_edit)
        form.addRow("Interval (s):", self.intervalo)
        form.addRow("", self.fade_chk)
        form.addRow("Fade name:", self.fadename)
        form.addRow("Fade step:", self.passo_fade)
        form.addRow("", self.aleatorio_chk)
        form.addRow("Extensions:", self.exts_edit)
        form.addRow(QLabel("Props (key=path, 1 per line):"), self.props_edit)

        layout.addLayout(form)
        layout.addStretch()

        self.fade_chk.toggled.connect(self._toggle_fade_fields)
        self._toggle_fade_fields(self.fade_chk.isChecked())

    def _h(self, lay):
        w = QWidget(); w.setLayout(lay); return w

    def _toggle_fade_fields(self, checked: bool):
        self.fadename.setEnabled(checked)
        self.passo_fade.setEnabled(checked)

    def pick_exe(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select wallpaper32.exe", "", "Executable (*.exe);;All files (*.*)")
        if path:
            self.exe_edit.setText(path)

    def to_dict(self) -> dict:
        exts = [e.strip() for e in self.exts_edit.text().split(",") if e.strip()]
        cfg = {
            "exe_path": self.exe_edit.text().strip(),
            "monitor": self.monitor_edit.text().strip(),
            "props": parse_props_text(self.props_edit.toPlainText()),
            "passo_fade": f"{self.passo_fade.value():.2f}",
            "intervalo_segundos": int(self.intervalo.value()),
            "aleatorio": bool(self.aleatorio_chk.isChecked()),
            "fade": bool(self.fade_chk.isChecked()),
            "fadename": self.fadename.text().strip() or "opaimg",
            "extensoes": exts if exts else [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".mp4"],
        }
        return cfg

    def from_dict(self, cfg: dict):
        self.exe_edit.setText(cfg.get("exe_path", ""))
        self.monitor_edit.setText(str(cfg.get("monitor", "")))
        self.intervalo.setValue(int(cfg.get("intervalo_segundos", 10)))
        self.fade_chk.setChecked(bool(cfg.get("fade", True)))
        self.fadename.setText(str(cfg.get("fadename", "opaimg")))
        try:
            self.passo_fade.setValue(float(cfg.get("passo_fade", "0.05")))
        except Exception:
            self.passo_fade.setValue(0.05)
        self.aleatorio_chk.setChecked(bool(cfg.get("aleatorio", True)))
        exts = cfg.get("extensoes", [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".mp4"])
        self.exts_edit.setText(",".join(exts))
        props = cfg.get("props", {})
        self.props_edit.setPlainText(props_to_text(props))

class MainWin(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.worker_thread: Thread | None = None
        self.stop_event: Event | None = None
        self.tray_quit = False
        self.current_cfgs: List[dict] | None = None
        self.in_shutdown = False  # evita popups no desligamento

        self.app_icon = self._load_app_icon()
        self.setWindowIcon(self.app_icon)

        self.build_ui()
        self.load_config_on_start()
        self.create_tray()

        # garantir fade final mesmo em saídas inesperadas
        QApplication.instance().aboutToQuit.connect(self._apply_final_fade)

        # autoplay
        if self.autoplay_chk.isChecked():
            QTimer.singleShot(0, self.start_worker)

    # ---- ícone do app ----
    def _load_app_icon(self) -> QIcon:
        p = Path(APP_ICON_FILE)
        if p.exists():
            return QIcon(str(p))
        return self.style().standardIcon(QStyle.SP_ComputerIcon)

    def build_ui(self):
        root = QWidget(); self.setCentralWidget(root)
        v = QVBoxLayout(root)

        self.tabs = QTabWidget()
        v.addWidget(self.tabs)

        btn_row = QHBoxLayout()
        self.btn_add = QPushButton("Add Monitor")
        self.btn_del = QPushButton("Remove Monitor")
        self.btn_load = QPushButton("Load")
        self.btn_save = QPushButton("Save")

        self.autoplay_chk = QCheckBox("Autoplay")
        self.btn_start = QPushButton("Start")
        self.btn_stop = QPushButton("Stop")
        self.btn_about = QPushButton("About")

        for b in [self.btn_add, self.btn_del, self.btn_load, self.btn_save,
                  self.autoplay_chk, self.btn_start, self.btn_stop, self.btn_about]:
            btn_row.addWidget(b)
        btn_row.addStretch()
        v.addLayout(btn_row)

        self.btn_add.clicked.connect(self.add_monitor_tab)
        self.btn_del.clicked.connect(self.del_monitor_tab)
        self.btn_load.clicked.connect(self.load_config_dialog)
        self.btn_save.clicked.connect(self.save_config_dialog)
        self.btn_start.clicked.connect(self.start_worker)
        self.btn_stop.clicked.connect(self.stop_worker)
        self.btn_about.clicked.connect(self.show_about)

        # começa com 2 abas
        self.add_monitor_tab()
        self.add_monitor_tab()

    # ---------- Tray ----------
    def create_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        def ico_white(sp: QStyle.StandardPixmap, size: int = 18) -> QIcon:
            base = self.style().standardIcon(sp)
            pm = base.pixmap(size, size)
            tinted = QPixmap(pm.size()); tinted.fill(Qt.transparent)
            p = QPainter(tinted); p.drawPixmap(0, 0, pm)
            p.setCompositionMode(QPainter.CompositionMode_SourceIn)
            p.fillRect(tinted.rect(), Qt.white); p.end()
            ic = QIcon()
            for mode in (QIcon.Normal, QIcon.Disabled, QIcon.Active, QIcon.Selected):
                ic.addPixmap(tinted, mode)
            return ic

        self.tray = QSystemTrayIcon(self.app_icon, self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu(self)

        act_show = menu.addAction(ico_white(QStyle.SP_DialogOpenButton), "Show")
        act_show.triggered.connect(self.restore_from_tray)

        menu.addSeparator()

        act_start = menu.addAction(ico_white(QStyle.SP_MediaPlay), "Start")
        act_start.triggered.connect(self.start_worker)

        act_stop = menu.addAction(ico_white(QStyle.SP_MediaStop), "Stop")
        act_stop.triggered.connect(self.stop_worker)

        menu.addSeparator()

        act_about = menu.addAction(ico_white(QStyle.SP_MessageBoxInformation), "About")
        act_about.triggered.connect(self.show_about)

        menu.addSeparator()

        act_exit = menu.addAction(ico_white(QStyle.SP_DialogCloseButton), "Exit")
        act_exit.triggered.connect(self.exit_app)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.restore_from_tray()
            if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None
        )
        self.tray.show()

    def show_about(self):
        if SUPPRESS_UI_ON_SHUTDOWN and self.in_shutdown:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Sobre")
        dlg.setWindowIcon(self.app_icon)

        lay = QVBoxLayout(dlg)
        lbl = QLabel(
            f"<b>{APP_NAME}</b><br>"
            f"Version {VERSION}<br>"
            f"Developed by Rafael Neves<br>"
            f'<a href="{WEBSITE}">{WEBSITE.replace("https://", "")}</a>'
        )
        lbl.setTextFormat(Qt.RichText)
        lbl.setOpenExternalLinks(True)
        lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
        lay.addWidget(lbl)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok, parent=dlg)
        buttons.accepted.connect(dlg.accept)
        lay.addWidget(buttons)

        dlg.exec()

    def exit_app(self):
        self.tray_quit = True
        try:
            self.stop_worker()  # inclui fade final = 1.00
        finally:
            if hasattr(self, "tray"):
                self.tray.hide()
                self.tray.deleteLater()
            QTimer.singleShot(0, QApplication.instance().quit)

    def restore_from_tray(self):
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized | Qt.WindowActive)
        self.raise_()
        self.activateWindow()

    def changeEvent(self, ev):
        if ev.type() == QEvent.WindowStateChange and self.isMinimized():
            QTimer.singleShot(0, self.hide)
        super().changeEvent(ev)

    def closeEvent(self, ev):
        # Minimiza para a bandeja, a menos que seja saída explícita
        if not getattr(self, "tray_quit", False) and hasattr(self, "tray") and not self.in_shutdown:
            ev.ignore()
            self.hide()
            self.tray.showMessage(
                APP_NAME,
                "Continuing in background. Use the tray icon.",
                QSystemTrayIcon.Information,
                2500,
            )
        else:
            try:
                self.stop_worker()  # inclui fade final = 1
            finally:
                super().closeEvent(ev)

    # ---------- Monitores ----------
    def add_monitor_tab(self):
        idx = self.tabs.count() + 1
        tab = MonitorTab(idx)
        tab.monitor_edit.setText(str(idx))
        self.tabs.addTab(tab, f"Monitor {idx}")

    def del_monitor_tab(self):
        i = self.tabs.currentIndex()
        if i >= 0:
            self.tabs.removeTab(i)

    def gather_configs(self) -> List[dict]:
        cfgs: List[dict] = []
        for i in range(self.tabs.count()):
            tab: MonitorTab = self.tabs.widget(i)
            cfg = tab.to_dict()
            if not cfg["exe_path"]:
                raise ValueError(f"Aba {i+1}: Executable path is empty.")
            if not Path(cfg["exe_path"]).exists():
                raise ValueError(f"Aba {i+1}: Executable not found.")
            if not cfg["monitor"]:
                raise ValueError(f"Aba {i+1}: Monitor field is empty.")
            if not cfg["props"]:
                raise ValueError(f"Aba {i+1}: Props field is empty.")
            cfgs.append(cfg)
        if not cfgs:
            raise ValueError("No monitor configured.")
        return cfgs

    # ---------- Config helpers ----------
    def _read_config_file(self, path: Path) -> tuple[bool, List[dict]]:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return False, data  # compat antiga
        if isinstance(data, dict):
            autoplay = bool(data.get("autoplay", False))
            monitors = data.get("monitors", [])
            return autoplay, monitors
        return False, []

    def _write_config_file(self, path: Path, autoplay: bool, monitors: List[dict]) -> None:
        data = {"autoplay": bool(autoplay), "monitors": monitors}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _get_last_configs(self) -> List[dict]:
        """Tenta usar a config da execução; senão lê do JSON; senão tenta da UI."""
        if self.current_cfgs:
            return self.current_cfgs
        p = Path(CONFIG_FILE)
        if p.exists():
            try:
                _, cfgs = self._read_config_file(p)
                if cfgs:
                    return cfgs
            except Exception:
                pass
        try:
            return self.gather_configs()
        except Exception:
            return []

    def _apply_final_fade(self):
        """Última ação: força fade=1.00 em todos os monitores."""
        cfgs = self._get_last_configs()
        if not cfgs:
            return
        creationflags = 0x08000000 if os.name == "nt" else 0
        for cfg in cfgs:
            exe_path = cfg.get("exe_path", "")
            monitor = cfg.get("monitor", "")
            fadename = (cfg.get("fadename") or "opaimg").strip()
            if not exe_path or not monitor or not fadename:
                continue
            prefix = f'"{exe_path}" -control applyProperties -monitor {monitor} -properties '
            raw = f'RAW~({{"{fadename}":1.00}})~END'
            cmd = prefix + raw
            try:
                subprocess.run(cmd, shell=False, creationflags=creationflags)
            except Exception as e:
                print("Falha no fade final:", e)

    # ---------- execução ----------
    def start_worker(self):
        if self.worker_thread and self.worker_thread.is_alive():
            if not (SUPPRESS_UI_ON_SHUTDOWN and self.in_shutdown):
                QMessageBox.information(self, "Info", "Already running.")
            return
        try:
            cfgs = self.gather_configs()
        except Exception as e:
            if not (SUPPRESS_UI_ON_SHUTDOWN and self.in_shutdown):
                QMessageBox.critical(self, "Error", str(e))
            return

        self.current_cfgs = cfgs

        try:
            self._write_config_file(Path(CONFIG_FILE), self.autoplay_chk.isChecked(), cfgs)
        except Exception as e:
            if not (SUPPRESS_UI_ON_SHUTDOWN and self.in_shutdown):
                QMessageBox.warning(self, "Warning", f"Failed to save config: {e}")

        self.stop_event = Event()
        def run():
            try:
                executar_multimonitor_com_stop(cfgs, self.stop_event)
            except Exception as e:
                print("Erro de execução:", e)

        self.worker_thread = Thread(target=run, daemon=True)
        self.worker_thread.start()
        self._toggle_controls(False)

    def stop_worker(self):
        if self.stop_event:
            self.stop_event.set()
        if self.worker_thread:
            self.worker_thread.join(timeout=2.0)

        self._apply_final_fade()
        self._toggle_controls(True)

    def _toggle_controls(self, enabled: bool):
        for btn in [self.btn_add, self.btn_del, self.btn_load, self.btn_save, self.btn_start]:
            btn.setEnabled(enabled)
        self.btn_stop.setEnabled(True)

    # ---------- carregar/salvar ----------
    def load_config_on_start(self):
        p = Path(CONFIG_FILE)
        if not p.exists():
            return
        try:
            autoplay, cfgs = self._read_config_file(p)
            self.apply_configs(cfgs)
            self.autoplay_chk.setChecked(bool(autoplay))
        except Exception as e:
            if not (SUPPRESS_UI_ON_SHUTDOWN and self.in_shutdown):
                QMessageBox.warning(self, "Warning", f"Could not read config: {e}")

    def apply_configs(self, cfgs: List[dict]):
        self.tabs.clear()
        for i, cfg in enumerate(cfgs, start=1):
            tab = MonitorTab(i)
            tab.from_dict(cfg)
            self.tabs.addTab(tab, f"Monitor {i}")
        if self.tabs.count() == 0:
            self.add_monitor_tab()

    def load_config_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load config", "", "JSON (*.json);;All files (*.*)")
        if not path:
            return
        try:
            autoplay, cfgs = self._read_config_file(Path(path))
            self.apply_configs(cfgs)
            self.autoplay_chk.setChecked(bool(autoplay))
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load: {e}")

    def save_config_dialog(self):
        try:
            cfgs = self.gather_configs()
        except Exception as e:
            QMessageBox.critical(self, "Erro", str(e))
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save config", CONFIG_FILE, "JSON (*.json)")
        if not path:
            return
        try:
            self._write_config_file(Path(path), self.autoplay_chk.isChecked(), cfgs)
            QMessageBox.information(self, "OK", "Config saved.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save config: {e}")

    # ---------- sinais de sessão/sistema ----------
    def begin_shutdown(self):
        """Acionada em WM_QUERYENDSESSION/WM_ENDSESSION e sinais equivalentes."""
        self.in_shutdown = True
        try:
            self.tray_quit = True
            self.stop_worker()
        except Exception:
            pass


def _install_global_handlers(app: QApplication, win: MainWin):
    # 1) Filtro nativo para WM_QUERYENDSESSION/WM_ENDSESSION
    if os.name == "nt":
        session_filter = WinSessionEndFilter(on_session_end=win.begin_shutdown)
        app.installNativeEventFilter(session_filter)

    # 2) Sinais POSIX e equivalentes
    def _graceful_exit(*_):
        win.begin_shutdown()
        QTimer.singleShot(0, app.quit)

    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if sig is not None:
            try:
                signal.signal(sig, _graceful_exit)
            except Exception:
                pass

    # 3) atexit para garantir fade final
    atexit.register(lambda: win._apply_final_fade())

    # 4) Console control handler (se houver console)
    install_console_ctrl_handler(lambda: _graceful_exit())


def _maybe_detach_console():
    if DETACH_FROM_CONSOLE and os.name == "nt":
        # Se rodar com python.exe na linha de comando, desanexa do console
        detach_from_console()


def main():
    # Exceções globais: loga e encerra sem popups de UI
    def _excepthook(exc_type, exc, tb):
        try:
            import traceback
            print("Exceção não tratada:", "".join(traceback.format_exception(exc_type, exc, tb)))
        except Exception:
            pass
        # Finaliza o app sem diálogos
        try:
            QApplication.instance().quit()
        except Exception:
            pass

    sys.excepthook = _excepthook

    _maybe_detach_console()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    if Path(APP_ICON_FILE).exists():
        app.setWindowIcon(QIcon(str(APP_ICON_FILE)))

    w = MainWin()
    _install_global_handlers(app, w)

    w.resize(820, 660)


    if w.autoplay_chk.isChecked():
        # Não mostra a janela: inicia em segundo plano na bandeja
        w.hide()
    else:
        w.show()

    # Execução protegida
    try:
        rc = app.exec()
    except Exception as e:
        print("Falha no loop principal:", repr(e))
        rc = 1
    finally:
        try:
            w.begin_shutdown()
        except Exception:
            pass
    sys.exit(rc)


# ---------- Verificação do Wallpaper Engine (com cache) ----------
_WE_NAMES = {"wallpaper32.exe", "wallpaper64.exe"}
_WE_CACHE = {"ts": 0.0, "ok": True}
CHECK_WE_INTERVAL = 5.0  # segundos de cache para a checagem

def _is_proc_running_windows(names: set[str]) -> bool:
    if os.name != "nt":
        return True
    try:
        # Evita abrir janela de console
        CREATE_NO_WINDOW = 0x08000000
        for n in names:
            r = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {n}"],
                capture_output=True, text=True, shell=False, creationflags=CREATE_NO_WINDOW
            )
            if r.returncode == 0 and n.lower() in r.stdout.lower():
                return True
        return False
    except Exception:
        # Em caso de erro, não derruba o app
        return True

def is_wallpaper_engine_running(force_refresh: bool = False) -> bool:
    now = time.monotonic()
    if not force_refresh and (now - _WE_CACHE["ts"]) < CHECK_WE_INTERVAL:
        return _WE_CACHE["ok"]
    ok = _is_proc_running_windows(_WE_NAMES)
    _WE_CACHE["ts"] = now
    _WE_CACHE["ok"] = ok
    return ok

if __name__ == "__main__":
    # Single instance lock
    lock_dir = Path(QStandardPaths.writableLocation(QStandardPaths.TempLocation))
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "random_images_wallpaper.lock"
    lock = QLockFile(str(lock_path))
    lock.setStaleLockTime(30_000)  # 30s para considerar lock obsoleto (crash, etc.)
    if not lock.tryLock(1):
        # Já está rodando: mensagem curta e sair
        # Se preferir sem GUI, deixe só o print + sys.exit(0)
        app = QApplication(sys.argv)
        QMessageBox.information(None, "Info", "Already running.")
        sys.exit(0)

    # A partir daqui, só a primeira instância continua
    MAX_WAIT = 30
    STEP = 2

    print("Verificando Wallpaper Engine...")

    elapsed = 0
    while elapsed < MAX_WAIT:
        if is_wallpaper_engine_running(force_refresh=True):
            print("Wallpaper Engine detectado. Iniciando aplicação.")
            main()
            # Ao sair do main(), o lock é liberado na destruição do objeto
            sys.exit(0)
        time.sleep(STEP)
        elapsed += STEP

    app = QApplication(sys.argv)
    QMessageBox.warning(
        None,
        "Wallpaper Engine not found",
        "Wallpaper Engine is not running.\n"
        "Please start Wallpaper Engine first, then run this program."
    )
    sys.exit(1)

