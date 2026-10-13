"""Рендеринг HTML-шаблонов (Jinja2) и конвертация HTML → PDF (WeasyPrint)."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, select_autoescape

load_dotenv()

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
REPORTS_DIR = BASE_DIR / "reports"
TEMPLATES = {
    "report": "report_template.html",
    "booklet": "booklet_template.html",
}
COMPANY = {
    "name": os.getenv("COMPANY_NAME", "Солнечный маршрут"),
    "phone": os.getenv("COMPANY_PHONE", "+7 800 000-00-00"),
    "email": os.getenv("COMPANY_EMAIL", "info@sunroute.ru"),
    "site": os.getenv("COMPANY_SITE", "sunroute.ru"),
}

_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
})

_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _or_dash(value: Any) -> Any:
    return "—" if value in (None, "", [], {}) else value


_env.filters["or_dash"] = _or_dash


def slugify(value: str | None, default: str = "client") -> str:
    """Преобразует имя клиента в безопасное имя папки (с транслитерацией)."""
    if not value:
        return default
    text = value.lower().translate(_TRANSLIT)
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or default


def get_client_dir(report_data: dict[str, Any], reports_dir: Path = REPORTS_DIR) -> Path:
    """Возвращает (и создаёт) подпапку клиента внутри reports/."""
    name = (report_data.get("client") or {}).get("name")
    client_dir = Path(reports_dir) / slugify(name)
    client_dir.mkdir(parents=True, exist_ok=True)
    return client_dir


def render_html(doc_type: str, **context: Any) -> str:
    """Подставляет данные в HTML-шаблон документа: doc_type = "report" | "booklet"."""
    if doc_type not in TEMPLATES:
        raise ValueError(f"Неизвестный тип документа: {doc_type}")
    template = _env.get_template(TEMPLATES[doc_type])
    logger.debug("Рендеринг шаблона %s", TEMPLATES[doc_type])
    return template.render(
        company=COMPANY,
        generated_at=datetime.now().strftime("%d.%m.%Y %H:%M"),
        **context,
    )


_GLIB_LOG_DOMAINS = (b"GLib", b"GLib-GIO", b"GLib-GObject")
_GLIB_WARNING_LEVELS = (1 << 3) | (1 << 4) | (1 << 5) | (1 << 6)  # CRITICAL | WARNING | MESSAGE | INFO
_glib_log_callback = None


def _redirect_glib_logs() -> None:
    """Перенаправляет предупреждения GLib (шум GTK про UWP-приложения Windows) в лог-файл."""
    global _glib_log_callback
    if _glib_log_callback is not None:
        return

    import ctypes
    import ctypes.util

    lib_path = ctypes.util.find_library("libglib-2.0-0") or ctypes.util.find_library("glib-2.0")
    if not lib_path:
        return
    try:
        glib = ctypes.CDLL(lib_path)
        log_func_type = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p)

        def _handler(domain, _level, message, _user_data):
            logger.debug("[%s] %s", (domain or b"").decode(errors="replace"),
                         (message or b"").decode(errors="replace"))

        _glib_log_callback = log_func_type(_handler)
        glib.g_log_set_handler.argtypes = [ctypes.c_char_p, ctypes.c_int, log_func_type, ctypes.c_void_p]
        for domain in _GLIB_LOG_DOMAINS:
            glib.g_log_set_handler(domain, _GLIB_WARNING_LEVELS, _glib_log_callback, None)
    except (OSError, AttributeError):
        logger.debug("Не удалось перехватить логи GLib", exc_info=True)


def html_to_pdf(html: str, output_path: Path) -> Path:
    """Конвертирует HTML-строку в PDF-файл."""
    try:
        from weasyprint import HTML
    except OSError as exc:
        logger.exception("WeasyPrint не смог загрузить системные библиотеки")
        raise RuntimeError(
            "WeasyPrint не нашёл библиотеки Pango/GTK. На Windows установите MSYS2 и "
            "пакет mingw-w64-x86_64-pango (см. README.md)."
        ) from exc
    _redirect_glib_logs()

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    logger.debug("Конвертация HTML в PDF: %s", output_path)
    HTML(string=html, base_url=str(TEMPLATES_DIR)).write_pdf(output_path)
    logger.info("PDF сохранён: %s", output_path)
    return output_path


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def save_json(data: dict[str, Any], output_path: Path) -> Path:
    output_path = Path(output_path)
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.debug("JSON сохранён: %s", output_path)
    return output_path


def generate_report_pdf(report_data: dict[str, Any], reports_dir: Path = REPORTS_DIR) -> Path:
    """Формирует PDF-отчёт по диалогу и сохраняет его в папку клиента."""
    client_dir = get_client_dir(report_data, reports_dir)
    stamp = _timestamp()
    save_json(report_data, client_dir / f"report_{stamp}.json")
    html = render_html("report", report=report_data)
    return html_to_pdf(html, client_dir / f"report_{stamp}.pdf")


def generate_booklet_pdf(
    report_data: dict[str, Any],
    offer_data: dict[str, Any],
    image_path: Path | None = None,
    reports_dir: Path = REPORTS_DIR,
) -> Path:
    """Формирует PDF-буклет с предложением по туру."""
    client_dir = get_client_dir(report_data, reports_dir)
    stamp = _timestamp()
    save_json(offer_data, client_dir / f"offer_{stamp}.json")
    image_uri = Path(image_path).resolve().as_uri() if image_path and Path(image_path).exists() else None
    html = render_html("booklet", report=report_data, offer=offer_data, image_uri=image_uri)
    return html_to_pdf(html, client_dir / f"booklet_{stamp}.pdf")
