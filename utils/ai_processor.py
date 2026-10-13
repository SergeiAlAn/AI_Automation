"""Взаимодействие с моделями OpenAI через ProxyAPI.

ProxyAPI предоставляет OpenAI-совместимый endpoint, поэтому используется
официальный SDK `openai` с подменённым `base_url`.
"""

from __future__ import annotations

import base64
import copy
import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI, OpenAIError

load_dotenv()

logger = logging.getLogger(__name__)

PROXYAPI_BASE_URL = os.getenv("PROXYAPI_BASE_URL", "https://api.proxyapi.ru/v1")
TEXT_MODEL = os.getenv("TEXT_MODEL", "openai/gpt-4o-mini")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "openai/gpt-image-1")
IMAGE_SIZE = "1024x1024"
IMAGE_QUALITY = "low"
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "120"))


class AIProcessingError(RuntimeError):
    """Ошибка при обращении к модели или разборе её ответа."""


REPORT_SKELETON: dict[str, Any] = {
    "client": {
        "name": None,
        "phone": None,
        "email": None,
        "city": None,
        "preferred_contact": None,
        "notes": None,
    },
    "vacation_types": [],
    "destinations": [],
    "travel_dates": {
        "start": None,
        "end": None,
        "duration": None,
        "flexibility": None,
    },
    "travelers": {
        "total": None,
        "adults": None,
        "children": None,
        "children_ages": [],
        "comment": None,
    },
    "budget": {
        "amount": None,
        "currency": None,
        "scope": None,
        "comment": None,
    },
    "hotel_preferences": [],
    "flight_preferences": [],
    "transfer_preferences": [],
    "other_wishes": [],
    "summary": None,
}

OFFER_SKELETON: dict[str, Any] = {
    "title": None,
    "tagline": None,
    "intro": None,
    "destination": {"country": None, "region": None, "why": None},
    "dates": None,
    "travelers": None,
    "vacation_types_covered": [],
    "program": [],
    "accommodation": {"name": None, "description": None, "meal_plan": None},
    "flight": None,
    "transfer": None,
    "price": {
        "total": None,
        "currency": None,
        "per_person": None,
        "includes": [],
        "not_included": [],
    },
    "extras": [],
    "requirements_match": [],
    "image_prompt": None,
}

REPORT_SYSTEM_PROMPT = """Ты — аналитик туристической компании. Тебе дают транскрибацию
разговора менеджера с потенциальным клиентом. Извлеки из неё ключевую информацию
и верни СТРОГО один JSON-объект без пояснений следующей структуры:

{
  "client": {
    "name": "имя клиента",
    "phone": "телефон",
    "email": "e-mail",
    "city": "город проживания / вылета",
    "preferred_contact": "удобный способ и время связи",
    "notes": "важные сведения о клиенте (опыт поездок, особенности, здоровье и т.п.)"
  },
  "vacation_types": ["виды отдыха: пляжный, гастрономический, экскурсионный, активный, ..."],
  "destinations": [
    {"country": "страна", "region": "регион/город или null", "comment": "отношение клиента: приоритет, сомнения"}
  ],
  "travel_dates": {
    "start": "дата/период начала", "end": "дата/период окончания",
    "duration": "продолжительность", "flexibility": "гибкость дат"
  },
  "travelers": {
    "total": число, "adults": число, "children": число,
    "children_ages": [числа], "comment": "кто едет, особенности"
  },
  "budget": {
    "amount": "сумма или диапазон", "currency": "валюта",
    "scope": "на всех / на человека, что включено", "comment": "уточнения"
  },
  "hotel_preferences": ["пожелания по отелю"],
  "flight_preferences": ["пожелания по перелёту"],
  "transfer_preferences": ["пожелания по трансферу"],
  "other_wishes": ["прочие пожелания: страховка, экскурсии, виза, питание и т.д."],
  "summary": "краткое резюме запроса клиента в 2-4 предложениях"
}

Правила:
- Пиши на русском языке.
- Используй только факты из диалога, ничего не выдумывай.
- Если сведений нет, ставь null (для строк/чисел) или [] (для списков).
"""

OFFER_SYSTEM_PROMPT = """Ты — опытный турменеджер. На основе структурированного запроса клиента
составь персональное предложение по туристическому продукту для рекламного буклета.
Предложение должно отвечать на КАЖДЫЙ пункт запроса: виды отдыха, направление, сроки,
состав группы, бюджет, отель, перелёт, трансфер и прочие пожелания.
Верни СТРОГО один JSON-объект без пояснений:

{
  "title": "яркое название тура",
  "tagline": "короткий слоган",
  "intro": "персональное обращение к клиенту по имени, 2-3 предложения",
  "destination": {"country": "страна", "region": "регион", "why": "почему подходит клиенту"},
  "dates": "предлагаемые даты и продолжительность",
  "travelers": "для кого тур (состав группы)",
  "vacation_types_covered": [{"type": "вид отдыха", "how": "как он реализован в туре"}],
  "program": [{"day": "День 1", "title": "заголовок", "description": "описание"}],
  "accommodation": {"name": "отель или тип размещения", "description": "почему подходит", "meal_plan": "питание"},
  "flight": "предлагаемый перелёт",
  "transfer": "предлагаемый трансфер",
  "price": {
    "total": "итоговая стоимость (число или диапазон)", "currency": "валюта",
    "per_person": "ориентировочно на человека",
    "includes": ["что входит"], "not_included": ["что не входит"]
  },
  "extras": ["как учтены прочие пожелания"],
  "requirements_match": [{"requirement": "пункт запроса клиента", "solution": "как он закрыт в предложении"}],
  "image_prompt": "детальный промпт на английском для генерации иллюстрации тура: место, атмосфера, люди (по составу группы), стиль — фотореалистичный рекламный кадр, без текста на изображении"
}

Правила:
- Пиши на русском языке (кроме image_prompt).
- Стоимость должна укладываться в бюджет клиента; если бюджета нет — предложи разумную.
- Суммы пиши с пробелами между разрядами ("485 000"), валюту — символом или кодом ("₽", "EUR").
- title — до 6 слов, tagline — до 10 слов, intro — не длиннее 3 предложений.
- Программа — 4-6 пунктов (группируй дни: "Дни 2-4"), описание пункта — 1-2 предложения.
- requirements_match должен покрывать все пункты запроса (8-10 строк), каждое решение — одно предложение.
"""


