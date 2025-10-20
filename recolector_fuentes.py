#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ejecutar CMD
python recolector_fuentes.py -r . -o repositorio.txt --exclude "tests/screenshots,**/*.snap" --include-ext ".md" --header-line

ejecutar PowerShell:
python .\recolector_fuentes.py -r . -o repositorio.txt --header-line


Recolector de fuentes para empaquetar el contexto de un proyecto
en uno o varios documentos de texto listos para pegar en ChatGPT.

Características:
- Recorre el workspace y concatena archivos de texto en bloques con fences Markdown por lenguaje.
- Filtra binarios por extensión y heurística.
- Excluye directorios y nombres ruidosos/sensibles por defecto (desactivable).
- Límite máximo por archivo (--max-bytes).
- Particiona la salida en chunks (--chunk-bytes) para ajustarse a límites de prompt.
- Índice global (árbol, lista de rutas y mapeo archivo→chunk) en el primer chunk.
- Ordena por relevancia para que los archivos más útiles aparezcan primero.
- Opciones finas de include/exclude por patrón y extensión.
"""

from __future__ import annotations
import argparse
import os
import sys
import time
from pathlib import Path
from typing import Iterable, List, Dict, Tuple, Optional, Set
import fnmatch
from datetime import datetime

# =========================
# Configuración por defecto
# =========================

DEFAULT_IGNORED_DIRS = {
    ".git", ".svn", ".hg", ".idea", ".vscode",
    "node_modules", "dist", "build", "out", "target",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".tox",
    ".venv", "venv", ".next", ".turbo", ".parcel-cache",
    "coverage", ".gradle", ".DS_Store"
}

DEFAULT_EXCLUDED_NAMES = {
    ".env", ".env.local", ".env.production", ".env.development",
}
DEFAULT_EXCLUDED_EXTS = {".log", ".lock"}

BINARY_EXTS = {
    # Imágenes y medios
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".ico",
    ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".mp4", ".mkv", ".avi", ".mov", ".webm",
    ".pdf", ".zip", ".rar", ".7z", ".tar", ".gz", ".xz", ".bz2",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".psd", ".ai", ".sketch", ".fig",
    # Otros binarios comunes
    ".exe", ".dll", ".so", ".dylib", ".bin", ".o", ".a", ".class", ".jar",
    ".wasm",
    # Snapshots y dumps
    ".snap", ".dump"
}

# Heurística: bytes de control permitidos en texto
_TEXT_WHITELIST = {9, 10, 13}  # tab, \n, \r

# Mapeo de extensión → lenguaje de fence
LANG_BY_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".jsx": "jsx",
    ".json": "json",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
    ".ini": "ini",
    ".conf": "ini",
    ".env": "",
    ".md": "markdown",
    ".rst": "rst",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".sass": "sass",
    ".less": "less",
    ".sql": "sql",
    ".sh": "bash",
    ".bat": "bat",
    ".ps1": "powershell",
    ".dockerfile": "dockerfile",
    "dockerfile": "dockerfile",
    ".gradle": "groovy",
    ".groovy": "groovy",
    ".kt": "kotlin",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".xml": "xml",
    ".vue": "vue",
}

RELEVANCE_DIR_HINTS = (
    "src/", "app/", "apps/", "backend/", "server/", "api/",
    "frontend/", "client/", "lib/", "core/", "services/", "packages/"
)
RELEVANCE_TOP_FILES = (
    "requirements.txt", "pyproject.toml", "poetry.lock",
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    ".env.example", ".tool-versions",
    "Dockerfile", "docker-compose.yml", "compose.yml", ".dockerignore",
    "Makefile", "README.md", "README.MD", "readme.md"
)
RELEVANCE_EXT_PRIORITY = (".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".yml", ".yaml", ".md", ".html", ".css")

# =========================
# Utilidades
# =========================

def human_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    size = float(n)
    for u in units:
        if size < 1024.0 or u == units[-1]:
            return f"{size:.1f} {u}"
        size /= 1024.0
    return f"{n} B"

def is_probably_binary(data: bytes) -> bool:
    if not data:
        return False
    if b"\x00" in data:
        return True
    ctrl = sum(1 for b in data if b < 32 and b not in _TEXT_WHITELIST)
    return (ctrl / len(data)) > 0.30

def get_lang_for_path(path: Path) -> str:
    name = path.name.lower()
    if name == "dockerfile" or name.endswith(".dockerfile"):
        return "dockerfile"
    ext = path.suffix.lower()
    return LANG_BY_EXT.get(ext, "")

def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def print_progress(prefix: str, current: int, total: int, width: int = 30) -> None:
    total = max(total, 1)
    filled = int(width * current / total)
    bar = "█" * filled + "─" * (width - filled)
    end = "\n" if current >= total else "\r"
    sys.stdout.write(f"{prefix} [{bar}] {current}/{total}")
    sys.stdout.write(end)
    sys.stdout.flush()

def build_tree(paths: List[Path], root: Path) -> str:
    sep = os.sep
    root_parts = len(root.parts)
    nodes: Dict[str, Set[str]] = {}

    for p in paths:
        parts = p.parts[root_parts:]
        for i in range(len(parts)):
            parent = sep.join(parts[:i])
            child = parts[i]
            nodes.setdefault(parent, set()).add(child)

    def walk(prefix: str = "", parent_key: str = "") -> List[str]:
        entries = sorted(nodes.get(parent_key, []), key=lambda s: (not s.endswith("/"), s.lower()))
        lines: List[str] = []
        for idx, entry in enumerate(entries):
            is_last = idx == len(entries) - 1
            connector = "└── " if is_last else "├── "
            path_key = f"{parent_key}{sep}{entry}" if parent_key else entry
            has_children = any(k.startswith(path_key + sep) for k in nodes.keys())
            lines.append(prefix + connector + entry)
            if has_children:
                extension = "    " if is_last else "│   "
                lines.extend(walk(prefix + extension, path_key))
        return lines

    return "\n".join(walk())

def match_any_patterns(rel: str, patterns: List[str]) -> bool:
    return any(fnmatch.fnmatch(rel, pat.strip()) for pat in patterns if pat.strip())

def normalize_exts(csv: Optional[str]) -> Set[str]:
    if not csv:
        return set()
    exts = set()
    for e in csv.split(","):
        s = e.strip()
        if not s:
            continue
        if not s.startswith("."):
            s = "." + s
        exts.add(s.lower())
    return exts

def split_csv(s: Optional[str]) -> List[str]:
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x.strip()]

# =========================
# Proceso principal
# =========================

class FileRecord:
    def __init__(self, rel: Path, lang: str, content: str, block_size: int):
        self.rel = rel
        self.lang = lang
        self.content = content
        self.block_size = block_size
        self.chunk_no: int = 1  # se asigna luego

def collect_candidates(
    root: Path,
    ignored_dirs: Set[str],
    exclude_patterns: List[str],
    include_ext: Set[str],
    exclude_ext: Set[str],
    use_default_excludes: bool,
    max_bytes: int,
) -> Tuple[List[Path], Dict[str, List[str]]]:
    included: List[Path] = []
    omitted: Dict[str, List[str]] = {
        "excluded_dir": [],
        "excluded_pattern": [],
        "excluded_name_or_ext": [],
        "binary_ext": [],
        "binary_heuristic": [],
        "too_large": [],
        "read_error": [],
    }

    default_names = DEFAULT_EXCLUDED_NAMES if use_default_excludes else set()
    default_exts = DEFAULT_EXCLUDED_EXTS if use_default_excludes else set()

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ignored_dirs]

        for name in filenames:
            abs_path = Path(dirpath) / name
            rel = abs_path.relative_to(root)
            rel_str = rel.as_posix()

            if exclude_patterns and match_any_patterns(rel_str, exclude_patterns):
                omitted["excluded_pattern"].append(rel_str)
                continue

            if use_default_excludes and (name in default_names or abs_path.suffix.lower() in default_exts):
                omitted["excluded_name_or_ext"].append(rel_str)
                continue

            ext = abs_path.suffix.lower()
            if exclude_ext and ext in exclude_ext:
                omitted["excluded_name_or_ext"].append(rel_str)
                continue

            if (ext in BINARY_EXTS) and (ext not in include_ext):
                omitted["binary_ext"].append(rel_str)
                continue

            try:
                size = abs_path.stat().st_size
            except Exception:
                omitted["read_error"].append(rel_str)
                continue
            if max_bytes > 0 and size > max_bytes:
                omitted["too_large"].append(f"{rel_str} ({human_bytes(size)})")
                continue

            try:
                with open(abs_path, "rb") as fh:
                    head = fh.read(8192)
                if is_probably_binary(head) and (ext not in include_ext):
                    omitted["binary_heuristic"].append(rel_str)
                    continue
            except Exception:
                omitted["read_error"].append(rel_str)
                continue

            included.append(rel)

    return included, omitted

def read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="strict")
    except Exception:
        return path.read_text(encoding="latin-1", errors="replace")

def relevance_key(rel: Path) -> Tuple[int, int, int, str]:
    rel_str = rel.as_posix().lower()
    top = 0 if any(rel.name == t for t in RELEVANCE_TOP_FILES) else 1
    dir_hint = 0 if any(rel_str.startswith(h) for h in RELEVANCE_DIR_HINTS) else 1
    ext_rank = next((i for i, e in enumerate(RELEVANCE_EXT_PRIORITY) if rel.suffix.lower() == e), 999)
    return (top, dir_hint, ext_rank, rel_str)

def build_blocks(
    root: Path,
    rel_paths: List[Path],
    header_line: str,
    max_bytes: int,
) -> Tuple[List[FileRecord], Dict[str, List[str]]]:
    extra_omitted: Dict[str, List[str]] = {
        "read_error": [],
        "too_large": [],
    }
    records: List[FileRecord] = []
    total = len(rel_paths)
    for i, rel in enumerate(rel_paths, 1):
        print_progress("Leyendo archivos", i, total)
        abs_path = root / rel
        try:
            if max_bytes > 0 and abs_path.stat().st_size > max_bytes:
                extra_omitted["too_large"].append(rel.as_posix())
                continue
            lang = get_lang_for_path(rel)
            content = read_text_safe(abs_path)
            block = f"{rel.as_posix()}\n{header_line}\n```{lang}\n{content}\n```\n\n"
            records.append(FileRecord(rel, lang, content, len(block)))
        except Exception:
            extra_omitted["read_error"].append(rel.as_posix())

    return records, extra_omitted

def assign_chunks(records: List[FileRecord], chunk_bytes: int) -> int:
    if chunk_bytes <= 0:
        for r in records:
            r.chunk_no = 1
        return 1

    current = 1
    acc = 0
    for r in records:
        if r.block_size > chunk_bytes and acc == 0:
            r.chunk_no = current
            current += 1
            acc = 0
            continue

        if acc + r.block_size > chunk_bytes:
            current += 1
            acc = 0
        r.chunk_no = current
        acc += r.block_size
    return current

def write_outputs(
    root: Path,
    output: Path,
    header_line: str,
    records: List[FileRecord],
    total_chunks: int,
    included_paths: List[Path],
    omitted: Dict[str, List[str]],
) -> None:
    def out_path_for(i: int) -> Path:
        if total_chunks == 1:
            return output
        return output.with_name(f"{output.stem}_{i:03d}{output.suffix}")

    tree_text = build_tree(included_paths, root) if included_paths else "(sin archivos incluidos)"
    file_to_chunk: List[Tuple[int, str]] = sorted(
        [(r.chunk_no, r.rel.as_posix()) for r in records],
        key=lambda t: (t[0], t[1].lower())
    )

    for chunk_no in range(1, total_chunks + 1):
        outp = out_path_for(chunk_no)
        outp.parent.mkdir(parents=True, exist_ok=True)
        with open(outp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# Recolector de fuentes\n\n")
            fh.write(f"- Fecha de generación: {now_iso()}\n")
            fh.write(f"- Raíz: `{root.resolve().as_posix()}`\n")
            fh.write(f"- Archivo de salida: `{outp.name}` (chunk {chunk_no}/{total_chunks})\n")
            fh.write("\n")

            if chunk_no == 1:
                fh.write("## Árbol de archivos incluidos\n\n")
                fh.write("```text\n")
                fh.write(tree_text)
                fh.write("\n```\n\n")

                fh.write("## Índice global (archivo → chunk)\n\n")
                for cn, p in file_to_chunk:
                    fh.write(f"- [{cn:03d}] {p}\n")
                fh.write("\n")

                fh.write("## Archivos omitidos (razones)\n\n")
                any_omitted = any(omitted.get(k) for k in omitted.keys())
                if not any_omitted:
                    fh.write("_No se omitieron archivos._\n\n")
                else:
                    for reason, items in omitted.items():
                        if not items:
                            continue
                        fh.write(f"### {reason}\n")
                        for it in items:
                            fh.write(f"- {it}\n")
                        fh.write("\n")
                fh.write("---\n\n")

            chunk_records = [r for r in records if r.chunk_no == chunk_no]
            total = len(chunk_records)
            for i, r in enumerate(chunk_records, 1):
                print_progress(f"Escribiendo chunk {chunk_no}/{total_chunks}", i, total)
                block = f"{r.rel.as_posix()}\n{header_line}\n```{r.lang}\n{r.content}\n```\n\n"
                fh.write(block)

        print(f"\n✅ Escrito: {outp}")

# =========================
# CLI
# =========================

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Recolector de fuentes: concatena archivos de texto del proyecto en uno o varios .txt con fences por lenguaje."
    )
    p.add_argument("-r", "--root", default=".", help="Directorio raíz del proyecto (por defecto, .)")
    p.add_argument("-o", "--output", default="repositorio.txt", help="Ruta de salida (por defecto, repositorio.txt)")
    p.add_argument("--exclude", default="", help="Patrones glob separados por coma para excluir (ej: 'tests/screenshots,**/*.snap')")
    p.add_argument("--include-ext", default="", help="Extensiones a FORZAR inclusión (csv, con . o sin .; ej: '.py,.md')")
    p.add_argument("--exclude-ext", default="", help="Extensiones a excluir explícitamente (csv)")
    p.add_argument("--ignored-dirs", default="", help="Directorios adicionales a ignorar (csv)")
    p.add_argument("--no-default-excludes", action="store_true", help="No aplicar exclusiones por defecto (.env, *.log, etc.)")
    p.add_argument("--max-bytes", type=int, default=2 * 1024 * 1024, help="Tamaño máximo por archivo (bytes). 0 para ilimitado. Por defecto 2MB")
    p.add_argument("--chunk-bytes", type=int, default=0, help="Tamaño máximo por archivo de salida (bytes). 0 para un solo fichero.")
    # ✔️ robusto: acepta --header-line SIN valor (usa '-----'), o con valor
    p.add_argument("--header-line", "--header", "-H",
                   nargs="?", const="-----", default="-----",
                   help="Separador entre ruta y contenido. Si se pasa sin valor, usa '-----' (por defecto).")
    return p.parse_args()

def main() -> None:
    args = parse_args()
    t0 = time.time()

    root = Path(args.root).resolve()
    output = Path(args.output)

    if not root.exists() or not root.is_dir():
        print(f"Error: raíz inválida: {root}", file=sys.stderr)
        sys.exit(1)

    ignored_dirs = set(DEFAULT_IGNORED_DIRS)
    if args.ignored_dirs:
        ignored_dirs |= set([d.strip() for d in args.ignored_dirs.split(",") if d.strip()])

    exclude_patterns = split_csv(args.exclude)
    include_ext = normalize_exts(args.include_ext)
    exclude_ext = normalize_exts(args.exclude_ext)
    use_default_excludes = not args.no_default_excludes

    print("🔎 Escaneando workspace...")
    included_paths, omitted = collect_candidates(
        root=root,
        ignored_dirs=ignored_dirs,
        exclude_patterns=exclude_patterns,
        include_ext=include_ext,
        exclude_ext=exclude_ext,
        use_default_excludes=use_default_excludes,
        max_bytes=args.max_bytes,
    )

    included_paths.sort(key=relevance_key)

    records, extra_omitted = build_blocks(
        root=root,
        rel_paths=included_paths,
        header_line=args.header_line,
        max_bytes=args.max_bytes,
    )
    for k, v in extra_omitted.items():
        omitted.setdefault(k, []).extend(v)

    total_chunks = assign_chunks(records, args.chunk_bytes)

    write_outputs(
        root=root,
        output=output,
        header_line=args.header_line,
        records=records,
        total_chunks=total_chunks,
        included_paths=included_paths,
        omitted=omitted,
    )

    dt = time.time() - t0
    total_files = len(records)
    total_size = sum(r.block_size for r in records)
    print("\nResumen:")
    print(f"- Archivos incluidos: {total_files}")
    print(f"- Tamaño total (bloques): {human_bytes(total_size)}")
    print(f"- Chunks generados: {total_chunks}")
    print(f"- Tiempo: {dt:.2f} s")

if __name__ == "__main__":
    main()
