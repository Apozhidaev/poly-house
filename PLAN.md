# poly-house — план проекта

Warehouse для Polymarket: сохраняем в MongoDB маркеты, события и связанные с ними данные. Утилита `sync` работает инкрементально по watermark'ам: маркеты — по `updatedAt`, трейды — по `timestamp`; каждый прогон забирает только то, что новее сохранённого, и пишет данные в порядке возрастания (упавший прогон не сдвигает watermark вперёд). Из трейдов попутно извлекаются users. Поверх базы — собственный REST API (`poly-house serve`), отдающий events, markets, tags, trades, users. MongoDB — локальная, без Docker.

## 1. Источники данных

Оба API публичные, аутентификация не нужна.

| API | Endpoint | Что берём |
|---|---|---|
| Gamma API | `GET /markets/keyset?include_tag=true&order=updatedAt&ascending=false` | единственный источник: маркеты по убыванию `updatedAt`; внутри — собственные `tags` маркета и вложенные `events` (а в них свои `tags` и `series`) |
| Data API | `GET /trades?market={conditionId}&takerOnly=true&limit=...&offset=...` | сделки по маркету (новые первыми, только taker-сделки); **максимальный limit — 1000, максимальный offset — 3000**; users извлекаем из самих трейдов в момент загрузки |

### Keyset-пагинация и инкрементальность по updatedAt

- `/markets/keyset` возвращает `{"markets": [...], "next_cursor": "..."}`; `next_cursor` — непрозрачный base64-курсор, передаём его в следующий запрос (`?cursor=...`).
- Запрашиваем с `order=updatedAt&ascending=false` — свежеобновлённые маркеты идут первыми.
- **Watermark**: перед прогоном берём из базы максимальный `updatedAt` сохранённых маркетов. Идём по страницам, пока не встретим маркеты с `updatedAt` меньше watermark, — дальше всё заведомо уже синхронизировано, скачивание останавливается.
- **Сохранение в порядке возрастания `updatedAt`**: скачанные страницы буферизуем и пишем в базу в обратном порядке (от старых к новым). Так watermark в базе растёт монотонно по мере записи: упавший прогон не сдвигает его вперёд, и следующий прогон докачивает ровно с места обрыва.
- **Первый прогон** (база пустая): watermark = `now - updated_in_days` (параметр конфига, по умолчанию 30 дней) — загружаем всё, что обновлялось за последние 30 дней.
- Курсор живёт только внутри прогона, между прогонами не сохраняется.

### Правило обновления

Всё, что пришло в прогон (т.е. обновилось позже watermark), просто upsert'им: новые маркеты вставляются, существующие перезаписываются свежим payload'ом. То же для вложенных событий и тегов. Отдельная логика для закрытых записей не нужна — Polymarket сам не меняет `updatedAt` у того, что не менялось.

### Events, tags и series

Всё берём из одного payload'а `/markets/keyset` с параметром `include_tag=true`. Важно: списки тегов **раздельные** — у маркета свой `markets[].tags`, у события свой `markets[].events[].tags`:

- события — из вложенных `events`, их `tag_ids` — из `events[].tags`; если у события тегов нет — fallback на теги маркета (`markets[].tags`);
- `tag_ids` маркета — из его собственного `markets[].tags` (не копия событийных);
- в коллекцию `tags` upsert'им объекты тегов из обоих списков;
- из `series` сохраняем только id (`series_ids` внутри события).

Никаких дополнительных запросов не нужно.

### Trades

Та же watermark-схема, что и у маркетов, но по `timestamp` и отдельно для каждого маркета:

- watermark маркета = max(`timestamp`) его сохранённых трейдов (трейдов нет → качаем всю доступную историю);
- качаем только taker-сделки (`takerOnly=true`);
- `/trades` отдаёт сделки новыми вперёд: качаем страницы, пока не дойдём до watermark или до потолка `offset=3000`, затем пишем в базу в обратном порядке (по возрастанию `timestamp`);
- на закрытость маркета не смотрим; уникальные `_id` не нужны — от дублей защищает watermark (записи получают обычный ObjectId).

Ограничение: качаем страницами по `limit=1000`; из-за потолка `offset=3000` за один прогон по одному маркету достаётся не более 4000 сделок (4 страницы). В watch-режиме это не проблема (интервалы короткие), но хвост истории очень активных маркетов может остаться недокачанным.

### Users

Отдельных запросов за профилями нет — каждый трейд уже содержит `proxyWallet`, `name`, `pseudonym`, `bio`, `profileImage`. Users upsert'ятся в момент загрузки трейдов.

## 2. Стек

Проект асинхронный (asyncio) — под уже написанный HTTP-слой.

