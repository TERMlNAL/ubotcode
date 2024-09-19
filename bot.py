
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

TESTING_MODE = False  # T Установите True для тестирования

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
    with sqlite3.connect('users.db') as conn:
        cursor = conn.cursor()
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

        # Открытие нового соединения для каждой операции
        with sqlite3.connect('users.db') as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO payments (user_id, amount, tariff, status) VALUES (?, ?, ?, ?)", 
                (user_id_int, out_sum_float, description, 'pending')
            )
            conn.commit()

            # Получаем inv_id последнего вставленного платежа
            inv_id = cursor.lastrowid
            logging.info(f"Inserted payment with inv_id: {inv_id}")

        # Формирование строки для подписи
        signature_string = f"{ROBOKASSA_MERCHANT_LOGIN}:{out_sum_str}:{inv_id}:{ROBOKASSA_PASSWORD1}"
        signature = hashlib.md5(signature_string.encode('utf-8')).hexdigest()

        params = {
        'MrchLogin': ROBOKASSA_MERCHANT_LOGIN,
        'OutSum': out_sum_str,
        'InvId': inv_id,
        'Desc': description,
        'SignatureValue': signature,
        'Culture': 'ru',
        'Encoding': 'utf-8',
        'IsTest': '1' if TESTING_MODE else '0',
        'ReturnUrl': ROBOKASSA_SUCCESS_URL,
        'CancelUrl': ROBOKASSA_FAIL_URL
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
                'Базовый': 3,        # Стоимость в рублях
                'Продвинутый': 15,
                'Премиум': 30
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
# import hashlib
# from urllib.parse import urlencode

# # Загрузка переменных окружения
# load_dotenv()

# # Настройка логирования
# logging.basicConfig(level=logging.INFO)

# # Проверка необходимых переменных окружения
# API_TOKEN = os.getenv('API_TOKEN')
# OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
# PAYMENT_PROVIDER_TOKEN = os.getenv('PAYMENT_PROVIDER_TOKEN')
# SUPPORT_BOT_USERNAME = os.getenv('SUPPORT_BOT_USERNAME')

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

# # Настройка базы данных (создание таблиц)
# def setup_database():
#     with sqlite3.connect('users.db') as conn:
#         cursor = conn.cursor()
#         # Создание таблицы users
#         cursor.execute('''
#             CREATE TABLE IF NOT EXISTS users (
#                 user_id INTEGER PRIMARY KEY,
#                 tariff TEXT DEFAULT 'Базовый',
#                 requests_left INTEGER,
#                 tokens_balance INTEGER DEFAULT 500,
#                 model TEXT,
#                 has_selected_model INTEGER DEFAULT 0
#             )
#         ''')
#         # Создание таблицы payments
#         cursor.execute('''
#             CREATE TABLE IF NOT EXISTS payments (
#                 inv_id INTEGER PRIMARY KEY AUTOINCREMENT,
#                 user_id INTEGER,
#                 amount REAL,
#                 tariff TEXT,
#                 status TEXT
#             )
#         ''')
#         conn.commit()

# # Вызов функции настройки базы данных
# setup_database()

# # Функция для инициализации/обновления пользователя
# def initialize_user(user_id, tariff):
#     tariff_tokens = {'Базовый': 1000, 'Продвинутый': 2000, 'Премиум': 3000}
#     tokens_to_add = tariff_tokens.get(tariff, 0)
#     with sqlite3.connect('users.db') as conn:
#         cursor = conn.cursor()
#         cursor.execute("SELECT tokens_balance FROM users WHERE user_id=?", (user_id,))
#         result = cursor.fetchone()
#         if result:
#             tokens_balance = result[0] + tokens_to_add
#             cursor.execute("UPDATE users SET tariff=?, tokens_balance=? WHERE user_id=?", (tariff, tokens_balance, user_id))
#         else:
#             cursor.execute("""
#                 INSERT INTO users (user_id, tariff, tokens_balance, has_selected_model) 
#                 VALUES (?, ?, ?, 0)
#             """, (user_id, tariff, tokens_to_add))
#         conn.commit()

# # Проверка доступа пользователя
# async def check_user_access(user_id, required_tariff="Базовый"):
#     with sqlite3.connect('users.db') as conn:
#         cursor = conn.cursor()
#         cursor.execute("SELECT tariff, tokens_balance FROM users WHERE user_id=?", (user_id,))
#         user = cursor.fetchone()
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
#     with sqlite3.connect('users.db') as conn:
#         cursor = conn.cursor()
#         cursor.execute("UPDATE users SET tokens_balance = tokens_balance - ? WHERE user_id=?", (tokens_used, user_id))
#         conn.commit()

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

# # Переменные для Робокассы
# ROBOKASSA_MERCHANT_LOGIN = os.getenv('ROBOKASSA_MERCHANT_LOGIN')
# ROBOKASSA_PASSWORD1 = os.getenv('ROBOKASSA_PASSWORD1')
# ROBOKASSA_RESULT_URL = os.getenv('ROBOKASSA_RESULT_URL')
# ROBOKASSA_SUCCESS_URL = os.getenv('ROBOKASSA_SUCCESS_URL')
# ROBOKASSA_FAIL_URL = os.getenv('ROBOKASSA_FAIL_URL')

# def generate_robokassa_link(out_sum, description, user_id):
#     try:
#         # Приведение типов данных
#         user_id_int = int(user_id)
#         out_sum_float = float(out_sum)
#         out_sum_str = f"{out_sum_float:.2f}"  # Форматирование суммы с двумя десятичными знаками

#         # Логирование параметров
#         logging.info(f"Generating payment for user_id: {user_id_int}, out_sum: {out_sum_float}, description: {description}")

#         # Открытие нового соединения для каждой операции
#         with sqlite3.connect('users.db') as conn:
#             cursor = conn.cursor()
#             cursor.execute(
#                 "INSERT INTO payments (user_id, amount, tariff, status) VALUES (?, ?, ?, ?)", 
#                 (user_id_int, out_sum_float, description, 'pending')
#             )
#             conn.commit()

#             # Получаем inv_id последнего вставленного платежа
#             inv_id = cursor.lastrowid
#             logging.info(f"Inserted payment with inv_id: {inv_id}")

#         # Формирование строки для подписи
#         signature_string = f"{ROBOKASSA_MERCHANT_LOGIN}:{out_sum_str}:{inv_id}:{ROBOKASSA_PASSWORD1}"
#         signature = hashlib.md5(signature_string.encode('utf-8')).hexdigest()

#         params = {
#             'MrchLogin': ROBOKASSA_MERCHANT_LOGIN,
#             'OutSum': out_sum_str,
#             'InvId': inv_id,
#             'Desc': description,
#             'SignatureValue': signature,
#             'Culture': 'ru',
#             'Encoding': 'utf-8',
#             'IsTest': '1' if TESTING_MODE else '0',
#             'ReturnUrl': ROBOKASSA_SUCCESS_URL,
#             'CancelUrl': ROBOKASSA_FAIL_URL
#         }

#         url = f"https://auth.robokassa.ru/Merchant/Index.aspx?{urlencode(params)}"
        
#         # Логирование сгенерированной ссылки
#         logging.info(f"Generated Robokassa link: {url}")
        
#         return url
#     except Exception as e:
#         logging.error(f"Error in generate_robokassa_link: {e}")
#         raise

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
#     with sqlite3.connect('users.db') as conn:
#         cursor = conn.cursor()
#         cursor.execute("SELECT * FROM users WHERE user_id=?", (message.from_user.id,))
#         if not cursor.fetchone():
#             initial_tokens = 500 if TESTING_MODE else 500
#             cursor.execute("INSERT INTO users (user_id, tokens_balance, tariff) VALUES (?, ?, ?)", 
#                            (message.from_user.id, initial_tokens, 'Базовый'))
#             conn.commit()
#             await message.answer(
#                 "**Добро пожаловать!**\n"
#                 "**У вас есть 500 токенов для пробного использования.**\n"
#                 "**Вы можете приобрести подписку с большим функционалом согласно нашим [Тарифам](https://telegra.ph/Tarify-09-16) в нижнем меню.**\n"
#                 "**[Наша оферта](https://telegra.ph/Oferta-09-16)**\n"
#                 "**Выберите модель и начните пользоваться!**",
#                 parse_mode="Markdown",
#                 reply_markup=keyboard_level1
#             )
#         else:
#             await message.answer("С возвращением! Вы можете начать использовать бота.", reply_markup=keyboard_level1)

# # Обработчик выбора тарифа
# @router.message(StateFilter(UserStates.selecting_tariff))
# async def process_tariff_selection(message: Message, state: FSMContext):
#     try:
#         tariff = message.text
#         logging.info(f"User {message.from_user.id} selected tariff: {tariff}")

#         if tariff == "🔙 Назад":
#             await message.answer("Вы вернулись в главное меню.", reply_markup=keyboard_level1)
#             await state.clear()
#             return
        
#         if tariff == "ℹ️ Инфо":
#             info_message = (
#                 "📋 **Информация о тарифах:**\n\n"
#                 "🏆 **Премиум**:\n- Неограниченные токены\n- Все модели\n- Приоритетная поддержка\n Стоимость: 3 000 р\n\n"
#                 "📈 **Продвинутый**:\n- 2000 токенов\n- Большинство моделей\n- Поддержка через бот\nСтоимость: 1 500 р\n\n"
#                 "📉 **Базовый**:\n- 1000 токенов\n- Ограниченные модели\n- Поддержка через FAQ\n Стоимость:  300 р"
#             )
#             await message.answer(info_message, parse_mode="Markdown", reply_markup=keyboard_tariff_info)
#             return
        
#         if tariff not in ["📉 Базовый", "📈 Продвинутый", "🏆 Премиум"]:
#             await message.answer("Неверный выбор. Вернитесь в главное меню.", reply_markup=keyboard_level1)
#             await state.clear()
#             return
        
#         tariff_clean = tariff.split(' ')[-1]
        
#         if TESTING_MODE:
#             initialize_user(message.from_user.id, tariff_clean)
#             await message.answer(f"Вы приобрели тариф {tariff_clean}.", reply_markup=keyboard_level1)
#             await state.clear()
#         else:
#             # Определение стоимости тарифа
#             tariff_prices = {
#                 'Базовый': 300,        # Стоимость в рублях
#                 'Продвинутый': 1500,
#                 'Премиум': 3000
#             }
#             out_sum = tariff_prices.get(tariff_clean, 0)
#             description = f'Покупка тарифа {tariff_clean}'

#             # Генерация ссылки на оплату
#             payment_link = generate_robokassa_link(out_sum, tariff_clean, message.from_user.id)

#             # Отправка ссылки пользователю
#             await message.answer(
#                 f"Для приобретения тарифа **{tariff_clean}** перейдите по [ссылке для оплаты]({payment_link}).",
#                 parse_mode="Markdown",
#                 disable_web_page_preview=True,
#                 reply_markup=keyboard_level1
#             )
#             await state.clear()
#     except Exception as e:
#         logging.error(f"Error in process_tariff_selection: {e}")
#         await message.answer("Произошла ошибка при обработке вашего запроса. Пожалуйста, попробуйте позже.")
#         await state.clear()

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
#     with sqlite3.connect('users.db') as conn:
#         cursor = conn.cursor()
#         cursor.execute("SELECT has_selected_model FROM users WHERE user_id=?", (user_id,))
#         result = cursor.fetchone()
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
#     with sqlite3.connect('users.db') as conn:
#         cursor = conn.cursor()
#         cursor.execute("SELECT tokens_balance, tariff FROM users WHERE user_id=?", (message.from_user.id,))
#         result = cursor.fetchone()
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
#     with sqlite3.connect('users.db') as conn:
#         cursor = conn.cursor()
#         cursor.execute("UPDATE users SET model=?, has_selected_model=1 WHERE user_id=?", (model_id, user_id))
#         conn.commit()
#     await message.answer(f"Вы выбрали модель {model_name}.", reply_markup=keyboard_level1)
#     await state.clear()

# async def handle_image_generation(message: Message, state: FSMContext, prompt: str):
#     user_id = message.from_user.id
#     with sqlite3.connect('users.db') as conn:
#         cursor = conn.cursor()
#         cursor.execute("SELECT model FROM users WHERE user_id=?", (user_id,))
#         result = cursor.fetchone()
#         model_id = result[0] if result else "o1-mini"
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
#         response = await asyncio.to_thread(openai.Image.create, prompt=prompt, n=1, size="1024x1024")
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
#     with sqlite3.connect('users.db') as conn:
#         cursor = conn.cursor()
#         cursor.execute("SELECT model FROM users WHERE user_id=?", (user_id,))
#         result = cursor.fetchone()
#         model_id = result[0] if result else "o1-mini"
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
#     with sqlite3.connect('users.db') as conn:
#         cursor = conn.cursor()
#         cursor.execute("SELECT tokens_balance FROM users WHERE user_id=?", (user_id,))
#         result = cursor.fetchone()
#         tokens = result[0] if result else 0
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
#         with sqlite3.connect('users.db') as conn:
#             cursor = conn.cursor()
#             cursor.execute("SELECT model FROM users WHERE user_id=?", (user_id,))
#             result = cursor.fetchone()
#             model_id = result[0] if result else "o1-mini"
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

# async def main():
#     # Включаем Router в Dispatcher
#     dp.include_router(router)

#     # Запуск бота
#     try:
#         await dp.start_polling(bot)
#     finally:
#         logging.info("Бот остановлен!")

# if __name__ == '__main__':
#     try:
#         asyncio.run(main())
#     except (KeyboardInterrupt, SystemExit):
#         logging.info("Бот остановлен!")
