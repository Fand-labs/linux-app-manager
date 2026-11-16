# Linux App Manager

A desktop application built with Python, GTK 4 and Libadwaita for managing installed applications on Linux.

Linux App Manager provides a graphical interface to view, launch, stop, export and uninstall applications installed through supported package managers.

## Features

- List installed applications.
- Detect packages installed through APT, Flatpak and Snap.
- Search and filter applications.
- Launch and stop applications.
- Create desktop shortcuts.
- Uninstall individual applications.
- Uninstall multiple applications with different confirmation modes.
- Export the application list to CSV.
- Application logs for troubleshooting.

## Supported Package Managers

| Package Manager | Support |
|---|---|
| APT | Supported |
| Flatpak | Supported |
| Snap | Supported |

Support for additional package managers may be added in future versions.

## Requirements

- Linux
- Python 3
- GTK 4
- Libadwaita
- PyGObject
- PolicyKit
- A supported package manager

## Installation

### Run from source

Install the required dependencies on Debian-based distributions:

```bash
sudo apt install python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 polkitd
```

Run the application:

```bash
python3 App.py
```

### Build a Debian package

To generate a `.deb` package, make sure `dpkg-deb` is installed:

```bash
sudo apt install dpkg-dev
```

Then run:

```bash
python3 build_deb.py
```

The generated package will be available in the project directory.

Install it with:

```bash
sudo apt install ./linuxappmanager_1.0.0_all.deb
```

## Project Structure

```text
linux-app-manager/
├── App.py
├── build_deb.py
├── icon.png
└── README.md
```

## Compatibility

The application is designed for Linux desktop environments that support GTK 4 and Libadwaita.

Package management functionality currently supports APT, Flatpak and Snap. The Debian package builder generates `.deb` packages for Debian-based distributions.

## Contributing

Contributions, bug reports and feature suggestions are welcome.

Feel free to open an issue or submit a pull request.

## License

This project is not yet distributed under a specified license. A license should be added before accepting external contributions or defining reuse permissions.

---

**Linux App Manager**  
A simple graphical application manager for Linux.