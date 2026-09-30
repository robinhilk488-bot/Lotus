"""ШАБЛОН плагина, который покупает товар у поставщика по API.

Файлы с «_» в начале не загружаются. Скопируйте файл под новым именем (например, approute.py),
замените адреса и поля под документацию API поставщика и установите через приложение.

Правила, которые защищают ваши деньги:
1. Если плагин ещё НИЧЕГО не купил и не отправил — raise ctx.Retry("причина"). Сервер повторит позже сам.
2. Если что-то пошло не так ПОСЛЕ покупки — просто дайте ошибке вылететь. Заказ уйдёт в «Нужна проверка»,
   и сервер не будет покупать второй раз без вашего решения.
3. Перед покупкой проверяйте ctx.storage: если по этому заказу уже есть покупка, второй раз не покупать.
4. При ctx.dry_run (пробный запуск) ничего не покупать и не отправлять — только проверить логику.
"""

NAME = "Поставщик (шаблон)"
DESCRIPTION = "Покупает товар у поставщика по API после оплаты заказа на FunPay."
VERSION = "1.0"
TIMEOUT = 90

SETTINGS = [
    {"key": "api_url", "label": "Адрес API", "type": "text", "default": "https://api.example.com",
     "required": True},
    {"key": "api_key", "label": "API-ключ", "type": "secret", "required": True,
     "hint": "Личный кабинет поставщика → API. Ключ хранится на сервере в зашифрованном виде."},
    {"key": "mapping", "label": "Привязка лотов", "type": "textarea", "required": True,
     "hint": "По строке на лот: часть названия лота = id товара у поставщика. Например: Steam 500 = 1201"},
    {"key": "max_price", "label": "Не покупать дороже, $", "type": "number", "default": 50,
     "hint": "Защита от ошибки в привязке или скачка цены у поставщика."},
]


def _headers(ctx):
    return {"Authorization": f"Bearer {ctx.config['api_key']}"}


def _product_for(order, ctx):
    for line in ctx.config["mapping"].splitlines():
        if "=" not in line:
            continue
        phrase, pid = (x.strip() for x in line.split("=", 1))
        if phrase and phrase.lower() in order["description"].lower():
            return pid
    return None


def test(ctx):
    """Кнопка «Проверить подключение»: должна только читать, ничего не покупать."""
    r = ctx.http.get(f"{ctx.config['api_url']}/balance", headers=_headers(ctx))
    if r.status_code == 401:
        raise Exception("Поставщик отклонил API-ключ")
    r.raise_for_status()
    return f"Ключ работает. Баланс: {r.json().get('balance')}"


def on_new_order(order, ctx):
    product = _product_for(order, ctx)
    if not product:
        return ctx.SKIP  # этот лот не наш — ничего не делаем

    done_key = f"purchase:{order['id']}"
    if ctx.storage.get(done_key):
        raise Exception("По этому заказу покупка уже была. Проверьте вручную, чтобы не купить дважды.")

    # 1. Проверяем цену до покупки. Любая ошибка здесь безопасна — ещё ничего не куплено.
    try:
        info = ctx.http.get(f"{ctx.config['api_url']}/products/{product}", headers=_headers(ctx))
    except Exception as e:
        raise ctx.Retry(f"API поставщика недоступно: {e}")
    if info.status_code >= 500:
        raise ctx.Retry(f"API поставщика ответило {info.status_code}")
    info.raise_for_status()
    price = float(info.json()["price"])
    if price > float(ctx.config["max_price"]):
        raise Exception(f"Цена {price} выше лимита {ctx.config['max_price']}. Покупка не сделана.")

    if ctx.dry_run:
        ctx.log(f"Пробный запуск: купил бы товар {product} за {price}")
        return "dry-run"

    # 2. Покупка. Отмечаем попытку ДО запроса: если сервер упадёт посреди покупки,
    #    повторно он не купит, а заказ уйдёт на ручную проверку.
    ctx.storage.set(done_key, {"product": product, "state": "started"})
    r = ctx.http.post(f"{ctx.config['api_url']}/orders", headers=_headers(ctx),
                      json={"product_id": product, "external_id": order["id"]})  # external_id — защита от дублей на стороне поставщика, если API это поддерживает
    r.raise_for_status()
    result = r.json()
    ctx.storage.set(done_key, {"product": product, "state": "bought", "supplier_order": result.get("id")})
    ctx.log(f"Заказ #{order['id']}: куплено у поставщика, заказ {result.get('id')}")
    return f"Поставщик: заказ {result.get('id')}"