- Python 3.12+, менеджер пакетов — `uv`
- `httpx` — async HTTP-клиент; поверх него **готовые** `RateLimiter` и `RateLimitedClient` (см. ниже)
- `pymongo` (async API, `AsyncMongoClient`) — работа с MongoDB (локальный инстанс, `mongodb://localhost:27017`)
- `typer` — CLI (внутри команды — `asyncio.run`)
- `fastapi` + `uvicorn` — собственный REST API поверх MongoDB
- `pydantic` + `pydantic-settings` — модели и конфиг из `.env`
- `structlog` — логирование (уже используется в `RateLimitedClient`)

### Готовый HTTP-слой

В репозитории уже есть `rate_limiter.py` и `rate_limited_client.py` — используем их как есть:

- `RateLimiter(interval, count)` — скользящее окно (по умолчанию 5 запросов/сек), `acquire()` перед каждым запросом.
- `RateLimitedClient` — обёртка над `httpx.AsyncClient` (timeout 30s): `get()` с 3 ретраями и экспоненциальным бэкофом, `get_or_none()` для некритичных запросов.

Файлы переносим в `src/poly_house/api/` и поправляем import-путь (`poly_house.api.rate_limiter`). Лимиты (`interval`, `count`) выносим в конфиг вместо хардкода на уровне модуля. Клиенты Gamma/Data API строим поверх `RateLimitedClient`.

## 3. Структура проекта

```
poly-house/
├── pyproject.toml
├── .env.example            # MONGO_URI, MONGO_DB, WATCH_INTERVAL, UPDATED_IN_DAYS, лимиты API
├── README.md
└── src/poly_house/
    ├── __init__.py
    ├── config.py           # настройки из .env (включая watch_interval и лимиты RateLimiter)
    ├── db.py               # подключение к Mongo (AsyncMongoClient), индексы
    ├── api/
    │   ├── rate_limiter.py         # существующий RateLimiter (перенос)
    │   ├── rate_limited_client.py  # существующий RateLimitedClient (перенос)
    │   ├── gamma.py                # клиент Gamma API поверх RateLimitedClient: markets/keyset
    │   └── data.py                 # клиент Data API поверх RateLimitedClient: trades
    ├── models/
    │   ├── __init__.py     # реэкспорт моделей
    │   ├── event.py        # Event
    │   ├── market.py       # Market
    │   ├── tag.py          # Tag
    │   ├── trade.py        # Trade
    │   └── user.py         # User
    ├── sync/
    │   ├── __init__.py
    │   ├── runner.py       # оркестрация прогона + режим watch
    │   ├── markets.py      # инкрементальный обход /markets/keyset по updatedAt до watermark (+events/tags)
    │   └── trades.py       # инкрементальная выгрузка trades по watermark timestamp + upsert users из трейдов
    ├── server/
    │   ├── __init__.py
    │   ├── app.py          # FastAPI-приложение, lifespan (подключение к Mongo)
    │   └── routes/
    │       ├── events.py   # GET /events, GET /events/{id}
    │       ├── markets.py  # GET /markets, GET /markets/{id}
    │       ├── tags.py     # GET /tags, GET /tags/{id}
    │       └── trades.py   # GET /trades
    └── cli.py              # entrypoints: `poly-house sync [--watch]`, `poly-house serve`
```

## 4. Модель данных MongoDB

camelCase-ключи payload'ов маппятся на snake_case-поля моделей через pydantic-алиасы (`alias_generator=to_camel`: `conditionId` → `condition_id` и т.д.) — сервис будет использоваться из питоновских приложений, храним и отдаём в питоновском стиле.

В базу попадают **только поля, описанные в pydantic-моделях** (`extra="ignore"`) — остальные свойства payload'а отбрасываются. Нужно новое поле — добавляем его в модель.

| Коллекция | `_id` | Содержимое | Индексы |
|---|---|---|---|
| `events` | gamma `id` | событие из payload маркета (вложенные markets заменяем на `market_ids`; ссылки `tag_ids`, `series_ids`) | `slug`, `closed_time` |
| `markets` | gamma `id` | маркет + `event_id`, `condition_id`, `tag_ids` (собственные теги маркета из `include_tag=true`) | `condition_id` (unique), `event_id`, `slug`, `tag_ids` |
| `tags` | gamma `id` | `id`, `label`, `slug` (из payload маркетов и событий) | `slug` |
| `trades` | ObjectId (авто) | поля модели Trade (`condition_id`, `proxy_wallet`, `timestamp`, `side`, `size`, `price`, ...) | `(condition_id, timestamp)`, `proxy_wallet`, `timestamp` |
| `users` | `proxy_wallet` | профиль из трейдов: name, pseudonym, bio, profile_image | — |

