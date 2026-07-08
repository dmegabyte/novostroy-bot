# MCP novostroym — структура базы и как использовать

Источник: структура MCP/БД `novostroym`, переданная пользователем 2026-07-03.

База данных: `novostroym`  
Назначение: каталог новостроек Москвы и Московской области — ЖК, корпуса, квартиры, застройщики, ипотека, ЕГРН.

---

## 1. Основные сущности

### `novos` — жилые комплексы

Главная таблица ЖК.

Ключевые поля:

- `id` — ID ЖК.
- `name` — название ЖК.
- `alias` — URL-slug.
- `type_object` — тип объекта: `novos`, `cottage`, etc.
- `detail_info` — описание ЖК.
- `district` — регион: `mo`, `msk`, `newmsk`.
- `location_id` → `location_2.id` — район.
- `street` — улица.
- `new_building_class` — класс: бизнес, комфорт, эконом, премиум и т.д.
- `building_type` — тип дома.
- `rooms` — доступные комнатности: `1`, `2`, `3`, `4`, `n`, `s`.
- `min_price`, `max_price` — диапазон цен.
- `price1`, `price2`, `price3`, `price4`, `price_s`, `price_n` — минимальные цены по комнатности.
- `price_square` — цена за квадратный метр.
- `comment_price1..comment_price_s` → `company.id` — компания-продавец по комнатности.
- `square_min`, `square_max` — диапазон площадей.
- `floors_total` — этажность.
- `delivered`, `built_year`, `ready_quarter` — сдача.
- `lat`, `long` — координаты.
- `distance_from_mkad` — расстояние от МКАД, км.
- `rating`, `count_ads` — рейтинг и количество объявлений.
- `object_site` — официальный сайт.
- `developer_description` — описание застройщика.
- `highway_name` — ближайшее шоссе.
- `state` — состояние, `2` = активный.

Удобства и благоустройство:

- `ipoteka`, `fz214`, `parking`, `elevator`, `concierge`, `garage`.
- `balcony`, `loggia`.
- `territory` — благоустроенная территория.
- `security` — охрана.
- `yard_without_cars` — двор без машин.
- `children_ground` — детская площадка.
- `sports_ground` — спортивная площадка.
- `heating_type`.
- `conditioning_type`.
- `finishing` — есть отделка.
- `apartments` — апартаменты.
- `taunhouse` — таунхаус.

Долгострой:

- `unfinished`.
- `unfinished_desc`.
- `unfinished_source_link`.
- `unfinished_source_title`.

Разрешительная документация:

- `project_declaration`.
- `construction_permit_start`, `construction_permit_end`.
- `construction_permit_number`, `construction_permit_active`.
- `exploitation_date`, `exploitation_number`.

### `novos_add` — дополнительная информация о ЖК

- `novos_id` → `novos.id`.
- `site_url` — прямой URL сайта ЖК.
- `utility_fee` — коммунальные платежи.
- `park_near`, `water_near` — рядом парк / водоём.
- `trade_in`.
- `is_investment` — инвестиционный признак: `0` нет, `1` программный, `2` ручной.
- `school`, `kindergarten` — школа / детсад.
- `priority_company_listing` → `company.id`.
- `ddu_escrow` — эскроу.
- `ads_type_list` — типы лотов: `flat`, `parking`, `storage`, `commerce`.
- `commerce_class`.
- `total_area` — общая площадь проекта.

### `company` — компании

Застройщики, продавцы, агентства.

- `id`, `name`, `alias`.
- `detail_text` — описание компании.
- `address`.
- `phone`, `adv_phone`.
- `f_year` — дата основания.
- `developer`, `seller`.
- `blacklisted` — компания в чёрном списке.
- `site`, `email`.
- `dealtype` — типы деятельности.
- `count_novos`, `count_ads`, `count_comments`.
- `chart_overdue` — флаг просрочек.
- `status`.
- `is_system` — системообразующий застройщик.
- `special_rate`.
- `state` — `2` = активная.

### `house` — корпуса в ЖК

- `id`, `name`, `relations`.
- `novos_id` → `novos.id`.
- `location_id`, `district`, `street`.
- `lat`, `long`.
- `built_year`, `ready_quarter`, `delivered`.
- `building_type`.
- `ceiling_height`, `ceiling_height_max`.
- `floors_total_min`, `floors_total_max`.
- `stage` — стадия строительства: `pit`, `lower_floor`, `middle_floor`, `high_floor`, `facade_decoration`, `facade`, `gk`, `done`, `reconstruction`.
- `finishing_list` — `no`, `for_final`, `final`, `white_box`, `designer`.
- `rooms`.
- `min_price`, `max_price`, `price1..price4`, `price_s`, `price_n`.
- `entrance_count`.
- `lift`, `lift_service`.
- `furniture`.
- `state`.

