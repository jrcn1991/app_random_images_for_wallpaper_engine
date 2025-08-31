import os
import json
import atexit
import signal
import subprocess
from pathlib import Path
from threading import Event, Thread
from typing import List

from PySide6.QtCore import QTimer, QAbstractNativeEventFilter, QByteArray
from PySide6.QtWidgets import QApplication

from .model import (
    executar_multimonitor_com_stop, is_wallpaper_engine_running,
)
from .view import MainWindow, CONFIG_FILE

SUPPRESS_UI_ON_SHUTDOWN = True  # não abrir messagebox ao desligar

# ---------- Filtro para fim de sessão (Windows) ----------
class WinSessionEndFilter(QAbstractNativeEventFilter):
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

# ---------- Controller ----------
class AppController:
    def __init__(self, app: QApplication, win: MainWindow):
        self.app = app
        self.win = win
        self.worker_thread: Thread | None = None
        self.stop_event: Event | None = None
        self.current_cfgs: List[dict] | None = None
        self.in_shutdown = False

        # conexões
        win.startRequested.connect(self.start_worker)
        win.stopRequested.connect(self.stop_worker)
        win.loadRequested.connect(self.load_config_dialog)
        win.saveRequested.connect(self.save_config_dialog)
        win.exitRequested.connect(self.exit_app)

        # instale handlers globais
        self._install_global_handlers()

    # ---------- Config helpers ----------
    def _read_config_file(self, path: Path) -> tuple[bool, List[dict]]:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return False, data
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
            return self.win.gather_configs()
        except Exception:
            return []

    def _apply_final_fade(self):
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

    # ---------- Execução ----------
    def start_worker(self):
        if self.worker_thread and self.worker_thread.is_alive():
            if not (SUPPRESS_UI_ON_SHUTDOWN and self.in_shutdown):
                self.win.show_info("Info", "Already running.")
            return
        try:
            cfgs = self.win.gather_configs()
        except Exception as e:
            if not (SUPPRESS_UI_ON_SHUTDOWN and self.in_shutdown):
                self.win.show_error("Error", str(e))
            return

        self.current_cfgs = cfgs
        try:
            self._write_config_file(Path(CONFIG_FILE), self.win.autoplay_checked(), cfgs)
        except Exception as e:
            if not (SUPPRESS_UI_ON_SHUTDOWN and self.in_shutdown):
                self.win.show_warning("Warning", f"Failed to save config: {e}")

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
        for btn in [self.win.btn_add, self.win.btn_del, self.win.btn_load, self.win.btn_save, self.win.btn_start]:
            btn.setEnabled(enabled)
        self.win.btn_stop.setEnabled(True)

    # ---------- carregar/salvar ----------
    def load_config_on_start(self):
        p = Path(CONFIG_FILE)
        if not p.exists():
            return
        try:
            autoplay, cfgs = self._read_config_file(p)
            self.win.apply_configs(cfgs)
            self.win.set_autoplay(bool(autoplay))
        except Exception as e:
            if not (SUPPRESS_UI_ON_SHUTDOWN and self.in_shutdown):
                self.win.show_warning("Warning", f"Could not read config: {e}")

    def load_config_dialog(self):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self.win, "Load config", "", "JSON (*.json);;All files (*.*)")
        if not path:
            return
        try:
            autoplay, cfgs = self._read_config_file(Path(path))
            self.win.apply_configs(cfgs)
            self.win.set_autoplay(bool(autoplay))
        except Exception as e:
            self.win.show_error("Error", f"Failed to load: {e}")

    def save_config_dialog(self):
        from PySide6.QtWidgets import QFileDialog
        try:
            cfgs = self.win.gather_configs()
        except Exception as e:
            self.win.show_error("Erro", str(e))
            return
        path, _ = QFileDialog.getSaveFileName(self.win, "Save config", CONFIG_FILE, "JSON (*.json)")
        if not path:
            return
        try:
            self._write_config_file(Path(path), self.win.autoplay_checked(), cfgs)
            self.win.show_info("OK", "Config saved.")
        except Exception as e:
            self.win.show_error("Error", f"Failed to save config: {e}")

    # ---------- sinais de sessão/sistema ----------
    def begin_shutdown(self):
        self.in_shutdown = True
        try:
            self.win.mark_shutdown()
            self.win.mark_tray_quit()
            self.stop_worker()
        except Exception:
            pass

    def exit_app(self):
        self.win.mark_tray_quit()
        try:
            self.stop_worker()
        finally:
            if hasattr(self.win, "tray"):
                self.win.tray.hide()
                self.win.tray.deleteLater()
            QTimer.singleShot(0, self.app.quit)

    # ---------- Handlers globais ----------
    def _install_global_handlers(self):
        # filtro nativo de sessão (Windows)
        if os.name == "nt":
            session_filter = WinSessionEndFilter(on_session_end=self.begin_shutdown)
            self.app.installNativeEventFilter(session_filter)

        # sinais POSIX e equivalentes
        def _graceful_exit(*_):
            self.begin_shutdown()
            QTimer.singleShot(0, self.app.quit)
        for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
            if sig is not None:
                try:
                    signal.signal(sig, _graceful_exit)
                except Exception:
                    pass

        # atexit para garantir fade final
        atexit.register(lambda: self._apply_final_fade())

    # ---------- utilidade de inicialização ----------
    def start_hidden_if_autoplay(self):
        # Evita abrir/mostrar janela se autoplay ativo
        if self.win.autoplay_checked():
            self.win.hide()
        else:
            self.win.show()

    def verify_wallpaper_engine_blocking(self, max_wait: int = 30, step: int = 2) -> bool:
        elapsed = 0
        while elapsed < max_wait:
            if is_wallpaper_engine_running(force_refresh=True):
                return True
            QTimer.singleShot(0, lambda: None)  # cede ao loop
            self.app.processEvents()
            self._sleep(step)
            elapsed += step
        return False

    @staticmethod
    def _sleep(seconds: int):
        import time
        time.sleep(seconds)
