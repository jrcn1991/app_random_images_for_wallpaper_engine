from __future__ import annotations
import os
import time
import json
import random
import subprocess
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from threading import Thread, Event
from typing import Dict, List, Tuple, Iterator, Union

# --------- Tipos e metadados básicos ---------
VERSION = "1.1.0"
APP_NAME = "Random Images • For Wallpaper Engine"
APP_ICON_FILE = "icon.ico"
WEBSITE = "https://rafaelneves.dev.br"

Item = Tuple[str, Union[str, float]]  # ("cmd", comando) ou ("sleep", segundos)

# ---------- Cache de diretórios ----------
_DIR_CACHE: Dict[Tuple[str, Tuple[str, ...]], List[str]] = {}

def list_images_cached(pasta: Path, extensoes: Tuple[str, ...]) -> List[str]:
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
            imgs = list_images_cached(path, extensoes)
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

# ---------- Utilidades de parsing ----------
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

# ---------- Verificação do Wallpaper Engine ----------
_WE_NAMES = {"wallpaper32.exe", "wallpaper64.exe"}
_WE_CACHE = {"ts": 0.0, "ok": True}
CHECK_WE_INTERVAL = 5.0  # segundos

def _is_proc_running_windows(names: set[str]) -> bool:
    if os.name != "nt":
        return True
    try:
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
        return True

def is_wallpaper_engine_running(force_refresh: bool = False) -> bool:
    now = time.monotonic()
    if not force_refresh and (now - _WE_CACHE["ts"]) < CHECK_WE_INTERVAL:
        return _WE_CACHE["ok"]
    ok = _is_proc_running_windows(_WE_NAMES)
    _WE_CACHE["ts"] = now
    _WE_CACHE["ok"] = ok
    return ok
