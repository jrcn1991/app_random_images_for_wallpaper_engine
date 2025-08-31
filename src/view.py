from pathlib import Path
from typing import Dict, List

from PySide6.QtCore import Qt, QEvent, QTimer, QByteArray, Signal
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QPushButton, QCheckBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit,
    QFileDialog, QMessageBox, QTabWidget, QLabel, QSystemTrayIcon, QMenu, QStyle,
    QDialog, QDialogButtonBox
)

from .model import APP_NAME, VERSION, APP_ICON_FILE, WEBSITE, parse_props_text, props_to_text

DEFAULT_EXTS = ".png,.jpg,.jpeg,.gif,.mp4"
CONFIG_FILE = "config_wallpaper.json"

class MonitorTab(QWidget):
    def __init__(self, idx: int):
        super().__init__()
        self.idx = idx
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.exe_edit = QLineEdit()
        btn_exe = QPushButton("Browse...")
        btn_exe.clicked.connect(self._pick_exe)
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

    def _pick_exe(self):
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


class MainWindow(QMainWindow):
    # Sinais para o controller
    startRequested = Signal()
    stopRequested = Signal()
    loadRequested = Signal()
    saveRequested = Signal()
    exitRequested = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.app_icon = self._load_app_icon()
        self.setWindowIcon(self.app_icon)
        self._in_shutdown = False
        self._tray_quit = False

        self._build_ui()
        self._create_tray()

    # ---- ícone do app ----
    def _load_app_icon(self) -> QIcon:
        p = Path(APP_ICON_FILE)
        if p.exists():
            return QIcon(str(p))
        return self.style().standardIcon(QStyle.SP_ComputerIcon)

    def _build_ui(self):
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
        self.btn_load.clicked.connect(self.loadRequested.emit)
        self.btn_save.clicked.connect(self.saveRequested.emit)
        self.btn_start.clicked.connect(self.startRequested.emit)
        self.btn_stop.clicked.connect(self.stopRequested.emit)
        self.btn_about.clicked.connect(self.show_about)

        # começa com 2 abas
        self.add_monitor_tab()
        self.add_monitor_tab()

    # ---------- Tray ----------
    def _create_tray(self):
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
        act_start.triggered.connect(self.startRequested.emit)

        act_stop = menu.addAction(ico_white(QStyle.SP_MediaStop), "Stop")
        act_stop.triggered.connect(self.stopRequested.emit)

        menu.addSeparator()

        act_about = menu.addAction(ico_white(QStyle.SP_MessageBoxInformation), "About")
        act_about.triggered.connect(self.show_about)

        menu.addSeparator()

        act_exit = menu.addAction(ico_white(QStyle.SP_DialogCloseButton), "Exit")
        act_exit.triggered.connect(self.exitRequested.emit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.restore_from_tray()
            if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None
        )
        self.tray.show()

    # ---------- públicos usados pelo controller ----------
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

    def apply_configs(self, cfgs: List[dict]):
        self.tabs.clear()
        for i, cfg in enumerate(cfgs, start=1):
            tab = MonitorTab(i)
            tab.from_dict(cfg)
            self.tabs.addTab(tab, f"Monitor {i}")
        if self.tabs.count() == 0:
            self.add_monitor_tab()

    def set_autoplay(self, checked: bool):
        self.autoplay_chk.setChecked(bool(checked))

    def autoplay_checked(self) -> bool:
        return bool(self.autoplay_chk.isChecked())

    def show_info(self, title: str, text: str):
        QMessageBox.information(self, title, text)

    def show_warning(self, title: str, text: str):
        QMessageBox.warning(self, title, text)

    def show_error(self, title: str, text: str):
        QMessageBox.critical(self, title, text)

    def show_about(self):
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

    def restore_from_tray(self):
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized | Qt.WindowActive)
        self.raise_()
        self.activateWindow()

    def notify_background(self):
        if hasattr(self, "tray"):
            self.tray.showMessage(
                APP_NAME,
                "Continuing in background. Use the tray icon.",
                QSystemTrayIcon.Information,
                2500,
            )

    def mark_shutdown(self):
        self._in_shutdown = True

    def mark_tray_quit(self):
        self._tray_quit = True

    def is_tray_quit(self) -> bool:
        return getattr(self, "_tray_quit", False)

    # ---------- eventos ----------
    def changeEvent(self, ev):
        if ev.type() == QEvent.WindowStateChange and self.isMinimized():
            QTimer.singleShot(0, self.hide)
        super().changeEvent(ev)

    def closeEvent(self, ev):
        if not self.is_tray_quit() and hasattr(self, "tray") and not getattr(self, "_in_shutdown", False):
            ev.ignore()
            self.hide()
            self.notify_background()
        else:
            super().closeEvent(ev)
