# Travel Leads AI

Автоматическое формирование PDF-отчётов по диалогам с потенциальными клиентами
туристической компании и персональных PDF-буклетов с предложением тура.

- Анализ текста: `openai/gpt-4o-mini` через [ProxyAPI](https://proxyapi.ru)
- Иллюстрация буклета: `openai/gpt-image-1` (quality `low`, 1024×1024)
- Шаблоны: Jinja2, конвертация в PDF: WeasyPrint

## Структура

```
travel_leads_ai/
├── main.py                    # CLI, интерактивный режим, Flask API
├── templates/
│   ├── report_template.html   # шаблон PDF-отчёта по диалогу
│   └── booklet_template.html  # единый шаблон PDF-буклета с предложением
├── reports/                   # готовые PDF (подпапка на каждого клиента)
├── transcripts/               # тестовые транскрибации диалогов
├── utils/
│   ├── ai_processor.py        # работа с ProxyAPI
│   └── pdf_generator.py       # HTML → PDF
├── logs/app.log               # журнал работы (создаётся автоматически)
├── .env                       # ключ API
└── requirements.txt
```

## Установка

```powershell
py -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

Укажите ключ в `.env`:

```
PROXYAPI_KEY=sk-...
```

### WeasyPrint на Windows

WeasyPrint использует системную библиотеку Pango. Если при запуске появляется ошибка
`cannot load library 'libgobject-2.0-0'`:

1. Установите [MSYS2](https://www.msys2.org/).
2. В терминале MSYS2 выполните: `pacman -S mingw-w64-x86_64-pango`
3. Добавьте `C:\msys64\mingw64\bin` в переменную `PATH` (или задайте
   `WEASYPRINT_DLL_DIRECTORIES=C:\msys64\mingw64\bin`).

## Запуск

```powershell
# Интерактивный режим: выбор файла из transcripts/ или ручной ввод
python main.py

# Отчёт по файлу
python main.py -f transcripts/dialog_1_family_beach.txt

# Отчёт + буклет с предложением тура и картинкой
python main.py -f transcripts/dialog_2_gastro_couple.txt --booklet

# Буклет по уже сохранённому отчёту (без повторного анализа диалога)
python main.py --from-json reports/ekaterina_belova/report_20261013_120000.json

# Подробные логи в консоли
python main.py -f transcripts/dialog_3_friends_active.txt -v
```

Результаты для каждого клиента сохраняются в `reports/<имя_клиента>/`:
`report_*.pdf`, `report_*.json`, `booklet_*.pdf`, `offer_*.json`, `tour_image.png`.

## Буклет

Все буклеты собираются по одному шаблону `templates/booklet_template.html` и имеют
одинаковую структуру из 4 страниц:

1. Обложка: картинка тура, название, слоган, обращение к клиенту, краткая сводка.
2. Направление, виды отдыха, программа по дням.
3. Проживание, перелёт, трансфер, стоимость, учтённые пожелания.
4. Таблица «Ваш запрос — наше решение» и контакты компании.

Разделы выводятся всегда; если ИИ не заполнил какой-то пункт, в нём будет
«Уточняется у менеджера». Название и контакты компании в шапке и подвале задаются
в `.env` (`COMPANY_NAME`, `COMPANY_PHONE`, `COMPANY_EMAIL`, `COMPANY_SITE`).

## Flask API

```powershell
python main.py --serve --port 5000
```

| Метод | URL | Описание |
|---|---|---|
| GET | `/api/health` | проверка работы |
| POST | `/api/report` | JSON `{"text": "...", "booklet": true}` или форма с полем `file` |
| GET | `/api/files/<путь>` | скачать PDF, путь берётся из ответа `/api/report` |

```powershell
curl.exe -X POST http://127.0.0.1:5000/api/report -F "file=@transcripts/dialog_1_family_beach.txt" -F "booklet=true"
```

## Логирование

Все этапы (чтение файла, запросы к API, расход токенов, сохранение PDF, ошибки
с трассировкой) пишутся в `logs/app.log` с ротацией (5 файлов по 2 МБ).
Сырые ответы модели логируются на уровне DEBUG.
