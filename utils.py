import atexit
import stat

import distro
import hashlib
import json
import logging
import os
import psutil
import queue
import re
import requests
import shutil
import signal
import stat
import subprocess
import sys
import threading
from urllib.parse import urlparse
from datetime import datetime, timedelta

from base64 import b64encode
from packaging import version
from pathlib import Path
from typing import List, Union
from xml.etree import ElementTree as ET
import tkinter as tk

import config
import msg
import wine
import tui


class Props():
    def __init__(self, uri=None):
        self.path = None
        self.size = None
        self.md5 = None
        if uri is not None:
            self.path = uri


class FileProps(Props):
    def __init__(self, f=None):
        super().__init__(f)
        if f is not None:
            self.path = Path(self.path)
            if self.path.is_file():
                self.get_size()
                # self.get_md5()

    def get_size(self):
        if self.path is None:
            return
        self.size = self.path.stat().st_size
        return self.size

    def get_md5(self):
        if self.path is None:
            return
        md5 = hashlib.md5()
        with self.path.open('rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                md5.update(chunk)
        self.md5 = b64encode(md5.digest()).decode('utf-8')
        logging.debug(f"{str(self.path)} MD5: {self.md5}")
        return self.md5


class UrlProps(Props):
    def __init__(self, url=None):
        super().__init__(url)
        self.headers = None
        if url is not None:
            self.get_headers()
            self.get_size()
            self.get_md5()

    def get_headers(self):
        if self.path is None:
            self.headers = None
        logging.debug(f"Getting headers from {self.path}.")
        try:
            h = {'Accept-Encoding': 'identity'}  # force non-compressed txfr
            r = requests.head(self.path, allow_redirects=True, headers=h)
        except requests.exceptions.ConnectionError:
            logging.critical("Failed to connect to the server.")
            return None
        except Exception as e:
            logging.error(e)
            return None
        except KeyboardInterrupt:
            print()
            msg.logos_error("Interrupted by Ctrl+C")
            return None
        self.headers = r.headers
        return self.headers

    def get_size(self):
        if self.headers is None:
            r = self.get_headers()
            if r is None:
                return
        content_length = self.headers.get('Content-Length')
        content_encoding = self.headers.get('Content-Encoding')
        if content_encoding is not None:
            logging.critical(f"The server requires receiving the file compressed as '{content_encoding}'.")
        logging.debug(f"{content_length = }")
        if content_length is not None:
            self.size = int(content_length)
        return self.size

    def get_md5(self):
        if self.headers is None:
            r = self.get_headers()
            if r is None:
                return
        if self.headers.get('server') == 'AmazonS3':
            content_md5 = self.headers.get('etag')
            if content_md5 is not None:
                # Convert from hex to base64
                content_md5_hex = content_md5.strip('"').strip("'")
                content_md5 = b64encode(bytes.fromhex(content_md5_hex)).decode()
        else:
            content_md5 = self.headers.get('Content-MD5')
        if content_md5 is not None:
            content_md5 = content_md5.strip('"').strip("'")
        logging.debug(f"{content_md5 = }")
        if content_md5 is not None:
            self.md5 = content_md5
        return self.md5


# Set "global" variables.
def set_default_config():
    get_os()
    get_package_manager()
    if config.CONFIG_FILE is None:
        config.CONFIG_FILE = config.DEFAULT_CONFIG_PATH
    config.PRESENT_WORKING_DIRECTORY = os.getcwd()
    config.MYDOWNLOADS = get_user_downloads_dir()
    os.makedirs(os.path.dirname(config.LOGOS_LOG), exist_ok=True)


def write_config(config_file_path):
    logging.info(f"Writing config to {config_file_path}")
    os.makedirs(os.path.dirname(config_file_path), exist_ok=True)

    config_data = {key: config.__dict__.get(key) for key in config.persistent_config_keys}

    try:
        with open(config_file_path, 'w') as config_file:
            json.dump(config_data, config_file, indent=4, sort_keys=True)
            config_file.write('\n')

    except IOError as e:
        msg.logos_error(f"Error writing to config file {config_file_path}: {e}")

def update_config_file(config_file_path, key, value):
    config_file_path = Path(config_file_path)
    with config_file_path.open(mode='r') as f:
        config_data = json.load(f)

    if config_data.get(key) != value:
        logging.info(f"Updating {str(config_file_path)} with: {key} = {value}")
        config_data[key] = value
        try:
            with config_file_path.open(mode='w') as f:
                json.dump(config_data, f, indent=4, sort_keys=True)
                f.write('\n')
        except IOError as e:
            msg.logos_error(f"Error writing to config file {config_file_path}: {e}")

def die_if_running():
    PIDF = '/tmp/LogosLinuxInstaller.pid'

    def remove_pid_file():
        if os.path.exists(PIDF):
            os.remove(PIDF)

    if os.path.isfile(PIDF):
        with open(PIDF, 'r') as f:
            pid = f.read().strip()
            message = f"The script is already running on PID {pid}. Should it be killed to allow this instance to run?"
            if config.DIALOG == "tk":
                # TODO: With the GUI this runs in a thread. It's not clear if the
                # messagebox will work correctly. It may need to be triggered from
                # here with an event and then opened from the main thread.
                tk_root = tk.Tk()
                tk_root.withdraw()
                confirm = tk.messagebox.askquestion("Confirmation", message)
                tk_root.destroy()
            elif config.DIALOG == "curses":
                confirm = tui.confirm("Confirmation", message)
            else:
                confirm = msg.cli_question(message)

            if confirm:
                os.kill(int(pid), signal.SIGKILL)

    atexit.register(remove_pid_file)
    with open(PIDF, 'w') as f:
        f.write(str(os.getpid()))


def die_if_root():
    if os.getuid() == 0 and not config.LOGOS_FORCE_ROOT:
        msg.logos_error(
            "Running Wine/winetricks as root is highly discouraged. Use -f|--force-root if you must run as root. See https://wiki.winehq.org/FAQ#Should_I_run_Wine_as_root.3F")


def die(message):
    logging.critical(message)
    sys.exit(1)


def reboot():
    logging.info("Rebooting system.")
    command = f"{config.SUPERUSER_COMMAND} reboot now"
    subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True, text=True)
    sys.exit(0)


def restart_lli():
    logging.debug("Restarting Logos Linux Installer.")
    pidfile = Path('/tmp/LogosLinuxInstaller.pid')
    if pidfile.is_file():
        pidfile.unlink()
    os.execv(sys.executable, [sys.executable])
    sys.exit()


def set_verbose():
    config.LOG_LEVEL = logging.INFO
    config.WINEDEBUG = ''


def set_debug():
    config.LOG_LEVEL = logging.DEBUG
    config.WINEDEBUG = ""


def t(command):
    if shutil.which(command) is not None:
        return True
    else:
        return False


def tl(library):
    try:
        __import__(library)
        return True
    except ImportError:
        return False


