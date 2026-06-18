# poly-house

Warehouse для [Polymarket](https://polymarket.com): инкрементальная синхронизация маркетов, событий, тегов, трейдов, пользователей и профилей в локальную MongoDB + собственный REST API поверх неё.

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

Синхронизация разбита на команды:

```bash
# маркеты + события + теги
uv run poly-house sync-market
uv run poly-house sync-market --skip-open

# трейды + пользователи (для маркетов, обновлённых после прошлого прогона)
uv run poly-house sync-trade

# снимки ордербука + top holders для открытых маркетов
uv run poly-house snapshot

# устаревшие profiles (synced_at старше суток) → positions + profiles
uv run poly-house sync-profile

# tag profiles holders (positions × market tags) → коллекция tag_profiles
uv run poly-house update-tag-profile

# бесконечный цикл с интервалом WATCH_INTERVAL из конфига (остановка — Ctrl+C)
uv run poly-house sync-market --watch
uv run poly-house sync-trade --watch
uv run poly-house snapshot --watch
uv run poly-house sync-profile --watch
uv run poly-house update-tag-profile --watch

# тестовый прогон: 2 страницы маркетов
uv run poly-house sync-market --max-pages 2

# тестовый прогон: 10 записей или один wallet
uv run poly-house sync-profile --limit 10
uv run poly-house sync-profile --wallet 0x...
uv run poly-house update-tag-profile --limit 10
uv run poly-house update-tag-profile --wallet 0x...
```

Как это работает:

- **sync-market**: маркеты качаются из Gamma API (`/markets/keyset?order=updatedAt&ascending=false`) до watermark'а (`sync_state.markets_watermark`); после успешного прогона watermark сдвигается на максимальный увиденный `updatedAt`. События, теги и `seriesIds` извлекаются из payload'а маркетов; отдельных запросов нет. Каждому записанному маркету проставляется `synced_at`.
- Первый прогон загружает маркеты, обновлявшиеся за последние `UPDATED_IN_DAYS` дней (по умолчанию 30). `--skip-open` ограничивает прогон закрытыми маркетами (`closed=true`).
- **sync-trade**: берёт из базы маркеты с `synced_at` позже собственного watermark'а (`sync_state.trades_watermark`; первый прогон — все маркеты) и качает их трейды из Data API (`/trades?takerOnly=true`) по watermark `timestamp` для каждого маркета (потолок API: `limit=1000`, `offset<=3000`). Пользователи извлекаются из трейдов. После успешного прогона watermark сдвигается на максимальный `synced_at` обработанных маркетов.
- **snapshot**: для открытых маркетов (`closed=false`, `accepting_orders=true`, оба `clob_token_ids`) — CLOB orderbook по `clob_token_ids[0]`; при успехе Data API `/holders` — upsert публичных профилей (`displayUsernamePublic=true`) в коллекцию `holders` (`name`, `bio`, `profile_image`); для holders, впервые попавших в `holders`, автоматически создаётся `profiles`, загружаются closed positions в `positions`, обновляются `tag_profiles` (как `sync-profile` / `update-tag-profile`); затем insert в `snapshots` с `captured_at`. `top_holders` = `[outcome_0, outcome_1]` по `entry.token` ↔ `clob_token_ids`; каждый holder включает метрики из `profiles` (`roi`, `stable_growth`, `total_balance`, `win_rate`), `tag_win_rate`, `tag_stable_growth` и `tag_roi` — по тегам маркета из `tag_profiles`, вес `1/markets_count`; только теги с `tag_profile`. Нужны `sync-market` и `tag_profiles`.
- **sync-profile**: берёт из `profiles` документы с `synced_at` старше суток (или без `synced_at`); для каждого — (1) closed positions из Data API `/closed-positions` (sort `TIMESTAMP DESC`) в коллекцию `positions` (`_id` — MongoDB auto); первый прогон — только за последние `POSITIONS_CLOSED_IN_DAYS` дней, повторный — только записи с `timestamp` больше max в базе для этого wallet; (2) upsert в `profiles`: метрики из Polymarket API — leaderboard (`pnl`, `volume`), accounting snapshot `equity.csv` (`positions_value`, `cash_balance`, `total_balance` = `equity`), user-pnl time series (`pnl_growth`, `pnl_stability` через линейную регрессию), `win_rate` — доля positions с `realized_pnl > 0` по всем positions wallet в базе, `synced_at` — время прогона; если API или positions не вернули данные — поле `null`. Первичное создание profiles для новых holders — в `snapshot`.
- **update-tag-profile**: для каждого holder из `holders` — позиции из `positions`, у которых `condition_id` есть в `markets`; по `tag_ids` маркета группирует метрики и upsert в `tag_profiles` (уникальность `(proxy_wallet, tag_id)`, `_id` — MongoDB ObjectId). Поля: `markets_count`, `positions_count`, `pnl` (sum `realized_pnl`), `volume` (sum `total_bought`), `win_rate` (доля позиций с `realized_pnl > 0`), `pnl_growth` / `pnl_stability` (линейная регрессия по `realized_pnl` с `use_cumulative=True`; `null` при одной позиции). Маркет с несколькими тегами учитывается в каждом теге. Нужны `sync-market`, `holders` и `positions`.
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
| `GET /trades` | `condition_id`, `outcome_index`, `user`, `side`, `from`, `to` |
| `GET /trades/take-prices` | `condition_id`, `outcome_index`, `side` |
| `GET /trades/stop-prices` | `condition_id`, `outcome_index`, `side` |
| `GET /trades/mid-prices` | `condition_id` |
| `GET /trades/to-outcome` | `target_outcome_index` (default 0), `condition_id`, `user`, `side`, `from`, `to` |

Все списки пагинируются через `limit` (default 50, max 500) и `offset`.

## Конфигурация

Все настройки — через `.env` (см. `.env.example`): подключение к MongoDB, интервал watch-режима, глубина первой загрузки, лимиты rate limiter'а и размеры страниц API.
