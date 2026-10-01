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
    ("approute", "AppRoute Reseller", "Покупает товар или пополнение в AppRoute после оплаты и пишет покупателю статус.",
     "Выдача через поставщиков", [FUNPAY_CHAT, api("AppRoute")]),
    ("lzt_market", "Lolz Market", "Покупка и автовыдача аккаунтов всех категорий через LZT Market.",
     "Выдача через поставщиков", [FUNPAY_CHAT, api("LZT Market")]),
    ("auto_tt", "Auto TT", "Покупка и автовыдача TikTok-аккаунтов через LZT Market.",
     "Выдача через поставщиков", [FUNPAY_CHAT, api("LZT Market")]),
    ("auto_ai_accounts", "AutoAIAccounts", "Покупка и выдача AI-аккаунтов через LZT Market.",
     "Выдача через поставщиков", [FUNPAY_CHAT, api("LZT Market")]),
    ("lis_skins", "LIS-SKINS Market", "Выдача скинов CS2, Dota 2 и Rust через LIS-SKINS по точному market name.",
     "Выдача через поставщиков", [FUNPAY_CHAT, api("LIS-SKINS")]),
    ("auto_steam_ns", "AutoSteamNS", "Пополнение Steam через NSGifts по заказам FunPay.",
     "Выдача через поставщиков", [FUNPAY_CHAT, api("NSGifts")]),
    ("nervixy", "Nervixy", "Пополнение Steam по логину: сумма из количества в заказе, с подтверждением покупателя.",
     "Выдача через поставщиков", [FUNPAY_CHAT, api("Nervixy")]),
    ("auto_smm", "AutoSMM", "Продажа SMM-услуг через подключённые SMM-панели.",
     "Выдача через поставщиков", [FUNPAY_CHAT, api("SMM-панели")]),
    ("auto_robux", "AutoRobux", "Выдача Robux через Roblox Game Pass.",
     "Выдача через поставщиков", [FUNPAY_CHAT, "способ покупки Game Pass (аккаунт Roblox или поставщик)"]),
    ("vip_roblox", "VIP Roblox", "Работа с VIP-серверами Roblox по заказам.",
     "Выдача через поставщиков", [FUNPAY_CHAT, "способ выдачи VIP-серверов"]),
    ("auto_discord_boost", "AutoDiscordBoost", "Discord boost по заказам.",
     "Выдача через поставщиков", [FUNPAY_CHAT, api("поставщика бустов")]),

    ("rent_steam", "RentSteam", "Аренда Steam-аккаунтов: выдача, отсчёт срока, смена пароля по окончании.",
     "Аренда аккаунтов", [FUNPAY_CHAT]),
    ("dota2_rental", "Dota 2 Rental", "Аренда Dota 2 аккаунтов с выдачей логина, пароля и Steam Guard.",
     "Аренда аккаунтов", [FUNPAY_CHAT]),
    ("kosell_rental", "KoSell Rental", "Аренда Steam-аккаунтов через KoSell.",
     "Аренда аккаунтов", [FUNPAY_CHAT, api("KoSell")]),
    ("valorant_rental", "Valorant Rental", "Выдача и сроки аренды аккаунтов Valorant с ручным подтверждением сброса доступа.",
     "Аренда аккаунтов", [FUNPAY_CHAT]),
    ("nft_gift_rental", "NFT Gift Rental", "Аренда Telegram Gifts и NFT через MarketApp.",
     "Аренда аккаунтов", [FUNPAY_CHAT, api("MarketApp")]),

    ("auto_stars", "AutoStars", "Автовыдача Telegram Stars по заказам.",
     "Telegram", [FUNPAY_CHAT, api("сервиса покупки Stars (например, Fragment)")]),
    ("auto_gift", "AutoGift", "Автоматическая отправка Telegram Stars Gifts по заказам.",
     "Telegram", [FUNPAY_CHAT, "Telegram-аккаунт для отправки подарков"]),
    ("gift_radar", "Gift Radar", "Цены Telegram-подарков на Portals, MRKT и Getgems, расчёт прибыли и покупки на Portals.",
     "Telegram", [api("Portals, MRKT и Getgems")]),

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
    ("auto_ticket", "AutoTicket", "Создаёт тикет в поддержку FunPay по просроченным заказам.",
     "Покупатели", ["создание тикетов в поддержке FunPay"]),
]