def get_dialog():
    if not os.environ.get('DISPLAY'):
        msg.logos_error("The installer does not work unless you are running a display")

    DIALOG = os.getenv('DIALOG')
    config.GUI = False
    # Set config.DIALOG.
    if DIALOG is not None:
        DIALOG = DIALOG.lower()
        if DIALOG not in ['curses', 'tk']:
            msg.logos_error("Valid values for DIALOG are 'curses' or 'tk'.")
        config.DIALOG = DIALOG
    elif sys.__stdin__.isatty():
        config.DIALOG = 'curses'
    else:
        config.DIALOG = 'tk'
    # Set config.GUI.
    if config.DIALOG == 'tk':
        config.GUI = True


def get_os():
    # TODO: Remove if we can verify these are no longer needed commented code.

    # Try reading /etc/os-release
    # try:
    #    with open('/etc/os-release', 'r') as f:
    #        os_release_content = f.read()
    #    match = re.search(r'^ID=(\S+).*?VERSION_ID=(\S+)', os_release_content, re.MULTILINE)
    #    if match:
    #        config.OS_NAME = match.group(1)
    #        config.OS_RELEASE = match.group(2)
    #        return config.OS_NAME, config.OS_RELEASE
    # except FileNotFoundError:
    #    pass

    ## Try using lsb_release command
    # try:
    #    config.OS_NAME = platform.linux_distribution()[0]
    #    config.OS_RELEASE = platform.linux_distribution()[1]
    #    return config.OS_NAME, config.OS_RELEASE
    # except AttributeError:
    #    pass

    ## Try reading /etc/lsb-release
    # try:
    #    with open('/etc/lsb-release', 'r') as f:
    #        lsb_release_content = f.read()
    #    match = re.search(r'^DISTRIB_ID=(\S+).*?DISTRIB_RELEASE=(\S+)', lsb_release_content, re.MULTILINE)
    #    if match:
    #        config.OS_NAME = match.group(1)
    #        config.OS_RELEASE = match.group(2)
    #        return config.OS_NAME, config.OS_RELEASE
    # except FileNotFoundError:
    #    pass

    ## Try reading /etc/debian_version
    # try:
    #    with open('/etc/debian_version', 'r') as f:
    #        config.OS_NAME = 'Debian'
    #        config.OS_RELEASE = f.read().strip()
    #        return config.OS_NAME, config.OS_RELEASE
    # except FileNotFoundError:
    #    pass

    # Add more conditions for other distributions as needed

    # Fallback to platform module
    config.OS_NAME = distro.id()  # FIXME: Not working. Returns "Linux".
    logging.info(f"OS name: {config.OS_NAME}")
    config.OS_RELEASE = distro.version()
    logging.info(f"OS release: {config.OS_RELEASE}")
    return config.OS_NAME, config.OS_RELEASE


def get_package_manager():
    # Check for superuser command
    if shutil.which('sudo') is not None:
        config.SUPERUSER_COMMAND = "sudo"
    elif shutil.which('doas') is not None:
        config.SUPERUSER_COMMAND = "doas"

    # Check for package manager and associated packages
    if shutil.which('apt') is not None:  # debian, ubuntu
        config.PACKAGE_MANAGER_COMMAND_INSTALL = "apt install -y"
        config.PACKAGE_MANAGER_COMMAND_REMOVE = "apt remove -y"
        config.PACKAGE_MANAGER_COMMAND_QUERY = "dpkg -l | grep -E '^.i  '"  # IDEA: Switch to Python APT library? See https://github.com/FaithLife-Community/LogosLinuxInstaller/pull/33#discussion_r1443623996
        config.PACKAGES = "binutils cabextract fuse wget winbind"
        config.L9PACKAGES = ""  # FIXME: Missing Logos 9 Packages
        config.BADPACKAGES = "appimagelauncher"
    elif shutil.which('dnf') is not None:  # rhel, fedora
        config.PACKAGE_MANAGER_COMMAND_INSTALL = "dnf install -y"
        config.PACKAGE_MANAGER_COMMAND_REMOVE = "dnf remove -y"
        config.PACKAGE_MANAGER_COMMAND_QUERY = "dnf list installed | grep -E ^"
        config.PACKAGES = "patch mod_auth_ntlm_winbind samba-winbind samba-winbind-clients cabextract bc libxml2 curl"
        config.L9PACKAGES = ""  # FIXME: Missing Logos 9 Packages
        config.BADPACKAGES = "appiamgelauncher"
    elif shutil.which('pamac') is not None:  # manjaro
        config.PACKAGE_MANAGER_COMMAND_INSTALL = "pamac install --no-upgrade --no-confirm"
        config.PACKAGE_MANAGER_COMMAND_REMOVE = "pamac remove --no-confirm"
        config.PACKAGE_MANAGER_COMMAND_QUERY = "pamac list -i | grep -E ^"
        config.PACKAGES = "patch wget sed grep gawk cabextract samba bc libxml2 curl"
        config.L9PACKAGES = ""  # FIXME: Missing Logos 9 Packages
        config.BADPACKAGES= "appimagelauncher"
    elif shutil.which('pacman') is not None:  # arch, steamOS
        config.PACKAGE_MANAGER_COMMAND_INSTALL = r"pacman -Syu --overwrite * --noconfirm --needed"
        config.PACKAGE_MANAGER_COMMAND_REMOVE = r"pacman -R --no-confirm"
        config.PACKAGE_MANAGER_COMMAND_QUERY = "pacman -Q | grep -E ^"
        config.PACKAGES = "patch wget sed grep gawk cabextract samba bc libxml2 curl print-manager system-config-printer cups-filters nss-mdns foomatic-db-engine foomatic-db-ppds foomatic-db-nonfree-ppds ghostscript glibc samba extra-rel/apparmor core-rel/libcurl-gnutls winetricks cabextract appmenu-gtk-module patch bc lib32-libjpeg-turbo qt5-virtualkeyboard wine-staging giflib lib32-giflib libpng lib32-libpng libldap lib32-libldap gnutls lib32-gnutls mpg123 lib32-mpg123 openal lib32-openal v4l-utils lib32-v4l-utils libpulse lib32-libpulse libgpg-error lib32-libgpg-error alsa-plugins lib32-alsa-plugins alsa-lib lib32-alsa-lib libjpeg-turbo lib32-libjpeg-turbo sqlite lib32-sqlite libxcomposite lib32-libxcomposite libxinerama lib32-libgcrypt libgcrypt lib32-libxinerama ncurses lib32-ncurses ocl-icd lib32-ocl-icd libxslt lib32-libxslt libva lib32-libva gtk3 lib32-gtk3 gst-plugins-base-libs lib32-gst-plugins-base-libs vulkan-icd-loader lib32-vulkan-icd-loader"
        config.L9PACKAGES = ""  # FIXME: Missing Logos 9 Packages
        config.BADPACKAGES = "appimagelauncher"
    # Add more conditions for other package managers as needed

    # Add logging output.
    logging.debug(f"{config.SUPERUSER_COMMAND = }")
    logging.debug(f"{config.PACKAGE_MANAGER_COMMAND_INSTALL = }")
    logging.debug(f"{config.PACKAGE_MANAGER_COMMAND_QUERY = }")
    logging.debug(f"{config.PACKAGES = }")
    logging.debug(f"{config.L9PACKAGES = }")


