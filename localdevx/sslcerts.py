"""Local certificate authority + per-site certificates, using XAMPP's bundled openssl.exe."""
import os

from .apache import run
from .model import APP_DIR, fwd

CA_DIR = os.path.join(APP_DIR, "ca")
CA_KEY = os.path.join(CA_DIR, "LocalDevX-RootCA.key")
CA_CRT = os.path.join(CA_DIR, "LocalDevX-RootCA.crt")
CA_NAME = "LocalDevX Local CA"


def _openssl(settings, args, cwd=None):
    env = dict(os.environ, OPENSSL_CONF=settings.openssl_cnf)
    import subprocess
    p = subprocess.run([settings.openssl_exe, *args], capture_output=True, text=True,
                       cwd=cwd, env=env, creationflags=0x08000000, errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def ensure_ca(settings):
    if os.path.isfile(CA_KEY) and os.path.isfile(CA_CRT):
        return True, "CA exists."
    os.makedirs(CA_DIR, exist_ok=True)
    code, out = _openssl(settings, [
        "req", "-x509", "-new", "-nodes", "-newkey", "rsa:2048", "-sha256", "-days", "3650",
        "-keyout", CA_KEY, "-out", CA_CRT,
        "-subj", f"/CN={CA_NAME}/O=LocalDevX",
        "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign",
    ])
    return code == 0, out


def ca_trusted():
    if not os.path.isfile(CA_CRT):
        return False
    code, out = run(["certutil", "-store", "Root", CA_NAME], timeout=20)
    return code == 0 and CA_NAME in out


def trust_ca():
    """Add the local CA to the Windows Trusted Root store (Chrome/Edge use it)."""
    code, out = run(["certutil", "-addstore", "-f", "Root", CA_CRT], timeout=30)
    return code == 0, out


def untrust_ca():
    code, out = run(["certutil", "-delstore", "Root", CA_NAME], timeout=30)
    return code == 0, out


def issue_cert(settings, site, extra_names=()):
    """Create key + certificate for a site, signed by the local CA."""
    ok, out = ensure_ca(settings)
    if not ok:
        return False, "Could not create local CA:\n" + out
    crt, key = fwd(site.cert_file), fwd(site.key_file)
    os.makedirs(os.path.dirname(crt), exist_ok=True)
    os.makedirs(os.path.dirname(key), exist_ok=True)
    names = []
    for n in [site.name, *site.aliases, *extra_names]:
        if n and n not in names:
            names.append(n)
    base = os.path.splitext(crt)[0]
    csr, ext = base + ".csr", base + ".ext"
    with open(ext, "w", encoding="ascii") as f:
        f.write("authorityKeyIdentifier=keyid,issuer\nbasicConstraints=CA:FALSE\n"
                "keyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n"
                "subjectAltName=@alt_names\n\n[alt_names]\n")
        for i, n in enumerate(names, 1):
            f.write(f"DNS.{i} = {n}\n")
    code, out = _openssl(settings, ["req", "-new", "-nodes", "-newkey", "rsa:2048",
                                    "-keyout", key, "-out", csr, "-subj", f"/CN={site.name}"])
    if code != 0:
        return False, out
    code, out2 = _openssl(settings, ["x509", "-req", "-in", csr, "-CA", CA_CRT, "-CAkey", CA_KEY,
                                     "-CAcreateserial", "-out", crt, "-days", "825", "-sha256",
                                     "-extfile", ext])
    if code != 0:
        return False, out2
    return True, f"Certificate issued for {', '.join(names)}"


def cert_info(settings, path):
    if not path or not os.path.isfile(path):
        return "No certificate file yet - one will be generated on Apply."
    code, out = _openssl(settings, ["x509", "-in", path, "-noout", "-issuer", "-enddate", "-ext", "subjectAltName"])
    return out.strip() if code == 0 else "Could not read certificate."
