# 🧠 Recolector de Fuentes

**Recolector de Fuentes** es una herramienta escrita en Python que permite **unificar el contenido de todo un proyecto** (código fuente, configuraciones, documentación, etc.) en **un solo archivo de texto plano** (`repositorio.txt` por defecto).

Su propósito principal es generar un documento que pueda **entregarse directamente a ChatGPT u otros modelos de IA**, para que entiendan el contexto completo del proyecto sin necesidad de copiar y pegar archivo por archivo.

---

## 🚀 Características principales

- 🔍 **Escanea recursivamente** todo el proyecto desde una carpeta raíz (`--root`).
- 🧩 **Concatena** archivos de texto en un único `.txt`, separados con fences Markdown según el lenguaje detectado.
- 🚫 **Filtra binarios y ruido** (por extensión, tamaño o heurística de contenido).
- ⚙️ **Altamente configurable** mediante argumentos CLI (`--exclude`, `--include-ext`, `--chunk-bytes`, etc.).
- 🧠 **Detecta automáticamente el lenguaje** de cada archivo para coloreado y segmentación correcta en Markdown.
- 🧾 **Genera un índice global** con:
  - Árbol del proyecto.
  - Mapeo archivo → chunk (si hay más de uno).
  - Lista de archivos omitidos y razones.
- 🪶 **Ordena los archivos por relevancia**, mostrando primero los más importantes (`.py`, `.js`, `README`, `requirements.txt`, etc.).
- 🧱 **Soporta división en múltiples archivos** (*chunks*) si el tamaño total es grande, para evitar límites de tokens.

---

## 🧰 Requisitos

- Python **3.8 o superior**
- No requiere instalación de librerías externas (usa solo módulos estándar).

Verifica tu versión de Python:

```bash
python --version