def get_runmode():
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return 'binary'
    else:
        return 'script'


def query_packages(packages, mode="install"):
    if config.SKIP_DEPENDENCIES:
        return

    missing_packages = []
    conflicting_packages = []

    for p in packages:
        command = f"{config.PACKAGE_MANAGER_COMMAND_QUERY}{p}"
        logging.debug(f"pkg query command: \"{command}\"")
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True, text=True)
        logging.debug(f"pkg query result: {result.returncode}")
        if result.returncode != 0 and mode == "install":
            missing_packages.append(p)
        elif result.returncode == 0 and mode == "remove":
            conflicting_packages.append(p)

    msg = 'None'
    if mode == "install":
        if missing_packages:
            msg = f"Missing packages: {' '.join(missing_packages)}"
        logging.info(f"Missing packages: {msg}")
        return missing_packages
    if mode == "remove":
        if conflicting_packages:
            msg = f"Conflicting packages: {' '.join(conflicting_packages)}"
        logging.info(f"Conflicting packages: {msg}")
        return conflicting_packages


def install_packages(packages):
    if config.SKIP_DEPENDENCIES:
        return

    if packages:
        command = f"{config.SUPERUSER_COMMAND} {config.PACKAGE_MANAGER_COMMAND_INSTALL} {' '.join(packages)}"
        logging.debug(f"install_packages cmd: {command}")
        subprocess.run(command, shell=True, check=True)


def remove_packages(packages):
    if config.SKIP_DEPENDENCIES:
        return

    if packages:
        command = f"{config.SUPERUSER_COMMAND} {config.PACKAGE_MANAGER_COMMAND_REMOVE} {' '.join(packages)}"
        logging.debug(f"remove_packages cmd: {command}")
        subprocess.run(command, shell=True, check=True)


def have_dep(cmd):
    if shutil.which(cmd) is not None:
        return True
    else:
        return False


def clean_all():
    logging.info("Cleaning all temp files…")
    os.system("rm -fr /tmp/LBS.*")
    os.system(f"rm -fr {config.WORKDIR}")
    os.system(f"rm -f {config.PRESENT_WORKING_DIRECTORY}/wget-log*")
    logging.info("done")


def mkdir_critical(directory):
    try:
        os.mkdir(directory)
    except OSError:
        msg.logos_error(f"Can't create the {directory} directory")


def get_user_downloads_dir():
    home = Path.home()
    xdg_config = Path(os.getenv('XDG_CONFIG_HOME', home / '.config'))
    user_dirs_file = xdg_config / 'user-dirs.dirs'
    downloads_path = str(home / 'Downloads')
    if user_dirs_file.is_file():
        with user_dirs_file.open() as f:
            for line in f.readlines():
                if 'DOWNLOAD' in line:
                    downloads_path = line.rstrip().split('=')[1].replace('$HOME', str(home)).strip('"')
                    break
    return downloads_path


def cli_download(uri, destination):
    message = f"Downloading '{uri}' to '{destination}'"
    logging.info(message)
    msg.cli_msg(message)
    filename = os.path.basename(uri)

    # Set target.
    if destination != destination.rstrip('/'):
        target = os.path.join(destination, os.path.basename(uri))
        if not os.path.isdir(destination):
            os.makedirs(destination)
    elif os.path.isdir(destination):
        target = os.path.join(destination, os.path.basename(uri))
    else:
        target = destination
        dirname = os.path.dirname(destination)
        if not os.path.isdir(dirname):
            os.makedirs(dirname)

    # Download from uri in thread while showing progress bar.
    cli_queue = queue.Queue()
    args = [uri, target]
    kwargs = {'q': cli_queue}
    t = threading.Thread(target=net_get, args=args, kwargs=kwargs, daemon=True)
    t.start()
    try:
        while t.is_alive():
            if cli_queue.empty():
                continue
            write_progress_bar(cli_queue.get())
        print()
    except KeyboardInterrupt:
        print()
        msg.logos_error('Interrupted with Ctrl+C')


def logos_reuse_download(SOURCEURL, FILE, TARGETDIR):
    DIRS = [
        config.INSTALLDIR,
        os.getcwd(),
        config.MYDOWNLOADS,
    ]
    FOUND = 1
    for i in DIRS:
        if i is not None:
            logging.debug(f"Checking {i} for {FILE}.")
            file_path = Path(i) / FILE
            if os.path.isfile(file_path):
                logging.info(f"{FILE} exists in {i}. Verifying properties.")
                if verify_downloaded_file(SOURCEURL, file_path):
                    logging.info(f"{FILE} properties match. Using it…")
                    msg.cli_msg(f"Copying {FILE} into {TARGETDIR}")
                    try:
                        shutil.copy(os.path.join(i, FILE), TARGETDIR)
                    except shutil.SameFileError:
                        pass
                    FOUND = 0
                    break
                else:
                    logging.info(f"Incomplete file: {file_path}.")
    if FOUND == 1:
        file_path = os.path.join(config.MYDOWNLOADS, FILE)
        cli_download(SOURCEURL, file_path)
        if verify_downloaded_file(SOURCEURL, file_path):
            msg.cli_msg(f"Copying: {FILE} into: {TARGETDIR}")
            try:
                shutil.copy(os.path.join(config.MYDOWNLOADS, FILE), TARGETDIR)
            except shutil.SameFileError:
                pass
        else:
            msg.logos_error(f"Bad file size or checksum: {file_path}")


def delete_symlink(symlink_path):
    symlink_path = Path(symlink_path)
    if symlink_path.is_symlink():
        try:
            symlink_path.unlink()
            logging.info(f"Symlink at {symlink_path} removed successfully.")
        except Exception as e:
            logging.error(f"Error removing symlink: {e}")


def make_skel(app_image_filename):
    config.SELECTED_APPIMAGE_FILENAME = f"{app_image_filename}"

    logging.info(f"* Creating the skeleton for Logos inside {config.INSTALLDIR}")
    os.makedirs(config.APPDIR_BINDIR, exist_ok=True)

    # Making the links
    current_dir = os.getcwd()
    try:
        os.chdir(config.APPDIR_BINDIR)
    except OSError as e:
        die(f"ERROR: Can't open dir: {config.APPDIR_BINDIR}: {e}")
    if not os.path.islink(f"{config.APPDIR_BINDIR}/{config.APPIMAGE_LINK_SELECTION_NAME}"):
        os.symlink(config.SELECTED_APPIMAGE_FILENAME, f"{config.APPDIR_BINDIR}/{config.APPIMAGE_LINK_SELECTION_NAME}")
    for name in ["wine", "wine64", "wineserver"]:
        if not os.path.islink(name):
            os.symlink(config.APPIMAGE_LINK_SELECTION_NAME, name)
    try:
        os.chdir(current_dir)
    except OSError as e:
        die("ERROR: Can't go back to previous dir!: {e}")

    os.makedirs(f"{config.APPDIR}/wine64_bottle", exist_ok=True)

    logging.info("Finished creating the skeleton.")


