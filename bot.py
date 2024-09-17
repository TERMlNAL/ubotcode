
# import asyncio
# import logging
# import sqlite3
# import os
# from aiogram import Bot, Dispatcher, types, Router, F
# from aiogram.types import (
#     ContentType, Message, ReplyKeyboardMarkup, KeyboardButton
# )
# from aiogram.fsm.storage.memory import MemoryStorage
# from aiogram.fsm.context import FSMContext
# from aiogram.fsm.state import State, StatesGroup
# from aiogram.filters import Command, StateFilter
# import openai
# from dotenv import load_dotenv
# from pydub import AudioSegment  # Для работы с аудио
# from io import BytesIO  # Для работы с потоками в памяти

# # Загрузка переменных окружения из файла .env
# load_dotenv()

# # Настройка логирования
# logging.basicConfig(level=logging.INFO)

# # Токены и ключи API
# API_TOKEN = os.getenv('API_TOKEN')
# OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
# PAYMENT_PROVIDER_TOKEN = os.getenv('PAYMENT_PROVIDER_TOKEN')
# SUPPORT_BOT_USERNAME = os.getenv('SUPPORT_BOT_USERNAME')  # Имя пользователя бота поддержки

# if not all([API_TOKEN, OPENAI_API_KEY, PAYMENT_PROVIDER_TOKEN, SUPPORT_BOT_USERNAME]):
#     logging.error("Пожалуйста, убедитесь, что все токены и ключи API заданы в файле .env")
#     exit(1)

# # **Режим тестирования**: установите в True, чтобы пропустить этап оплаты
# TESTING_MODE = False  # Установите False для отключения режима тестирования

# # Инициализация бота и диспетчера
# bot = Bot(token=API_TOKEN)
# storage = MemoryStorage()
# dp = Dispatcher(storage=storage)
# router = Router()

# # Настройка OpenAI
# openai.api_key = OPENAI_API_KEY

# # Определение состояний
# class UserStates(StatesGroup):
#     selecting_tariff = State()
#     selecting_model = State()
#     purchasing_tariff = State()

# # Настройка базы данных
# conn = sqlite3.connect('users.db')
# cursor = conn.cursor()

# # Создание таблицы users, если её нет
# cursor.execute('''CREATE TABLE IF NOT EXISTS users
#                   (user_id INTEGER PRIMARY KEY,
#                    tariff TEXT,
#                    requests_left INTEGER,
#                    tokens_balance INTEGER DEFAULT 500,
#                    model TEXT,
#                    has_selected_model INTEGER DEFAULT 0)''')
# conn.commit()

# # Проверяем, есть ли колонка has_selected_model в таблице users
# cursor.execute("PRAGMA table_info(users)")
# columns = [column[1] for column in cursor.fetchall()]
# if 'has_selected_model' not in columns:
#     cursor.execute("ALTER TABLE users ADD COLUMN has_selected_model INTEGER DEFAULT 0")
#     conn.commit()

# # Обновляем тариф для существующих пользователей, если он отсутствует
# cursor.execute("UPDATE users SET tariff='Базовый' WHERE tariff IS NULL OR tariff=''")
# conn.commit()

# # Функция для инициализации баланса токенов в зависимости от тарифа
# def initialize_user(user_id, tariff):
#     tariff_tokens = {
#         'Базовый': 1000,
#         'Продвинутый': 2000,
#         'Премиум': 3000
#     }
#     tokens_to_add = tariff_tokens.get(tariff, 0)
#     # Получаем текущий баланс токенов пользователя
#     cursor.execute("SELECT tokens_balance FROM users WHERE user_id=?", (user_id,))
#     result = cursor.fetchone()
#     if result:
#         tokens_balance = result[0] + tokens_to_add
#     else:
#         tokens_balance = tokens_to_add
#     # Обновляем информацию о пользователе
#     cursor.execute("INSERT OR REPLACE INTO users (user_id, tariff, tokens_balance) VALUES (?, ?, ?)",
#                    (user_id, tariff, tokens_balance))
#     conn.commit()

# # Проверка доступа пользователя
# async def check_user_access(user_id, required_tariff="Базовый"):
#     cursor.execute("SELECT tariff, tokens_balance FROM users WHERE user_id=?", (user_id,))
#     user = cursor.fetchone()
#     if user is None:
#         return False, "У вас нет активного тарифа. Пожалуйста, выберите тариф, нажав на кнопку 'Подписка'."
#     tariff, tokens_balance = user
#     if not tariff:
#         tariff = 'Базовый'
#     tariffs_order = {"Базовый": 1, "Продвинутый": 2, "Премиум": 3}
#     if tokens_balance <= 0:
#         return False, "Ваш пакет токенов закончился."
#     if tariffs_order.get(tariff, 0) < tariffs_order.get(required_tariff, 0):
#         return False, "Ваш тариф не поддерживает эту функцию."
#     return True, None

# # Обновление баланса токенов
# async def update_tokens_balance(user_id, tokens_used):
#     cursor.execute("SELECT tokens_balance FROM users WHERE user_id=?", (user_id,))
#     result = cursor.fetchone()
#     if result:
#         tokens_balance = result[0]
#         tokens_balance -= tokens_used
#         if tokens_balance < 0:
#             tokens_balance = 0
#         cursor.execute("UPDATE users SET tokens_balance=? WHERE user_id=?", (tokens_balance, user_id))
#         conn.commit()

# # Функция для определения языка сообщения
# def detect_language(text):
#     if any('а' <= c <= 'я' or 'А' <= c <= 'Я' for c in text):
#         return 'ru'
#     else:
#         return 'en'

# # Функция для отображения анимации загрузки с тремя точками
# async def show_loading_animation(msg: types.Message, base_text: str):
#     await msg.edit_text(f"{base_text} .")
#     for _ in range(3):
#         for dots in ['.', '..', '...', '']:
#             try:
#                 await msg.edit_text(f"{base_text} {dots}")
#             except:
#                 pass
#             await asyncio.sleep(0.5)

# # Глобальные переменные для клавиатур
# # Уровень 1
# level1_buttons = [
#     [KeyboardButton(text="🔍 Выбор модели"), KeyboardButton(text="🆕 Новый чат")],
#     [KeyboardButton(text="📊 Остаток"), KeyboardButton(text="💼 Подписка")],
#     [KeyboardButton(text="➕ Еще"), KeyboardButton(text="🛠 Поддержка")]
# ]
# keyboard_level1 = ReplyKeyboardMarkup(
#     keyboard=level1_buttons,
#     resize_keyboard=True,
#     one_time_keyboard=False
# )

# # Клавиатура для выбора модели
# model_buttons = [
#     [KeyboardButton(text="🧠 GPT-4o"), KeyboardButton(text="🧠 GPT-4o-mini")],
#     [KeyboardButton(text="🧠 o1-mini")],
#     [KeyboardButton(text="🔙 Назад")]
# ]
# keyboard_model_selection = ReplyKeyboardMarkup(
#     keyboard=model_buttons,
#     resize_keyboard=True,
#     one_time_keyboard=False
# )

# # Клавиатура для выбора тарифа
# tariff_buttons = [
#     [KeyboardButton(text="📉 Базовый"), KeyboardButton(text="📈 Продвинутый")],
#     [KeyboardButton(text="🏆 Премиум")],
#     [KeyboardButton(text="🔙 Назад")]
# ]
# keyboard_tariff_selection = ReplyKeyboardMarkup(
#     keyboard=tariff_buttons,
#     resize_keyboard=True,
#     one_time_keyboard=False
# )

# # Клавиатура для меню подписок с кнопкой "Инфо"
# tariff_buttons_with_info = [
#     [KeyboardButton(text="📉 Базовый"), KeyboardButton(text="📈 Продвинутый")],
#     [KeyboardButton(text="🏆 Премиум")],
#     [KeyboardButton(text="ℹ️ Инфо"), KeyboardButton(text="🔙 Назад")]
# ]
# keyboard_tariff_selection_with_info = ReplyKeyboardMarkup(
#     keyboard=tariff_buttons_with_info,
#     resize_keyboard=True,
#     one_time_keyboard=False
# )

# # Обработчик команды /start
# @router.message(Command('start'))
# async def cmd_start(message: Message, state: FSMContext):
#     # Проверяем, есть ли пользователь в базе данных
#     cursor.execute("SELECT * FROM users WHERE user_id=?", (message.from_user.id,))
#     user = cursor.fetchone()

#     if TESTING_MODE:
#         # В режиме тестирования предоставляем начальный баланс 500 токенов
#         cursor.execute("INSERT OR IGNORE INTO users (user_id, tokens_balance, tariff, has_selected_model) VALUES (?, ?, ?, ?)",
#                        (message.from_user.id, 500, 'Базовый', 0))
#         conn.commit()
#         cursor.execute("SELECT * FROM users WHERE user_id=?", (message.from_user.id,))
#         user = cursor.fetchone()

