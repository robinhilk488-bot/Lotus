"""Каталог встроенных плагинов.

Плагин из каталога, для которого ещё нет файла в plugins/, показывается в приложении
как «В разработке» и не может быть включён. Как только появляется plugins/<id>.py,
вместо заглушки работает настоящий плагин.
"""

CATEGORIES = [
    "Основное",
    "Выдача через поставщиков",
    "Аренда аккаунтов",
    "Telegram",
    "Steam",
    "Лоты",
    "Покупатели",
    "Уведомления",
]

FUNPAY_CHAT = "отправка сообщений покупателю на FunPay"
FUNPAY_LOTS = "управление лотами FunPay"


def api(service):
    return f"документация API и ключ {service}"


CATALOG = [
    # id, название, описание, категория, что нужно для запуска
    ("autodelivery", "Автовыдача", "Отправляет покупателю товар сразу после оплаты: текст или уникальную строку из списка.",
     "Основное", [FUNPAY_CHAT]),
    ("big_orders", "Крупные заказы", "Уведомляет о заказах на крупную сумму, чтобы не пропустить важное.",
     "Уведомления", [FUNPAY_CHAT]),
    ("approute", "AppRoute Reseller", "Покупает товар или пополнение в AppRoute после оплаты и пишет покупателю статус.",
     "Выдача через поставщиков", [FUNPAY_CHAT, api("AppRoute")]),
    ("lzt_market", "Lolz Market", "Покупка и автовыдача аккаунтов всех категорий через LZT Market.",
     "Выдача через поставщиков", [FUNPAY_CHAT, api("LZT Market")]),
    ("auto_tt", "Auto TT", "Покупка и автовыдача TikTok-аккаунтов через LZT Market.",
     "Выдача через поставщиков", [FUNPAY_CHAT, api("LZT Market")]),
    ("auto_ai_accounts", "AutoAIAccounts", "Покупка и выдача AI-аккаунтов через LZT Market.",
     "Выдача через поставщиков", [FUNPAY_CHAT, api("LZT Market")]),
    ("lis_skins", "LIS-SKINS Market", "Выдача скинов CS2, Dota 2 и Rust через LIS-SKINS по привязке лот=скин.",
     "Выдача через поставщиков", [FUNPAY_CHAT, api("LIS-SKINS")]),
    ("auto_steam", "AutoSteam", "Пополнение баланса Steam по логину через ns.gifts.",
     "Выдача через поставщиков", [FUNPAY_CHAT, api("ns.gifts")]),
    ("auto_applecard", "AutoAppleCard", "Выдача подарочных карт Apple (TR/US/RU) по номиналу через ns.gifts.",
     "Выдача через поставщиков", [FUNPAY_CHAT, api("ns.gifts")]),
    ("nervixy", "Nervixy", "Пополнение Steam по логину: сумма из количества в заказе, с подтверждением покупателя.",
     "Выдача через поставщиков", [FUNPAY_CHAT, api("Nervixy")]),
    ("auto_discord_boost", "AutoDiscordBoost", "Discord boost по заказам.",
     "Выдача через поставщиков", [FUNPAY_CHAT, api("поставщика бустов")]),

    ("rent_steam", "RentSteam", "Аренда Steam-аккаунтов: выдача, отсчёт срока, смена пароля по окончании.",
     "Аренда аккаунтов", [FUNPAY_CHAT]),
    ("dota_rent", "Dota 2 Rental", "Аренда Dota-аккаунтов: выдача по времени, Steam Guard по команде, ранг и MMR в названии.",
     "Аренда аккаунтов", [FUNPAY_CHAT]),
    ("roblox_vip", "AutoRobux", "Выдаёт Robux покупателю: он создаёт Game Pass, бот покупает его через Roblox-аккаунты.",
     "Выдача через поставщиков", [FUNPAY_CHAT, "Roblox cookie"]),
    ("kosell_rental", "KoSell Rental", "Аренда Steam-аккаунтов через KoSell.",
     "Аренда аккаунтов", [FUNPAY_CHAT, api("KoSell")]),
    ("valorant_rental", "Valorant Rental", "Выдача и сроки аренды аккаунтов Valorant с ручным подтверждением сброса доступа.",
     "Аренда аккаунтов", [FUNPAY_CHAT]),
    ("nft_gift_rental", "NFT Gift Rental", "Аренда Telegram Gifts и NFT через MarketApp.",
     "Аренда аккаунтов", [FUNPAY_CHAT, api("MarketApp")]),

    ("auto_stars", "AutoStars", "Автовыдача Telegram Stars напрямую через Fragment и TON-кошелёк.",
     "Telegram", [FUNPAY_CHAT, "TON-кошелёк", api("Fragment")]),
    ("auto_premium", "AutoPremium", "Автовыдача Telegram Premium (3/6/12 мес) через Fragment API.",
     "Telegram", [FUNPAY_CHAT, api("fragment-api.com"), "TON-кошелёк"]),
    ("auto_gifts", "AutoGifts", "Выдаёт Telegram-подарки покупателю с вашего Telegram-аккаунта.",
     "Telegram", [FUNPAY_CHAT, "Telegram-аккаунт с запасом Stars"]),
    ("gift_radar", "Gift Radar", "Цены Telegram-подарков на Portals, MRKT и Getgems, расчёт прибыли и покупки на Portals.",
     "Telegram", [api("Portals, MRKT и Getgems")]),

    ("autosmm", "AutoSMM", "Накрутка подписчиков, лайков и просмотров через SMM-панели по заказам.",
     "Выдача через поставщиков", [FUNPAY_CHAT, api("SMM-панели")]),

    ("auto_bonus", "Счётчики продаж", "Дописывает в описание лотов статистику продаж: за день, неделю и всего.",
     "Лоты", [FUNPAY_CHAT]),

    ("offline_activite", "Offline Activite", "Выдаёт покупателю код Steam Guard по команде !guard для настроенных аккаунтов.",
     "Steam", [FUNPAY_CHAT]),
    ("email_code", "EmailCode", "Получает коды подтверждения из почты через IMAP и отправляет покупателю.",
     "Steam", [FUNPAY_CHAT]),

    ("trade_manager", "TradeManager", "Включает и выключает лоты по расписанию.",
     "Лоты", [FUNPAY_LOTS]),
    ("auto_dump", "AutoDump", "Корректирует цены по публичному рынку FunPay.",
     "Лоты", [FUNPAY_LOTS]),
    ("copy_lots", "CopyLots", "Копирует лоты между вашими аккаунтами FunPay.",
     "Лоты", [FUNPAY_LOTS]),

    ("autoresponder", "Автоответчик", "Автоматические ответы на сообщения покупателей по ключевым словам.",
     "Покупатели", [FUNPAY_CHAT]),
    ("ai_assistant", "ИИ-ответы", "ИИ отвечает на лёгкие вопросы покупателей по лоту.",
     "Покупатели", [FUNPAY_CHAT, "ключ OpenAI-совместимого ИИ-API"]),
    ("autoticket", "AutoTicket", "Напоминает покупателям подтвердить просроченные заказы.",
     "Покупатели", [FUNPAY_CHAT]),
]