def steam_preinstall_dependencies():
    subprocess.run([config.SUPERUSER_COMMAND, "steamos-readonly", "disable"], check=True)
    subprocess.run([config.SUPERUSER_COMMAND, "pacman-key", "--init"], check=True)
    subprocess.run([config.SUPERUSER_COMMAND, "pacman-key", "--populate", "archlinux"], check=True)


def steam_postinstall_dependencies():
    subprocess.run([config.SUPERUSER_COMMAND, "sed", '-i',
                    's/mymachines resolve/mymachines mdns_minimal [NOTFOUND=return] resolve/', '/etc/nsswitch.conf'],
                   check=True)
    subprocess.run([config.SUPERUSER_COMMAND, "locale-gen"], check=True)
    subprocess.run([config.SUPERUSER_COMMAND, "systemctl", "enable", "--now", "avahi-daemon"], check=True)
    subprocess.run([config.SUPERUSER_COMMAND, "systemctl", "enable", "--now", "cups"], check=True)
    subprocess.run([config.SUPERUSER_COMMAND, "steamos-readonly", "enable"], check=True)


def install_dependencies(packages, badpackages, logos9_packages=None):
    missing_packages = []
    conflicting_packages = []
    package_list = []
    if packages:
        package_list = packages.split()
    bad_package_list = []
    if badpackages:
        bad_package_list = badpackages.split()
    if logos9_packages:
        package_list.extend(logos9_packages.split())

    if config.PACKAGE_MANAGER_COMMAND_QUERY:
        missing_packages = query_packages(package_list)
        conflicting_packages = query_packages(bad_package_list, "remove")

    if config.PACKAGE_MANAGER_COMMAND_INSTALL:
        if missing_packages and conflicting_packages:
            message = f"Your {config.OS_NAME} computer requires installing and removing some software. To continue, the program will attempt to install the package(s): {missing_packages} by using ({config.PACKAGE_MANAGER_COMMAND_INSTALL}) and will remove the package(s): {conflicting_packages} by using ({config.PACKAGE_MANAGER_COMMAND_REMOVE}). Proceed?"
        elif missing_packages:
            message = f"Your {config.OS_NAME} computer requires installing some software. To continue, the program will attempt to install the package(s): {missing_packages} by using ({config.PACKAGE_MANAGER_COMMAND_INSTALL}). Proceed?"
        elif conflicting_packages:
            message = f"Your {config.OS_NAME} computer requires removing some software. To continue, the program will attempt to remove the package(s): {conflicting_packages} by using ({config.PACKAGE_MANAGER_COMMAND_REMOVE}). Proceed?"
        else:
            logging.debug("No missing or conflicting dependencies found.")

        #TODO: Need to send continue question to user based on DIALOG.
        # All we do above is create a message that we never send.
        # Do we need a TK continue question? I see we have a CLI and curses one in msg.py

        if config.OS_NAME == "Steam":
            steam_preinstall_dependencies()

        check_libs(["libfuse"]) # libfuse: needed for AppImage use. This is the only known needed library.

        if missing_packages:
            install_packages(missing_packages)

        if conflicting_packages:
            remove_packages(conflicting_packages) # AppImage Launcher is the only known conflicting package.
            config.REBOOT_REQUIRED = True

        if config.OS_NAME == "Steam":
            steam_postinstall_dependencies()

        if config.REBOOT_REQUIRED:
            #TODO: Add resumable install functionality to speed up running the program after reboot. See #19.
            reboot()

    else:
        msg.logos_error(
            f"The script could not determine your {config.OS_NAME} install's package manager or it is unsupported. Your computer is missing the command(s) {missing_packages}. Please install your distro's package(s) associated with {missing_packages} for {config.OS_NAME}.")


def have_lib(library, ld_library_path):
    roots = ['/usr/lib', '/lib']
    if ld_library_path is not None:
        roots = [*ld_library_path.split(':'), *roots]
    for root in roots:
        libs = [l for l in Path(root).rglob(f"{library}*")]
        if len(libs) > 0:
            logging.debug(f"'{library}' found at '{libs[0]}'")
            return True
    return False


def check_libs(libraries):
    ld_library_path = os.environ.get('LD_LIBRARY_PATH', '')
    for library in libraries:
        have_lib_result = have_lib(library, ld_library_path)
        if have_lib_result:
            logging.info(f"* {library} is installed!")
        else:
            if config.PACKAGE_MANAGER_COMMAND_INSTALL:
                message = f"Your {config.OS_NAME} install is missing the library: {library}. To continue, the script will attempt to install the library by using {config.PACKAGE_MANAGER_COMMAND_INSTALL}. Proceed?"
                if msg.cli_continue_question(message, "", ""):
                    install_packages(config.PACKAGES)
            else:
                msg.logos_error(
                    f"The script could not determine your {config.OS_NAME} install's package manager or it is unsupported. Your computer is missing the library: {library}. Please install the package associated with {library} for {config.OS_NAME}.")


def check_dependencies():
    if config.TARGETVERSION:
        targetversion = int(config.TARGETVERSION)
    else:
        targetversion = 10
    logging.info(f"Checking Logos {str(targetversion)} dependencies…")
    if targetversion == 10:
        install_dependencies(config.PACKAGES, config.BADPACKAGES)
    elif targetversion == 9:
        install_dependencies(config.PACKAGES, config.BADPACKAGES, config.L9PACKAGES)
    else:
        logging.error(f"TARGETVERSION not found: {config.TARGETVERSION}.")


def file_exists(file_path):
    if file_path is not None:
        expanded_path = os.path.expanduser(file_path)
        return os.path.isfile(expanded_path)
    else:
        return False


def check_logos_release_version(version, threshold, check_version_part):
    version_parts = list(map(int, version.split('.')))
    return version_parts[check_version_part - 1] < threshold


def filter_versions(versions, threshold, check_version_part):
    return [version for version in versions if check_logos_release_version(version, threshold, check_version_part)]


def get_logos_releases(app=None):
    msg.cli_msg(f"Downloading release list for {config.FLPRODUCT} {config.TARGETVERSION}...")  # noqa: E501
    url = f"https://clientservices.logos.com/update/v1/feed/logos{config.TARGETVERSION}/stable.xml"  # noqa: E501

    response_xml = net_get(url)
    # if response_xml is None and None not in [q, app]:
    if response_xml is None:
        if app:
            app.release_q.put(None)
            app.root.event_generate("<<ReleaseCheckProgress>>")
        return None

    # Parse XML
    root = ET.fromstring(response_xml)

    # Define namespaces
    namespaces = {
        'ns0': 'http://www.w3.org/2005/Atom',
        'ns1': 'http://services.logos.com/update/v1/'
    }

    # Extract versions
    releases = []
    # Obtain all listed releases.
    for entry in root.findall('.//ns1:version', namespaces):
        release = entry.text
        releases.append(release)
        # if len(releases) == 5:
        #    break

    filtered_releases = filter_versions(releases, 30, 1)
    logging.debug(f"Available releases: {', '.join(releases)}")
    logging.debug(f"Filtered releases: {', '.join(filtered_releases)}")

    if app:
        app.release_q.put(filtered_releases)
        app.root.event_generate("<<ReleaseCheckProgress>>")
    return filtered_releases


