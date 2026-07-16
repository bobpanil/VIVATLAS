# VivAtlas

Каталог скиллов, агентов и инструментов из ваших Git-репозиториев.

План: [docs/PLAN.md](docs/PLAN.md)

## Что уже работает (этап 1 из 7)

Подключение к Gitea и список репозиториев в базе. Ничего не пишется в Git — только чтение.

## Запуск

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"    # Windows
# .venv/bin/python -m pip install -e ".[dev]"          # Linux

cp .env.example .env      # при необходимости поправить адрес Gitea

.venv/Scripts/python.exe -m vivatlas.cli init-db    # создать базу
.venv/Scripts/python.exe -m vivatlas.cli scan       # забрать репозитории
.venv/Scripts/python.exe -m uvicorn vivatlas.api:app --reload
```

Проверить: <http://127.0.0.1:8000/health>

## Адреса

| Адрес | Что показывает |
|---|---|
| `/health` | Жив ли сервис и сколько репозиториев в базе |
| `/api/repositories` | Список репозиториев |
| `/api/scan-runs` | История сканирований |

## Правила, зашитые в код

- **Приватные репозитории не сканируются никогда.** Это не настройка — переключателя нет ни в `.env`, ни в базе. Если хостинг не сообщил, приватный репозиторий или нет, он считается приватным.
- **Ничего не пишется в Git.** Только чтение.
- Пропавший репозиторий помечается, а не удаляется из базы.

## Тесты

```bash
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m ruff check src tests
```

## Устройство

```
src/vivatlas/
  config.py            настройки из .env
  db.py                подключение к SQLite
  models.py            таблицы
  scanner.py           сканирование + правило про приватные
  api.py               REST
  cli.py               команды в терминале
  providers/
    base.py            общий интерфейс к хостингу («розетка»)
    gitea.py           Gitea
    github.py          заглушка — место под GitHub
```

Чтобы добавить GitHub: реализовать методы в `providers/github.py` и включить в `providers/__init__.py`. Остальной код не меняется.
