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
uv run poly-house sync-open-market
uv run poly-house sync-close-market

# трейды + пользователи (для маркетов, обновлённых после прошлого прогона)
uv run poly-house sync-trade

# holder snapshots: ордербук + top holders для открытых маркетов
uv run poly-house holder-snapshot

# устаревшие profiles (synced_at старше суток) → activity cache, activities, profiles
uv run poly-house sync-profile

# tag profiles holders (activities × market tags) → коллекция tag_profiles
uv run poly-house update-tag-profile

# бесконечный цикл с интервалом WATCH_INTERVAL из конфига (остановка — Ctrl+C)
uv run poly-house sync-open-market --watch
uv run poly-house sync-close-market --watch
uv run poly-house sync-trade --watch
uv run poly-house holder-snapshot --watch
uv run poly-house sync-profile --watch
uv run poly-house update-tag-profile --watch

# тестовый прогон: 2 страницы маркетов
uv run poly-house sync-open-market --max-pages 2
uv run poly-house sync-close-market --max-pages 2

# тестовый прогон: 10 записей или один wallet
uv run poly-house sync-profile --limit 10
uv run poly-house sync-profile --wallet 0x...
uv run poly-house update-tag-profile --limit 10
uv run poly-house update-tag-profile --wallet 0x...
```

Как это работает:

- **sync-open-market**: открытые маркеты (`closed=false`) из Gamma API (`/markets/keyset?order=updatedAt&ascending=false`) до watermark'а (`sync_state.open_markets_watermark`); после успешного прогона watermark сдвигается на максимальный увиденный `updatedAt`. События, теги и `seriesIds` извлекаются из payload'а маркетов; отдельных запросов нет. Каждому записанному маркету проставляется `synced_at`.
- **sync-close-market**: то же для закрытых маркетов (`closed=true`), watermark — `sync_state.closed_markets_watermark`.
- Первый прогон каждой команды загружает маркеты, обновлявшиеся за последние `UPDATED_IN_DAYS` дней (по умолчанию 30).
- **sync-trade**: берёт из базы маркеты с `synced_at` позже собственного watermark'а (`sync_state.trades_watermark`; первый прогон — все маркеты) и качает их трейды из Data API (`/trades?takerOnly=true`) по watermark `timestamp` для каждого маркета (потолок API: `limit=1000`, `offset<=3000`). Пользователи извлекаются из трейдов. После успешного прогона watermark сдвигается на максимальный `synced_at` обработанных маркетов.
- **holder-snapshot**: для открытых маркетов (`closed=false`, `accepting_orders=true`, оба `clob_token_ids`) — CLOB orderbook по `clob_token_ids[0]`; при успехе Data API `/holders` — для публичных holders (`displayUsernamePublic=true`) без недавнего `profiles.synced_at` загружаются activity cache в `activity_caches` (источник `/closed-positions`), activity в `activities` и upsert в `profiles`, обновляются `tag_profiles` по тегам маркета; затем insert в `holder_snapshots` с `captured_at`. `top_holders` = `[outcome_0, outcome_1]` по `entry.token` ↔ `clob_token_ids`; каждый holder включает метрики из `profiles` (`pnl`, `volume`, `stable_growth`, `total_balance`, `house_win_rate`), `tag_win_rate`, `tag_stable_growth`, `tag_pnl` и `tag_volume` — по тегам маркета из `tag_profiles`, вес `1/markets_count`; только теги с `tag_profile`. Нужны `sync-open-market`, `sync-close-market` и `tag_profiles`.
- **sync-profile**: берёт из `profiles` документы с `synced_at` старше суток (или без `synced_at`); для каждого — (1) activity cache из Data API `/closed-positions` (sort `TIMESTAMP DESC`) в коллекцию `activity_caches` (уникальность `(proxy_wallet, condition_id)`, upsert; поля: `proxy_wallet`, `condition_id`, `timestamp`, `activity_synced=false`); первый прогон — за последние `ACTIVITY_CACHE_IN_DAYS` дней (по умолчанию 365, до `offset<=100000`), повторный — только записи с `timestamp` больше max в базе для этого wallet; (2) activity по pending cache entries (`activity_synced=false`, `condition_id` есть в `markets`): для каждой записи `/activity?user=...&market=<condition_id>`, watermark — max `timestamp` в `activities` по паре `(proxy_wallet, condition_id)`, insert в `activities`, затем `activity_synced=true`; (3) upsert в `profiles`: метрики из Polymarket API — leaderboard (`pnl`, `volume`), accounting snapshot `equity.csv` (`positions_value`, `cash_balance`, `total_balance` = `equity`), user-pnl time series (`stable_growth` = `pnl_stability` при `pnl_growth > 0`, иначе `0`), `house_win_rate` / `house_markets_count` / `house_pnl` / `house_volume` / `house_stable_growth` — по activities wallet в базе (sum per-market pnl/volume через `build_market_activity`; `house_stable_growth` — та же формула по per-market `pnl`, `null` при одном маркете), `synced_at` — время прогона; если API не вернул данные — поле `null`. Первичное создание profiles для holders — inline при `holder-snapshot`.
- **update-tag-profile**: для каждого wallet из `profiles` — activities из `activities`, у которых `condition_id` есть в `markets`; агрегирует в `market_activities` (по `condition_id`: `volume` = sum `usdc_size`, `pnl` = signed sum TRADE BUY/SELL); по `tag_ids` маркета группирует метрики и upsert в `tag_profiles` (уникальность `(proxy_wallet, tag_id)`, `_id` — MongoDB ObjectId). Поля: `markets_count`, `pnl` (sum per-market `pnl`), `volume` (sum per-market `volume`), `win_rate` (доля market_activities с `pnl > 0`), `pnl_growth` / `pnl_stability` (линейная регрессия по per-market `pnl` с `use_cumulative=True`; `null` при одном маркете). Маркет с несколькими тегами учитывается в каждом теге. Нужны `sync-close-market`, `profiles` и `activities`.
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
| `GET /activity` | `condition_id`, `outcome_index`, `user`, `type`, `side`, `from`, `to` |
| `GET /market-activity` | `user` (required), `condition_id` |

Все списки пагинируются через `limit` (default 50, max 500) и `offset`.

## Конфигурация

Все настройки — через `.env` (см. `.env.example`): подключение к MongoDB, интервал watch-режима, глубина первой загрузки, лимиты rate limiter'а и размеры страниц API.