def get_winebin_code_and_desc(binary):
    # Set binary code, description, and path based on path
    codes = {
        "Recommended": "Use the recommended AppImage",
        "AppImage": "AppImage of Wine64",
        "System": "Use the system binary (i.e., /usr/bin/wine64). WINE must be 7.18-staging or later, or 8.16-devel or later, and cannot be version 8.0.",
        "Proton": "Install using the Steam Proton fork of WINE.",
        "PlayOnLinux": "Install using a PlayOnLinux WINE64 binary.",
        "Custom": "Use a WINE64 binary from another directory.",
    }
    # TODO: The GUI currently cannot distinguish between the recommended AppImage and another on the system.
    # We need to add some manner of making this distintion in the GUI, which is why the wine binary codes exist.
    # Currently the GUI only accept an array with a single element, the binary itself; this will need to be modified to
    # a two variable array, at the least, even if we hide the wine binary code, but it might be useful to tell the GUI
    # user that a particular AppImage/binary is recommended.
    # Below is my best guess for how to do this with the single element array… Does it work?
    if binary == f"{config.APPDIR_BINDIR}/{config.RECOMMENDED_WINE64_APPIMAGE_FULL_FILENAME}":
        code = "Recommended"
    elif binary.lower().endswith('.appimage'):
        code = "AppImage"
    elif "/usr/bin/" in binary:
        code = "System"
    elif "Proton" in binary:
        code = "Proton"
    elif "PlayOnLinux" in binary:
        code = "PlayOnLinux"
    else:
        code = "Custom"
    desc = codes.get(code)
    return code, desc


def get_wine_options(appimages, binaries) -> Union[List[List[str]], List[str]]:
    wine_binary_options = []

    # Add AppImages to list
    if config.TARGETVERSION != "9":
        if config.DIALOG == "curses":
            appimage_entries = [["AppImage", filename, "AppImage of Wine64"] for filename in appimages] # [Code, File Path, Description]
            wine_binary_options.append(
                ["Recommended", # Code
                 f'{config.APPDIR_BINDIR}/{config.RECOMMENDED_WINE64_APPIMAGE_FULL_FILENAME}', # File Path
                f"AppImage of Wine64 {config.RECOMMENDED_WINE64_APPIMAGE_FULL_VERSION}"]) # Description
            wine_binary_options.extend(appimage_entries)
        elif config.DIALOG == 'tk':
            wine_binary_options.append(f"{config.APPDIR_BINDIR}/{config.RECOMMENDED_WINE64_APPIMAGE_FULL_FILENAME}")
            wine_binary_options.extend(appimages)

    sorted_binaries = sorted(set(binaries))

    for binary in binaries:
        WINEBIN_PATH = binary
        WINEBIN_CODE, WINEBIN_DESCRIPTION = get_winebin_code_and_desc(binary)

        # Create wine binary option array
        if config.DIALOG == "curses":
            wine_binary_options.append([WINEBIN_CODE, WINEBIN_PATH, WINEBIN_DESCRIPTION])
        elif config.DIALOG == 'tk':
            wine_binary_options.append(WINEBIN_PATH)

    if config.DIALOG == "curses":
        wine_binary_options.append(["Exit", "Exit", "Cancel installation."])

    return wine_binary_options


def get_system_winetricks():
    try:
        p = subprocess.run(['winetricks', '--version'], capture_output=True, text=True)
        version = int(p.stdout.rstrip()[:8])
        path = shutil.which('winetricks')
        return (path, version)
    except FileNotFoundError:
        return None


def get_pids_using_file(file_path, mode=None):
    pids = set()
    for proc in psutil.process_iter(['pid', 'open_files']):
        try:
            if mode is not None:
                paths = [f.path for f in proc.open_files() if f.mode == mode]
            else:
                paths = [f.path for f in proc.open_files()]
            if len(paths) > 0 and file_path in paths:
                pids.add(proc.pid)
        except psutil.AccessDenied:
            pass
    return pids


def wait_process_using_dir(directory):
    logging.info(f"* Starting wait_process_using_dir for {directory}…")

    # Get pids and wait for them to finish.
    pids = get_pids_using_file(directory)
    for pid in pids:
        logging.info(f"wait_process_using_dir PID: {pid}")
        psutil.wait(pid)

    logging.info("* End of wait_process_using_dir.")


def wget(uri, target, q=None, app=None, evt=None):
    cmd = ['wget', '-q', '--show-progress', '--progress=dot', '-c', uri, '-O', target]
    with subprocess.Popen(cmd, stderr=subprocess.PIPE, encoding='UTF8') as proc:
        while True:
            line = proc.stderr.readline()
            if not line:
                break
            m = re.search(r'[0-9]+%', line)
            if m is not None:
                p = m[0].rstrip('%')
                if None not in [q, app, evt]:
                    q.put(p)
                    app.root.event_generate(evt)


def net_get(url, target=None, app=None, evt=None, q=None):
    # TODO:
    # - Check available disk space before starting download
    logging.debug(f"Download source: {url}")
    logging.debug(f"Download destination: {target}")
    target = FileProps(target)  # sets path and size attribs
    parsed_url = urlparse(url)
    domain = parsed_url.netloc  # Gets the requested domain
    url = UrlProps(url)  # uses requests to set headers, size, md5 attribs
    if url.headers is None:
        logging.critical("Could not get headers.")
        return None

    # Initialize variables.
    local_size = 0
    total_size = url.size  # None or int
    logging.debug(f"File size on server: {total_size}")
    percent = None
    chunk_size = 100 * 1024  # 100 KB default
    if type(total_size) is int:
        chunk_size = min([int(total_size / 50), 2 * 1024 * 1024])  # smaller of 2% of filesize or 2 MB
    headers = {'Accept-Encoding': 'identity'}  # force non-compressed file transfer
    file_mode = 'wb'

    # If file exists and URL is resumable, set download Range.
    if target.path is not None and target.path.is_file():
        logging.debug(f"File exists: {str(target.path)}")
        local_size = target.get_size()
        logging.info(f"Current downloaded size in bytes: {local_size}")
        if url.headers.get('Accept-Ranges') == 'bytes':
            logging.debug(f"Server accepts byte range; attempting to resume download.")
            file_mode = 'ab'
            if type(url.size) is int:
                headers['Range'] = f'bytes={local_size}-{total_size}'
            else:
                headers['Range'] = f'bytes={local_size}-'

    logging.debug(f"{chunk_size = }; {file_mode = }; {headers = }")

    # Log download type.
    if 'Range' in headers.keys():
        message = f"Continuing download for {url.path}."
    else:
        message = f"Starting new download for {url.path}."
    logging.info(message)

    # Initiate download request.
    try:
        if target.path is None:  # return url content as text
            with requests.get(url.path, headers=headers) as r:
                if callable(r):
                    logging.error("Failed to retrieve data from the URL.")
                    return None

                try:
                    r.raise_for_status()
                except requests.exceptions.HTTPError as e:
                    if domain == "github.com":
                        if e.response.status_code == 403 or e.response.status_code == 429:
                            logging.error("GitHub API rate limit exceeded. Please wait before trying again.")
                    else:
                        logging.error(f"HTTP error occurred: {e.response.status_code}")
                    return None

                return r.text
        else:  # download url to target.path
            with requests.get(url.path, stream=True, headers=headers) as r:
                with target.path.open(mode=file_mode) as f:
                    if file_mode == 'wb':
                        mode_text = 'Writing'
                    else:
                        mode_text = 'Appending'
                    logging.debug(f"{mode_text} data to file {str(target.path)}.")
                    for chunk in r.iter_content(chunk_size=chunk_size):
                        f.write(chunk)
                        local_size = target.get_size()
                        if type(total_size) is int:
                            percent = round(local_size / total_size * 100)
                            if None not in [app, evt]:
                                # Send progress value to tk window.
                                app.get_q.put(percent)
                                app.root.event_generate(evt)
                            elif q is not None:
                                # Send progress value to queue param.
                                q.put(percent)
    except requests.exceptions.RequestException as e:
        logging.error(f"Error occurred during HTTP request: {e}")
        return None, r  # Return None values to indicate an error condition
    except Exception as e:
        msg.logos_error(e)
    except KeyboardInterrupt:
        print()
        msg.logos_error("Killed with Ctrl+C")


