import json
import logging
import os
import tempfile


# Config file variables to preserve post-install.
APPDIR = os.getenv('APPDIR')
APPDIR_BINDIR = os.getenv('APPDIR_BINDIR')
APPIMAGE_LINK_SELECTION_NAME = os.getenv('APPIMAGE_LINK_SELECTION_NAME')
BACKUPDIR = os.getenv('BACKUPDIR')
FLPRODUCT = os.getenv('FLPRODUCT')
FLPRODUCTi = os.getenv('FLPRODUCTi')
INSTALLDIR = os.getenv('INSTALLDIR')
LAST_UPDATED = None
LOGOS_EXE = os.getenv('LOGOS_EXE')
LOGOS_DIR = os.path.dirname(LOGOS_EXE) if LOGOS_EXE is not None else None
LOGOS_EXECUTABLE = os.getenv('LOGOS_EXECUTABLE')
LOGS = os.getenv('LOGS')
SKIP_FONTS = os.getenv('SKIP_FONTS', False)
CHECK_UPDATES = os.getenv('CHECK_UPDATES', False)
SKIP_DEPENDENCIES = os.getenv('SKIP_DEPENDENCIES', False)
TARGETVERSION = os.getenv('TARGETVERSION')
WINE_EXE = os.getenv('WINE_EXE')
SELECTED_APPIMAGE_FILENAME = os.getenv('APPIMAGE_FILENAME')
LLI_LATEST_VERSION = None
LOGOS_LATEST_VERSION_FILENAME = "LogosLinuxInstaller"
LOGOS_LATEST_VERSION_URL = None
RECOMMENDED_WINE64_APPIMAGE_FULL_URL = os.getenv('WINE64_APPIMAGE_FULL_URL')
RECOMMENDED_WINE64_APPIMAGE_FULL_FILENAME = None
RECOMMENDED_WINE64_APPIMAGE_FULL_VERSION = None
RECOMMENDED_WINE64_APPIMAGE_VERSION = None
RECOMMENDED_WINE64_APPIMAGE_BRANCH = None
WINEBIN_CODE = os.getenv('WINEBIN_CODE')
WINECMD_ENCODING = None
WINEPREFIX = os.getenv('WINEPREFIX')
WINESERVER_EXE = os.getenv('WINESERVER_EXE')
WINETRICKSBIN = os.getenv('WINETRICKSBIN')

# Variables that can be set in the environment.
CONFIG_FILE = os.getenv('CONFIG_FILE')
CUSTOMBINPATH = os.getenv('CUSTOMBINPATH')
DEBUG = os.getenv('DEBUG', False)
DELETE_LOG = os.getenv('DELETE_INSTALL_LOG', False)
DIALOG = os.getenv('DIALOG')
LEGACY_CONFIG_FILE = os.path.expanduser("~/.config/Logos_on_Linux/Logos_on_Linux.conf")  # noqa: E501
LOGOS_LOG = os.getenv('LOGOS_LOG', os.path.expanduser("~/.local/state/Logos_on_Linux/Logos_on_Linux.log"))  # noqa: E501
LOGOS_VERSION = os.getenv('LOGOS_VERSION')
LOGOS64_MSI = os.getenv('LOGOS64_MSI')
LOGOS64_URL = os.getenv('LOGOS64_URL')
REINSTALL_DEPENDENCIES = os.getenv('REINSTALL_DEPENDENCIES', False)
SKEL = os.getenv('SKEL')
VERBOSE = os.getenv('VERBOSE', False)
WINEDEBUG = os.getenv('WINEDEBUG', "fixme-all,err-all")
WINEDLLOVERRIDES = os.getenv('WINEDLLOVERRIDES', '')
WINETRICKS_UNATTENDED = os.getenv('WINETRICKS_UNATTENDED')