### `ads` — объявления / квартиры в продаже

- `id`, `title`.
- `novos_id` → `novos.id`.
- `house_id` → `house.id`.
- `district`.
- `area`, `living_space`, `square_kitchen`.
- `rooms`, `floor`, `floors_total`.
- `price` — цена за м².
- `fullprice` — полная цена.
- `renovation` — отделка: евро, дизайнерский, черновая, с отделкой, white box, нет, etc.
- `balcony`, `bathroom_unit`, `ceiling_height`, `window_view`, `floor_covering`.
- `mortgage`.
- `seller` → `company.id`.
- `seller_name`, `seller_category`, `organization`.
- `status` — `1` бронь, `2` в продаже.
- `state` — `2` активное.
- `apart` — `0` квартира, `1` апартаменты.
- `new_flat` — `1` первичка, `2` вторичка.
- `section`, `number`.

### `ads_add` — дополнительная информация к объявлению

- `ads_id` → `ads.id`.
- `agent_name`, `agent_phone`, `agent_category`, `agent_organization`.
- `image_link`, `feed_images`.
- `cadastral_number`, `lot_number`.
- `smart_plan`, `corner_window`, `laundry_room`, `master_bedroom`, `dressing_room`.
- `stat_price` — история изменения цен.

### `apartment_types` — типы планировок

- `id`, `name`.
- `build_id` → `house.id`.
- `area`, `kitchen_area`, `living_space`.
- `rooms`.
- `studio`, `euro`, `penthouse`.
- `loggia`, `balcony`, `terrace`, `wardrobe`, `bathroom_in_bedroom`.
- `two_level`, `three_level`.
- `fireplace`, `laundry`, `split_bedrooms`.
- `status`.

---

## 2. Справочники

### `location_2` — районы

- `id`, `name`, `alias`, `level`, `group`, `ecology_rating`.

### `metro` — станции метро

- `id`, `metro_line_id` → `metro_line.id`, `name`, `alias`, `lat`, `long`, `state`.

### `metro_line` — линии метро

- `id`, `name`, `color`, `color_name`.

### `highway` — шоссе

- `id`, `name`, `alias`, `direction`.

### `railway` — железнодорожные станции

- `id`, `name`, `alias`, `mkjd`, `mdjd`, `distance`.

---

## 3. Связи и свойства EAV

### `property_type` — реестр типов свойств

- `id`, `name`, `code`, `source`.

### `property_values` — значения свойств

- `prop_type_id` → `property_type.id`.
- `value`.
- `source_id` — ID объекта.
- `other_value`.

### `property_metro` — привязка к метро

- `source_id` — ID ЖК/корпуса.
- `value` → `metro.id`.
- `on_foot`, `on_transport`, `by_car` — минуты.

### `property_parking` — парковки

- `source_id`, `volume`, `building_access`, `min_price`.

### `property_railway` — привязка к ЖД

- `source_id`.
- `value` → `railway.id`.
- `on_foot`, `on_transport`, `by_car`.

---

## 4. Ипотека и финансы

### `mortgage` — ипотечные программы

- `id`, `bank_id`, `name`.
- `year_percent` — ставка.
- `min_fee` — минимальный взнос.
- `credit_month` — максимальный срок.
- `max_sum`, `detail_text`, `state`, `is_special`.

### `mortgage_calc` — ипотека по ЖК

- `novos_id` → `novos.id`.
- `mortgage_id`, `bank_id`, `bank_name`.
- `min_percent`, `min_fee`, `credit_month`.
- `min_price`, `max_price`, `rooms`, `house_name`.
- `state` — `2` активная.

### `discount` — скидки и акции

- `id`, `title`, `detail_text`.
- `seller_id` → `company.id`.
- `publish`, `unpublish`.
- `state`, `sale_percent`, `mortgage_special`.

### `payment_by_installments` — рассрочка

- `company_id` → `company.id`.
- `name`, `min_first`, `month`, `price`, `detail_text`.

---

## 5. ЕГРН

### `egrn` — периоды данных ЕГРН

- `id`, `name`, `status`, `region`, `last_sale`.

### `egrn_contracts` — сделки ЕГРН

Таблица большая: около 105 млн записей. Для неё нужны только агрегаты или очень точечные запросы.

