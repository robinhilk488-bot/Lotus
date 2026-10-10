// Демо-режим: имитирует ответы воркера, чтобы посмотреть интерфейс без сервера.
const Demo = (() => {
  let seed = 7;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const pick = a => a[Math.floor(rnd() * a.length)];

  const accounts = [
    { id: 1, name: "Основной", username: "NightTrader", user_id: 14089460, avatar: "", balance: 48210.5, currency: "₽", status: "ok", error: null, last_sync: Date.now()/1000-90, proxy: "1.2.3.4:8080:user:pass", proxy_on: true, orders_total: 265, today_orders: 1, today_revenue: 2350 },
    { id: 2, name: "Скины", username: "skinbox_de", user_id: 9910233, avatar: "", balance: 17340, currency: "₽", status: "ok", error: null, last_sync: Date.now()/1000-140, proxy_on: false, orders_total: 177, today_orders: 0, today_revenue: 0 },
    { id: 3, name: "Запасной", username: "reserve_acc", user_id: 7733110, avatar: "", balance: 2105, currency: "₽", status: "error", error: "golden_key недействителен: FunPay не узнал аккаунт", last_sync: Date.now()/1000-3600, proxy_on: false, orders_total: 0, today_orders: 0, today_revenue: 0 },
  ];
  const lots = [
    ["Telegram Stars, 500 шт, быстрая выдача", 740], ["Steam ключ Elden Ring", 1890], ["CS2 AK-47 | Redline (FT)", 2350],
    ["Пополнение Steam 1000 ₽", 1080], ["Discord Nitro 1 месяц", 390], ["Robux 800 через Game Pass", 610],
    ["Аренда аккаунта Dota 2, 24 часа", 250], ["Brawl Stars гемы 360", 520], ["Spotify Premium 3 месяца", 470],
  ];
  const buyers = ["m1ster", "xoxo_kate", "dimon228", "LuckyShot", "nevermore", "artemka", "sonya.k", "Rust_King", "vlad0s", "hamster"];

  const orders = [];
  const now = Date.now() / 1000;
  for (let d = 0; d < 90; d++) {
    const n = Math.max(0, Math.round(3 + rnd() * 6 - d / 30 + (d % 7 === 5 ? 3 : 0)));
    for (let i = 0; i < n; i++) {
      const [desc, price] = pick(lots);
      const acc = rnd() < .62 ? accounts[0] : accounts[1];
      const st = d === 0 && i < 2 ? "paid" : rnd() < .05 ? "refunded" : "closed";
      orders.push({
        id: (Math.floor(rnd() * 0xFFFFFF)).toString(16).toUpperCase().padStart(8, "A"),
        account_id: acc.id, account: acc.name, ts: now - d * 86400 - rnd() * 80000,
        buyer: pick(buyers), description: desc, amount: price, currency: "₽", status: st,
        cost: Math.round(price * (0.45 + rnd() * 0.2)),
      });
    }
  }
  orders.sort((a, b) => b.ts - a.ts);

  // Каталог плагинов (как на сервере: core/catalog.py). Готов пока только «Крупные заказы».
  const plugins = [
    { id: "autodelivery", ready: true, name: "Автовыдача", version: "1.0", category: "Основное", enabled: true, can_test: false, can_dry_run: true,
      description: "Отправляет покупателю товар сразу после оплаты: текст или уникальную строку из списка.",
      settings: [], config: {}, secrets_set: {}, missing: [], tasks: { done: 152, pending: 0, attention: 0 } },
    { id: "rent_steam", ready: true, name: "Аренда Steam", version: "1.0", category: "Аренда аккаунтов", enabled: true, can_test: false, can_dry_run: true,
      description: "Сдаёт Steam-аккаунты в аренду: выдача, коды Guard, продление отдельным лотом, смена пароля после аренды.",
      settings: [{key:"extend_offer_id",label:"ID лота продления на FunPay",type:"text",required:true,hint:"Создайте выключенный лот «Продление», ID из ссылки offer?id=..."},
                 {key:"extend_command",label:"Команда продления",type:"text",default:"!продлить"},
                 {key:"remind_before_min",label:"Напоминать за, минут",type:"number",default:15},
                 {key:"hide_lot_on_rent",label:"Скрывать лот аккаунта на время аренды",type:"bool",default:true,hint:"Если указан ID лота у аккаунта, он прячется на время аренды."},
                 {key:"onlypc_check",label:"Проверка OnlyPC (фото из клуба)",type:"bool",default:false,hint:"После оплаты бот просит фото из компьютерного клуба и ждёт вашего решения."}],
      config: { extend_offer_id: "3910042", extend_command: "!продлить", remind_before_min: 15, hide_lot_on_rent: true, onlypc_check: false }, secrets_set: {}, missing: [], tasks: { done: 64, pending: 0, attention: 0 } },
    { id: "autosmm", ready: true, name: "AutoSMM", version: "1.0", category: "Выдача через поставщиков", enabled: false, can_test: true, can_dry_run: true,
      description: "Продажа SMM-услуг через SMM-панели (TwiBoost и совместимые). Код услуги #1-1234 в конце описания лота.",
      settings: [
        {key:"url1",label:"Поставщик 1 · адрес API",type:"text",default:"https://twiboost.com/api/v2",hint:"Например: https://twiboost.com/api/v2"},
        {key:"key1",label:"Поставщик 1 · API-ключ",type:"secret",hint:"В панели: раздел API. Хранится в зашифрованном виде."},
        {key:"url2",label:"Поставщик 2 · адрес API",type:"text",default:"",hint:"Необязательно. Для кодов #2-1234."},
        {key:"key2",label:"Поставщик 2 · API-ключ",type:"secret",hint:"Необязательно."},
        {key:"max_price",label:"Не заказывать дороже, $ за заказ",type:"number",default:10,hint:"0 — без лимита."},
        {key:"ask_link",label:"Запрос ссылки",type:"textarea",default:"Спасибо за заказ! Пришлите ссылку, куда выполнить накрутку {quantity} шт."},
        {key:"confirm",label:"Подтверждение ссылки",type:"textarea",default:"Проверьте ссылку: {link}\nВсё верно? + если да, − если изменить."},
        {key:"done_text",label:"Накрутка завершена",type:"textarea",default:"Накрутка выполнена полностью, спасибо!"}
      ],
      config: {url1:"https://twiboost.com/api/v2",url2:"",max_price:10,ask_link:"Спасибо за заказ! Пришлите ссылку, куда выполнить накрутку {quantity} шт.",confirm:"Проверьте ссылку: {link}\nВсё верно? + если да, − если изменить.",done_text:"Накрутка выполнена полностью, спасибо!"},
      secrets_set:{key1:false,key2:false}, missing:[], tasks:{done:0,pending:0,attention:0} },
    { id: "autoresponder", ready: true, name: "Автоответчик", version: "1.0", category: "Покупатели", enabled: true, can_test: false, can_dry_run: false,
      description: "Отвечает на сообщения покупателей по ключевым словам. Правила: «слово = ответ», по строке на правило.",
      settings: [
        {key:"rules",label:"Правила",type:"textarea",default:"",hint:"По строке: ключевые слова = ответ. Несколько слов через | . Регистр не важен."},
        {key:"cooldown_min",label:"Не повторять ответ чаще, минут",type:"number",default:10,hint:"Защита от спама."},
        {key:"first_only",label:"Отвечать только на первое совпадение",type:"bool",default:true}
      ],
      config:{rules:"привет | здравствуй = Здравствуйте! Товар выдаётся автоматически после оплаты.\nгарантия | возврат = Гарантия 24 часа, при проблеме вернём деньги.",cooldown_min:10,first_only:true},
      secrets_set:{}, missing:[], tasks:{done:0,pending:0,attention:0} },
    { id: "offline_activite", ready: true, name: "Offline Activite", version: "1.0", category: "Steam", enabled: false, can_test: false, can_dry_run: false,
      description: "Выдаёт покупателю код Steam Guard по команде !guard для настроенных аккаунтов.",
      settings:[{key:"command",label:"Команда для кода",type:"text",default:"!guard"},{key:"only_buyers",label:"Отвечать только покупателям этого аккаунта",type:"bool",default:true},{key:"reply",label:"Текст с кодом",type:"text",default:"Код Steam Guard: {code}"}],
      config:{command:"!guard",only_buyers:true,reply:"Код Steam Guard: {code} (действует ~30 секунд)"}, secrets_set:{}, missing:[], tasks:{done:0,pending:0,attention:0} },
    { id: "email_code", ready: true, name: "EmailCode", version: "1.0", category: "Steam", enabled: false, can_test: true, can_dry_run: false,
      description: "Присылает покупателю код подтверждения из почты (IMAP) по команде !code.",
      settings:[{key:"imap_host",label:"IMAP-сервер",type:"text",default:"imap.gmail.com",hint:"Например: imap.gmail.com, imap.mail.ru"},{key:"email",label:"Почта",type:"text",default:""},{key:"password",label:"Пароль (пароль приложения)",type:"secret",hint:"Создайте пароль приложения в настройках почты."},{key:"command",label:"Команда для кода",type:"text",default:"!code"},{key:"fresh_min",label:"Искать код в письмах за, минут",type:"number",default:10},{key:"only_buyers",label:"Отвечать только покупателям",type:"bool",default:true}],
      config:{imap_host:"imap.gmail.com",email:"",command:"!code",fresh_min:10,only_buyers:true}, secrets_set:{password:false}, missing:[], tasks:{done:0,pending:0,attention:0} },
    { id: "ai_assistant", ready: true, name: "ИИ-ответы", version: "1.0", category: "Покупатели", enabled: false, can_test: true, can_dry_run: true,
      description: "ИИ отвечает на лёгкие вопросы покупателей по лоту. Про оплату и возвраты — передаёт продавцу.",
      settings:[
        {key:"base_url",label:"Адрес API",type:"text",default:"https://api.openai.com/v1",hint:"OpenAI-совместимый API."},
        {key:"api_key",label:"API-ключ",type:"secret",hint:"Ключ провайдера ИИ. Платится по использованию."},
        {key:"model",label:"Модель",type:"text",default:"gpt-4o-mini",hint:"Берите недорогую модель."},
        {key:"faq",label:"Памятка для ИИ (FAQ)",type:"textarea",default:"",hint:"Факты о товарах: сроки, совместимость, активация."},
        {key:"daily_limit",label:"Ответов одному покупателю в день",type:"number",default:5,hint:"0 — без лимита."},
        {key:"stop_words",label:"Стоп-слова (ИИ молчит)",type:"text",default:"возврат, чарджбэк, жалоба",hint:"Через запятую."}
      ],
      config:{base_url:"https://api.openai.com/v1",model:"gpt-4o-mini",faq:"Выдача автоматическая, сразу после оплаты. Гарантия 24 часа. Если товар не пришёл — напишите !help.",daily_limit:5,stop_words:"возврат, чарджбэк, жалоба"},
      secrets_set:{api_key:false}, missing:[], tasks:{done:0,pending:0,attention:0} },
    { id: "big_orders", ready: true, name: "Крупные заказы", version: "1.0", category: "Уведомления", enabled: true, can_test: false, can_dry_run: true,
      description: "Пишет в журнал, когда приходит заказ дороже заданной суммы.",
      settings: [{ key: "threshold", label: "Сумма от, ₽", type: "number", default: 1000 },
                 { key: "note", label: "Пометка в журнале", type: "text", default: "Крупный заказ" }],
      config: { threshold: 1500, note: "Крупный заказ" }, secrets_set: {}, missing: [], tasks: { done: 389, pending: 0, attention: 0 } },
    ...[{"id": "approute", "ready": false, "name": "AppRoute Reseller", "description": "Покупает товар или пополнение в AppRoute после оплаты и пишет покупателю статус.", "category": "Выдача через поставщиков", "needs": ["отправка сообщений покупателю на FunPay", "документация API и ключ AppRoute"], "enabled": false, "settings": [], "config": {}, "secrets_set": {}, "missing": [], "can_test": false, "can_dry_run": false, "tasks": {"done": 0, "attention": 0, "pending": 0}}, {"id": "lzt_market", "ready": false, "name": "Lolz Market", "description": "Покупка и автовыдача аккаунтов всех категорий через LZT Market.", "category": "Выдача через поставщиков", "needs": ["отправка сообщений покупателю на FunPay", "документация API и ключ LZT Market"], "enabled": false, "settings": [], "config": {}, "secrets_set": {}, "missing": [], "can_test": false, "can_dry_run": false, "tasks": {"done": 0, "attention": 0, "pending": 0}}, {"id": "auto_tt", "ready": false, "name": "Auto TT", "description": "Покупка и автовыдача TikTok-аккаунтов через LZT Market.", "category": "Выдача через поставщиков", "needs": ["отправка сообщений покупателю на FunPay", "документация API и ключ LZT Market"], "enabled": false, "settings": [], "config": {}, "secrets_set": {}, "missing": [], "can_test": false, "can_dry_run": false, "tasks": {"done": 0, "attention": 0, "pending": 0}}, {"id": "auto_ai_accounts", "ready": false, "name": "AutoAIAccounts", "description": "Покупка и выдача AI-аккаунтов через LZT Market.", "category": "Выдача через поставщиков", "needs": ["отправка сообщений покупателю на FunPay", "документация API и ключ LZT Market"], "enabled": false, "settings": [], "config": {}, "secrets_set": {}, "missing": [], "can_test": false, "can_dry_run": false, "tasks": {"done": 0, "attention": 0, "pending": 0}}, {"id": "lis_skins", "ready": false, "name": "LIS-SKINS Market", "description": "Выдача скинов CS2, Dota 2 и Rust через LIS-SKINS по точному market name.", "category": "Выдача через поставщиков", "needs": ["отправка сообщений покупателю на FunPay", "документация API и ключ LIS-SKINS"], "enabled": false, "settings": [], "config": {}, "secrets_set": {}, "missing": [], "can_test": false, "can_dry_run": false, "tasks": {"done": 0, "attention": 0, "pending": 0}}, {"id": "auto_steam_ns", "ready": false, "name": "AutoSteamNS", "description": "Пополнение Steam через NSGifts по заказам FunPay.", "category": "Выдача через поставщиков", "needs": ["отправка сообщений покупателю на FunPay", "документация API и ключ NSGifts"], "enabled": false, "settings": [], "config": {}, "secrets_set": {}, "missing": [], "can_test": false, "can_dry_run": false, "tasks": {"done": 0, "attention": 0, "pending": 0}}, {"id": "nervixy", "ready": false, "name": "Nervixy", "description": "Пополнение Steam по логину: сумма из количества в заказе, с подтверждением покупателя.", "category": "Выдача через поставщиков", "needs": ["отправка сообщений покупателю на FunPay", "документация API и ключ Nervixy"], "enabled": false, "settings": [], "config": {}, "secrets_set": {}, "missing": [], "can_test": false, "can_dry_run": false, "tasks": {"done": 0, "attention": 0, "pending": 0}}, {"id": "auto_smm", "ready": false, "name": "AutoSMM", "description": "Продажа SMM-услуг через подключённые SMM-панели.", "category": "Выдача через поставщиков", "needs": ["отправка сообщений покупателю на FunPay", "документация API и ключ SMM-панели"], "enabled": false, "settings": [], "config": {}, "secrets_set": {}, "missing": [], "can_test": false, "can_dry_run": false, "tasks": {"done": 0, "attention": 0, "pending": 0}}, {"id": "auto_robux", "ready": false, "name": "AutoRobux", "description": "Выдача Robux через Roblox Game Pass.", "category": "Выдача через поставщиков", "needs": ["отправка сообщений покупателю на FunPay", "способ покупки Game Pass (аккаунт Roblox или поставщик)"], "enabled": false, "settings": [], "config": {}, "secrets_set": {}, "missing": [], "can_test": false, "can_dry_run": false, "tasks": {"done": 0, "attention": 0, "pending": 0}}, {"id": "vip_roblox", "ready": false, "name": "VIP Roblox", "description": "Работа с VIP-серверами Roblox по заказам.", "category": "Выдача через поставщиков", "needs": ["отправка сообщений покупателю на FunPay", "способ выдачи VIP-серверов"], "enabled": false, "settings": [], "config": {}, "secrets_set": {}, "missing": [], "can_test": false, "can_dry_run": false, "tasks": {"done": 0, "attention": 0, "pending": 0}}, {"id": "auto_discord_boost", "ready": false, "name": "AutoDiscordBoost", "description": "Discord boost по заказам.", "category": "Выдача через поставщиков", "needs": ["отправка сообщений покупателю на FunPay", "документация API и ключ поставщика бустов"], "enabled": false, "settings": [], "config": {}, "secrets_set": {}, "missing": [], "can_test": false, "can_dry_run": false, "tasks": {"done": 0, "attention": 0, "pending": 0}}, {"id": "rent_steam", "ready": false, "name": "RentSteam", "description": "Аренда Steam-аккаунтов: выдача, отсчёт срока, смена пароля по окончании.", "category": "Аренда аккаунтов", "needs": ["отправка сообщений покупателю на FunPay"], "enabled": false, "settings": [], "config": {}, "secrets_set": {}, "missing": [], "can_test": false, "can_dry_run": false, "tasks": {"done": 0, "attention": 0, "pending": 0}}, {"id": "dota2_rental", "ready": false, "name": "Dota 2 Rental", "description": "Аренда Dota 2 аккаунтов с выдачей логина, пароля и Steam Guard.", "category": "Аренда аккаунтов", "needs": ["отправка сообщений покупателю на FunPay"], "enabled": false, "settings": [], "config": {}, "secrets_set": {}, "missing": [], "can_test": false, "can_dry_run": false, "tasks": {"done": 0, "attention": 0, "pending": 0}}, {"id": "kosell_rental", "ready": false, "name": "KoSell Rental", "description": "Аренда Steam-аккаунтов через KoSell.", "category": "Аренда аккаунтов", "needs": ["отправка сообщений покупателю на FunPay", "документация API и ключ KoSell"], "enabled": false, "settings": [], "config": {}, "secrets_set": {}, "missing": [], "can_test": false, "can_dry_run": false, "tasks": {"done": 0, "attention": 0, "pending": 0}}, {"id": "valorant_rental", "ready": false, "name": "Valorant Rental", "description": "Выдача и сроки аренды аккаунтов Valorant с ручным подтверждением сброса доступа.", "category": "Аренда аккаунтов", "needs": ["отправка сообщений покупателю на FunPay"], "enabled": false, "settings": [], "config": {}, "secrets_set": {}, "missing": [], "can_test": false, "can_dry_run": false, "tasks": {"done": 0, "attention": 0, "pending": 0}}, {"id": "nft_gift_rental", "ready": false, "name": "NFT Gift Rental", "description": "Аренда Telegram Gifts и NFT через MarketApp.", "category": "Аренда аккаунтов", "needs": ["отправка сообщений покупателю на FunPay", "документация API и ключ MarketApp"], "enabled": false, "settings": [], "config": {}, "secrets_set": {}, "missing": [], "can_test": false, "can_dry_run": false, "tasks": {"done": 0, "attention": 0, "pending": 0}}, {"id": "auto_stars", "ready": false, "name": "AutoStars", "description": "Автовыдача Telegram Stars по заказам.", "category": "Telegram", "needs": ["отправка сообщений покупателю на FunPay", "документация API и ключ сервиса покупки Stars (например, Fragment)"], "enabled": false, "settings": [], "config": {}, "secrets_set": {}, "missing": [], "can_test": false, "can_dry_run": false, "tasks": {"done": 0, "attention": 0, "pending": 0}}, {"id": "auto_gift", "ready": false, "name": "AutoGift", "description": "Автоматическая отправка Telegram Stars Gifts по заказам.", "category": "Telegram", "needs": ["отправка сообщений покупателю на FunPay", "Telegram-аккаунт для отправки подарков"], "enabled": false, "settings": [], "config": {}, "secrets_set": {}, "missing": [], "can_test": false, "can_dry_run": false, "tasks": {"done": 0, "attention": 0, "pending": 0}}, {"id": "gift_radar", "ready": false, "name": "Gift Radar", "description": "Цены Telegram-подарков на Portals, MRKT и Getgems, расчёт прибыли и покупки на Portals.", "category": "Telegram", "needs": ["документация API и ключ Portals, MRKT и Getgems"], "enabled": false, "settings": [], "config": {}, "secrets_set": {}, "missing": [], "can_test": false, "can_dry_run": false, "tasks": {"done": 0, "attention": 0, "pending": 0}}, {"id": "offline_activite", "ready": false, "name": "Offline Activite", "description": "Выдаёт покупателю код Steam Guard по команде !guard для настроенных аккаунтов.", "category": "Steam", "needs": ["отправка сообщений покупателю на FunPay"], "enabled": false, "settings": [], "config": {}, "secrets_set": {}, "missing": [], "can_test": false, "can_dry_run": false, "tasks": {"done": 0, "attention": 0, "pending": 0}}, {"id": "email_code", "ready": false, "name": "EmailCode", "description": "Получает коды подтверждения из почты через IMAP и отправляет покупателю.", "category": "Steam", "needs": ["отправка сообщений покупателю на FunPay"], "enabled": false, "settings": [], "config": {}, "secrets_set": {}, "missing": [], "can_test": false, "can_dry_run": false, "tasks": {"done": 0, "attention": 0, "pending": 0}}, {"id": "trade_manager", "ready": false, "name": "TradeManager", "description": "Включает и выключает лоты по расписанию.", "category": "Лоты", "needs": ["управление лотами FunPay"], "enabled": false, "settings": [], "config": {}, "secrets_set": {}, "missing": [], "can_test": false, "can_dry_run": false, "tasks": {"done": 0, "attention": 0, "pending": 0}}, {"id": "auto_dump", "ready": false, "name": "AutoDump", "description": "Корректирует цены по публичному рынку FunPay.", "category": "Лоты", "needs": ["управление лотами FunPay"], "enabled": false, "settings": [], "config": {}, "secrets_set": {}, "missing": [], "can_test": false, "can_dry_run": false, "tasks": {"done": 0, "attention": 0, "pending": 0}}, {"id": "copy_lots", "ready": false, "name": "CopyLots", "description": "Копирует лоты между вашими аккаунтами FunPay.", "category": "Лоты", "needs": ["управление лотами FunPay"], "enabled": false, "settings": [], "config": {}, "secrets_set": {}, "missing": [], "can_test": false, "can_dry_run": false, "tasks": {"done": 0, "attention": 0, "pending": 0}}, {"id": "autoresponder", "ready": true, "name": "Автоответчик", "version": "2.0", "description": "Приветствие, ответы по ключевым словам, просьба об отзыве и ответы под отзывами.", "category": "Покупатели", "needs": ["отправка сообщений покупателю на FunPay"], "enabled": true, "can_test": false, "can_dry_run": false, "tasks": {"done": 12, "attention": 0, "pending": 0}, "secrets_set": {}, "missing": [], "images_set": {"greet_photo": true, "ask_review_photo": false}, "config": {"greet_enabled": true, "greet_text": "Здравствуйте, {buyer}!", "ask_review_enabled": true, "ask_review_text": "Оставьте отзыв, {buyer}!", "review_reply_enabled": false}, "settings": [{"key":"greet_enabled","label":"Приветствие на первое сообщение","type":"bool","default":true},{"key":"greet_text","label":"Текст приветствия","type":"textarea","default":""},{"key":"greet_photo","label":"Фото к приветствию (необязательно)","type":"image","hint":"Картинка отправится вместе с приветствием."},{"key":"ask_review_enabled","label":"Просить отзыв после подтверждения заказа","type":"bool","default":true},{"key":"ask_review_text","label":"Текст просьбы об отзыве","type":"textarea","default":""},{"key":"ask_review_photo","label":"Фото к просьбе об отзыве (необязательно)","type":"image","hint":"Например, картинка-инструкция."}]}, {"id": "auto_ticket", "ready": false, "name": "AutoTicket", "description": "Создаёт тикет в поддержку FunPay по просроченным заказам.", "category": "Покупатели", "needs": ["создание тикетов в поддержке FunPay"], "enabled": false, "settings": [], "config": {}, "secrets_set": {}, "missing": [], "can_test": false, "can_dry_run": false, "tasks": {"done": 0, "attention": 0, "pending": 0}}],
  ];
  const tasks = [
    { id: 40, plugin_id: "big_orders", plugin_name: "Крупные заказы", order_id: "B0C2E118", status: "done", attempts: 1, error: null,
      result: null, updated: now - 640, buyer: "Rust_King", description: "CS2 AK-47 | Redline (FT)", amount: 2350, currency: "₽" },
    { id: 39, plugin_id: "big_orders", plugin_name: "Крупные заказы", order_id: "A91F02C4", status: "skipped", attempts: 1, error: null,
      result: null, updated: now - 95, buyer: "xoxo_kate", description: "Telegram Stars, 500 шт, быстрая выдача", amount: 740, currency: "₽" },
  ];

  let offlineAccs = null;
  let sub = { active: true, source: "trial", until: now + 3*86400, days_left: 3, trial_until: now + 3*86400, offline: false, configured: true };
  let settings = {
    sync_interval_min: 5, chat_interval_sec: 10, notify_new_orders: true,
    greeting_enabled: true, greeting_text: "Здравствуйте! Спасибо, что написали. Отвечу в ближайшее время. Если нужна срочная помощь, напишите !help",
    help_enabled: true, help_command: "!help", help_reply: "Продавец получил уведомление и скоро ответит.",
    review_thanks_enabled: true, review_thanks_min: 5, review_thanks_text: "Спасибо за отзыв, {buyer}! Будем рады видеть вас снова.",
    raise_enabled: true, raise_interval_min: 120, commission_pct: 0, blacklist: ["scam_master", "refund_hunter"],
    tg_token: "", tg_token_set: true, tg_chat_id: "123456789",
    tg_on_attention: true, tg_on_account: true, tg_on_help: true, tg_on_review: true, tg_on_stock: true, tg_on_blacklist: true, tg_on_order: false,
  };
  const chats = [
    { account_id: 1, chat_id: "91201", name: "xoxo_kate", account: "Основной", unread: 1, last_ts: now - 70, last_text: "А можно сразу два?" },
    { account_id: 1, chat_id: "91188", name: "Rust_King", account: "Основной", unread: 0, last_ts: now - 700, last_text: "Спасибо за отзыв, Rust_King! Будем рады видеть вас снова." },
    { account_id: 2, chat_id: "88410", name: "dimon228", account: "Скины", unread: 1, last_ts: now - 2400, last_text: "!help скин не пришёл" },
    { account_id: 1, chat_id: "91002", name: "sonya.k", account: "Основной", unread: 0, last_ts: now - 86400, last_text: "Всё пришло, спасибо" },
  ];
  const msgs = {
    "91201": [
      { id: 1, author_id: 0, text: "Покупатель xoxo_kate оплатил заказ #A91F02C4. Telegram Stars, 500 шт, быстрая выдача.", ts: now - 400, mine: 0 },
      { id: 2, author_id: 777, text: "Спасибо за покупку, xoxo_kate! Ваш товар:\nSTARS-7F3K-PQ21", ts: now - 395, mine: 1 },
      { id: 3, author_id: 501, text: "Здравствуйте, всё пришло 👍", ts: now - 200, mine: 0 },
      { id: 4, author_id: 501, text: "А можно сразу два?", ts: now - 70, mine: 0 },
    ],
    "88410": [
      { id: 1, author_id: 502, text: "Здравствуйте", ts: now - 2600, mine: 0 },
      { id: 2, author_id: 777, text: "Здравствуйте! Спасибо, что написали. Отвечу в ближайшее время. Если нужна срочная помощь, напишите !help", ts: now - 2590, mine: 1 },
      { id: 3, author_id: 502, text: "!help скин не пришёл", ts: now - 2400, mine: 0 },
      { id: 4, author_id: 777, text: "Продавец получил уведомление и скоро ответит.", ts: now - 2395, mine: 1 },
    ],
  };
  let onlypcJobs = [
    { order_id: "RENT9001", buyer: "clubkid", account_id: 1, got_photo: true, created: now - 300 },
    { order_id: "RENT9002", buyer: "newguy", account_id: 1, got_photo: false, created: now - 1200 },
  ];
  const rentAccounts = [
    { login: "csgo_rent_01", title: "КС Прайм 2000ч", password: "Kp9xLm2Qwe", has_mafile: true, enabled: true, state: "rented", rented_until: now + 5400, rented_by: "renter_max", order_id: "RENT7788", offer_id: "3910101", extend_offer_id: "3911001", onlypc_check: true, hide_lot_on_rent: null, review_bonus_min_hours: "" },
    { login: "csgo_rent_02", title: "КС Прайм 2000ч", password: "Zt4hNb8Rty", has_mafile: true, enabled: true, state: "free", rented_until: null, rented_by: null, order_id: null },
    { login: "dota_rent_01", title: "Дота Калибровка", password: "Wq1vCx7Uio", has_mafile: false, enabled: true, state: "needs_reset", rented_until: null, rented_by: null, order_id: null },
    { login: "csgo_rent_03", password: "Mn5jDk3Poi", has_mafile: true, enabled: false, state: "free", rented_until: null, rented_by: null, order_id: null },
  ];
  const delivery = [
    { id: 1, phrase: "Telegram Stars, 500", mode: "fifo", text: "", cost: 420, enabled: 1, stock: 37, sold: 152 },
    { id: 2, phrase: "Steam ключ Elden Ring", mode: "fifo", text: "Ваш ключ: {item}\nАктивация: Steam → Игры → Активировать продукт", cost: 1350, enabled: 1, stock: 0, sold: 18 },
    { id: 3, phrase: "Аренда аккаунта Dota 2", mode: "text", text: "Инструкция по входу отправлю в течение 5 минут. {buyer}, не меняйте пароль!", cost: 0, enabled: 0, stock: 0, sold: 0 },
  ];
  const t = s => now - s;
  const events = [
    { id: 6, ts: t(95), level: "order", text: "Новый заказ #A91F02C4 от xoxo_kate: 740 ₽" },
    { id: 5, ts: t(610), level: "order", text: "[Крупные заказы] Крупный заказ: #B0C2E118 на 2350 ₽ (Скины)" },
    { id: 4, ts: t(640), level: "order", text: "Новый заказ #B0C2E118 от Rust_King: 2350 ₽" },
    { id: 3, ts: t(3600), level: "error", text: "Запасной: golden_key недействителен: FunPay не узнал аккаунт" },
    { id: 2, ts: t(7200), level: "info", text: "Плагин big_orders включён" },
    { id: 1, ts: t(9000), level: "info", text: "Воркер запущен, версия 0.1.0" },
  ];

  function stats(days, account) {
    const start = new Date(); start.setHours(0, 0, 0, 0); start.setDate(start.getDate() - days + 1);
    const rows = orders.filter(o => o.ts >= start / 1000 && (!account || o.account_id === account));
    const daily = [];
    for (let i = 0; i < days; i++) {
      const d = new Date(start); d.setDate(d.getDate() + i);
      daily.push({ date: d.toISOString().slice(0, 10), revenue: 0, orders: 0, profit: 0, _d: d });
    }
    let revenue = 0, refunds = 0, count = 0, costs = 0;
    const lots = {};
    rows.forEach(o => {
      if (o.status === "refunded") { refunds += o.amount; return; }
      o.profit = o.amount - o.cost;
      const idx = Math.floor((o.ts * 1000 - start) / 86400000);
      if (daily[idx]) { daily[idx].revenue += o.amount; daily[idx].orders++; daily[idx].profit += o.profit; }
      const l = lots[o.description] ||= { lot: o.description, orders: 0, revenue: 0, profit: 0 };
      l.orders++; l.revenue += o.amount; l.profit += o.profit;
      revenue += o.amount; costs += o.cost; count++;
    });
    return { days, revenue, orders: count, refunds, avg_check: count ? Math.round(revenue / count) : 0,
             costs, commission: 0, commission_pct: 0, profit: revenue - costs,
             top_lots: Object.values(lots).sort((a, b) => b.profit - a.profit).slice(0, 6),
             daily: daily.map(({ _d, ...x }) => x), recent: rows.slice(0, 200) };
  }

  return {
    async request(method, path, body) {
      await new Promise(r => setTimeout(r, 120));
      const [p, q] = path.split("?");
      const qs = new URLSearchParams(q || "");
      if (p === "/api/status") return { version: "0.1.0-demo", uptime: 9000, accounts: 3, accounts_ok: 2, plugins_enabled: 2, attention: tasks.filter(t => t.status === "attention").length, unread: chats.filter(c => c.unread).length,
        subscription: sub };
      if (p === "/api/accounts" && method === "GET")
        return { data: accounts.map(a => ({ ...a, orders_total: orders.filter(o => o.account_id === a.id).length })) };
      if (p === "/api/accounts" && method === "POST") return { error: "В демо-режиме аккаунты не добавляются. Подключите свой сервер." };
      if (p.startsWith("/api/accounts/") && method === "DELETE") return { error: "В демо-режиме аккаунты не удаляются." };
      if (p.startsWith("/api/accounts/") && method === "PATCH") {
        const a = accounts.find(x => x.id === +p.split("/")[3]); if (a) a.name = body.name; return { ok: true };
      }
      if (p === "/api/stats") return stats(+qs.get("days") || 30, +qs.get("account") || 0);
      if (p === "/api/plugins" && method === "GET") return { data: plugins.filter((x, i) => plugins.findIndex(y => y.id === x.id) === i) };
      if (p.endsWith("/enabled")) {
        const pl = plugins.find(x => x.id === p.split("/")[3]);
        if (!pl.ready) return { error: "Этот плагин ещё в разработке и пока не может быть включён" };
        if (body.enabled && pl.missing.length) return { error: "Сначала заполните в настройках: " + pl.missing.join(", ") };
        pl.enabled = body.enabled; return { ok: true };
      }
      if (p.endsWith("/test")) return p.includes("autosmm") ? { ok: true, message: "Поставщик 1: Баланс: 50.00 USD" } : p.includes("email_code") ? { ok: true, message: "Подключение работает. Свежих писем с кодом нет." } : p.includes("ai_assistant") ? { ok: true, message: "Работает. Модель ответила: тест" } : p.includes("lzt") ? { ok: false, message: "Не заполнено: API-токен LZT" } : { ok: true, message: "Ключ работает. Баланс: 184.20 $" };
      if (p.endsWith("/dry-run")) {
        const big = body.amount >= plugins[0].config.threshold;
        return { ok: true, message: big ? "Отработал без ошибок" : "Пропущен плагином",
                 log: big ? [`${plugins[0].config.note}: #TEST0001 на ${body.amount} ₽`] : [] };
      }
      if (p === "/api/tasks") return { data: tasks };
      if (p.endsWith("/retry") || p.endsWith("/resolve")) {
        const t = tasks.find(x => x.id === +p.split("/")[3]);
        t.status = p.endsWith("/retry") ? "done" : "done"; t.error = null; t.result = p.endsWith("/retry") ? "AppRoute: заказ 5581290" : "Закрыто вручную";
        plugins[0].tasks.attention = 0; return { ok: true };
      }
      if (p.endsWith("/config")) {
        const pl = plugins.find(x => x.id === p.split("/")[3]);
        pl.settings.forEach(st => {
          if (st.type === "secret") { if (body[st.key]) pl.secrets_set[st.key] = true; if ((body.__clear__ || []).includes(st.key)) pl.secrets_set[st.key] = false; }
          else if (st.key in body) pl.config[st.key] = body[st.key];
        });
        pl.missing = pl.settings.filter(st => st.required && (st.type === "secret" ? !pl.secrets_set[st.key] : !pl.config[st.key])).map(st => st.label);
        if (pl.missing.length) pl.enabled = false;
        return { ok: true };
      }
      if (p === "/api/subscription" && method === "GET") return sub;
      if (p === "/api/subscription/refresh") return sub;
      if (p === "/api/subscription/activate") {
        const code = (body.code || "").trim().toUpperCase();
        if (!/^KSA-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$/.test(code)) return { error: "Код не найден. Проверьте, правильно ли он введён." };
        const base = Math.max(sub.until, now); sub = { active: true, source: "paid", until: base + 30*86400, days_left: Math.round((base + 30*86400 - now)/86400), trial_until: sub.trial_until, offline: false, configured: true };
        return sub;
      }
      if (p === "/api/settings" && method === "GET") return settings;
      if (p === "/api/settings" && method === "PUT") {
        const { tg_token, __clear__, ...rest } = body;
        if (tg_token) rest.tg_token_set = true;
        if (Array.isArray(rest.blacklist)) rest.blacklist = rest.blacklist.map(x => x.trim()).filter(Boolean);
        return (settings = { ...settings, ...rest });
      }
      if (p === "/api/telegram/test") return { ok: true, message: "Тестовое сообщение отправлено — проверьте Telegram" };
      if (p === "/api/backups" && method === "GET") return { data: [
        { name: "kassa-20261003-030000.db", size: 48128, ts: now - 21600 },
        { name: "kassa-20261002-030000.db", size: 47900, ts: now - 108000 },
        { name: "kassa-20261001-030000.db", size: 47100, ts: now - 194400 },
      ] };
      if (p === "/api/backups" && method === "POST") return { ok: true, name: "kassa-20261003-091500.db" };
      if (p === "/api/raise" && method === "GET") return { data: [{ account: "Основной", ts: now - 1800, report: ["Предложения подняты."] }, { account: "Скины", ts: now - 1800, report: ["Подождите 2 часа."] }] };
      if (p === "/api/raise") return { "Основной": ["Подождите 1 час."], "Скины": ["Подождите 1 час."] };
      if (p === "/api/chats") return { data: chats };
      if (p.match(/\/api\/accounts\/\d+\/proxy\/test/)) return { data: { ok: true, ms: 342, message: "Работает · отклик 342 мс" } };
      if (p.match(/\/api\/accounts\/\d+\/proxy/) && method === "PUT") return { data: { ok: true } };
      if (p === "/api/rent/top") return { data: { total: 4820, currency: "₽", count: 37,
        games: [{game:"CS2 Prime 2000ч",count:18,sum:2140},{game:"Dota 2 Калибровка",count:12,sum:1680},{game:"GTA 5 Online",count:7,sum:1000}],
        accounts: [{account:"csgo_rent_01",count:11,sum:1320},{account:"csgo_rent_02",count:9,sum:1080},{account:"dota_rent_01",count:8,sum:1120}] } };
      if (p.startsWith("/api/chats/")) {
        const id = decodeURIComponent(p.split("/")[4]);
        const list = msgs[id] ||= [{ id: 1, author_id: 503, text: chats.find(c => c.chat_id === id)?.last_text || "", ts: now - 600, mine: 0 }];
        const c = chats.find(x => x.chat_id === id);
        if (method === "POST") { list.push({ id: list.length + 1, author_id: 777, text: body.text, ts: Date.now() / 1000, mine: 1 }); if (c) { c.last_text = body.text; c.last_ts = Date.now() / 1000; } }
        if (c) c.unread = 0;
        const ord = orders.find(o => o.buyer === (c && c.name));
        return { messages: list, warning: null, order: ord ? { order_id: ord.id, lot: ord.description } : null };
      }
      if (p.startsWith("/api/offline/accounts")) {
        offlineAccs ||= [{login:"cs2_offline_01",has_mafile:true},{login:"dota_offline_02",has_mafile:true}];
        const login = decodeURIComponent(p.split("/")[4] || "");
        if (method === "GET") return { data: offlineAccs };
        if (method === "POST") { offlineAccs.push({login:body.login,has_mafile:!!body.mafile}); return { ok: true }; }
        if (method === "DELETE") { offlineAccs = offlineAccs.filter(a=>a.login!==login); return { ok: true }; }
      }
      if (p === "/api/rent/onlypc" && method === "GET") return { data: onlypcJobs };
      if (p.startsWith("/api/rent/onlypc/")) {
        const id = p.split("/")[4];
        onlypcJobs = onlypcJobs.filter(j => j.order_id !== id);
        return { ok: true, message: p.endsWith("approve") ? "Аккаунт выдан" : "Отклонено" };
      }
      if (p.startsWith("/api/rent/accounts")) {
        const seg = p.split("/"); const login = decodeURIComponent(seg[4] || "");
        if (p === "/api/rent/accounts" && method === "GET") return { data: rentAccounts };
        if (p === "/api/rent/accounts" && method === "POST") { rentAccounts.push({ login: body.login, password: body.password, has_mafile: !!body.mafile, enabled: true, state: "free", rented_until: null, rented_by: null, order_id: null }); return { ok: true }; }
        const a = rentAccounts.find(x => x.login === login);
        if (!a) return { error: "Аккаунт не найден" };
        if (seg[5] === "test") return { ok: true, message: "Вход работает, пароль верный" };
        if (seg[5] === "logout") return { ok: true, message: "Все сессии аккаунта завершены" };
        if (seg[5] === "reset-done") { a.state = "free"; if (body.password) a.password = body.password; a.rented_by = null; a.rented_until = null; return { ok: true }; }
        if (method === "PUT") { if ("enabled" in body) a.enabled = body.enabled; if (body.password) a.password = body.password; if (body.mafile) a.has_mafile = true; return { ok: true }; }
        if (method === "DELETE") { rentAccounts.splice(rentAccounts.indexOf(a), 1); return { ok: true }; }
      }
      if (p === "/api/delivery" && method === "GET") return { data: delivery };
      if (p === "/api/delivery" && method === "POST") { delivery.push({ id: delivery.length + 1, ...body, enabled: 1, stock: 0, sold: 0 }); return { ok: true }; }
      if (p.startsWith("/api/delivery/")) {
        const l = delivery.find(x => x.id === +p.split("/")[3]);
        if (p.endsWith("/stock")) { if (method === "POST") { const n = body.lines.split("\n").filter(x => x.trim()).length; if (!n) return { error: "Список пуст: вставьте товары, по одному на строку" }; l.stock += n; return { added: n }; } l.stock = 0; return { ok: true }; }
        if (method === "PUT") { Object.assign(l, body, { enabled: body.enabled ? 1 : 0 }); return { ok: true }; }
        if (method === "DELETE") { delivery.splice(delivery.indexOf(l), 1); return { ok: true }; }
      }
      if (p === "/api/events") return { data: events };
      if (p === "/api/sync") return { ok: true };
      return { error: "Недоступно в демо-режиме" };
    },
  };
})();