#     if user is None:
#         # Предоставляем начальный баланс 500 токенов и тариф "Базовый"
#         cursor.execute("INSERT INTO users (user_id, tokens_balance, tariff, has_selected_model) VALUES (?, ?, ?, ?)",
#                        (message.from_user.id, 500, 'Базовый', 0))
#         conn.commit()
#         await message.answer("Добро пожаловать! У вас есть 500 токенов для пробного использования.", reply_markup=keyboard_level1)
#     else:
#         await message.answer("С возвращением! Вы можете начать использовать бота.", reply_markup=keyboard_level1)

# # Обработчик выбора тарифа
# @router.message(StateFilter(UserStates.selecting_tariff))
# async def process_tariff_selection(message: Message, state: FSMContext):
#     tariff = message.text
#     if tariff == "🔙 Назад":
#         await message.answer("Вы вернулись в главное меню.", reply_markup=keyboard_level1)
#         await state.clear()
#         return
#     if tariff not in ["📉 Базовый", "📈 Продвинутый", "🏆 Премиум", "ℹ️ Инфо"]:
#         # Если пользователь ввёл что-то другое, выходим из состояния и продолжаем диалог
#         await message.answer("Вы вернулись в главное меню.", reply_markup=keyboard_level1)
#         await state.clear()
#         # Обрабатываем сообщение как обычное текстовое
#         await handle_text(message, state)
#         return
#     # Обработка нажатия на "Инфо"
#     if tariff == "ℹ️ Инфо":
#         info_message = (
#             "📋 **Информация о тарифах:**\n\n"
#             "🏆 **Премиум**:\n"
#             "- Неограниченное количество токенов\n"
#             "- Доступ ко всем моделям\n"
#             "- Приоритетная поддержка\n\n"
#             "📈 **Продвинутый**:\n"
#             "- 2000 токенов\n"
#             "- Доступ к большинству моделей\n"
#             "- Поддержка через бот\n\n"
#             "📉 **Базовый**:\n"
#             "- 1000 токенов\n"
#             "- Ограниченный доступ к моделям\n"
#             "- Поддержка через FAQ"
#         )
#         await message.answer(info_message, parse_mode="Markdown", reply_markup=keyboard_tariff_selection_with_info)
#         return

#     # Остальной код для обработки покупки тарифа
#     # Извлекаем тариф без эмодзи
#     tariff_clean = tariff.split(' ')[-1]
#     if TESTING_MODE:
#         initialize_user(message.from_user.id, tariff_clean)
#         await message.answer(f"Вы приобрели тариф {tariff_clean}.", reply_markup=keyboard_level1)
#         await state.clear()
#     else:
#         # Здесь нужно инициировать процесс оплаты
#         prices = {
#             'Базовый': [types.LabeledPrice(label='📉 Базовый', amount=1000 * 100)],
#             'Продвинутый': [types.LabeledPrice(label='📈 Продвинутый', amount=2000 * 100)],
#             'Премиум': [types.LabeledPrice(label='🏆 Премиум', amount=3000 * 100)],
#         }
#         await bot.send_invoice(
#             chat_id=message.chat.id,
#             title=f'Покупка тарифа {tariff_clean}',
#             description=f'Оплата тарифа {tariff_clean}',
#             payload=f'tariff_{tariff_clean}',
#             provider_token=PAYMENT_PROVIDER_TOKEN,
#             currency='RUB',
#             prices=prices[tariff_clean],
#             start_parameter='purchase_tariff',
#         )
#         await state.update_data(selected_tariff=tariff_clean)
#         await state.set_state(UserStates.purchasing_tariff)

# # Обработчик пред-проверки оплаты
# @router.pre_checkout_query()
# async def process_pre_checkout_query(pre_checkout_query: types.PreCheckoutQuery):
#     await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

# # Обработчик успешной оплаты
# @router.message(F.content_type == ContentType.SUCCESSFUL_PAYMENT)
# async def process_successful_payment(message: Message, state: FSMContext):
#     # Получаем выбранный тариф из состояния
#     data = await state.get_data()
#     tariff = data.get('selected_tariff')
#     if not tariff:
#         await message.answer("Ошибка: не удалось определить выбранный тариф.")
#         return
#     # Активируем тариф
#     initialize_user(message.from_user.id, tariff)
#     await message.answer(f"Спасибо за покупку! Вы приобрели тариф {tariff}.", reply_markup=keyboard_level1)
#     await state.clear()

# # Обработчик текстовых сообщений
# @router.message(F.content_type == ContentType.TEXT)
# async def handle_text(message: Message, state: FSMContext):
#     current_state = await state.get_state()
#     # Проверка нажатия на кнопку "Остаток"
#     if message.text == "📊 Остаток":
#         cursor.execute("SELECT tokens_balance, tariff FROM users WHERE user_id=?", (message.from_user.id,))
#         result = cursor.fetchone()
#         if result:
#             tokens_balance, tariff = result
#             await message.answer(
#                 f"📦 **Ваш тариф:** {tariff}\n"
#                 f"🔢 **Ваш остаток токенов:** {tokens_balance}\n\n"
#                 "Если вы хотите получить больше токенов, вы можете приобрести дополнительную подписку. Токены суммируются.",
#                 parse_mode="Markdown",
#                 reply_markup=keyboard_level1
#             )
#         else:
#             await message.answer("❌ Не удалось получить информацию об остатке.", reply_markup=keyboard_level1)
#         return

#     # Проверка нажатия на кнопку "Подписка"
#     if message.text == "💼 Подписка":
#         await message.answer("Пожалуйста, выберите тарифный план:", reply_markup=keyboard_tariff_selection_with_info)
#         await state.set_state(UserStates.selecting_tariff)
#         return

#     # Проверка нажатия на кнопку "Поддержка"
#     if message.text == "🛠 Поддержка":
#         await message.answer(f"Если у вас возникли вопросы или проблемы, обратитесь в поддержку: @{SUPPORT_BOT_USERNAME}", reply_markup=keyboard_level1)
#         return

#     # Проверка нажатия на кнопку "Еще"
#     if message.text == "➕ Еще":
#         await message.answer(
#             "Если вам нужны следующие функции:\n"
#             "- Генерация видео\n"
#             "- Аудио в текст / Текст в аудио\n"
#             "- ИИ Аватары\n"
#             "- Функция написания книг и статей\n"
#             "- Еще более 200 ИИ инструментов\n\n"
#             "То переходите на наш Pro портал [uonium.com](https://uonium.com)\n"
#             "Там что-то невероятное.",
#             parse_mode="Markdown",
#             reply_markup=keyboard_level1
#         )
#         return

#     # Проверка нажатия на кнопку "Выбор модели"
#     if message.text == "🔍 Выбор модели":
#         await message.answer("Пожалуйста, выберите модель:", reply_markup=keyboard_model_selection)
#         await state.set_state(UserStates.selecting_model)
#         return

#     # Обработка выбора модели
#     if current_state == UserStates.selecting_model.state:
#         model_name = message.text
#         if model_name == "🔙 Назад":
#             await message.answer("Вы вернулись в главное меню.", reply_markup=keyboard_level1)
#             await state.clear()
#             return
#         if model_name not in ["🧠 GPT-4o", "🧠 GPT-4o-mini", "🧠 o1-mini"]:
#             await message.answer("Пожалуйста, выберите одну из доступных моделей.", reply_markup=keyboard_model_selection)
#             return
#         model_mapping = {
#             "🧠 GPT-4o": "gpt-4o",
#             "🧠 GPT-4o-mini": "gpt-4o-mini",
#             "🧠 o1-mini": "o1-mini"
#         }
#         model_id = model_mapping.get(model_name)

#         # Проверяем доступ к модели "o1-mini"
#         if model_id == "o1-mini":
#             has_access, error_message = await check_user_access(message.from_user.id, "Премиум")
#             if not has_access:
#                 await message.answer("Модель 'o1-mini' доступна только в тарифе 'Премиум'. Пожалуйста, приобретите подписку для доступа к этой модели.", reply_markup=keyboard_tariff_selection_with_info)
#                 await state.set_state(UserStates.selecting_tariff)
#                 return

#         # Обновляем информацию о модели в базе данных и отмечаем, что пользователь выбрал модель
#         cursor.execute("UPDATE users SET model=?, has_selected_model=1 WHERE user_id=?", (model_id, message.from_user.id))
#         conn.commit()
#         await message.answer(f"Вы выбрали модель {model_name}.", reply_markup=keyboard_level1)
#         await state.clear()
#         return

#     # Проверка нажатия на кнопку "Новый чат"
#     if message.text == "🆕 Новый чат":
#         await state.update_data(conversation=[])
#         await message.answer("Контекст очищен. Вы можете начать новый чат.", reply_markup=keyboard_level1)
#         return

