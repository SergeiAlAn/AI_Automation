"""Travel Leads AI — PDF-отчёты и буклеты по диалогам с клиентами турагентства.

Примеры запуска:
    python main.py                                      # интерактивный режим
    python main.py -f transcripts/dialog_1_family_beach.txt
    python main.py -f transcripts/dialog_2_gastro_couple.txt --booklet
    python main.py --from-json reports/<клиент>/report_<дата>.json   # буклет по готовому отчёту
    python main.py --serve --port 5000                  # Flask API
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from utils.ai_processor import (
    AIProcessingError,
    generate_tour_image,
    generate_tour_offer,
    process_dialog_with_ai,
)
from utils.pdf_generator import (
    REPORTS_DIR,
    generate_booklet_pdf,
    generate_report_pdf,
    get_client_dir,
)

BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR = BASE_DIR / "logs"
TRANSCRIPTS_DIR = BASE_DIR / "transcripts"

logger = logging.getLogger("travel_leads")


def setup_logging(verbose: bool = False) -> None:
    """Логи: консоль (INFO/DEBUG) + файл logs/app.log (DEBUG, с ротацией)."""
    LOGS_DIR.mkdir(exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        LOGS_DIR / "app.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(levelname)-8s | %(message)s"))

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers = [file_handler, console_handler]

    for noisy in ("httpx", "httpcore", "openai", "fontTools", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("weasyprint").setLevel(logging.ERROR)


# ---------------------------------------------------------------------------
# Основной конвейер
# ---------------------------------------------------------------------------

def read_transcript(path: str | Path) -> str:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Файл не найден: {path}")
    logger.info("Чтение транскрибации: %s", path)
    for encoding in ("utf-8", "utf-8-sig", "cp1251"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("unknown", b"", 0, 1, f"Не удалось определить кодировку {path}")


def create_report(text: str) -> tuple[dict[str, Any], Path]:
    """Транскрибация → ИИ → JSON → PDF-отчёт."""
    report_data = process_dialog_with_ai(text)
    pdf_path = generate_report_pdf(report_data)
    return report_data, pdf_path


def create_booklet(report_data: dict[str, Any]) -> Path:
    """Данные отчёта → предложение по туру + картинка → PDF-буклет."""
    offer = generate_tour_offer(report_data)
    client_dir = get_client_dir(report_data)

    image_path = None
    prompt = offer.get("image_prompt") or offer.get("title")
    try:
        image_path = generate_tour_image(prompt, client_dir / "tour_image.png")
    except (AIProcessingError, ValueError) as exc:
        logger.warning("Буклет будет сформирован без картинки: %s", exc)

    return generate_booklet_pdf(report_data, offer, image_path)


# ---------------------------------------------------------------------------
# Интерактивный режим
# ---------------------------------------------------------------------------

def ask_yes_no(question: str) -> bool:
    return input(f"{question} [y/n]: ").strip().lower() in {"y", "yes", "д", "да"}


def read_manual_input() -> str:
    print("Вставьте текст диалога. Для завершения введите строку END:")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == "END":
            break
        lines.append(line)
    return "\n".join(lines)


def choose_transcript_interactively() -> str:
    files = sorted(TRANSCRIPTS_DIR.glob("*.txt")) if TRANSCRIPTS_DIR.exists() else []
    print("\nИсточник транскрибации:")
    for i, f in enumerate(files, 1):
        print(f"  {i}. {f.name}")
    print("  p. Указать путь к файлу")
    print("  m. Ввести текст вручную")
    choice = input("Ваш выбор: ").strip().lower()

    if choice == "m":
        return read_manual_input()
    if choice == "p":
        return read_transcript(input("Путь к файлу: ").strip().strip('"'))
    if choice.isdigit() and 1 <= int(choice) <= len(files):
        return read_transcript(files[int(choice) - 1])
    raise ValueError("Некорректный выбор")


def run_interactive() -> None:
    print("=== Travel Leads AI: отчёты по запросам клиентов ===")
    text = choose_transcript_interactively()
    report_data, report_pdf = create_report(text)
    print(f"\nОтчёт готов: {report_pdf}")
    if ask_yes_no("Сформировать предложение по туру (PDF-буклет)?"):
        booklet_pdf = create_booklet(report_data)
        print(f"Буклет готов: {booklet_pdf}")


# ---------------------------------------------------------------------------
# Flask API (опционально)
# ---------------------------------------------------------------------------

def create_app():
    from flask import Flask, jsonify, request, send_file

    app = Flask(__name__)
    app.json.ensure_ascii = False

    def _relative(path: Path) -> str:
        return path.resolve().relative_to(REPORTS_DIR.resolve()).as_posix()

    @app.get("/api/health")
    def health():
        return jsonify(status="ok")

    @app.post("/api/report")
    def api_report():
        """Принимает JSON {"text": "...", "booklet": false} или файл в поле 'file'."""
        if "file" in request.files:
            text = request.files["file"].read().decode("utf-8", errors="replace")
            booklet = request.form.get("booklet", "false").lower() in {"1", "true", "yes"}
        else:
            payload = request.get_json(silent=True) or {}
            text = payload.get("text", "")
            booklet = bool(payload.get("booklet"))

        if not text.strip():
            return jsonify(error="Пустой текст диалога"), 400

        try:
            report_data, report_pdf = create_report(text)
            result = {"report": report_data, "report_pdf": _relative(report_pdf)}
            if booklet:
                result["booklet_pdf"] = _relative(create_booklet(report_data))
            return jsonify(result)
        except AIProcessingError as exc:
            logger.error("API: ошибка ИИ: %s", exc)
            return jsonify(error=str(exc)), 502
        except Exception as exc:
            logger.exception("API: необработанная ошибка")
            return jsonify(error=str(exc)), 500

    @app.get("/api/files/<path:relpath>")
    def api_file(relpath: str):
        target = (REPORTS_DIR / relpath).resolve()
        if not target.is_relative_to(REPORTS_DIR.resolve()) or not target.is_file():
            return jsonify(error="Файл не найден"), 404
        return send_file(target)

    return app


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PDF-отчёты по диалогам с клиентами турагентства")
    parser.add_argument("-f", "--file", help="путь к текстовому файлу с транскрибацией")
    parser.add_argument("-t", "--text", help="текст диалога прямо в командной строке")
    parser.add_argument("-b", "--booklet", action="store_true", help="дополнительно сформировать PDF-буклет")
    parser.add_argument("--from-json", help="сформировать буклет по сохранённому report_*.json")
    parser.add_argument("--serve", action="store_true", help="запустить Flask API")
    parser.add_argument("--port", type=int, default=5000, help="порт Flask API")
    parser.add_argument("-v", "--verbose", action="store_true", help="подробный вывод в консоль")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(args.verbose)
    logger.info("Запуск приложения")

    try:
        if args.serve:
            create_app().run(host="127.0.0.1", port=args.port)
            return 0

        if args.from_json:
            report_data = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
            print(f"Буклет готов: {create_booklet(report_data)}")
            return 0

        if args.file or args.text:
            text = read_transcript(args.file) if args.file else args.text
            report_data, report_pdf = create_report(text)
            print(f"Отчёт готов: {report_pdf}")
            if args.booklet:
                print(f"Буклет готов: {create_booklet(report_data)}")
            return 0

        run_interactive()
        return 0

    except KeyboardInterrupt:
        logger.info("Прервано пользователем")
        return 130
    except (AIProcessingError, FileNotFoundError, ValueError) as exc:
        logger.error("%s", exc)
        return 1
    except Exception:
        logger.exception("Непредвиденная ошибка")
        return 1
    finally:
        logger.info("Завершение работы")


if __name__ == "__main__":
    sys.exit(main())
