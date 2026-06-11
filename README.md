# poly-house

Warehouse для [Polymarket](https://polymarket.com): инкрементальная синхронизация маркетов, событий, тегов, трейдов и пользователей в локальную MongoDB + собственный REST API поверх неё.

## Требования

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Локальная MongoDB (`mongodb://localhost:27017`)

## Установка

```bash
uv sync
cp .env.example .env  # при необходимости поправить настройки
```

## Использование

### Синхронизация

```bash
# один прогон
uv run poly-house sync

# бесконечный цикл с интервалом WATCH_INTERVAL из конфига (остановка — Ctrl+C)
uv run poly-house sync --watch

# тестовый прогон: 2 страницы маркетов, без трейдов
uv run poly-house sync --max-pages 2 --skip-trades
```

Как это работает:

- Маркеты качаются из Gamma API (`/markets/keyset?order=updatedAt&ascending=false`) до watermark — максимального `updatedAt`, сохранённого в базе. Записываются в порядке возрастания `updatedAt`, поэтому упавший прогон не сдвигает watermark вперёд.
- Первый прогон загружает всё, что обновлялось за последние `UPDATED_IN_DAYS` дней (по умолчанию 30).
- События, теги и `seriesIds` извлекаются из payload'а маркетов; отдельных запросов нет.
- Трейды качаются из Data API (`/trades?takerOnly=true`) по watermark `timestamp` для каждого маркета, пришедшего в прогоне (потолок API: `limit=1000`, `offset<=3000`). Пользователи извлекаются из трейдов.
- Все ключи payload'ов при записи конвертируются из camelCase в snake_case (`conditionId` → `condition_id`) — в MongoDB и в ответах REST API имена полей питоновские.

### REST API

```bash
uv run poly-house serve
```

Swagger UI: <http://127.0.0.1:8000/docs>

| Endpoint | Фильтры |
|---|---|
| `GET /events` | `closed`, `tag_id`, `slug` |
| `GET /events/{id}` | — |
| `GET /markets` | `closed`, `event_id`, `tag_id`, `slug`, `condition_id` |
| `GET /markets/{id}` | — |
| `GET /tags` | `slug` |
| `GET /tags/{id}` | — |
| `GET /trades` | `condition_id`, `user`, `side`, `from`, `to` |

Все списки пагинируются через `limit` (default 50, max 500) и `offset`.

## Конфигурация

Все настройки — через `.env` (см. `.env.example`): подключение к MongoDB, интервал watch-режима, глубина первой загрузки, лимиты rate limiter'а и размеры страниц API.