#     # Проверяем, выбрал ли пользователь модель
#     cursor.execute("SELECT has_selected_model FROM users WHERE user_id=?", (message.from_user.id,))
#     result = cursor.fetchone()
#     has_selected_model = result[0] if result else 0
#     if not has_selected_model:
#         await message.answer("Пожалуйста, выберите модель для продолжения.", reply_markup=keyboard_model_selection)
#         await state.set_state(UserStates.selecting_model)
#         return

#     # Проверяем доступ пользователя
#     has_access, error_message = await check_user_access(message.from_user.id)
#     if not has_access:
#         # Если у пользователя закончились токены
#         if error_message == "Ваш пакет токенов закончился.":
#             await message.answer("Ваш пакет токенов закончился. Пожалуйста, выберите новый тарифный план:", reply_markup=keyboard_tariff_selection_with_info)
#             await state.set_state(UserStates.selecting_tariff)
#         else:
#             await message.answer(error_message)
#         return

#     # Получаем выбранную модель пользователя
#     cursor.execute("SELECT model FROM users WHERE user_id=?", (message.from_user.id,))
#     result = cursor.fetchone()
#     model_id = result[0] if result else "o1-mini"

#     # Проверяем доступ к модели "o1-mini" при отправке сообщений
#     if model_id == "o1-mini":
#         has_access, error_message = await check_user_access(message.from_user.id, "Премиум")
#         if not has_access:
#             await message.answer("Модель 'o1-mini' доступна только в тарифе 'Премиум'. Пожалуйста, приобретите подписку для доступа к этой модели.", reply_markup=keyboard_tariff_selection_with_info)
#             await state.set_state(UserStates.selecting_tariff)
#             return

#     # Список триггерных фраз для генерации изображений
#     image_triggers = [
#         "сгенерируй фото", "создай фото", "создай изображение", "сделай фото",
#         "сделай изображение", "мне нужно фото", "мне нужно изображение",
#         "нарисуй фото", "нарисуй изображение", "нарисуй картинку",
#         "сгенерируй картинку", "сделай картинку", "создай картинку",
#         "сгенерируй изображение", "мне нужна картинка"
#     ]

#     # Проверка запроса на генерацию изображения
#     if any(phrase in message.text.lower() for phrase in image_triggers):
#         if model_id == "o1-mini":
#             await message.answer("Генерация изображений недоступна для выбранной модели 'o1-mini'.", reply_markup=keyboard_level1)
#             return
#         has_access, error_message = await check_user_access(message.from_user.id, "Продвинутый")
#         if not has_access:
#             # Если у пользователя закончились токены
#             if error_message == "Ваш пакет токенов закончился.":
#                 await message.answer("Ваш пакет токенов закончился. Пожалуйста, выберите новый тарифный план:", reply_markup=keyboard_tariff_selection_with_info)
#                 await state.set_state(UserStates.selecting_tariff)
#             else:
#                 await message.answer(error_message)
#             return

#         # Извлекаем описание изображения
#         prompt = message.text

#         # Отправляем сообщение с анимацией загрузки
#         msg = await message.answer("Генерация изображения")

#         try:
#             # Запускаем анимацию загрузки с тремя точками
#             animation_task = asyncio.create_task(show_loading_animation(msg, "Генерация изображения"))

#             # Используем модель DALL-E 3
#             image_model = "dall-e-3"

#             # Генерация изображения в отдельном потоке
#             response = await asyncio.to_thread(openai.Image.create,
#                                                prompt=prompt,
#                                                n=1,
#                                                size="1024x1024",
#                                                model=image_model)

#             # Отмена задачи обновления анимации
#             animation_task.cancel()

#             image_url = response['data'][0]['url']

#             await msg.delete()
#             await message.answer_photo(image_url, reply_markup=keyboard_level1)

#             # Обновляем баланс токенов (например, 100 токенов за изображение)
#             await update_tokens_balance(message.from_user.id, 100)

#         except openai.error.OpenAIError as e:
#             logging.error(f"Ошибка при генерации изображения: {e}")
#             await msg.delete()
#             await message.answer("Произошла ошибка при генерации изображения. Пожалуйста, попробуйте позже.", reply_markup=keyboard_level1)

#         except Exception as e:
#             logging.error(f"Непредвиденная ошибка: {e}")
#             await msg.delete()
#             await message.answer("Произошла непредвиденная ошибка. Пожалуйста, попробуйте позже.", reply_markup=keyboard_level1)

#         return

#     # Работа с контекстом беседы
#     data = await state.get_data()
#     conversation = data.get('conversation', [])

#     # Добавляем сообщение пользователя в контекст
#     conversation.append({"role": "user", "content": message.text})

#     # Определяем язык сообщения
#     user_language = detect_language(message.text)

#     # Добавляем системное сообщение для установки языка ответа
#     system_message = {
#         "role": "system",
#         "content": "Отвечай всегда на русском языке."
#     }

#     # Готовим сообщения для отправки в OpenAI
#     conversation_with_system = [system_message] + conversation

#     # Отправляем сообщение с анимацией загрузки
#     msg = await message.answer("[генерация ответа]")

#     try:
#         if model_id == "o1-mini":
#             # Слова для отображения вместо анимации
#             loading_words = ["[думаю]", "[анализирую]", "[решаю]"]

#             async def update_loading_words(msg):
#                 index = 0
#                 while True:
#                     word = loading_words[index % len(loading_words)]
#                     try:
#                         await msg.edit_text(word)
#                     except:
#                         pass
#                     index += 1
#                     await asyncio.sleep(0.7)

#             # Запускаем задачу обновления отображаемых слов
#             animation_task = asyncio.create_task(update_loading_words(msg))
#         else:
#             # Запускаем анимацию загрузки с тремя точками
#             animation_task = asyncio.create_task(show_loading_animation(msg, "[генерация ответа]"))

#         # Отправляем запрос к OpenAI в отдельном потоке
#         response = await asyncio.to_thread(openai.ChatCompletion.create,
#                                            model=model_id,
#                                            messages=conversation_with_system)

#         # Отмена задачи обновления анимации
#         animation_task.cancel()

#         reply_text = response['choices'][0]['message']['content']

#         # Добавляем ответ ассистента в контекст
#         conversation.append({"role": "assistant", "content": reply_text})

#         # Сохраняем обновленный контекст
#         await state.update_data(conversation=conversation)

#         await msg.delete()
#         # Отправляем ответ пользователю
#         await message.answer(reply_text, reply_markup=keyboard_level1)

#         # Обновляем баланс токенов на основе использования
#         tokens_used = response['usage']['total_tokens']
#         await update_tokens_balance(message.from_user.id, tokens_used)

#     except openai.error.OpenAIError as e:
#         logging.error(f"Ошибка при генерации текста: {e}")
#         await msg.delete()
#         await message.answer("Произошла ошибка при обработке вашего запроса. Пожалуйста, попробуйте позже.", reply_markup=keyboard_level1)

#     except Exception as e:
#         logging.error(f"Непредвиденная ошибка: {e}")
#         await msg.delete()
#         await message.answer("Произошла непредвиденная ошибка. Пожалуйста, попробуйте позже.", reply_markup=keyboard_level1)

# # Обработчик голосовых сообщений
# @router.message(F.voice)
# async def handle_voice_message(message: Message, state: FSMContext):
#     # Проверяем доступ пользователя
#     has_access, error_message = await check_user_access(message.from_user.id, required_tariff="Премиум")
#     if not has_access:
#         await message.answer(
#             "🔒 Распознавание голосовых сообщений доступно только для пользователей "
#             "с тарифом 'Премиум'. Пожалуйста, приобретите подписку для доступа "
#             "к этой функции.",
#             reply_markup=keyboard_tariff_selection_with_info
#         )
#         await state.set_state(UserStates.selecting_tariff)
#         return

#     # Проверяем баланс токенов
#     cursor.execute("SELECT tokens_balance FROM users WHERE user_id=?", (message.from_user.id,))
#     result = cursor.fetchone()
#     if result:
#         tokens_balance = result[0]
#         if tokens_balance <= 0:
#             await message.answer(
#                 "❌ Ваш пакет токенов закончился. Пожалуйста, приобретите новую подписку.",
#                 reply_markup=keyboard_tariff_selection_with_info
#             )
#             await state.set_state(UserStates.selecting_tariff)
#             return
#     else:
#         await message.answer("❌ Не удалось получить информацию об остатке.", reply_markup=keyboard_level1)
#         return

#     # Отправляем сообщение с анимацией загрузки "Распознавание аудио"
#     msg = await message.answer("[распознавание аудио]")

#     try:
#         # Получаем file_id голосового сообщения
#         file_id = message.voice.file_id

#         # Скачиваем файл в память
#         voice_file = BytesIO()
#         await bot.download(file_id, destination=voice_file)
#         voice_file.seek(0)

#         # Преобразуем OGG в WAV формат
#         audio = AudioSegment.from_file(voice_file, format="ogg")
#         wav_io = BytesIO()
#         audio.export(wav_io, format="wav")
#         wav_io.seek(0)

#         # Добавляем атрибут name к BytesIO объекту
#         wav_io.name = "temp_audio.wav"