Markets/events/tags/users — upsert по стабильным `_id`; trades — обычные insert, от дублей защищает watermark по `timestamp`. У events/markets дополнительно храним `synced_at`; по `markets.updated_at` — индекс (нужен для вычисления watermark). Составной индекс `(condition_id, timestamp)` обслуживает и watermark трейдов по маркету, и фильтры REST API.

## 5. Утилита sync — алгоритм

`poly-house sync` — один прогон; `poly-house sync --watch` — бесконечный цикл прогонов с интервалом из конфига (`WATCH_INTERVAL`, сек), до остановки Ctrl+C (обрабатываем SIGINT, корректно завершаем текущую операцию).

Один прогон:

1. **Маркеты**: вычисляем watermark = max(`updatedAt`) по коллекции `markets` (база пустая → `now - updated_in_days`); качаем страницы `/markets/keyset?order=updatedAt&ascending=false`, пока не дойдём до watermark, затем пишем в базу **в порядке возрастания `updatedAt`**:
   - маркет → upsert в `markets` (с его `tag_ids`);
   - вложенные `events` → upsert в `events` (с `tag_ids` — при их отсутствии берём `tag_ids` маркета — и `series_ids`);
   - объекты тегов из `markets[].tags` и `events[].tags` → upsert в `tags`.
2. **Трейды + users**: для каждого маркета, пришедшего в этом прогоне, — watermark = max(`timestamp`) его трейдов; качаем `/trades?market={conditionId}` страницами до watermark (или до потолка `offset=3000`), затем пишем **в порядке возрастания `timestamp`**:
   - трейды insert в `trades`;
   - из каждого трейда — upsert профиля в `users` (proxyWallet, name, pseudonym, bio, profileImage).

Свойства:

- **Идемпотентность** — markets/events/tags/users upsert'ятся по стабильным `_id`, трейды отсекаются watermark'ом по `timestamp`.
- **Восстановление после сбоя** — данные пишутся в порядке возрастания watermark-поля, поэтому упавший прогон не сдвигает watermark вперёд: следующий прогон докачивает ровно с места обрыва.
- **Rate limiting** — готовый `RateLimiter` (скользящее окно, лимиты из конфига) + ретраи с экспоненциальным бэкофом в `RateLimitedClient`.
- **Логирование** — прогресс: страницы keyset, маркеты, число трейдов; сводка по итогам прогона (в watch-режиме — после каждой итерации).

CLI-опции: `--watch` (бесконечный цикл с интервалом из конфига), `--limit` (размер страницы), `--max-pages` (ограничить прогон для теста), `--skip-trades`.

## 6. REST API

`poly-house serve` — запускает FastAPI через uvicorn (host/port из конфига). Read-only API поверх MongoDB, отдаёт данные warehouse. Параллельно может работать `sync --watch`.

Общие правила:

- Пагинация на всех списках: `limit` (default 50, max 500) + `offset`; ответ — `{"items": [...]}` (без `total`).
- Получение по id — 404, если записи нет.
- Swagger UI доступен из коробки (`/docs`).

| Endpoint | Фильтры | Описание |
|---|---|---|
| `GET /events` | `closed`, `tag_id`, `slug` | список событий |
| `GET /events/{id}` | — | событие по id |
| `GET /markets` | `closed`, `event_id`, `tag_id`, `slug`, `condition_id` | список маркетов |
| `GET /markets/{id}` | — | маркет по id |
| `GET /tags` | `slug` | список тегов |
| `GET /tags/{id}` | — | тег по id |
| `GET /trades` | `condition_id`, `user` (proxyWallet), `side`, `from`/`to` (timestamp; в коде — alias, `from` зарезервирован), сортировка по `timestamp` desc | список сделок |

Фильтры ложатся на уже заложенные индексы (`tag_ids`, `event_id`, `(condition_id, timestamp)`, `proxy_wallet`, `timestamp`).

## 7. Этапы реализации

1. **Каркас**: pyproject, конфиг, перенос `rate_limiter.py` / `rate_limited_client.py` в `api/`, подключение к локальной Mongo, создание индексов, CLI-скелет.
2. **Клиенты API**: gamma-клиент (markets/keyset) и data-клиент (trades) поверх `RateLimitedClient`, с keyset-пагинацией.
3. **Sync маркетов**: инкрементальный обход по `updatedAt` до watermark (первый прогон — `updated_in_days`), запись в порядке возрастания, upsert маркетов + события/tags/seriesIds из payload.
4. **Трейды + users**: инкрементальная выгрузка trades по watermark `timestamp` (потолок `offset=3000`), запись в порядке возрастания, upsert users из трейдов.
5. **REST API**: FastAPI-приложение, роуты events/markets/tags/trades с пагинацией и фильтрами, команда `poly-house serve`.
6. **Watch-режим и полировка**: `--watch` с graceful shutdown по Ctrl+C, логирование, сводка прогона, README, `.env.example`, тестовый прогон на `--max-pages`.