def verify_downloaded_file(url, file_path, app=None, evt=None):
    res = False
    msg = f"{file_path} is the wrong size."
    right_size = same_size(url, file_path)
    if right_size:
        msg = f"{file_path} has the wrong MD5 sum."
        right_md5 = same_md5(url, file_path)
        if right_md5:
            msg = f"{file_path} is verified."
            res = True
    logging.info(msg)
    if None in [app, evt]:
        return res
    app.check_q.put((evt, res))
    app.root.event_generate(evt)


def same_md5(url, file_path):
    logging.debug(f"Comparing MD5 of {url} and {file_path}.")
    url_md5 = UrlProps(url).get_md5()
    logging.debug(f"{url_md5 = }")
    if url_md5 is None:  # skip MD5 check if not provided with URL
        res = True
    else:
        file_md5 = FileProps(file_path).get_md5()
        logging.debug(f"{file_md5 = }")
        res = url_md5 == file_md5
    return res


def same_size(url, file_path):
    logging.debug(f"Comparing size of {url} and {file_path}.")
    url_size = UrlProps(url).size
    file_size = FileProps(file_path).size
    logging.debug(f"{url_size = } B; {file_size = } B")
    res = url_size == file_size
    return res


def write_progress_bar(percent, screen_width=80):
    y = '.'
    n = ' '
    l_f = int(screen_width * 0.75)  # progress bar length
    l_y = int(l_f * percent / 100)  # num. of chars. complete
    l_n = l_f - l_y  # num. of chars. incomplete
    print(f" [{y * l_y}{n * l_n}] {percent:>3}%", end='\r')  # end='\x1b[1K\r' to erase to end of line


def app_is_installed():
    return config.LOGOS_EXE is not None and os.access(config.LOGOS_EXE, os.X_OK)

def log_current_persistent_config():
    logging.debug("Current persistent config:")
    for k in config.persistent_config_keys:
        logging.debug(f"{k}: {config.__dict__.get(k)}")

def enough_disk_space(dest_dir, bytes_required):
    free_bytes = shutil.disk_usage(dest_dir).free
    logging.debug(f"{free_bytes = }; {bytes_required = }")
    return free_bytes > bytes_required

def get_path_size(file_path):
    file_path = Path(file_path)
    if not file_path.exists():
        path_size = None
    else:
        path_size = sum(f.stat().st_size for f in file_path.rglob('*')) + file_path.stat().st_size
    return path_size

def get_folder_group_size(src_dirs, q):
    src_size = 0
    for d in src_dirs:
        if not d.is_dir():
            continue
        src_size += get_path_size(d)
    q.put(src_size)

def get_copy_progress(dest_path, txfr_size, dest_size_init=0):
    dest_size_now = get_path_size(dest_path)
    if dest_size_now is None:
        dest_size_now = 0
    size_diff = dest_size_now - dest_size_init
    progress = round(size_diff / txfr_size * 100)
    return progress

def get_latest_folder(folder_path):
    folders = [f for f in Path(folder_path).glob('*')]
    if not folders:
        logging.warning(f"No folders found in {folder_path}")
        return None
    folders.sort()
    logging.info(f"Found {len(folders)} backup folders.")
    latest = folders[-1]
    logging.info(f"Latest folder: {latest}")
    return latest


def get_latest_release_data(releases_url):
    data = net_get(releases_url)
    if data:
        try:
            json_data = json.loads(data)
            logging.debug(f"{json_data=}")
        except json.JSONDecodeError as e:
            logging.error(f"Error decoding JSON response: {e}")
            return None

        if not isinstance(json_data, list) or len(json_data) == 0:
            logging.error("Invalid or empty JSON response.")
            return None
        else:
            return json_data
    else:
        logging.critical("Could not get latest release URL.")
        return None


def get_latest_release_url(json_data):
    release_url = json_data[0].get('assets')[0].get('browser_download_url')  # noqa: E501
    logging.info(f"Release URL: {release_url}")
    return release_url


def get_latest_release_version_tag_name(json_data):
    release_tag_name = json_data[0].get('tag_name')  # noqa: E501
    logging.info(f"Release URL Tag Name: {release_tag_name}")
    return release_tag_name


def set_logoslinuxinstaller_latest_release_config():
    releases_url = "https://api.github.com/repos/FaithLife-Community/LogosLinuxInstaller/releases"  # noqa: E501
    json_data = get_latest_release_data(releases_url)
    logoslinuxinstaller_url = get_latest_release_url(json_data)
    logoslinuxinstaller_tag_name = get_latest_release_version_tag_name(json_data)
    if logoslinuxinstaller_url is None:
        logging.critical("Unable to set LogosLinuxInstaller release without URL.")  # noqa: E501
        return
    config.LOGOS_LATEST_VERSION_URL = logoslinuxinstaller_url
    config.LOGOS_LATEST_VERSION_FILENAME = os.path.basename(logoslinuxinstaller_url) #noqa: #501
    # Getting version relies on the the tag_name field in the JSON data. This is already parsed down to vX.X.X.
    # Therefore we must strip the v.
    config.LLI_LATEST_VERSION = logoslinuxinstaller_tag_name.lstrip('v')
    logging.info(f"{config.LLI_LATEST_VERSION}")