#         # Отправляем аудиофайл в Whisper для распознавания
#         transcript = openai.Audio.transcribe("whisper-1", wav_io)

#         # Получаем текст распознанного аудио
#         recognized_text = transcript["text"]

#         # Обновляем сообщение о том, что запрос обрабатывается
#         await show_loading_animation(msg, "[генерация ответа]")


#         # Работа с контекстом беседы
#         data = await state.get_data()
#         conversation = data.get('conversation', [])

#         # Добавляем сообщение пользователя в контекст
#         conversation.append({"role": "user", "content": recognized_text})

#         # Добавляем системное сообщение для установки языка ответа
#         system_message = {
#             "role": "system",
#             "content": "Отвечай всегда на русском языке."
#         }

#         # Готовим сообщения для отправки в OpenAI
#         conversation_with_system = [system_message] + conversation

#         # Получаем выбранную модель пользователя
#         cursor.execute("SELECT model FROM users WHERE user_id=?", (message.from_user.id,))
#         result = cursor.fetchone()
#         model_id = result[0] if result else "o1-mini"

#         # Отправляем запрос к OpenAI в отдельном потоке
#         response = await asyncio.to_thread(openai.ChatCompletion.create,
#                                            model=model_id,
#                                            messages=conversation_with_system)

#         reply_text = response['choices'][0]['message']['content']

#         # Добавляем ответ ассистента в контекст
#         conversation.append({"role": "assistant", "content": reply_text})

#         # Сохраняем обновленный контекст
#         await state.update_data(conversation=conversation)

#         await msg.delete()
#         # Отправляем ответ пользователю
#         await message.answer(reply_text, reply_markup=keyboard_level1)

#         # Обновляем баланс токенов на основе использования
#         tokens_used = response['usage']['total_tokens']
#         await update_tokens_balance(message.from_user.id, tokens_used)

#     except Exception as e:
#         logging.error(f"Ошибка при обработке голосового сообщения: {e}")
#         await msg.delete()
#         await message.answer(
#             "❌ Произошла ошибка при обработке вашего голосового сообщения. "
#             "Пожалуйста, попробуйте позже.", reply_markup=keyboard_level1
#         )

# async def main():
#     # Включаем Router в Dispatcher
#     dp.include_router(router)

#     # Запуск бота
#     try:
#         await dp.start_polling(bot)
#     finally:
#         # Закрываем соединение с базой данных
#         conn.close()

# if __name__ == '__main__':
#     try:
#         asyncio.run(main())
#     except (KeyboardInterrupt, SystemExit):
#         logging.info("Бот остановлен!")





# import asyncio
# import logging
# import os
# import sqlite3
# from aiogram import Bot, Dispatcher, types, Router, F
# from aiogram.types import ContentType, Message, ReplyKeyboardMarkup, KeyboardButton
# from aiogram.fsm.storage.memory import MemoryStorage
# from aiogram.fsm.context import FSMContext
# from aiogram.fsm.state import StatesGroup, State
# from aiogram.filters import Command, StateFilter
# import openai
# from dotenv import load_dotenv
# from pydub import AudioSegment
# from io import BytesIO

# # Загрузка переменных окружения
# load_dotenv()

# # Настройка логирования
# logging.basicConfig(level=logging.INFO)

# # Проверка необходимых переменных окружения
# API_TOKEN, OPENAI_API_KEY, PAYMENT_PROVIDER_TOKEN, SUPPORT_BOT_USERNAME = (
#     os.getenv(key) for key in ['API_TOKEN', 'OPENAI_API_KEY', 'PAYMENT_PROVIDER_TOKEN', 'SUPPORT_BOT_USERNAME']
# )
# if not all([API_TOKEN, OPENAI_API_KEY, PAYMENT_PROVIDER_TOKEN, SUPPORT_BOT_USERNAME]):
#     logging.error("Пожалуйста, убедитесь, что все токены и ключи API заданы в файле .env")
#     exit(1)

# TESTING_MODE = False  # Установите True для тестирования

# # Инициализация бота и диспетчера
# bot = Bot(token=API_TOKEN)
# storage = MemoryStorage()
# dp = Dispatcher(storage=storage)
# router = Router()
# openai.api_key = OPENAI_API_KEY

# # Определение состояний
# class UserStates(StatesGroup):
#     selecting_tariff = State()
#     selecting_model = State()
#     purchasing_tariff = State()

# # Настройка базы данных
# conn = sqlite3.connect('users.db')
# cursor = conn.cursor()
# cursor.execute('''
#     CREATE TABLE IF NOT EXISTS users (
#         user_id INTEGER PRIMARY KEY,
#         tariff TEXT DEFAULT 'Базовый',
#         requests_left INTEGER,
#         tokens_balance INTEGER DEFAULT 500,
#         model TEXT,
#         has_selected_model INTEGER DEFAULT 0
#     )
# ''')
# conn.commit()

# # Функция для инициализации/обновления пользователя
# def initialize_user(user_id, tariff):
#     tariff_tokens = {'Базовый': 1000, 'Продвинутый': 2000, 'Премиум': 3000}
#     tokens_to_add = tariff_tokens.get(tariff, 0)
#     cursor.execute("SELECT tokens_balance FROM users WHERE user_id=?", (user_id,))
#     result = cursor.fetchone()
#     if result:
#         tokens_balance = result[0] + tokens_to_add
#         cursor.execute("UPDATE users SET tariff=?, tokens_balance=? WHERE user_id=?", (tariff, tokens_balance, user_id))
#     else:
#         cursor.execute("""
#             INSERT INTO users (user_id, tariff, tokens_balance, has_selected_model) 
#             VALUES (?, ?, ?, 0)
#         """, (user_id, tariff, tokens_to_add))
#     conn.commit()

# # Проверка доступа пользователя
# async def check_user_access(user_id, required_tariff="Базовый"):
#     cursor.execute("SELECT tariff, tokens_balance FROM users WHERE user_id=?", (user_id,))
#     user = cursor.fetchone()
#     if not user:
#         return False, "У вас нет активного тарифа. Пожалуйста, выберите тариф, нажав на кнопку 'Подписка'."
#     tariff, tokens = user
#     tariffs_order = {"Базовый": 1, "Продвинутый": 2, "Премиум": 3}
#     if tokens <= 0:
#         return False, "Ваш пакет токенов закончился."
#     if tariffs_order.get(tariff, 0) < tariffs_order.get(required_tariff, 0):
#         return False, "Ваш тариф не поддерживает эту функцию."
#     return True, None

# # Обновление баланса токенов
# async def update_tokens_balance(user_id, tokens_used):
#     cursor.execute("UPDATE users SET tokens_balance = tokens_balance - ? WHERE user_id=?", (tokens_used, user_id))
#     conn.commit()

# # Определение языка
# def detect_language(text):
#     return 'ru' if any('а' <= c <= 'я' or 'А' <= c <= 'Я' for c in text) else 'en'

# # Анимация загрузки
# async def show_loading_animation(msg: Message, base_text: str, dots=3, delay=0.5):
#     for _ in range(dots):
#         for i in range(dots + 1):
#             try:
#                 await msg.edit_text(f"{base_text} {'•' * i}")
#                 await asyncio.sleep(delay)
#             except:
#                 pass

# # Функция для создания клавиатур
# def create_keyboard(buttons):
#     keyboard = [
#         [KeyboardButton(text=button) for button in row]
#         for row in buttons
#     ]
#     return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

# # Создание клавиатур
# keyboard_level1 = create_keyboard([
#     ["🔍 Выбор модели", "🆕 Новый чат"],
#     ["📊 Остаток", "💼 Подписка"],
#     ["➕ Еще", "🛠 Поддержка"]
# ])

# keyboard_model_selection = create_keyboard([
#     ["🧠 GPT-4o", "🧠 GPT-4o-mini"],
#     ["🧠 o1-mini"],
#     ["🔙 Назад"]
# ])

# keyboard_tariff_selection = create_keyboard([
#     ["📉 Базовый", "📈 Продвинутый"],
#     ["🏆 Премиум"],
#     ["🔙 Назад"]
# ])

# keyboard_tariff_info = create_keyboard([
#     ["📉 Базовый", "📈 Продвинутый"],
#     ["🏆 Премиум"],
#     ["ℹ️ Инфо", "🔙 Назад"]
# ])

# # Обработчик команды /start
# @router.message(Command('start'))
# async def cmd_start(message: Message, state: FSMContext):
#     cursor.execute("SELECT * FROM users WHERE user_id=?", (message.from_user.id,))
#     if not cursor.fetchone():
#         initial_tokens = 500 if TESTING_MODE else 500
#         cursor.execute("INSERT INTO users (user_id, tokens_balance, tariff) VALUES (?, ?, ?)", 
#                        (message.from_user.id, initial_tokens, 'Базовый'))
#         conn.commit()
#         await message.answer(
#     "**Добро пожаловать!**\n"
#     "**У вас есть 500 токенов для пробного использования.**\n"
#     "**Вы можете приобрести подписку с большим функционалом согласно нашим [Тарифам](https://telegra.ph/Tarify-09-16) в нижнем меню.**\n"
#     "**[Наша оферта](https://telegra.ph/Oferta-09-16)**\n"
#     "**Выберите модель и начните пользоваться!**",
#     parse_mode="Markdown",
#     reply_markup=keyboard_level1
# )