- `id`, `egrn_id` → `egrn.id`.
- `novos_id` → `novos.id`.
- `house_id` → `house.id`.
- `first_sell_date`, `date`, `ddu_date` — Unix timestamp.
- `ddu_number`, `description`.
- `organization`.
- `mortgage`.
- `obj_type`, `obj_rooms`, `obj_floor`, `obj_building`, `obj_number`, `obj_area`, `obj_place`.
- `obj_cession` — уступка.
- `legal_entity` — юрлицо.
- `obj_category`, `id_dom`.

### `egrn_top_novos` — рейтинг продаж по ЕГРН

- `novos_id`, `egrn_id`.
- `sales`, `mortgages`, `position`, `build_class`.
- `prev_sales`, `prev_mortgages`, `prev_position`.
- `all` — все сделки.

### `egrn_banks` — банки ЕГРН

- `bank_id`, `name`, `status`.

---

## 6. Прочее

### `counter_novos` — счётчики ЖК

- `novos_id`.
- `count_ads`, `count_panoram`, `count_camera`, `count_comments`, `count_discounts`, `count_commerce`.

### `news` — новости

- `id`, `title`, `detail_text`, `publish`, `type`, `state`.

---

## 7. Ключевые связи

- `novos.comment_price1..comment_price_s` → `company.id` — компании, продающие в ЖК.
- `novos_add.priority_company_listing` → `company.id` — приоритетный застройщик.
- `ads.seller` → `company.id` — продавец квартиры.
- `discount.seller_id` → `company.id` — акции от компании.
- `payment_by_installments.company_id` → `company.id` — рассрочка от компании.
- `novos.location_id` → `location_2.id` — район.
- `house.novos_id` → `novos.id` — корпуса ЖК.
- `ads.novos_id` → `novos.id`, `ads.house_id` → `house.id` — квартиры.
- `apartment_types.build_id` → `house.id` — планировки корпуса.
- `property_metro.source_id` → `novos.id`, `property_metro.value` → `metro.id` — метро.
- `egrn_contracts.novos_id` → `novos.id` — сделки ЕГРН.
- `egrn_top_novos.novos_id` → `novos.id` — рейтинг продаж.

---

## 8. SQL tool contract

Инструмент: `sql_query`.

Назначение: выполняет `SELECT`-запрос к базе каталога новостроек.

Правила безопасности:

- Разрешены только `SELECT`.
- Всегда использовать `LIMIT`, рекомендуемо до `500`.
- Для больших таблиц использовать агрегацию: `COUNT`, `AVG`, `SUM`, `MIN`, `MAX`.
- Для кириллицы и спецсимволов использовать `query_base64`, если инструмент это поддерживает.
- В `egrn_contracts` даты лежат как Unix timestamp, для читаемого вида использовать `FROM_UNIXTIME()`.

---

## 9. Базовые SQL-шаблоны

### 9.1. ЖК с ценами и районом

```sql
SELECT n.id, n.name, n.district, n.new_building_class, n.min_price, n.max_price,
       n.price_square, l.name AS location, n.floors_total
FROM novos n
LEFT JOIN location_2 l ON n.location_id = l.id
WHERE n.state = 2
ORDER BY n.rating DESC
LIMIT 20;
```

### 9.2. Застройщик ЖК через `comment_price`

```sql
SELECT DISTINCT c.id, c.name, c.developer, c.blacklisted, c.f_year, c.site,
       c.count_novos, c.is_system, c.status
FROM company c
WHERE c.id IN (
  SELECT comment_price1 FROM novos WHERE id = <ID> AND comment_price1 IS NOT NULL
  UNION SELECT comment_price2 FROM novos WHERE id = <ID> AND comment_price2 IS NOT NULL
);
```

### 9.3. Все ЖК застройщика

```sql
SELECT n.id, n.name, n.district, n.min_price, n.max_price, n.delivered
FROM novos n
WHERE n.state = 2 AND (
  n.comment_price1 = <company_id> OR n.comment_price2 = <company_id>
  OR n.comment_price3 = <company_id> OR n.comment_price4 = <company_id>
)
ORDER BY n.name;
```

### 9.4. Квартиры в продаже в ЖК

```sql
SELECT a.rooms, a.area, a.floor, a.price, a.fullprice, a.renovation, a.status
FROM ads a
WHERE a.novos_id = <ID> AND a.state = 2 AND a.status = 2
ORDER BY a.price
LIMIT 50;
```

### 9.5. Ближайшее метро ЖК

