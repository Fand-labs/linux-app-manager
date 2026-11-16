import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "devflat"
APP_VERSION = "1.0.0"
APP_ID = "dev.fand.devflat"

BASE_DIR = Path(__file__).resolve().parent
SOURCE = BASE_DIR / "App.py"
ICON = BASE_DIR / "icon.png"

BUILD_DIR = BASE_DIR / f"{APP_NAME}_{APP_VERSION}_all"
OUTPUT = BASE_DIR / f"{APP_NAME}_{APP_VERSION}_all.deb"

DEPENDENCIES = [
    "python3",
    "python3-gi",
    "gir1.2-gtk-4.0",
    "gir1.2-adw-1",
    "polkitd",
]


def run(command):
    subprocess.run(command, check=True)


def main():
    print("================================")
    print("       DEVFLAT - DEB")
    print("================================")

    if not SOURCE.is_file():
        print(f"ERRO: Arquivo não encontrado: {SOURCE}")
        sys.exit(1)

    if not ICON.is_file():
        print(f"ERRO: Ícone não encontrado: {ICON}")
        sys.exit(1)

    if shutil.which("dpkg-deb") is None:
        print("ERRO: dpkg-deb não encontrado.")
        print("Instale com: sudo apt install dpkg-dev")
        sys.exit(1)

    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)

    if OUTPUT.exists():
        OUTPUT.unlink()

    directories = [
        BUILD_DIR / "DEBIAN",
        BUILD_DIR / "usr" / "lib" / APP_NAME,
        BUILD_DIR / "usr" / "bin",
        BUILD_DIR / "usr" / "share" / "applications",
        BUILD_DIR / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps",
    ]

    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)

    print("[1/6] Copiando programa...")
    shutil.copy2(
        SOURCE,
        BUILD_DIR / "usr" / "lib" / APP_NAME / "App.py"
    )

    print("[2/6] Copiando ícone...")
    shutil.copy2(
        ICON,
        BUILD_DIR / "usr" / "share" / "icons" / "hicolor" /
        "256x256" / "apps" / f"{APP_NAME}.png"
    )

    print("[3/6] Criando configuração do pacote...")

    control = f"""Package: {APP_NAME}
Version: {APP_VERSION}
Section: utils
Priority: optional
Architecture: all
Depends: {", ".join(DEPENDENCIES)}
Maintainer: DevFlat Contributors
Description: Linux application manager
 Graphical application manager for APT, Flatpak and Snap packages.
"""

    (BUILD_DIR / "DEBIAN" / "control").write_text(
        control,
        encoding="utf-8"
    )

    print("[4/6] Criando inicializador...")

    launcher = f"""#!/bin/sh
exec /usr/bin/python3 /usr/lib/{APP_NAME}/App.py "$@"
"""

    launcher_path = BUILD_DIR / "usr" / "bin" / APP_NAME
    launcher_path.write_text(launcher, encoding="utf-8")
    launcher_path.chmod(0o755)

    print("[5/6] Criando atalho no menu...")

    desktop = f"""[Desktop Entry]
Version=1.0
Type=Application
Name=DevFlat
Comment=Linux application manager
Exec={APP_NAME}
Icon={APP_NAME}
Terminal=false
Categories=System;Utility;
StartupNotify=true
"""

    desktop_path = (
        BUILD_DIR / "usr" / "share" / "applications" /
        f"{APP_ID}.desktop"
    )

    desktop_path.write_text(desktop, encoding="utf-8")

    print("[6/6] Gerando pacote DEB...")

    run([
        "dpkg-deb",
        "--root-owner-group",
        "--build",
        str(BUILD_DIR),
        str(OUTPUT),
    ])

    print("\n================================")
    print("       PACOTE GERADO!")
    print("================================")
    print(f"Arquivo: {OUTPUT}")
    print(f"Tamanho: {OUTPUT.stat().st_size / 1024:.1f} KB")
    print("\nPara instalar:")
    print(f"sudo apt install ./{OUTPUT.name}")

    shutil.rmtree(BUILD_DIR)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        print(f"\nERRO durante a compilação: {error}")
        sys.exit(1)
    except Exception as error:
        print(f"\nERRO: {error}")
        sys.exit(1)