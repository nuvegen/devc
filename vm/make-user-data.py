#!/usr/bin/env python3
"""Erzeugt aus vm/cloud-init.yaml die fertige cloud-init-Datei (user-data) für eine devc-VM.

    python3 build.py                                   # erzeugt dist/devc.pyz
    python3 vm/make-user-data.py --ssh-key ~/.ssh/id_ed25519.pub -o user-data

Die Datei enthält install.sh und devc.pyz; die VM braucht beim ersten Start nur Netzzugang für LXD, Caddy und Images.
Nur Standardbibliothek.
"""

import argparse
import base64
import gzip
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOMAIN_RE = re.compile(r"(?=.{1,215}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*")
KEY_RE = re.compile(r"(ssh-(ed25519|rsa)|ecdsa-sha2-nistp(256|384|521)|sk-[a-z0-9@.-]+) [A-Za-z0-9+/=]+( [^\r\n]*)?")


def port(text: str) -> int:
    value = int(text)
    if not 1 <= value <= 65535:
        raise argparse.ArgumentTypeError("Port muss zwischen 1 und 65535 liegen")
    return value


def build(template: str, install_sh: bytes, pyz: bytes, base_domain: str, public_port: int, http_port, pool_size: str, keys: list[str], pool_driver: str = "dir") -> str:
    if not DOMAIN_RE.fullmatch(base_domain):
        raise ValueError(f"Basisdomain ist kein DNS-Name in Kleinbuchstaben: {base_domain}")
    if http_port is not None and not (base_domain == "localhost" or base_domain.endswith(".localhost")):
        raise ValueError("--http-port gibt es nur für Basisdomains unter localhost")
    if pool_driver not in ("dir", "btrfs"):
        raise ValueError(f"--pool-driver muss dir oder btrfs sein: {pool_driver}")
    if pool_size and pool_driver != "btrfs":
        raise ValueError("--pool-size gibt es nur mit --pool-driver btrfs; ein dir-Pool hat keine eigene Größe")
    if pool_size and not re.fullmatch(r"\d+GiB", pool_size):
        raise ValueError(f"--pool-size braucht die Form 30GiB: {pool_size}")
    for key in keys:
        if not KEY_RE.fullmatch(key):
            raise ValueError("SSH-Schlüssel hat nicht die Form einer .pub-Datei (eine Zeile, öffentlicher Schlüssel)")
    values = {
        "SSH_KEYS": json.dumps(keys),  # JSON-Liste ist gültiges YAML
        "INSTALL_SH": base64.b64encode(gzip.compress(install_sh, mtime=0)).decode("ascii"),
        "DEVC_PYZ": base64.b64encode(gzip.compress(pyz, mtime=0)).decode("ascii"),
        "BASE_DOMAIN": base_domain,
        "PUBLIC_PORT": str(public_port),
        "HTTP_PORT": "" if http_port is None else str(http_port),
        "POOL_DRIVER": pool_driver,
        "POOL_SIZE": pool_size,
    }
    text = re.sub(r"@([A-Z_]+)@", lambda match: values[match.group(1)], template)
    # die Erklärung der Vorlage gehört nicht in die fertige Datei
    return "\n".join(line for line in text.split("\n") if not line.startswith("# Das ist eine Vorlage")) 


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="cloud-init-Datei für eine devc-VM erzeugen")
    parser.add_argument("--base-domain", default="localhost", help="Basisdomain, Standard localhost")
    parser.add_argument("--public-port", type=port, default=443, help="Port, den der Browser benutzt, Standard 443")
    parser.add_argument("--http-port", type=port, help="zusätzlich unverschlüsselt auf diesem Port (nur localhost)")
    parser.add_argument("--pool-driver", default="dir", choices=("dir", "btrfs"),
                        help="Speicherpool für die Container: dir (Standard, direkt im Dateisystem der VM) oder btrfs (Pool-Datei fester Größe)")
    parser.add_argument("--pool-size", default="", help="nur mit --pool-driver btrfs: Größe des Pools, z. B. 30GiB; Standard: 70 %% des freien Platzes")
    parser.add_argument("--ssh-key", action="append", default=[], metavar="DATEI", help="öffentlicher SSH-Schlüssel für den Nutzer ubuntu (mehrfach möglich)")
    parser.add_argument("--pyz", default=str(HERE.parent / "dist" / "devc.pyz"), help="devc.pyz, Standard dist/devc.pyz")
    parser.add_argument("-o", "--output", default="-", help="Zieldatei, Standard Standardausgabe")
    args = parser.parse_args(argv)
    try:
        keys = [line.strip() for file in args.ssh_key for line in Path(file).expanduser().read_text(encoding="utf-8").splitlines() if line.strip()]
        text = build((HERE / "cloud-init.yaml").read_text(encoding="utf-8"), (HERE / "install.sh").read_bytes(), Path(args.pyz).read_bytes(),
                     args.base_domain, args.public_port, args.http_port, args.pool_size, keys, args.pool_driver)
    except (OSError, ValueError) as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 2
    if args.output == "-":
        sys.stdout.write(text)
    else:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"{args.output} geschrieben ({len(text) // 1024} KiB)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