def set_recommended_appimage_config():
    releases_url = "https://api.github.com/repos/FaithLife-Community/wine-appimages/releases"  # noqa: E501
    json_data = get_latest_release_data(releases_url)
    appimage_url = get_latest_release_url(json_data)
    if appimage_url is None:
        logging.critical("Unable to set recommended appimage config without URL.")  # noqa: E501
        return
    config.RECOMMENDED_WINE64_APPIMAGE_FULL_URL = appimage_url
    config.RECOMMENDED_WINE64_APPIMAGE_URL = appimage_url
    config.RECOMMENDED_WINE64_APPIMAGE_FULL_FILENAME = os.path.basename(appimage_url)  # noqa: E501
    config.RECOMMENDED_WINE64_APPIMAGE_FILENAME = config.RECOMMENDED_WINE64_APPIMAGE_FULL_FILENAME.split(".AppImage")[0]  # noqa: E501
    # Getting version and branch rely on the filename having this format:
    #   wine-[branch]_[version]-[arch]
    parts = config.RECOMMENDED_WINE64_APPIMAGE_FILENAME.split('-')
    branch_version = parts[1]
    branch, version = branch_version.split('_')
    config.RECOMMENDED_WINE64_APPIMAGE_FULL_VERSION = f"v{version}-{branch}"
    config.RECOMMENDED_WINE64_APPIMAGE_VERSION = f"{version}"
    config.RECOMMENDED_WINE64_APPIMAGE_BRANCH = f"{branch}"


def check_for_updates():
    # We limit the number of times set_recommended_appimage_config is run in order to avoid GitHub API limits.
    # This sets the check to once every 12 hours.

    now = datetime.now().replace(microsecond=0)
    if config.CHECK_UPDATES:
        check_again = now
    elif config.LAST_UPDATED is not None:
        check_again = datetime.strptime(config.LAST_UPDATED.strip(), '%Y-%m-%dT%H:%M:%S')
        check_again += timedelta(hours=12)
    else:
        check_again = now

    if now >= check_again:
        logging.debug("Running self-update.")

        set_logoslinuxinstaller_latest_release_config()
        set_recommended_appimage_config()

        config.LAST_UPDATED = now.isoformat()
        write_config(config.CONFIG_FILE)
    else:
        logging.debug("Skipping self-update.")


def get_recommended_appimage():
    wine64_appimage_full_filename = Path(config.RECOMMENDED_WINE64_APPIMAGE_FULL_FILENAME)
    appdir_bindir = Path(config.APPDIR_BINDIR)
    dest_path = appdir_bindir / wine64_appimage_full_filename
    if dest_path.is_file():
        return
    else:
        logos_reuse_download(config.RECOMMENDED_WINE64_APPIMAGE_FULL_URL, config.RECOMMENDED_WINE64_APPIMAGE_FULL_FILENAME, config.APPDIR_BINDIR)


def compare_logos_linux_installer_version():
    if config.LLI_CURRENT_VERSION is not None and config.LLI_LATEST_VERSION is not None:
        logging.debug(f"{config.LLI_CURRENT_VERSION=}; {config.LLI_LATEST_VERSION=}")
        if version.parse(config.LLI_CURRENT_VERSION) < version.parse(config.LLI_LATEST_VERSION):
            # Current release is older than recommended.
            status = 0
            message = "yes"
        elif version.parse(config.LLI_CURRENT_VERSION) == version.parse(config.LLI_LATEST_VERSION):
            # Current release is latest.
            status = 1
            message = "uptodate"
        elif version.parse(config.LLI_CURRENT_VERSION) > version.parse(config.LLI_LATEST_VERSION):
            # Installed version is custom.
            status = 2
            message = "no"
    else:
        status = False
        message = "config.LLI_CURRENT_VERSION or config.LLI_LATEST_VERSION is not set."

    logging.debug(f"{status=}; {message=}")
    return status, message


def compare_recommended_appimage_version():
    wine_release = []
    if config.WINE_EXE is not None:
        wine_release, error_message = wine.get_wine_release(config.WINE_EXE)
        if wine_release is not None and wine_release is not False:
            current_version = '.'.join([str(n) for n in wine_release[:2]])
            logging.debug(f"Current wine release: {current_version}")

            if config.RECOMMENDED_WINE64_APPIMAGE_VERSION:
                logging.debug(f"Recommended wine release: {config.RECOMMENDED_WINE64_APPIMAGE_VERSION}")  # noqa: E501
                if current_version < config.RECOMMENDED_WINE64_APPIMAGE_VERSION:  # noqa: E501
                    # Current release is older than recommended.
                    status = 0
                    message = "yes"
                elif current_version == config.RECOMMENDED_WINE64_APPIMAGE_VERSION:  # noqa: E501
                    # Current release is latest.
                    status = 1
                    message = "uptodate"
                elif current_version > config.RECOMMENDED_WINE64_APPIMAGE_VERSION:  # noqa: E501
                    # Installed version is custom
                    status = 2
                    message = "no"
            else:
                status = False
                message = f"Error: {error_message}"
        else:
            status = False
            message = f"Error: {error_message}"
    else:
        status = False
        message = "config.WINE_EXE is not set."

    logging.debug(f"{status=}; {message=}")
    return status, message


def update_lli_binary():
    lli_file_path = os.path.realpath(sys.argv[0])
    lli_download_path = Path(config.MYDOWNLOADS) / "LogosLinuxInstaller"
    temp_path = Path(config.MYDOWNLOADS) / "LogosLinuxInstaller.tmp"
    logging.debug(f"Updating Logos Linux Installer to latest version by overwriting: {lli_file_path}")
    logos_reuse_download(config.LOGOS_LATEST_VERSION_URL, "LogosLinuxInstaller", config.MYDOWNLOADS)
    shutil.copy(lli_download_path, temp_path)
    try:
        shutil.move(temp_path, lli_file_path)
    except Exception as e:
        logging.error(f"Failed to replace the binary: {e}")
        return

    os.chmod(sys.argv[0], os.stat(sys.argv[0]).st_mode | 0o111)
    logging.debug("Successfully updated Logos Linux Installer.")
    restart_lli()


def is_appimage(file_path):
    # Ref:
    # - https://cgit.freedesktop.org/xdg/shared-mime-info/commit/?id=c643cab25b8a4ea17e73eae5bc318c840f0e3d4b
    # - https://github.com/AppImage/AppImageSpec/blob/master/draft.md#image-format
    # Note:
    # result is a tuple: (is AppImage: True|False, AppImage type: 1|2|None)
    # result = (False, None)
    expanded_path = Path(file_path).expanduser().resolve()
    logging.debug(f"Converting path to expanded_path: {expanded_path}")
    if file_exists(expanded_path):
        logging.debug(f"{expanded_path} exists!")
        with file_path.open('rb') as f:
            f.seek(1)
            elf_sig = f.read(3)
            f.seek(8)
            ai_sig = f.read(2)
            f.seek(10)
            v_sig = f.read(1)

        appimage_check = elf_sig == b'ELF' and ai_sig == b'AI'
        appimage_type = int.from_bytes(v_sig)

        return (appimage_check, appimage_type)
    else:
        return (False, None)