def get_client() -> OpenAI:
    """Создаёт клиента OpenAI SDK, настроенного на ProxyAPI."""
    api_key = os.getenv("PROXYAPI_KEY")
    if not api_key or api_key.startswith("your_"):
        raise AIProcessingError(
            "Не задан PROXYAPI_KEY. Укажите ключ ProxyAPI в файле .env"
        )
    return OpenAI(api_key=api_key, base_url=PROXYAPI_BASE_URL, timeout=REQUEST_TIMEOUT)


def _merge_with_skeleton(data: Any, skeleton: Any) -> Any:
    """Дополняет ответ модели недостающими ключами, чтобы шаблон не ломался."""
    if isinstance(skeleton, dict):
        source = data if isinstance(data, dict) else {}
        result = {key: _merge_with_skeleton(source.get(key), value) for key, value in skeleton.items()}
        for key, value in source.items():
            result.setdefault(key, value)
        return result
    if isinstance(skeleton, list):
        if isinstance(data, list):
            return data
        return [data] if data not in (None, "") else []
    return data if data is not None else copy.deepcopy(skeleton)


def _chat_json(system_prompt: str, user_content: str, temperature: float) -> dict[str, Any]:
    """Отправляет запрос в чат-модель и возвращает распарсенный JSON."""
    client = get_client()
    logger.info("Запрос к модели %s (символов во входе: %d)", TEXT_MODEL, len(user_content))
    try:
        response = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=temperature,
        )
    except OpenAIError as exc:
        logger.exception("Ошибка обращения к ProxyAPI")
        raise AIProcessingError(f"Ошибка обращения к ProxyAPI: {exc}") from exc

    content = response.choices[0].message.content or ""
    usage = getattr(response, "usage", None)
    if usage:
        logger.info(
            "Токены: prompt=%s, completion=%s, total=%s",
            usage.prompt_tokens, usage.completion_tokens, usage.total_tokens,
        )
    logger.debug("Сырой ответ модели: %s", content)

    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        logger.error("Модель вернула невалидный JSON: %s", content[:500])
        raise AIProcessingError("Модель вернула невалидный JSON") from exc


def process_dialog_with_ai(text: str) -> dict[str, Any]:
    """Анализирует транскрибацию диалога и возвращает структурированные данные отчёта."""
    if not text or not text.strip():
        raise ValueError("Текст диалога пуст")

    data = _chat_json(
        REPORT_SYSTEM_PROMPT,
        f"Транскрибация диалога:\n\n{text.strip()}",
        temperature=0.1,
    )
    report = _merge_with_skeleton(data, REPORT_SKELETON)
    logger.info("Диалог обработан, клиент: %s", report["client"].get("name") or "не указан")
    return report


def generate_tour_offer(report_data: dict[str, Any]) -> dict[str, Any]:
    """Формирует предложение по туру на основе данных отчёта."""
    payload = json.dumps(report_data, ensure_ascii=False, indent=2)
    today = date.today().strftime("%d.%m.%Y")
    data = _chat_json(
        OFFER_SYSTEM_PROMPT,
        f"Сегодня {today}. Если год поездки не указан, используй ближайшие будущие даты.\n\n"
        f"Запрос клиента (JSON):\n\n{payload}",
        temperature=0.7,
    )
    offer = _merge_with_skeleton(data, OFFER_SKELETON)
    logger.info("Предложение сформировано: %s", offer.get("title"))
    return offer


def generate_tour_image(prompt: str, output_path: Path) -> Path:
    """Генерирует иллюстрацию тура (gpt-image-1, quality=low, 1024x1024) и сохраняет PNG."""
    if not prompt:
        raise ValueError("Пустой промпт для генерации изображения")

    client = get_client()
    logger.info("Генерация изображения моделью %s (%s, %s)", IMAGE_MODEL, IMAGE_SIZE, IMAGE_QUALITY)
    logger.debug("Промпт изображения: %s", prompt)
    try:
        response = client.images.generate(
            model=IMAGE_MODEL,
            prompt=prompt,
            size=IMAGE_SIZE,
            quality=IMAGE_QUALITY,
            n=1,
        )
    except OpenAIError as exc:
        logger.exception("Ошибка генерации изображения")
        raise AIProcessingError(f"Ошибка генерации изображения: {exc}") from exc

    image_b64 = response.data[0].b64_json if response.data else None
    if not image_b64:
        raise AIProcessingError("API не вернул изображение в формате base64")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(base64.b64decode(image_b64))
    logger.info("Изображение сохранено: %s", output_path)
    return output_path
