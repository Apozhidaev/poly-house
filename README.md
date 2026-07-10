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
# sync-event — events + nested markets + tags
uv run poly-house sync-event
uv run poly-house sync-event --open
uv run poly-house sync-event --watch                    # период SYNC_EVENT_WATCH_INTERVAL между стартами
uv run poly-house sync-event --open --watch
uv run poly-house sync-event --max-pages 2              # тест: 2 страницы
uv run poly-house sync-event --open --max-pages 2

# refresh-event — один event или все из базы
uv run poly-house refresh-event --slug <slug>
uv run poly-house refresh-event

# sync-trade — трейды + пользователи
uv run poly-house sync-trade
uv run poly-house sync-trade --watch                    # период SYNC_TRADE_WATCH_INTERVAL между стартами

# sync-kline — orderbook open markets → hourly bid/ask klines
uv run poly-house sync-kline
uv run poly-house sync-kline --watch                    # период SYNC_KLINE_WATCH_INTERVAL между стартами

# sync-holder — holders → profiles / home_profiles / tag_profiles
uv run poly-house sync-holder
uv run poly-house sync-holder --watch                   # период SYNC_HOLDER_WATCH_INTERVAL между стартами

# refresh-profile — устаревшие profiles (synced_at > PROFILE_REFRESH_INTERVAL_DAYS)
uv run poly-house refresh-profile
uv run poly-house refresh-profile --limit 10
uv run poly-house refresh-profile --wallet 0x...

# refresh-home-profile — устаревшие home_profiles (synced_at > HOME_PROFILE_REFRESH_INTERVAL_DAYS)
uv run poly-house refresh-home-profile
uv run poly-house refresh-home-profile --limit 10
uv run poly-house refresh-home-profile --wallet 0x...

# update-tag-profile — tag profiles из activities × market tags
uv run poly-house update-tag-profile
uv run poly-house update-tag-profile --limit 10
uv run poly-house update-tag-profile --wallet 0x...

# add-user — один пользователь + profiles / home_profiles / tag_profiles
uv run poly-house add-user 0x...

# sync-activity — перезагрузка activity для wallet+event
uv run poly-house sync-activity --wallet 0x... --event some-event-slug