def check_appimage(file):
    logging.debug(f"Checking if {file} is a usable AppImage.")
    if file is None:
        logging.error(f"check_appimage: received None for file.")
        return False
    
    file_path=Path(file)

    appimage, appimage_type = is_appimage(file_path)
    if appimage:
        logging.debug(f"It is an AppImage!")
        if appimage_type == 1:
            logging.error(f"{file_path}: Can't handle AppImage version {str(appimage_type)} yet.")
            return False
        else:
            logging.debug(f"It is a usable AppImage!")
            return True
    else:
        logging.debug(f"It is not an AppImage!")
        return False


def find_appimage_files():
    appimages = []
    directories = [
        os.path.expanduser("~") + "/bin",
        config.APPDIR_BINDIR,
        get_user_downloads_dir()
    ]
    if config.CUSTOMBINPATH is not None:
        directories.append(config.CUSTOMBINPATH)

    if sys.version_info < (3, 12):
        raise RuntimeError("Python 3.12 or higher is required for .rglob() flag `case-sensitive` ")

    for d in directories:
        appimage_paths = Path(d).rglob('wine*.appimage', case_sensitive=False)
        for p in appimage_paths:
            if p is not None and check_appimage(p):
                output1, output2 = wine.check_wine_version_and_branch(p)
                if output1 is not None and output1:
                    appimages.append(str(p))
                else:
                    logging.info(f"AppImage file {p} not added: {output2}")

    return appimages


def find_wine_binary_files():
    wine_binary_path_list = [
        "/usr/local/bin",
        os.path.expanduser("~") + "/bin",
        os.path.expanduser("~") + "/PlayOnLinux/wine/linux-amd64/*/bin",
        os.path.expanduser("~") + "/.steam/steam/steamapps/common/Proton*/files/bin",
    ]

    if config.CUSTOMBINPATH is not None:
        wine_binary_path_list.append(config.CUSTOMBINPATH)

    # Temporarily modify PATH for additional WINE64 binaries.
    for p in wine_binary_path_list:
        if p is None:
            continue
        if p not in os.environ['PATH'] and os.path.isdir(p):
            os.environ['PATH'] = os.environ['PATH'] + os.pathsep + p

    # Check each directory in PATH for wine64; add to list
    binaries = []
    paths = os.environ["PATH"].split(":")
    for path in paths:
        binary_path = os.path.join(path, "wine64")
        if os.path.exists(binary_path) and os.access(binary_path, os.X_OK):
            binaries.append(binary_path)

    for binary in binaries[:]:
        output1, output2 = wine.check_wine_version_and_branch(binary)
        if output1 is not None and output1:
            continue
        else:
            binaries.remove(binary)
            logging.info(f"Removing binary: {binary} because: {output2}")

    return binaries


def set_appimage_symlink(app=None):
    # This function assumes make_skel() has been run once.
    if config.APPIMAGE_FILE_PATH is None:
        config.APPIMAGE_FILE_PATH = config.RECOMMENDED_WINE64_APPIMAGE_FULL_FILENAME  # noqa: E501

    logging.debug(f"{config.APPIMAGE_FILE_PATH=}")
    if config.APPIMAGE_FILE_PATH == config.RECOMMENDED_WINE64_APPIMAGE_FULL_FILENAME:  # noqa: E501
        get_recommended_appimage()
        selected_appimage_file_path = Path(config.APPDIR_BINDIR) / config.APPIMAGE_FILE_PATH  # noqa: E501
    else:
        selected_appimage_file_path = Path(config.APPIMAGE_FILE_PATH)

    if not check_appimage(selected_appimage_file_path):
        logging.warning(f"Cannot use {selected_appimage_file_path}.")
        return

    appimage_filename = selected_appimage_file_path.name
    appimage_filepath = Path(f"{config.APPDIR_BINDIR}/{appimage_filename}")

    copy_message = (
        f"Should the program copy {selected_appimage_file_path} to the"
        f" {config.APPDIR_BINDIR} directory?"
    )

    # Determine if user wants their AppImage in the Logos on Linux bin dir.
    if appimage_filepath.exists():
        confirm = False
    else:
        if config.DIALOG == "tk":
            # TODO: With the GUI this runs in a thread. It's not clear if the
            # messagebox will work correctly. It may need to be triggered from
            # here with an event and then opened from the main thread.
            tk_root = tk.Tk()
            tk_root.withdraw()
            confirm = tk.messagebox.askquestion("Confirmation", copy_message)
            tk_root.destroy()
        elif config.DIALOG == "curses":
            confirm = tui.confirm("Confirmation", copy_message)
        else:
            confirm = msg.cli_question(copy_message)
    # FIXME: What if user cancels the confirmation dialog?

    appimage_symlink_path = Path(f"{config.APPDIR_BINDIR}/{config.APPIMAGE_LINK_SELECTION_NAME}")  # noqa: E501
    delete_symlink(appimage_symlink_path)

    # FIXME: confirm is always False b/c appimage_filepath always exists b/c
    # it's copied in place via logos_reuse_download function above in
    # get_recommended_appimage.
    if confirm is True or confirm == 'yes':
        logging.info(f"Copying {selected_appimage_file_path} to {config.APPDIR_BINDIR}.")  # noqa: E501
        shutil.copy(selected_appimage_file_path, f"{config.APPDIR_BINDIR}")
        os.symlink(appimage_filepath, appimage_symlink_path)
        config.SELECTED_APPIMAGE_FILENAME = f"{appimage_filename}"
    # If not, use the selected AppImage's full path for link creation.
    elif confirm is False or confirm == 'no':
        logging.debug(f"{appimage_filepath} already exists in {config.APPDIR_BINDIR}. No need to copy.")  # noqa: E501
        os.symlink(selected_appimage_file_path, appimage_symlink_path)
        logging.debug("AppImage symlink updated.")
        config.SELECTED_APPIMAGE_FILENAME = f"{selected_appimage_file_path}"
        logging.debug("Updated config with new AppImage path.")
    else:
        logging.error("Error getting user confirmation.")

    write_config(config.CONFIG_FILE)
    if app:
        app.root.event_generate("<<UpdateLatestAppImageButton>>")


def update_to_latest_lli_release():
    status, _ = compare_logos_linux_installer_version()

    if get_runmode() != 'binary':
        logging.error("Can't update LogosLinuxInstaller when run as a script.")
    elif status == 0:
        update_lli_binary()
    elif status == 1:
        logging.debug(f"{config.LLI_TITLE} is already at the latest version.")
    elif status == 2:
        logging.debug(f"{config.LLI_TITLE} is at a newer version than the latest.") # noqa: 501


def update_to_latest_recommended_appimage():
    config.APPIMAGE_FILE_PATH = config.RECOMMENDED_WINE64_APPIMAGE_FULL_FILENAME  # noqa: E501
    status, _ = compare_recommended_appimage_version()
    if status == 0:
        set_appimage_symlink()
    elif status == 1:
        logging.debug("The AppImage is already set to the latest recommended.")
    elif status == 2:
        logging.debug("The AppImage version is newer than the latest recommended.")  # noqa: E501
