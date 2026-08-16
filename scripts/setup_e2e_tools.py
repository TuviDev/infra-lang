#!/usr/bin/env python3
"""Download the live-E2E tools (kind, kubectl, kubeconform) for the current OS.

This is an optional convenience helper. It installs the tools into a local
``.e2e-tools/bin`` directory (no system-wide changes) and prints how to add
them to PATH so ``pytest -m live_e2e`` can find them.

Requires: curl and (on Windows) PowerShell available on the system.
Docker must be installed separately (e.g. Docker Desktop).

Usage:
    python scripts/setup_e2e_tools.py [--dir .e2e-tools/bin]
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
import urllib.request
from pathlib import Path

# Latest known-good versions (pin to keep the helper deterministic).
KIND_VERSION = "v0.20.0"
KUBECONFORM_VERSION = "v0.6.7"


def _download(url: str, dest: Path, *, mode: str = "wb") -> None:
    print(f"  downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as resp, open(dest, mode) as fh:
        fh.write(resp.read())
    print(f"  -> {dest}")


def _ensure_exec(path: Path) -> None:
    path.chmod(path.stat().st_mode | 0o111)


def main() -> int:
    ap = argparse.ArgumentParser(description="Download live-E2E tools locally")
    ap.add_argument(
        "--dir", default=".e2e-tools/bin", help="destination directory"
    )
    args = ap.parse_args()

    dest = Path(args.dir)
    dest.mkdir(parents=True, exist_ok=True)
    os = platform.system().lower()
    arch = platform.machine().lower()
    if arch in ("x86_64", "amd64"):
        arch = "amd64"
    elif arch in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        print(f"unsupported arch: {arch}")
        return 2

    kind = dest / ("kind.exe" if os == "windows" else "kind")
    kubectl = dest / ("kubectl.exe" if os == "windows" else "kubectl")
    kubeconform = dest / ("kubeconform.exe" if os == "windows" else "kubeconform")

    if os == "darwin":
        kind_url = f"https://kind.sigs.k8s.io/dl/{KIND_VERSION}/kind-darwin-{arch}"
        kc_url = (
            f"https://github.com/yannh/kubeconform/releases/download/"
            f"{KUBECONFORM_VERSION}/kubeconform-darwin-{arch}.tar.gz"
        )
    elif os in ("linux", "windows"):
        plat = "windows" if os == "windows" else "linux"
        kind_url = f"https://kind.sigs.k8s.io/dl/{KIND_VERSION}/kind-{plat}-{arch}"
        kc_url = (
            f"https://github.com/yannh/kubeconform/releases/download/"
            f"{KUBECONFORM_VERSION}/kubeconform-{plat}-{arch}.tar.gz"
        )
    else:
        print(f"unsupported OS: {os}")
        return 2

    kubectl_url = (
        "https://dl.k8s.io/release/"
        + subprocess.run(
            ["curl", "-L", "-s", "https://dl.k8s.io/release/stable.txt"],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        + f"/bin/{os}/amd64/kubectl"
    )

    print("[1/3] kind")
    _download(kind_url, kind)
    _ensure_exec(kind)

    print("[2/3] kubectl")
    _download(kubectl_url, kubectl)
    _ensure_exec(kubectl)

    print("[3/3] kubeconform (tar.gz, extracted next to binary)")
    kc_tgz = dest / "kubeconform.tar.gz"
    _download(kc_url, kc_tgz)
    if os == "windows":
        subprocess.run(["tar", "-xf", str(kc_tgz), "-C", str(dest)], check=True)
        subprocess.run(
            ["move", str(dest / "kubeconform.exe"), str(kubeconform)], check=False
        )
    else:
        subprocess.run(["tar", "-xzf", str(kc_tgz), "-C", str(dest)], check=True)
    kc_tgz.unlink(missing_ok=True)
    _ensure_exec(kubeconform)

    print("\nDone. Add to PATH and run live E2E:")
    if os == "windows":
        print(f"  $env:PATH = '{dest.resolve()};' + $env:PATH")
    else:
        print(f"  export PATH=\"{dest.resolve()}:$PATH\"")
    print("  pytest tests -m live_e2e -q")
    return 0


if __name__ == "__main__":
    sys.exit(main())