```sql
SELECT m.name, ml.name AS line, ml.color, pm.on_foot, pm.on_transport
FROM property_metro pm
JOIN metro m ON pm.value = m.id
JOIN metro_line ml ON m.metro_line_id = ml.id
WHERE pm.source_id = <novos_id>
ORDER BY COALESCE(pm.on_foot, 999)
LIMIT 10;
```

### 9.6. Статистика продаж ЕГРН

```sql
SELECT et.sales, et.mortgages, et.position, et.build_class, e.name AS period
FROM egrn_top_novos et
JOIN egrn e ON et.egrn_id = e.id
WHERE et.novos_id = <ID>
ORDER BY et.egrn_id DESC
LIMIT 5;
```

### 9.7. Агрегат сделок ЕГРН по ЖК

```sql
SELECT COUNT(*) AS total,
       SUM(mortgage) AS with_mortgage,
       SUM(obj_cession) AS cessions,
       SUM(legal_entity) AS legal_entities,
       MAX(FROM_UNIXTIME(date)) AS last_deal
FROM egrn_contracts
WHERE novos_id = <ID>;
```

### 9.8. Поиск ЖК по метро

```sql
SELECT n.id, n.name, l.name AS location
FROM novos n
LEFT JOIN location_2 l ON n.location_id = l.id
WHERE n.state = 2 AND EXISTS (
  SELECT 1
  FROM property_metro pm
  JOIN metro m ON pm.value = m.id
  WHERE pm.source_id = n.id AND m.name LIKE '%Фили%'
)
LIMIT 20;
```

### 9.9. Типы планировок корпуса

```sql
SELECT at.name, at.rooms, at.area, at.kitchen_area, at.living_space,
       at.studio, at.euro, at.penthouse, at.two_level, at.fireplace
FROM apartment_types at
WHERE at.build_id = <house_id> AND at.status = 'active'
LIMIT 100;
```

### 9.10. Компании в чёрном списке

```sql
SELECT id, name, detail_text, f_year, count_novos
FROM company
WHERE blacklisted = 1 AND state = 2
LIMIT 100;
```

---

## 10. Как использовать это в nmbot / Ирине

### 10.1. Сценарий family → search profile

Для family-запроса MCP-search должен вернуть компактную карточку ЖК и отдельный блок `family_infrastructure`. В карточке держим только подтверждённые поля: `id`, `name`, `location`, `district`, `price_range`, `rooms`, `area`, `finishing`, `ready`, `metro`, `developer`, `link`, плюс семейные факты из MCP: `novos_add.school`, `novos_add.kindergarten`, `novos_add.park_near`, `novos_add.water_near`, `novos.yard_without_cars`, `novos.children_ground`, `novos.sports_ground`, `novos.security`, `property_metro`, `location_2.ecology_rating`.

### 10.2. Что важно для family

Если нужных family-полей нет — не выдумывать. Если есть — chat-фаза сама выберет 1-2 самых сильных факта и превратит их в живую причину выбора ЖК.

### 10.3. Финансовый запрос

Если клиент спрашивает про ипотеку, платёж, ставку, рассрочку, скидки:

- `mortgage_calc` по `novos_id`.
- `mortgage` для деталей программы.
- `discount` по продавцу / застройщику.
- `payment_by_installments` по компании.
- цены из `ads` и `novos`.

Ответ должен говорить только проверенные числа: ставка, первый взнос, срок, цена, если они пришли из MCP.

Ипотека — это facet, а не отдельный взаимоисключающий сценарий. Если клиент одновременно говорит «для семьи» и «семейная ипотека», основной сценарий остаётся `family`, а финансовый слой добавляет к MCP-запросу:

- `facets:["mortgage"]`.
- `mortgage_type: family_mortgage | it_mortgage | subsidized_mortgage`, если тип понятен из текста.
- `need`: `mortgage_calc`, `mortgage`, `discount`, `payment_by_installments`, `price`.

Пример: `family + mortgage` должен вернуть и семейные факты (`schools`, `kindergartens`, `parks`, `yard_without_cars`), и finance-блок, если MCP его знает. Если finance-блок пустой — не писать ставку / взнос / программу как факт.

### 10.4. Запрос “расскажи подробнее про ЖК”

Для выбранного ЖК нужно собрать mini-dossier:

- `novos` — базовая карточка.
- `novos_add` — доп. свойства.
- `company` — застройщик / продавец.
- `property_metro` + `metro` + `metro_line` — транспорт.
- `house` — корпуса и сроки.
- `ads` — реальные квартиры в продаже.
- `mortgage_calc` — ипотека.
- `egrn_top_novos` — продажи, если нужен аргумент спроса.