# Other run-time variables.
ACTION = 'app'
APPIMAGE_LINK_SELECTION_NAME = "selected_wine.AppImage"
APPIMAGE_FILE_PATH = None
DEFAULT_CONFIG_PATH = os.path.expanduser("~/.config/Logos_on_Linux/Logos_on_Linux.json")  # noqa: E501
GUI = None
LOGOS_FORCE_ROOT = False
LOGOS_ICON_FILENAME = None
LOGOS_ICON_URL = None
LOGOS_RELEASE_VERSION = None
LLI_TITLE = "Logos Linux Installer"
LLI_AUTHOR = "Ferion11, John Goodman, T. H. Wright, N. Marti"
LLI_CURRENT_VERSION = "4.0.0-alpha.4"
MYDOWNLOADS = None
PASSIVE = None
PRESENT_WORKING_DIRECTORY = os.getcwd()
LOG_LEVEL = logging.WARNING
LOGOS_BLUE = '#0082FF'
LOGOS_GRAY = '#E7E7E7'
# LOGOS_WHITE = '#F7F7F7'
LOGOS_WHITE = '#FCFCFC'
VERBUM_PATH = None
LOGOS9_WINE64_BOTTLE_TARGZ_NAME = "wine64_bottle.tar.gz"
LOGOS9_WINE64_BOTTLE_TARGZ_URL = f"https://github.com/ferion11/wine64_bottle_dotnet/releases/download/v5.11b/{LOGOS9_WINE64_BOTTLE_TARGZ_NAME}"  # noqa: E501
WINETRICKS_DOWNLOADER = "wget"
WINETRICKS_URL = "https://raw.githubusercontent.com/Winetricks/winetricks/5904ee355e37dff4a3ab37e1573c56cffe6ce223/src/winetricks"  # noqa: E501
WORKDIR = tempfile.mkdtemp(prefix="/tmp/LBS.")
REBOOT_REQUIRED = None

OS_NAME = None
OS_RELEASE = None
PACKAGE_MANAGER_COMMAND_INSTALL = None
PACKAGE_MANAGER_COMMAND_REMOVE = None
PACKAGE_MANAGER_COMMAND_QUERY = None
PACKAGES = None
L9PACKAGES = None
BADPACKAGES = None
SUPERUSER_COMMAND = None

persistent_config_keys = [
    "FLPRODUCT", "FLPRODUCTi", "TARGETVERSION", "INSTALLDIR", "APPDIR",
    "APPDIR_BINDIR", "WINETRICKSBIN", "WINEPREFIX", "WINEBIN_CODE", "WINE_EXE",
    "WINESERVER_EXE", "APPIMAGE_FILENAME", "WINECMD_ENCODING",
    "SELECTED_APPIMAGE_FILENAME", "LOGOS_EXECUTABLE", "LOGOS_EXE", "LOGOS_DIR",
    "LOGS", "BACKUPDIR", "RECOMMENDED_WINE64_APPIMAGE_FULL_URL",
    "RECOMMENDED_WINE64_APPIMAGE_FULL_FILENAME",
    "RECOMMENDED_WINE64_APPIMAGE_FULL_VERSION",
    "RECOMMENDED_WINE64_APPIMAGE_VERSION",
    "RECOMMENDED_WINE64_APPIMAGE_BRANCH", "LAST_UPDATED",
    "LOGOS_LATEST_VERSION", "LOGOS_LATEST_VERSION_FILENAME", "LOGOS_LATEST_VERSION_URL",
]


def get_config_file_dict(config_file_path):
    config_dict = {}
    if config_file_path.endswith('.json'):
        try:
            with open(config_file_path, 'r') as config_file:
                cfg = json.load(config_file)

            for key, value in cfg.items():
                config_dict[key] = value
            return config_dict
        except TypeError as e:
            logging.error("Error opening Config file.")
            logging.error(e)
            return None
        except FileNotFoundError:
            logging.info(f"No config file not found at {config_file_path}")
            return config_dict
        except json.JSONDecodeError as e:
            logging.error("Config file could not be read.")
            logging.error(e)
            return None
    elif config_file_path.endswith('.conf'):
        # Legacy config from bash script.
        logging.info("Reading from legacy config file.")
        with open(config_file_path, 'r') as config_file:
            for line in config_file:
                line = line.strip()
                if len(line) == 0:  # skip blank lines
                    continue
                if line[0] == '#':  # skip commented lines
                    continue
                parts = line.split('=')
                if len(parts) == 2:
                    value = parts[1].strip('"').strip("'")  # remove quotes
                    vparts = value.split('#')  # get rid of potential comment
                    if len(vparts) > 1:
                        value = vparts[0].strip().strip('"').strip("'")
                    config_dict[parts[0]] = value
        return config_dict


def set_config_env(config_file_path):
    config_dict = get_config_file_dict(config_file_path)
    if config_dict is None:
        return
        # msg.logos_error(f"Error: Unable to get config at {config_file_path}")
    logging.info(f"Setting {len(config_dict)} variables from config file.")
    for key, value in config_dict.items():
        globals()[key] = value


def get_env_config():
    for var in globals().keys():
        val = os.getenv(var)
        if val is not None:
            logging.info(f"Setting '{var}' to '{val}'")
            globals()[var] = val