#     else:
#         await message.answer("С возвращением! Вы можете начать использовать бота.", reply_markup=keyboard_level1)

# # Обработчик выбора тарифа
# @router.message(StateFilter(UserStates.selecting_tariff))
# async def process_tariff_selection(message: Message, state: FSMContext):
#     tariff = message.text
#     if tariff == "🔙 Назад":
#         await message.answer("Вы вернулись в главное меню.", reply_markup=keyboard_level1)
#         await state.clear()
#         return
#     if tariff == "ℹ️ Инфо":
#         info_message = (
#             "📋 **Информация о тарифах:**\n\n"
#             "🏆 **Премиум**:\n- Неограниченные токены\n- Все модели\n- Приоритетная поддержка\n Стоимость: 3 000 р\n\n"
#             "📈 **Продвинутый**:\n- 2000 токенов\n- Большинство моделей\n- Поддержка через бот\nСтоимость: 1 500 р\n\n"
#             "📉 **Базовый**:\n- 1000 токенов\n- Ограниченные модели\n- Поддержка через FAQ\n Стоимость:  300 р"
#         )
#         await message.answer(info_message, parse_mode="Markdown", reply_markup=keyboard_tariff_info)
#         return
#     if tariff not in ["📉 Базовый", "📈 Продвинутый", "🏆 Премиум"]:
#         await message.answer("Неверный выбор. Вернитесь в главное меню.", reply_markup=keyboard_level1)
#         await state.clear()
#         return
#     tariff_clean = tariff.split(' ')[-1]
#     if TESTING_MODE:
#         initialize_user(message.from_user.id, tariff_clean)
#         await message.answer(f"Вы приобрели тариф {tariff_clean}.", reply_markup=keyboard_level1)
#         await state.clear()
#     else:
#         prices = {
#             'Базовый': [types.LabeledPrice(label='📉 Базовый', amount=1000 * 100)],
#             'Продвинутый': [types.LabeledPrice(label='📈 Продвинутый', amount=2000 * 100)],
#             'Премиум': [types.LabeledPrice(label='🏆 Премиум', amount=3000 * 100)],
#         }
#         await bot.send_invoice(
#             chat_id=message.chat.id,
#             title=f'Покупка тарифа {tariff_clean}',
#             description=f'Оплата тарифа {tariff_clean}',
#             payload=f'tariff_{tariff_clean}',
#             provider_token=PAYMENT_PROVIDER_TOKEN,
#             currency='RUB',
#             prices=prices[tariff_clean],
#             start_parameter='purchase_tariff',
#         )
#         await state.update_data(selected_tariff=tariff_clean)
#         await state.set_state(UserStates.purchasing_tariff)

# # Обработчик пред-проверки оплаты
# @router.pre_checkout_query()
# async def process_pre_checkout_query(pre_checkout_query: types.PreCheckoutQuery):
#     await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

# # Обработчик успешной оплаты
# @router.message(F.content_type == ContentType.SUCCESSFUL_PAYMENT)
# async def process_successful_payment(message: Message, state: FSMContext):
#     data = await state.get_data()
#     tariff = data.get('selected_tariff')
#     if tariff:
#         initialize_user(message.from_user.id, tariff)
#         await message.answer(f"Спасибо за покупку! Вы приобрели тариф {tariff}.", reply_markup=keyboard_level1)
#         await state.clear()
#     else:
#         await message.answer("Ошибка: не удалось определить выбранный тариф.", reply_markup=keyboard_level1)

# # Обработчик текстовых сообщений
# @router.message(F.content_type == ContentType.TEXT)
# async def handle_text(message: Message, state: FSMContext):
#     text = message.text
#     user_id = message.from_user.id
#     current_state = await state.get_state()

#     # Обработка кнопок
#     if text == "📊 Остаток":
#         await show_balance(message)
#         return
#     elif text == "💼 Подписка":
#         await message.answer("Выберите тариф:", reply_markup=keyboard_tariff_info)
#         await state.set_state(UserStates.selecting_tariff)
#         return
#     elif text == "🛠 Поддержка":
#         await message.answer(f"Свяжитесь с поддержкой: @{SUPPORT_BOT_USERNAME}", reply_markup=keyboard_level1)
#         return
#     elif text == "➕ Еще":
#         await message.answer(
#             "Оферта: https://telegra.ph/Oferta-09-16",
#             parse_mode="Markdown",
#             reply_markup=keyboard_level1
#         )
#         return
#     elif text == "🔍 Выбор модели":
#         await message.answer("Выберите модель:", reply_markup=keyboard_model_selection)
#         await state.set_state(UserStates.selecting_model)
#         return
#     elif text == "🆕 Новый чат":
#         await state.update_data(conversation=[])
#         await message.answer("Контекст очищен. Начните новый чат.", reply_markup=keyboard_level1)
#         return

#     # Обработка выбора модели
#     if current_state == UserStates.selecting_model.state:
#         await handle_model_selection(message, state, text)
#         return

#     # Проверка, выбрал ли пользователь модель
#     cursor.execute("SELECT has_selected_model FROM users WHERE user_id=?", (user_id,))
#     result = cursor.fetchone()
#     if not result or not result[0]:
#         await message.answer("Пожалуйста, выберите модель для продолжения.", reply_markup=keyboard_model_selection)
#         await state.set_state(UserStates.selecting_model)
#         return

#     # Проверка доступа и баланса
#     access, error = await check_user_access(user_id)
#     if not access:
#         await handle_access_error(message, state, error)
#         return

#     # Список триггерных фраз для генерации изображений
#     image_triggers = [
#         "сгенерируй фото", "создай фото", "создай изображение", "сделай фото",
#         "сделай изображение", "мне нужно фото", "мне нужно изображение",
#         "нарисуй фото", "нарисуй изображение", "нарисуй картинку",
#         "сгенерируй картинку", "сделай картинку", "создай картинку",
#         "сгенерируй изображение", "мне нужна картинка"
#     ]
#     if any(trigger in text.lower() for trigger in image_triggers):
#         await handle_image_generation(message, state, text)
#         return

#     # Работа с контекстом и генерация ответа
#     await handle_chat_response(message, state)

# # Функции для обработки различных действий
# async def show_balance(message: Message):
#     cursor.execute("SELECT tokens_balance, tariff FROM users WHERE user_id=?", (message.from_user.id,))
#     result = cursor.fetchone()
#     if result:
#         tokens, tariff = result
#         await message.answer(
#             f"📦 **Тариф:** {tariff}\n🔢 **Токены:** {tokens}",
#             parse_mode="Markdown",
#             reply_markup=keyboard_level1
#         )
#     else:
#         await message.answer("Не удалось получить информацию об остатке.", reply_markup=keyboard_level1)

# async def handle_access_error(message: Message, state: FSMContext, error_message: str):
#     if error_message == "Ваш пакет токенов закончился.":
#         await message.answer("Ваш пакет токенов закончился. Выберите новый тариф:", reply_markup=keyboard_tariff_info)
#         await state.set_state(UserStates.selecting_tariff)
#     else:
#         await message.answer(error_message)

# async def handle_model_selection(message: Message, state: FSMContext, model_name: str):
#     user_id = message.from_user.id
#     if model_name == "🔙 Назад":
#         await message.answer("Вернулись в главное меню.", reply_markup=keyboard_level1)
#         await state.clear()
#         return
#     models = {"🧠 GPT-4o": "gpt-4o", "🧠 GPT-4o-mini": "gpt-4o-mini", "🧠 o1-mini": "o1-mini"}
#     if model_name not in models:
#         await message.answer("Выберите доступную модель.", reply_markup=keyboard_model_selection)
#         return
#     model_id = models[model_name]
#     if model_id == "o1-mini":
#         access, error = await check_user_access(user_id, "Премиум")
#         if not access:
#             await message.answer("Модель 'o1-mini' доступна только в тарифе 'Премиум'.", reply_markup=keyboard_tariff_info)
#             await state.set_state(UserStates.selecting_tariff)
#             return
#     cursor.execute("UPDATE users SET model=?, has_selected_model=1 WHERE user_id=?", (model_id, user_id))
#     conn.commit()
#     await message.answer(f"Вы выбрали модель {model_name}.", reply_markup=keyboard_level1)
#     await state.clear()