# data-fix — wallet+event_slug с >= 3500 activities
uv run poly-house data-fix
uv run poly-house data-fix --apply                       # удалить activities + activity_caches
```

Как это работает:

- **sync-event**: events из Gateway (`GET /gamma/events?order=updatedAt&ascending=false`) до watermark'а (`sync_state.events_watermark`); параметр `closed` не передаётся. Nested `markets` и `tags` (только `event.tags`) пишутся вместе с event; `seriesIds` — из event. После успешного прогона watermark сдвигается на максимальный `event.updated_at`. Фильтры: `EVENTS_TAG_IDS`, `EVENTS_EXCLUDE_TAG_IDS` (API), `EVENTS_NEG_RISK`, `MARKETS_YES_NO_ONLY` (пропуск event, если среди nested markets есть не Yes/No). `--watch`: цикл; период между стартами прогонов `SYNC_EVENT_WATCH_INTERVAL` (время работы вычитается из паузы).
- **sync-event --open**: то же, с `closed=false` в API; watermark общий — `events_watermark`; `--watch` — тот же период.
- **refresh-event**: один event по `--slug` или все events из базы — Gateway `/gamma/events/slug/{slug}` → `tags`, `events`, nested `markets` (тот же `_write_page`, что у sync-event); без watermark и без фильтров `_keep_event`. В bulk-режиме ошибки по slug пропускаются с warning в логе.
- Первый прогон загружает events, обновлявшиеся за последние `UPDATED_IN_DAYS` дней (по умолчанию 30).
- **sync-trade**: берёт из базы маркеты с `synced_at` позже собственного watermark'а (`sync_state.trades_watermark`; первый прогон — все маркеты) и качает их трейды через Gateway `/data/trades?takerOnly=true` по watermark `timestamp` для каждого маркета (потолок API: `limit=1000`, `offset<=3000`). Пользователи извлекаются из трейдов. После успешного прогона watermark сдвигается на максимальный `synced_at` обработанных маркетов. `--watch`: цикл; период между стартами `SYNC_TRADE_WATCH_INTERVAL` (время работы вычитается из паузы).
- **sync-kline**: для open markets (`active`, `closed=false`, `accepting_orders`, непустые `clob_token_ids`) — Gateway orderbook → upsert hourly bid/ask в `klines`. `--watch`: период между стартами `SYNC_KLINE_WATCH_INTERVAL`.
- **sync-holder**: для открытых маркетов (`closed=false`, `accepting_orders=true`, оба `clob_token_ids`) — Gateway `/data/holders`; для публичных holders (`displayUsernamePublic=true`) inline sync: `sync_profiles_for_wallets` (stale > `PROFILE_REFRESH_INTERVAL_DAYS`), `sync_home_profiles_for_wallets` (stale > `HOME_PROFILE_REFRESH_INTERVAL_DAYS`: activity cache → activities → upsert); `tag_profiles` обновляются только после home sync. Маркет пропускается, если `/data/holders` недоступен. Нужны `sync-event` и закрытые маркеты с тегами для `tag_profiles`. `--watch`: период между стартами `SYNC_HOLDER_WATCH_INTERVAL`.
- **refresh-profile**: для wallet'ов с устаревшим `profiles.synced_at` (> `PROFILE_REFRESH_INTERVAL_DAYS`) — Gateway `/data/leaderboard`, `/data/accounting/snapshot`, `/pnl/user-pnl` → upsert `profiles`. Без activity cache / activities. Первичное создание — inline при `sync-holder`.
- **refresh-home-profile**: для wallet'ов с устаревшим `home_profiles.synced_at` (> `HOME_PROFILE_REFRESH_INTERVAL_DAYS`) — activity cache из Gateway `/data/closed-positions` в `activity_caches` (ключ `proxy_wallet` + `event_slug`); activity по pending cache entries через `/data/activity?eventId=...` (только `event_slug` из `events`) в `activities`; upsert `home_profiles` (метрики только по activities с `condition_id` из `markets` с `closed=true`). Первичное создание — inline при `sync-holder`.
- **update-tag-profile**: для каждого wallet из `profiles` — activities из `activities`, у которых `condition_id` есть в `markets`; агрегирует в `market_activities` (по `condition_id`: `volume` = sum `usdc_size`, `pnl` = signed sum TRADE BUY/SELL); по `tag_ids` маркета группирует метрики и upsert в `tag_profiles` (уникальность `(proxy_wallet, tag_id)`, `_id` — MongoDB ObjectId). Поля: `markets_count`, `pnl` (sum per-market `pnl`), `volume` (sum per-market `volume`), `win_rate` (доля market_activities с `pnl > 0`), `hit_rate` (`win_holder_count / markets_count`), `error_rate` (`lose_holder_count / markets_count`), `pnl_growth` / `pnl_stability` (линейная регрессия по per-market `pnl` с `use_cumulative=True`; `null` при одном маркете). Маркет с несколькими тегами учитывается в каждом теге. Нужны `sync-event`, `profiles` и `activities`.
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
| `GET /markets` | `closed`, `event_id`, `tag_id`, `slug`, `condition_id`, `resolved`, `temporal` |
| `GET /markets/deadline-soon` | `tag_ids`, `neg_risk`, `q`, `future_only`; sort `deadline` ASC; `items` + `total` |
| `GET /markets/tag-distribution` | `tag_id` (required); co-occurrence тегов по `tag_ids` маркетов; sort `markets_count` DESC; `items` + `total`; пагинация `limit`, `offset` |
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
| `POST /predict/by-holder` | body: `{ "holders": [{ "proxy_wallet", "outcome_index", "amount" }], "tag_ids": [...], "condition_id" }` → `predictions`: `MARKET_VOTE`; при `102486` в `tag_ids` и валидном deadline маркета — также `UKRAINE_MAP` (holder-boost) |
| `GET /experts/{tag_id}` | holder-boost features → users + profiles + home_profiles; sort `importance` DESC; без пагинации |
| `GET /trades` | `condition_id`, `outcome_index`, `user`, `side`, `from`, `to` |
| `GET /trades/take-prices` | `condition_id`, `outcome_index`, `side` |
| `GET /trades/stop-prices` | `condition_id`, `outcome_index`, `side` |
| `GET /trades/mid-prices` | `condition_id` |
| `GET /trades/to-outcome` | `target_outcome_index` (default 0), `condition_id`, `user`, `side`, `from`, `to` |
| `GET /activity` | `condition_id`, `outcome_index`, `user`, `type`, `side`, `from`, `to` |
| `GET /activity/by-market` | `user` (required), `condition_id` |
| `GET /activity/holders` | `condition_id` (required), `profit_only` (default `true`) |
| `GET /activity/holder-distribution` | `condition_id` (required), `proxy_wallet` (required) |

Все списки пагинируются через `limit` (default 50, max 500) и `offset`.

## Конфигурация

Все настройки — через `.env` (см. `.env.example`): подключение к MongoDB, интервал watch-режима, глубина первой загрузки, лимиты rate limiter'а и размеры страниц API.
