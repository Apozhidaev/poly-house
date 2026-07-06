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

## Проверка типов

Dev-зависимости (basedpyright):

```bash
uv sync --group dev
uv run basedpyright src/poly_house/
```

## Использование

### Синхронизация

Синхронизация разбита на команды:

```bash
# events + nested markets + tags
uv run poly-house sync-event
uv run poly-house sync-event --open

# один event по slug: Gateway /gamma/events/slug → tags, event, markets
uv run poly-house add-event <slug>

# трейды + пользователи (для маркетов, обновлённых после прошлого прогона)
uv run poly-house sync-trade

# orderbook open markets → hourly bid/ask klines
uv run poly-house sync-kline
uv run poly-house sync-kline --watch

# holders открытых маркетов → sync profiles/home_profiles/tag_profiles
uv run poly-house sync-holder

# устаревшие profiles (Polymarket API, synced_at > 24h)
uv run poly-house sync-profile

# устаревшие home_profiles (activity cache → activities, synced_at > 6h)
uv run poly-house sync-home-profile

# tag profiles holders (activities × market tags) → коллекция tag_profiles
uv run poly-house update-tag-profile

# один пользователь: Gateway /gamma/public-profile → users + profiles/home_profiles/tag_profiles
uv run poly-house add-user 0x...

# перезагрузка activity для wallet+event (delete + full fetch по event из Gateway)
uv run poly-house sync-activity --wallet 0x... --event some-event-slug

# диагностика: wallet+event_slug с >= 3500 activities (TSV + total)
uv run poly-house data-fix
uv run poly-house data-fix --apply   # удалить activities (пары) + activity_caches (кошельки)

# бесконечный цикл (остановка — Ctrl+C); sync-event — SYNC_EVENT_WATCH_INTERVAL
uv run poly-house sync-event --watch
uv run poly-house sync-event --open --watch
# остальные --watch — WATCH_INTERVAL (sync-kline — SYNC_KLINE_WATCH_INTERVAL, sync-holder — SYNC_HOLDER_WATCH_INTERVAL)
uv run poly-house sync-trade --watch
uv run poly-house sync-kline --watch
uv run poly-house sync-holder --watch

# тестовый прогон: 2 страницы events
uv run poly-house sync-event --max-pages 2
uv run poly-house sync-event --open --max-pages 2

# тестовый прогон: 10 записей или один wallet
uv run poly-house sync-profile --limit 10
uv run poly-house sync-profile --wallet 0x...
uv run poly-house sync-home-profile --limit 10
uv run poly-house sync-home-profile --wallet 0x...
uv run poly-house update-tag-profile --limit 10
uv run poly-house update-tag-profile --wallet 0x...
```

Как это работает:

- **sync-event**: events из Gateway (`GET /gamma/events?order=updatedAt&ascending=false`) до watermark'а (`sync_state.events_watermark`); параметр `closed` не передаётся. Nested `markets` и `tags` (только `event.tags`) пишутся вместе с event; `seriesIds` — из event. После успешного прогона watermark сдвигается на максимальный `event.updated_at`. Фильтры: `EVENTS_TAG_IDS`, `EVENTS_EXCLUDE_TAG_IDS` (API), `EVENTS_NEG_RISK`, `MARKETS_YES_NO_ONLY` (пропуск event, если среди nested markets есть не Yes/No). `--watch`: цикл с интервалом `SYNC_EVENT_WATCH_INTERVAL`.
- **sync-event --open**: то же, с `closed=false` в API; watermark общий — `events_watermark`; `--watch` — тот же `SYNC_EVENT_WATCH_INTERVAL`.
- **add-event**: один event по slug из Gateway `/gamma/events/slug/{slug}` → `tags`, `events`, nested `markets` (тот же `_write_page`, что у sync-event); без watermark и без фильтров `_keep_event`.
- Первый прогон загружает events, обновлявшиеся за последние `UPDATED_IN_DAYS` дней (по умолчанию 30).
- **sync-trade**: берёт из базы маркеты с `synced_at` позже собственного watermark'а (`sync_state.trades_watermark`; первый прогон — все маркеты) и качает их трейды через Gateway `/data/trades?takerOnly=true` по watermark `timestamp` для каждого маркета (потолок API: `limit=1000`, `offset<=3000`). Пользователи извлекаются из трейдов. После успешного прогона watermark сдвигается на максимальный `synced_at` обработанных маркетов.
- **sync-kline**: для open markets (`active`, `closed=false`, `accepting_orders`, непустые `clob_token_ids`) — Gateway orderbook → upsert hourly bid/ask в `klines`. `--watch`: цикл с интервалом `SYNC_KLINE_WATCH_INTERVAL`.
- **sync-holder**: для открытых маркетов (`closed=false`, `accepting_orders=true`, оба `clob_token_ids`) — Gateway `/data/holders`; для публичных holders (`displayUsernamePublic=true`) inline sync: `sync_profiles_for_wallets` (stale > `PROFILE_SYNC_INTERVAL_DAYS`), `sync_home_profiles_for_wallets` (stale > `HOME_PROFILE_SYNC_INTERVAL_HOURS`: activity cache → activities → upsert); `tag_profiles` обновляются только после home sync. Маркет пропускается, если `/data/holders` недоступен. Нужны `sync-event` и закрытые маркеты с тегами для `tag_profiles`. `--watch`: цикл с интервалом `SYNC_HOLDER_WATCH_INTERVAL`.
- **sync-profile**: для wallet'ов с устаревшим `profiles.synced_at` (> 24h) — Gateway `/data/leaderboard`, `/data/accounting/snapshot`, `/pnl/user-pnl` → upsert `profiles`. Без activity cache / activities. Первичное создание — inline при `sync-holder`.
- **sync-home-profile**: для wallet'ов с устаревшим `home_profiles.synced_at` (> 6h) — activity cache из Gateway `/data/closed-positions` в `activity_caches` (ключ `proxy_wallet` + `event_slug`); activity по pending cache entries через `/data/activity?eventId=...` (только `event_slug` из `events`) в `activities`; upsert `home_profiles` (метрики только по activities с `condition_id` из `markets` с `closed=true`). Первичное создание — inline при `sync-holder`.
- **update-tag-profile**: для каждого wallet из `profiles` — activities из `activities`, у которых `condition_id` есть в `markets`; агрегирует в `market_activities` (по `condition_id`: `volume` = sum `usdc_size`, `pnl` = signed sum TRADE BUY/SELL); по `tag_ids` маркета группирует метрики и upsert в `tag_profiles` (уникальность `(proxy_wallet, tag_id)`, `_id` — MongoDB ObjectId). Поля: `markets_count`, `pnl` (sum per-market `pnl`), `volume` (sum per-market `volume`), `win_rate` (доля market_activities с `pnl > 0`), `pnl_growth` / `pnl_stability` (линейная регрессия по per-market `pnl` с `use_cumulative=True`; `null` при одном маркете). Маркет с несколькими тегами учитывается в каждом теге. Нужны `sync-event`, `profiles` и `activities`.
- **data-fix**: aggregate по `activities` — группы `(proxy_wallet, event_slug)` с count >= 3500; stdout TSV `count`, `proxy_wallet`, `event_slug`, последняя строка `total`. С `--apply`: удаление activities по найденным парам и всех `activity_caches` для затронутых wallets.
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
| `GET /markets/deadline-soon` | `tag_ids`, `neg_risk`, `q`, `future_only`; sort `deadline` ASC; `items` + `total` |
| `GET /markets/{id}` | — |
| `GET /tags` | `slug` |
| `GET /tags/{id}` | — |
| `GET /users` | пагинация `limit`, `offset` |
| `GET /users/{proxy_wallet}` | — |
| `GET /profile` | пагинация `limit`, `offset` |
| `GET /profile/{proxy_wallet}` | — |
| `POST /profile/batch` | body: `{ "proxy_wallets": [...] }` |
| `GET /home-profile` | пагинация `limit`, `offset` |
| `GET /home-profile/{proxy_wallet}` | — |
| `POST /home-profile/batch` | body: `{ "proxy_wallets": [...] }` |
| `GET /tag-profile` | `tag_id` (required, list), пагинация `limit`, `offset`; агрегат по весам `1/tags.markets_count` |
| `GET /tag-profile/{proxy_wallet}` | `tag_id` (required, list) |
| `POST /tag-profile/batch` | body: `{ "proxy_wallets": [...], "tag_ids": [...] }` |
| `GET /profile-summary/{proxy_wallet}` | `tag_id` (required, list); `pnl`, `total_balance` из `profiles`, `tag_pnl` — агрегат по `tag_ids` |
| `POST /profile-summary/batch` | body: `{ "proxy_wallets": [...], "tag_ids": [...] }` |
| `POST /predict/by-holder` | body: `{ "holders": [{ "proxy_wallet", "outcome_index", "amount" }], "tag_ids": [...] }` → `predictions: [{ "type": "MARKET_VOTE", "predict_prices": [p0, p1] }]` |
| `GET /trades` | `condition_id`, `outcome_index`, `user`, `side`, `from`, `to` |
| `GET /trades/take-prices` | `condition_id`, `outcome_index`, `side` |
| `GET /trades/stop-prices` | `condition_id`, `outcome_index`, `side` |
| `GET /trades/mid-prices` | `condition_id` |
| `GET /trades/to-outcome` | `target_outcome_index` (default 0), `condition_id`, `user`, `side`, `from`, `to` |
| `GET /activity` | `condition_id`, `outcome_index`, `user`, `type`, `side`, `from`, `to` |
| `GET /activity/by-market` | `user` (required), `condition_id` |

Все списки пагинируются через `limit` (default 50, max 500) и `offset`.

## Конфигурация

Все настройки — через `.env` (см. `.env.example`): подключение к MongoDB, интервал watch-режима, глубина первой загрузки, лимиты rate limiter'а и размеры страниц API.