# async def handle_image_generation(message: Message, state: FSMContext, prompt: str):
#     user_id = message.from_user.id
#     cursor.execute("SELECT model FROM users WHERE user_id=?", (user_id,))
#     result = cursor.fetchone()
#     model_id = result[0] if result else "o1-mini"
#     if model_id == "o1-mini":
#         await message.answer("Генерация изображений недоступна для модели 'o1-mini'.", reply_markup=keyboard_level1)
#         return
#     access, error = await check_user_access(user_id, "Продвинутый")
#     if not access:
#         await handle_access_error(message, state, error)
#         return
#     msg = await message.answer("Генерация изображения")
#     try:
#         animation = asyncio.create_task(show_loading_animation(msg, "Генерация изображения"))
#         response = await asyncio.to_thread(openai.Image.create, prompt=prompt, n=1, size="1024x1024", model="dall-e-3")
#         animation.cancel()
#         image_url = response['data'][0]['url']
#         await msg.delete()
#         await message.answer_photo(image_url, reply_markup=keyboard_level1)
#         await update_tokens_balance(user_id, 100)
#     except Exception as e:
#         logging.error(f"Ошибка генерации изображения: {e}")
#         await msg.delete()
#         await message.answer("Ошибка при генерации изображения.", reply_markup=keyboard_level1)

# async def handle_chat_response(message: Message, state: FSMContext):
#     user_id = message.from_user.id
#     text = message.text
#     data = await state.get_data()
#     conversation = data.get('conversation', []) + [{"role": "user", "content": text}]
#     conversation_with_system = [{"role": "system", "content": "Отвечай всегда на русском языке."}] + conversation
#     msg = await message.answer("Генерация ответа")
#     cursor.execute("SELECT model FROM users WHERE user_id=?", (user_id,))
#     result = cursor.fetchone()
#     model_id = result[0] if result else "o1-mini"
#     try:
#         if model_id == "o1-mini":
#             animation = asyncio.create_task(show_loading_animation(msg, "[думаю]", dots=3, delay=0.7))
#         else:
#             animation = asyncio.create_task(show_loading_animation(msg, "Генерация ответа"))
#         response = await asyncio.to_thread(openai.ChatCompletion.create, model=model_id, messages=conversation_with_system)
#         animation.cancel()
#         reply = response['choices'][0]['message']['content']
#         conversation.append({"role": "assistant", "content": reply})
#         await state.update_data(conversation=conversation)
#         await msg.delete()
#         await message.answer(reply, reply_markup=keyboard_level1)
#         await update_tokens_balance(user_id, response['usage']['total_tokens'])
#     except Exception as e:
#         logging.error(f"Ошибка генерации ответа: {e}")
#         await msg.delete()
#         await message.answer("Ошибка при обработке запроса.", reply_markup=keyboard_level1)

# # Обработчик голосовых сообщений
# @router.message(F.voice)
# async def handle_voice_message(message: Message, state: FSMContext):
#     user_id = message.from_user.id
#     access, error = await check_user_access(user_id, "Премиум")
#     if not access:
#         await message.answer(
#             "🔒 Распознавание голосовых сообщений доступно только для 'Премиум'.",
#             reply_markup=keyboard_tariff_info
#         )
#         await state.set_state(UserStates.selecting_tariff)
#         return
#     cursor.execute("SELECT tokens_balance FROM users WHERE user_id=?", (user_id,))
#     result = cursor.fetchone()
#     tokens = result[0] if result else 0
#     if tokens <= 0:
#         await message.answer("❌ Токены закончились. Выберите тариф:", reply_markup=keyboard_tariff_info)
#         await state.set_state(UserStates.selecting_tariff)
#         return
#     msg = await message.answer("[распознавание аудио]")
#     try:
#         file = await bot.get_file(message.voice.file_id)
#         voice = await bot.download_file(file.file_path)
#         audio = AudioSegment.from_file(BytesIO(voice.read()), format="ogg")
#         wav_io = BytesIO()
#         audio.export(wav_io, format="wav")
#         wav_io.name = "temp_audio.wav"
#         transcript = await asyncio.to_thread(openai.Audio.transcribe, "whisper-1", wav_io)
#         recognized_text = transcript["text"]
#         await show_loading_animation(msg, "Генерация ответа")
#         data = await state.get_data()
#         conversation = data.get('conversation', []) + [{"role": "user", "content": recognized_text}]
#         conversation_with_system = [{"role": "system", "content": "Отвечай всегда на русском языке."}] + conversation
#         cursor.execute("SELECT model FROM users WHERE user_id=?", (user_id,))
#         result = cursor.fetchone()
#         model_id = result[0] if result else "o1-mini"
#         response = await asyncio.to_thread(openai.ChatCompletion.create, model=model_id, messages=conversation_with_system)
#         reply = response['choices'][0]['message']['content']
#         conversation.append({"role": "assistant", "content": reply})
#         await state.update_data(conversation=conversation)
#         await msg.delete()
#         await message.answer(reply, reply_markup=keyboard_level1)
#         await update_tokens_balance(user_id, response['usage']['total_tokens'])
#     except Exception as e:
#         logging.error(f"Ошибка обработки голоса: {e}")
#         await msg.delete()
#         await message.answer("Ошибка при обработке голосового сообщения.", reply_markup=keyboard_level1)

#         # Функция для удаления пользователя
# def delete_user(user_id):
#     cursor.execute("DELETE FROM users WHERE user_id=?", (user_id,))
#     conn.commit()

# # Пример использования функции для удаления пользователя с user_id 7451063626
# delete_user(7451063626)


# async def main():
#     # Включаем Router в Dispatcher
#     dp.include_router(router)

#     # Запуск бота
#     try:
#         await dp.start_polling(bot)
#     finally:
#         # Закрываем соединение с базой данных
#         conn.close()

# if __name__ == '__main__':
#     try:
#         asyncio.run(main())
#     except (KeyboardInterrupt, SystemExit):
#         logging.info("Бот остановлен!")







import asyncio
import logging
import os
import sqlite3
from aiogram import Bot, Dispatcher, types, Router, F
from aiogram.types import ContentType, Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.filters import Command, StateFilter
import openai
from dotenv import load_dotenv
from pydub import AudioSegment
from io import BytesIO
import hashlib
from urllib.parse import urlencode
import uuid

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Проверка необходимых переменных окружения
API_TOKEN, OPENAI_API_KEY, PAYMENT_PROVIDER_TOKEN, SUPPORT_BOT_USERNAME = (
    os.getenv(key) for key in ['API_TOKEN', 'OPENAI_API_KEY', 'PAYMENT_PROVIDER_TOKEN', 'SUPPORT_BOT_USERNAME']
)
if not all([API_TOKEN, OPENAI_API_KEY, PAYMENT_PROVIDER_TOKEN, SUPPORT_BOT_USERNAME]):
    logging.error("Пожалуйста, убедитесь, что все токены и ключи API заданы в файле .env")
    exit(1)

TESTING_MODE = False  # Установите True для тестирования

# Инициализация бота и диспетчера
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
openai.api_key = OPENAI_API_KEY

# Определение состояний
class UserStates(StatesGroup):
    selecting_tariff = State()
    selecting_model = State()
    purchasing_tariff = State()

# Настройка базы данных
conn = sqlite3.connect('users.db')
cursor = conn.cursor()

# Создание таблицы users
cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        tariff TEXT DEFAULT 'Базовый',
        requests_left INTEGER,
        tokens_balance INTEGER DEFAULT 500,
        model TEXT,
        has_selected_model INTEGER DEFAULT 0
    )
''')

# Создание таблицы payments
cursor.execute('''
    CREATE TABLE IF NOT EXISTS payments (
        inv_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount REAL,
        tariff TEXT,
        status TEXT
    )
