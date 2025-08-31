import os
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QStandardPaths, QLockFile

from src.view import MainWindow
from src.controller import AppController


DETACH_FROM_CONSOLE = True

APP_ICON_FILE = "icon.ico"

def detach_from_console():
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

def maybe_detach_console():
    if DETACH_FROM_CONSOLE and os.name == "nt":
        detach_from_console()

def excepthook(exc_type, exc, tb):
    try:
        import traceback
        print("Exceção não tratada:", "".join(traceback.format_exception(exc_type, exc, tb)))
    except Exception:
        pass
    try:
        QApplication.instance().quit()
    except Exception:
        pass

def single_instance_lock() -> QLockFile | None:
    lock_dir = Path(QStandardPaths.writableLocation(QStandardPaths.TempLocation))
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "random_images_wallpaper.lock"
    lock = QLockFile(str(lock_path))
    lock.setStaleLockTime(30_000)
    if not lock.tryLock(1):
        # já existe uma instância; mensagem curta e sair
        app = QApplication(sys.argv)
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(None, "Info", "Already running.")
        sys.exit(0)
    return lock

def main():
    sys.excepthook = excepthook
    maybe_detach_console()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    if Path(APP_ICON_FILE).exists():
        app.setWindowIcon(QIcon(str(APP_ICON_FILE)))

    win = MainWindow()
    ctrl = AppController(app, win)
    ctrl.load_config_on_start()

    # Start invisível se autoplay marcado (não pisca a janela)
    ctrl.start_hidden_if_autoplay()

    # Execução protegida
    try:
        rc = app.exec()
    except Exception as e:
        print("Falha no loop principal:", repr(e))
        rc = 1
    finally:
        try:
            ctrl.begin_shutdown()
        except Exception:
            pass
    sys.exit(rc)

if __name__ == "__main__":
    _lock = single_instance_lock()  # garante instância única
    # Espera o Wallpaper Engine (sem abrir janela cedo)
    # Se não encontrar, alerta rápido e encerra
    app = QApplication(sys.argv)
    win = MainWindow()
    ctrl = AppController(app, win)
    ctrl.load_config_on_start()

    if not ctrl.verify_wallpaper_engine_blocking(max_wait=30, step=2):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(
            None,
            "Wallpaper Engine not found",
            "Wallpaper Engine is not running.\n"
            "Please start Wallpaper Engine first, then run this program."
        )
        sys.exit(1)

    # Se chegou aqui, roda normalmente (respeita autoplay e evita piscar)
    if Path(APP_ICON_FILE).exists():
        app.setWindowIcon(QIcon(str(APP_ICON_FILE)))
    ctrl.start_hidden_if_autoplay()
    try:
        rc = app.exec()
    finally:
        try:
            ctrl.begin_shutdown()
        except Exception:
            pass
    sys.exit(rc)
