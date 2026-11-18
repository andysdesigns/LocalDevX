"""Maintain a managed block in the Windows hosts file."""
from . import APP_TITLE, legacy

BEGIN = "# >>> LocalDevX BEGIN (managed - do not edit inside this block)"
END = "# <<< LocalDevX END"
BEGINS = (BEGIN, legacy.OLD_HOSTS_BEGIN)  # a block written before the rename is replaced too
ENDS = (END, legacy.OLD_HOSTS_END)


def hostnames_for(sites):
    names = []
    for s in sites:
        if not s.enabled:
            continue
        for h in [s.name, *s.aliases]:
            if h and "*" not in h and h not in names:
                names.append(h)
    return names


def build_hosts(text, sites, unmanage=()):
    """Return new hosts-file text.

    - Replaces (or appends) the managed block with entries for every enabled site.
    - Removes loose 127.0.0.1 / ::1 lines outside the block whose host names are all
      managed by LocalDevX (or listed in `unmanage`, e.g. deleted sites), so stale or
      duplicate entries don't pile up. Lines that mention any other host are left alone.
    """
    names = hostnames_for(sites)
    owned = set(names) | set(unmanage) | {f"www.{n}" for n in unmanage}
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()

    kept, inside = [], False
    for line in lines:
        st = line.strip()
        if st in BEGINS:
            inside = True
            continue
        if inside:
            if st in ENDS:
                inside = False
            continue
        body = st.split("#", 1)[0].split()
        if len(body) >= 2 and body[0] in ("127.0.0.1", "::1") and all(h.lower() in owned for h in body[1:]):
            continue
        kept.append(line)

    while kept and not kept[-1].strip():
        kept.pop()
    block = [BEGIN, f"# written by {APP_TITLE}"] + [f"127.0.0.1\t{h}" for h in names] + [END]
    return nl.join(kept + ["", *block]) + nl


def read_hosts(path):
    with open(path, "rb") as f:
        raw = f.read()
    return raw.decode("utf-8-sig", errors="replace")


def write_hosts(path, text):
    # Windows' resolver reads ANSI/UTF-8 without BOM.
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
