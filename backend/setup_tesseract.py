"""
Automatic User-Space Tesseract Provisioner for Linux / Render / PaaS environments.
Locates system Tesseract or auto-provisions a standalone statically linked Tesseract
binary and English neural model into backend/bin/ without requiring root/sudo privileges.
"""

import os
import sys
import shutil
import platform
import logging
import stat
from typing import Optional

logger = logging.getLogger("pdf-to-mindmap-backend")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
BIN_DIR = os.path.join(BACKEND_DIR, "bin")
TESSDATA_DIR = os.path.join(BIN_DIR, "tessdata")
LOCAL_TESS_BIN = os.path.join(BIN_DIR, "tesseract")
LOCAL_TRAINED_DATA = os.path.join(TESSDATA_DIR, "eng.traineddata")

# Statically linked Musl-based Tesseract binaries (Zero external shared library dependencies)
TESSERACT_STATIC_URLS = {
    "x86_64": "https://github.com/DanielMYT/tesseract-static/releases/download/tesseract-5.5.3-rebuild/tesseract.x86_64",
    "amd64": "https://github.com/DanielMYT/tesseract-static/releases/download/tesseract-5.5.3-rebuild/tesseract.x86_64",
    "aarch64": "https://github.com/DanielMYT/tesseract-static/releases/download/tesseract-5.5.3-rebuild/tesseract.aarch64",
    "arm64": "https://github.com/DanielMYT/tesseract-static/releases/download/tesseract-5.5.3-rebuild/tesseract.aarch64",
}
ENG_TRAINEDDATA_URL = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/eng.traineddata"


def _download_file(url: str, dest_path: str, min_size: int = 1000) -> bool:
    """
    Downloads a remote file with streaming, saving to a temporary file before atomic rename.
    """
    import httpx

    temp_path = f"{dest_path}.tmp"
    try:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        logger.info(f"[setup_tesseract] Downloading {url} -> {dest_path}...")
        with httpx.Client(follow_redirects=True, timeout=60.0) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                with open(temp_path, "wb") as f:
                    for chunk in response.iter_bytes(chunk_size=65536):
                        f.write(chunk)

        if os.path.exists(temp_path) and os.path.getsize(temp_path) >= min_size:
            os.replace(temp_path, dest_path)
            logger.info(f"[setup_tesseract] Successfully downloaded to {dest_path} ({os.path.getsize(dest_path)} bytes).")
            return True
        else:
            logger.error(f"[setup_tesseract] Downloaded file {temp_path} is smaller than expected.")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return False
    except Exception as e:
        logger.error(f"[setup_tesseract] Failed to download {url}: {e}")
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        return False


def _ensure_executable(path: str) -> None:
    """Ensures user, group, and other execute permissions are set on the binary."""
    try:
        st = os.stat(path)
        os.chmod(path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH | stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    except Exception as e:
        logger.warning(f"[setup_tesseract] Failed to set executable mode on {path}: {e}")


def get_tesseract_cmd() -> Optional[str]:
    """
    Locates the Tesseract binary across environment variables, local project bin/,
    PATH, and standard system paths. Configures pytesseract.tesseract_cmd when found.
    """
    import pytesseract

    env_cmd = os.environ.get("TESSERACT_CMD")
    if env_cmd and os.path.exists(env_cmd) and os.access(env_cmd, os.X_OK):
        pytesseract.pytesseract.tesseract_cmd = env_cmd
        return env_cmd

    # Check local standalone binary inside backend/bin/
    if os.path.exists(LOCAL_TESS_BIN) and os.access(LOCAL_TESS_BIN, os.X_OK):
        pytesseract.pytesseract.tesseract_cmd = LOCAL_TESS_BIN
        return LOCAL_TESS_BIN

    path_cmd = shutil.which("tesseract")
    if path_cmd:
        pytesseract.pytesseract.tesseract_cmd = path_cmd
        return path_cmd

    candidates = [
        "/usr/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/opt/homebrew/bin/tesseract",
        "/usr/bin/tesseract-ocr",
        "/app/bin/tesseract",
        os.path.expanduser("~/.local/bin/tesseract"),
        "/var/lib/tesseract/bin/tesseract",
    ]
    for c in candidates:
        if os.path.exists(c) and os.access(c, os.X_OK):
            pytesseract.pytesseract.tesseract_cmd = c
            return c

    return None


def configure_tessdata_prefix() -> Optional[str]:
    """
    Discovers or configures TESSDATA_PREFIX for language model resolution.
    """
    if os.path.exists(LOCAL_TRAINED_DATA):
        os.environ["TESSDATA_PREFIX"] = TESSDATA_DIR
        return TESSDATA_DIR

    if "TESSDATA_PREFIX" in os.environ and os.path.isdir(os.environ["TESSDATA_PREFIX"]):
        return os.environ["TESSDATA_PREFIX"]

    tessdata_candidates = [
        "/usr/share/tesseract-ocr/5/tessdata",
        "/usr/share/tesseract-ocr/4.00/tessdata",
        "/usr/share/tessdata",
        "/usr/local/share/tessdata",
        "/opt/homebrew/share/tessdata",
    ]
    for td in tessdata_candidates:
        if os.path.isdir(td) and os.path.exists(os.path.join(td, "eng.traineddata")):
            os.environ["TESSDATA_PREFIX"] = td
            return td

    return None


def ensure_tesseract_installed() -> bool:
    """
    Ensures Tesseract is available. If missing and running on Linux, auto-provisions
    the standalone static binary and traineddata into backend/bin/.
    """
    tess_cmd = get_tesseract_cmd()
    configure_tessdata_prefix()

    if tess_cmd and ("TESSDATA_PREFIX" in os.environ or os.path.exists(LOCAL_TRAINED_DATA)):
        return True

    # If running on Linux (e.g. Render Web Service), auto-provision standalone binary
    if platform.system().lower() == "linux":
        logger.info("[setup_tesseract] Linux host detected without system Tesseract. Auto-provisioning user-space Tesseract...")
        arch = platform.machine().lower()
        download_url = TESSERACT_STATIC_URLS.get(arch)
        if not download_url:
            logger.error(f"[setup_tesseract] Unsupported architecture '{arch}' for static tesseract.")
            return False

        if not (os.path.exists(LOCAL_TESS_BIN) and os.access(LOCAL_TESS_BIN, os.X_OK)):
            success = _download_file(download_url, LOCAL_TESS_BIN, min_size=5_000_000)
            if success:
                _ensure_executable(LOCAL_TESS_BIN)

        if not os.path.exists(LOCAL_TRAINED_DATA):
            _download_file(ENG_TRAINEDDATA_URL, LOCAL_TRAINED_DATA, min_size=1_000_000)

        tess_cmd = get_tesseract_cmd()
        configure_tessdata_prefix()
        if tess_cmd:
            logger.info(f"[setup_tesseract] Tesseract successfully provisioned at: {tess_cmd}")
            return True
        else:
            logger.error("[setup_tesseract] Provisioning completed but binary could not be verified.")
            return False

    return bool(tess_cmd)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("Executing Tesseract verification / auto-provisioner...")
    ready = ensure_tesseract_installed()
    cmd = get_tesseract_cmd()
    prefix = os.environ.get("TESSDATA_PREFIX", "not set")
    logger.info(f"Result: ready={ready}, cmd={cmd}, TESSDATA_PREFIX={prefix}")
    sys.exit(0 if ready else 1)