### 10.5. Запрос конкретной квартиры

Если клиент просит “однушка до 8 млн”, “двушка с отделкой”, “студия у метро”:

- фильтровать `ads.state = 2` и `ads.status = 2`.
- `ads.rooms`, `ads.fullprice`, `ads.area`, `ads.renovation`.
- подтягивать `novos.name`, `novos.location_id`, `novos.finishing`, `novos.ready`.
- для корпуса — `house.ready_quarter`, `house.built_year`, `house.finishing_list`.

### 10.6. Инвестиционный запрос

Если клиент говорит “для инвестиций”, “ликвидность”, “спрос”, “перепродажа”:

- `novos_add.is_investment`.
- `egrn_top_novos.sales`, `position`, `mortgages`.
- `counter_novos.count_ads`, `count_discounts`.
- динамику цен можно использовать только если есть надёжное поле вроде `ads_add.stat_price`; лучше аккуратно и без обещаний доходности.

Рекомендуемый инвестиционный search-profile:

- сначала собрать базовую карточку ЖК по `novos` + `novos_add`;
- если MCP позволяет, собрать 2–3 ЖК, а не один, чтобы был нормальный shortlist для сравнения входа;
- затем добавить `mortgage_calc` / `mortgage` / `discount`, если клиент смотрит на вход;
- затем добавить `egrn_top_novos` и `counter_novos`, если нужен сигнал спроса;
- если нужно показать не только ЖК, но и конкретные варианты входа, дополнительно взять `ads` и `apartment_types`;
- если клиент не уточнил формат, приоритетно показывать компактные лоты: студию, однушку или евро-формат, если они есть в MCP;
- не обещать аренду / доходность / окупаемость — только подтверждённые сигналы по входу, спросу, сроку и локации.

### 10.6.1. Запрос под сдачу в аренду

Если клиент говорит “под сдачу в аренду”, “для аренды”, “арендный вариант”:

- использовать тот же базовый search-profile, что и для инвестиции, но смотреть на объект как на арендо-пригодный вход;
- приоритетно собирать `novos` + `novos_add`, `ads`, `apartment_types`, `counter_novos`, `egrn_top_novos`, `property_metro` / `metro`;
- если клиент не уточнил формат, сначала показывать студию, однушку или евро-формат, если они есть в MCP;
- делать акцент на компактности, отделке, метро, быстрой готовности, районе и подтверждённом спросе;
- не называть точную ставку аренды и не обещать доходность / окупаемость.

### 10.7. Антигаллюцинации

Если MCP не вернул конкретное поле, Ирина не говорит это как факт.

Примеры:

- Нет `school/kindergarten` → нельзя писать “рядом школа и сад”.
- Нет `yard_without_cars` → нельзя писать “безопасный двор без машин”.
- Нет `mortgage_calc.min_percent` → нельзя писать ставку.
- Нет `ads.fullprice` → нельзя называть цену конкретной квартиры.
- Нет `egrn_top_novos` → нельзя утверждать “хорошо продаётся”.

---

## 11. Рекомендуемый формат карточки для search-фазы

```json
{
  "facts": [
    {
      "id": 123,
      "name": "ЖК ...",
      "location": "...",
      "district": "msk|mo|newmsk",
      "price_range": "...",
      "rooms": "...",
      "area": "...",
      "finishing": "...",
      "ready": "...",
      "metro": "...",
      "developer": "...",
      "family_infrastructure": {
        "school": true,
        "kindergarten": true,
        "park_near": true,
        "yard_without_cars": true,
        "children_ground": true
      },
      "finance": {
        "mortgage_min_percent": null,
        "min_fee": null,
        "installment": null,
        "discount": null
      },
      "sales_signal": {
        "egrn_sales": null,
        "egrn_position": null
      },
      "link": "..."
    }
  ],
  "near": [],
  "missing": [],
  "params": {
    "rooms": null,
    "max_price": null,
    "district": null,
    "purpose": "family|investment|own_living|null"
  }
}
```

---

## 12. Практический вывод

Эта схема нужна не для того, чтобы Ирина сама писала SQL. Она нужна, чтобы:

1. Правильно формулировать MCP/search-запрос.
2. Знать, какие поля надо требовать от `get_flat_info`.
3. Не терять полезные MCP-факты между search-фазой и chat-фазой.
4. Делать семейные, финансовые, инвестиционные и “подробнее про ЖК” ответы богаче без выдумок.
5. Сохранять жёсткий контракт: нет поля в MCP/search → нет факта в ответе.