''')

conn.commit()


# Функция для инициализации/обновления пользователя
def initialize_user(user_id, tariff):
    tariff_tokens = {'Базовый': 1000, 'Продвинутый': 2000, 'Премиум': 3000}
    tokens_to_add = tariff_tokens.get(tariff, 0)
    cursor.execute("SELECT tokens_balance FROM users WHERE user_id=?", (user_id,))
    result = cursor.fetchone()
    if result:
        tokens_balance = result[0] + tokens_to_add
        cursor.execute("UPDATE users SET tariff=?, tokens_balance=? WHERE user_id=?", (tariff, tokens_balance, user_id))
    else:
        cursor.execute("""
            INSERT INTO users (user_id, tariff, tokens_balance, has_selected_model) 
            VALUES (?, ?, ?, 0)
        """, (user_id, tariff, tokens_to_add))
    conn.commit()

# Проверка доступа пользователя
async def check_user_access(user_id, required_tariff="Базовый"):
    cursor.execute("SELECT tariff, tokens_balance FROM users WHERE user_id=?", (user_id,))
    user = cursor.fetchone()
    if not user:
        return False, "У вас нет активного тарифа. Пожалуйста, выберите тариф, нажав на кнопку 'Подписка'."
    tariff, tokens = user
    tariffs_order = {"Базовый": 1, "Продвинутый": 2, "Премиум": 3}
    if tokens <= 0:
        return False, "Ваш пакет токенов закончился."
    if tariffs_order.get(tariff, 0) < tariffs_order.get(required_tariff, 0):
        return False, "Ваш тариф не поддерживает эту функцию."
    return True, None

# Обновление баланса токенов
async def update_tokens_balance(user_id, tokens_used):
    cursor.execute("UPDATE users SET tokens_balance = tokens_balance - ? WHERE user_id=?", (tokens_used, user_id))
    conn.commit()

# Определение языка
def detect_language(text):
    return 'ru' if any('а' <= c <= 'я' or 'А' <= c <= 'Я' for c in text) else 'en'

# Анимация загрузки
async def show_loading_animation(msg: Message, base_text: str, dots=3, delay=0.5):
    for _ in range(dots):
        for i in range(dots + 1):
            try:
                await msg.edit_text(f"{base_text} {'•' * i}")
                await asyncio.sleep(delay)
            except:
                pass

# Функция для создания клавиатур
def create_keyboard(buttons):
    keyboard = [
        [KeyboardButton(text=button) for button in row]
        for row in buttons
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)



ROBOKASSA_MERCHANT_LOGIN = os.getenv('ROBOKASSA_MERCHANT_LOGIN')
ROBOKASSA_PASSWORD1 = os.getenv('ROBOKASSA_PASSWORD1')
ROBOKASSA_RESULT_URL = os.getenv('ROBOKASSA_RESULT_URL')
ROBOKASSA_SUCCESS_URL = os.getenv('ROBOKASSA_SUCCESS_URL')
ROBOKASSA_FAIL_URL = os.getenv('ROBOKASSA_FAIL_URL')

def generate_robokassa_link(out_sum, description, user_id):
    try:
        # Приведение типов данных
        user_id_int = int(user_id)
        out_sum_float = float(out_sum)
        out_sum_str = f"{out_sum_float:.2f}"  # Форматирование суммы с двумя десятичными знаками

        # Логирование параметров
        logging.info(f"Generating payment for user_id: {user_id_int}, out_sum: {out_sum_float}, description: {description}")

        # Сохранение платежа в базе данных
        cursor.execute(
            "INSERT INTO payments (user_id, amount, tariff, status) VALUES (?, ?, ?, ?)", 
            (user_id_int, out_sum_float, description, 'pending')
        )
        conn.commit()

        # Получаем inv_id последнего вставленного платежа
        inv_id = cursor.lastrowid

        # Формирование строки для подписи
        signature_string = f"{ROBOKASSA_MERCHANT_LOGIN}:{out_sum_str}:{inv_id}:{ROBOKASSA_PASSWORD1}"
        signature = hashlib.md5(signature_string.encode('utf-8')).hexdigest()

        params = {
            'MrchLogin': ROBOKASSA_MERCHANT_LOGIN,
            'OutSum': out_sum_str,  # Используем форматированную сумму
            'InvId': inv_id,
            'Desc': description,
            'SignatureValue': signature,
            'Culture': 'ru',
            'Encoding': 'utf-8',
            # Дополнительные параметры, если необходимы
            # 'Shp_user': user_id
        }

        url = f"https://auth.robokassa.ru/Merchant/Index.aspx?{urlencode(params)}"
        
        # Логирование сгенерированной ссылки
        logging.info(f"Generated Robokassa link: {url}")
        
        return url
    except Exception as e:
        logging.error(f"Error in generate_robokassa_link: {e}")
        raise



# Создание клавиатур
keyboard_level1 = create_keyboard([
    ["🔍 Выбор модели", "🆕 Новый чат"],
    ["📊 Остаток", "💼 Подписка"],
    ["➕ Еще", "🛠 Поддержка"]
])

keyboard_model_selection = create_keyboard([
    ["🧠 GPT-4o", "🧠 GPT-4o-mini"],
    ["🧠 o1-mini"],
    ["🔙 Назад"]
])

keyboard_tariff_selection = create_keyboard([
    ["📉 Базовый", "📈 Продвинутый"],
    ["🏆 Премиум"],
    ["🔙 Назад"]
])

keyboard_tariff_info = create_keyboard([
    ["📉 Базовый", "📈 Продвинутый"],
    ["🏆 Премиум"],
    ["ℹ️ Инфо", "🔙 Назад"]
])

# Обработчик команды /start
@router.message(Command('start'))
async def cmd_start(message: Message, state: FSMContext):
    cursor.execute("SELECT * FROM users WHERE user_id=?", (message.from_user.id,))
    if not cursor.fetchone():
        initial_tokens = 500 if TESTING_MODE else 500
        cursor.execute("INSERT INTO users (user_id, tokens_balance, tariff) VALUES (?, ?, ?)", 
                       (message.from_user.id, initial_tokens, 'Базовый'))
        conn.commit()
        await message.answer(
    "**Добро пожаловать!**\n"
    "**У вас есть 500 токенов для пробного использования.**\n"
    "**Вы можете приобрести подписку с большим функционалом согласно нашим [Тарифам](https://telegra.ph/Tarify-09-16) в нижнем меню.**\n"
    "**[Наша оферта](https://telegra.ph/Oferta-09-16)**\n"
    "**Выберите модель и начните пользоваться!**",
    parse_mode="Markdown",
    reply_markup=keyboard_level1
)

    else:
        await message.answer("С возвращением! Вы можете начать использовать бота.", reply_markup=keyboard_level1)

# Обработчик выбора тарифа
@router.message(StateFilter(UserStates.selecting_tariff))
async def process_tariff_selection(message: Message, state: FSMContext):
    try:
        tariff = message.text
        logging.info(f"User {message.from_user.id} selected tariff: {tariff}")

        if tariff == "🔙 Назад":
            await message.answer("Вы вернулись в главное меню.", reply_markup=keyboard_level1)
            await state.clear()
            return
        
        if tariff == "ℹ️ Инфо":
            info_message = (
                "📋 **Информация о тарифах:**\n\n"
                "🏆 **Премиум**:\n- Неограниченные токены\n- Все модели\n- Приоритетная поддержка\n Стоимость: 3 000 р\n\n"
                "📈 **Продвинутый**:\n- 2000 токенов\n- Большинство моделей\n- Поддержка через бот\nСтоимость: 1 500 р\n\n"
                "📉 **Базовый**:\n- 1000 токенов\n- Ограниченные модели\n- Поддержка через FAQ\n Стоимость:  300 р"
            )
            await message.answer(info_message, parse_mode="Markdown", reply_markup=keyboard_tariff_info)
            return
        
        if tariff not in ["📉 Базовый", "📈 Продвинутый", "🏆 Премиум"]:
            await message.answer("Неверный выбор. Вернитесь в главное меню.", reply_markup=keyboard_level1)
            await state.clear()
            return
        
        tariff_clean = tariff.split(' ')[-1]
        
        if TESTING_MODE:
            initialize_user(message.from_user.id, tariff_clean)
            await message.answer(f"Вы приобрели тариф {tariff_clean}.", reply_markup=keyboard_level1)
            await state.clear()
        else:
            # Определение стоимости тарифа
            tariff_prices = {
                'Базовый': 300,        # Стоимость в рублях
                'Продвинутый': 1500,
                'Премиум': 3000
            }
            out_sum = tariff_prices.get(tariff_clean, 0)
            description = f'Покупка тарифа {tariff_clean}'

            # Генерация ссылки на оплату
            payment_link = generate_robokassa_link(out_sum, tariff_clean, message.from_user.id)

            # Отправка ссылки пользователю
            await message.answer(
                f"Для приобретения тарифа **{tariff_clean}** перейдите по [ссылке для оплаты]({payment_link}).",
                parse_mode="Markdown",
                disable_web_page_preview=True,
                reply_markup=keyboard_level1
            )
            await state.clear()
    except Exception as e:
        logging.error(f"Error in process_tariff_selection: {e}")
        await message.answer("Произошла ошибка при обработке вашего запроса. Пожалуйста, попробуйте позже.")
        await state.clear()





# Обработчик текстовых сообщений
@router.message(F.content_type == ContentType.TEXT)
async def handle_text(message: Message, state: FSMContext):
    text = message.text
    user_id = message.from_user.id
    current_state = await state.get_state()

    # Обработка кнопок
    if text == "📊 Остаток":
        await show_balance(message)
        return
    elif text == "💼 Подписка":
        await message.answer("Выберите тариф:", reply_markup=keyboard_tariff_info)
        await state.set_state(UserStates.selecting_tariff)
        return
    elif text == "🛠 Поддержка":
        await message.answer(f"Свяжитесь с поддержкой: @{SUPPORT_BOT_USERNAME}", reply_markup=keyboard_level1)
        return
    elif text == "➕ Еще":
        await message.answer(
            "Оферта: https://telegra.ph/Oferta-09-16",
            parse_mode="Markdown",
            reply_markup=keyboard_level1
        )
        return
    elif text == "🔍 Выбор модели":
        await message.answer("Выберите модель:", reply_markup=keyboard_model_selection)
        await state.set_state(UserStates.selecting_model)
        return
    elif text == "🆕 Новый чат":
        await state.update_data(conversation=[])
        await message.answer("Контекст очищен. Начните новый чат.", reply_markup=keyboard_level1)
        return

    # Обработка выбора модели
    if current_state == UserStates.selecting_model.state:
        await handle_model_selection(message, state, text)
        return

    # Проверка, выбрал ли пользователь модель
    cursor.execute("SELECT has_selected_model FROM users WHERE user_id=?", (user_id,))
    result = cursor.fetchone()
    if not result or not result[0]:
        await message.answer("Пожалуйста, выберите модель для продолжения.", reply_markup=keyboard_model_selection)
        await state.set_state(UserStates.selecting_model)
        return

    # Проверка доступа и баланса
    access, error = await check_user_access(user_id)
    if not access:
        await handle_access_error(message, state, error)
        return

    # Список триггерных фраз для генерации изображений
    image_triggers = [
        "сгенерируй фото", "создай фото", "создай изображение", "сделай фото",
        "сделай изображение", "мне нужно фото", "мне нужно изображение",
        "нарисуй фото", "нарисуй изображение", "нарисуй картинку",
        "сгенерируй картинку", "сделай картинку", "создай картинку",
        "сгенерируй изображение", "мне нужна картинка"
    ]
    if any(trigger in text.lower() for trigger in image_triggers):
        await handle_image_generation(message, state, text)
        return

    # Работа с контекстом и генерация ответа
    await handle_chat_response(message, state)

# Функции для обработки различных действий
async def show_balance(message: Message):
    cursor.execute("SELECT tokens_balance, tariff FROM users WHERE user_id=?", (message.from_user.id,))
    result = cursor.fetchone()
    if result:
        tokens, tariff = result
        await message.answer(
            f"📦 **Тариф:** {tariff}\n🔢 **Токены:** {tokens}",
            parse_mode="Markdown",
            reply_markup=keyboard_level1
        )
    else:
        await message.answer("Не удалось получить информацию об остатке.", reply_markup=keyboard_level1)

async def handle_access_error(message: Message, state: FSMContext, error_message: str):
    if error_message == "Ваш пакет токенов закончился.":
        await message.answer("Ваш пакет токенов закончился. Выберите новый тариф:", reply_markup=keyboard_tariff_info)
        await state.set_state(UserStates.selecting_tariff)
    else:
        await message.answer(error_message)

async def handle_model_selection(message: Message, state: FSMContext, model_name: str):
    user_id = message.from_user.id
    if model_name == "🔙 Назад":
        await message.answer("Вернулись в главное меню.", reply_markup=keyboard_level1)
        await state.clear()
        return
    models = {"🧠 GPT-4o": "gpt-4o", "🧠 GPT-4o-mini": "gpt-4o-mini", "🧠 o1-mini": "o1-mini"}
    if model_name not in models:
        await message.answer("Выберите доступную модель.", reply_markup=keyboard_model_selection)
        return
    model_id = models[model_name]
    if model_id == "o1-mini":
        access, error = await check_user_access(user_id, "Премиум")
        if not access:
            await message.answer("Модель 'o1-mini' доступна только в тарифе 'Премиум'.", reply_markup=keyboard_tariff_info)
            await state.set_state(UserStates.selecting_tariff)
            return
    cursor.execute("UPDATE users SET model=?, has_selected_model=1 WHERE user_id=?", (model_id, user_id))
    conn.commit()
    await message.answer(f"Вы выбрали модель {model_name}.", reply_markup=keyboard_level1)
    await state.clear()

async def handle_image_generation(message: Message, state: FSMContext, prompt: str):
    user_id = message.from_user.id
    cursor.execute("SELECT model FROM users WHERE user_id=?", (user_id,))
    result = cursor.fetchone()
    model_id = result[0] if result else "o1-mini"
    if model_id == "o1-mini":
        await message.answer("Генерация изображений недоступна для модели 'o1-mini'.", reply_markup=keyboard_level1)
        return
    access, error = await check_user_access(user_id, "Продвинутый")
    if not access:
        await handle_access_error(message, state, error)
        return
    msg = await message.answer("Генерация изображения")
    try:
        animation = asyncio.create_task(show_loading_animation(msg, "Генерация изображения"))
        response = await asyncio.to_thread(openai.Image.create, prompt=prompt, n=1, size="1024x1024", model="dall-e-3")
        animation.cancel()
        image_url = response['data'][0]['url']
        await msg.delete()
        await message.answer_photo(image_url, reply_markup=keyboard_level1)
        await update_tokens_balance(user_id, 100)
    except Exception as e:
        logging.error(f"Ошибка генерации изображения: {e}")
        await msg.delete()
        await message.answer("Ошибка при генерации изображения.", reply_markup=keyboard_level1)

async def handle_chat_response(message: Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text
    data = await state.get_data()
    conversation = data.get('conversation', []) + [{"role": "user", "content": text}]
    conversation_with_system = [{"role": "system", "content": "Отвечай всегда на русском языке."}] + conversation
    msg = await message.answer("Генерация ответа")
    cursor.execute("SELECT model FROM users WHERE user_id=?", (user_id,))
    result = cursor.fetchone()
    model_id = result[0] if result else "o1-mini"
    try:
        if model_id == "o1-mini":
            animation = asyncio.create_task(show_loading_animation(msg, "[думаю]", dots=3, delay=0.7))
        else:
            animation = asyncio.create_task(show_loading_animation(msg, "Генерация ответа"))
        response = await asyncio.to_thread(openai.ChatCompletion.create, model=model_id, messages=conversation_with_system)
        animation.cancel()
        reply = response['choices'][0]['message']['content']
        conversation.append({"role": "assistant", "content": reply})
        await state.update_data(conversation=conversation)
        await msg.delete()
        await message.answer(reply, reply_markup=keyboard_level1)
        await update_tokens_balance(user_id, response['usage']['total_tokens'])
    except Exception as e:
        logging.error(f"Ошибка генерации ответа: {e}")
        await msg.delete()
        await message.answer("Ошибка при обработке запроса.", reply_markup=keyboard_level1)

# Обработчик голосовых сообщений
@router.message(F.voice)
async def handle_voice_message(message: Message, state: FSMContext):
    user_id = message.from_user.id
    access, error = await check_user_access(user_id, "Премиум")
    if not access:
        await message.answer(
            "🔒 Распознавание голосовых сообщений доступно только для 'Премиум'.",
            reply_markup=keyboard_tariff_info
        )
        await state.set_state(UserStates.selecting_tariff)
        return
    cursor.execute("SELECT tokens_balance FROM users WHERE user_id=?", (user_id,))
    result = cursor.fetchone()
    tokens = result[0] if result else 0
    if tokens <= 0:
        await message.answer("❌ Токены закончились. Выберите тариф:", reply_markup=keyboard_tariff_info)
        await state.set_state(UserStates.selecting_tariff)
        return
    msg = await message.answer("[распознавание аудио]")
    try:
        file = await bot.get_file(message.voice.file_id)
        voice = await bot.download_file(file.file_path)
        audio = AudioSegment.from_file(BytesIO(voice.read()), format="ogg")
        wav_io = BytesIO()
        audio.export(wav_io, format="wav")
        wav_io.name = "temp_audio.wav"
        transcript = await asyncio.to_thread(openai.Audio.transcribe, "whisper-1", wav_io)
        recognized_text = transcript["text"]
        await show_loading_animation(msg, "Генерация ответа")
        data = await state.get_data()
        conversation = data.get('conversation', []) + [{"role": "user", "content": recognized_text}]
        conversation_with_system = [{"role": "system", "content": "Отвечай всегда на русском языке."}] + conversation
        cursor.execute("SELECT model FROM users WHERE user_id=?", (user_id,))
        result = cursor.fetchone()
        model_id = result[0] if result else "o1-mini"
        response = await asyncio.to_thread(openai.ChatCompletion.create, model=model_id, messages=conversation_with_system)
        reply = response['choices'][0]['message']['content']
        conversation.append({"role": "assistant", "content": reply})
        await state.update_data(conversation=conversation)
        await msg.delete()
        await message.answer(reply, reply_markup=keyboard_level1)
        await update_tokens_balance(user_id, response['usage']['total_tokens'])
    except Exception as e:
        logging.error(f"Ошибка обработки голоса: {e}")
        await msg.delete()
        await message.answer("Ошибка при обработке голосового сообщения.", reply_markup=keyboard_level1)

#         # Функция для удаления пользователя
# def delete_user(user_id):
#     cursor.execute("DELETE FROM users WHERE user_id=?", (user_id,))
#     conn.commit()

# # Пример использования функции для удаления пользователя с user_id 7451063626
# delete_user(7451063626)


async def main():
    # Включаем Router в Dispatcher
    dp.include_router(router)

    # Запуск бота
    try:
        await dp.start_polling(bot)
    finally:
        # Закрываем соединение с базой данных
        conn.close()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен!")




