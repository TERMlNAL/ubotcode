import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, types, Router, F
from aiogram.types import ContentType, Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.filters import Command, StateFilter
from aiogram.fsm.storage.redis import RedisStorage
from dotenv import load_dotenv
from pydub import AudioSegment
from io import BytesIO
import hashlib
from urllib.parse import urlencode
import openai
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, BigInteger, Integer, String, Float, select, delete, update, text
from sqlalchemy import Column, DateTime

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Проверка необходимых переменных окружения
API_TOKEN = os.getenv('API_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
PAYMENT_PROVIDER_TOKEN = os.getenv('PAYMENT_PROVIDER_TOKEN')
SUPPORT_BOT_USERNAME = os.getenv('SUPPORT_BOT_USERNAME')
ROBOKASSA_MERCHANT_LOGIN = os.getenv('ROBOKASSA_MERCHANT_LOGIN')
ROBOKASSA_PASSWORD1 = os.getenv('ROBOKASSA_PASSWORD1')
ROBOKASSA_RESULT_URL = os.getenv('ROBOKASSA_RESULT_URL')
ROBOKASSA_SUCCESS_URL = os.getenv('ROBOKASSA_SUCCESS_URL')
ROBOKASSA_FAIL_URL = os.getenv('ROBOKASSA_FAIL_URL')

if not all([API_TOKEN, OPENAI_API_KEY, PAYMENT_PROVIDER_TOKEN, SUPPORT_BOT_USERNAME,
            ROBOKASSA_MERCHANT_LOGIN, ROBOKASSA_PASSWORD1, ROBOKASSA_RESULT_URL,
            ROBOKASSA_SUCCESS_URL, ROBOKASSA_FAIL_URL]):
    logger.error("Пожалуйста, убедитесь, что все токены и ключи API заданы в файле .env")
    exit(1)

TESTING_MODE = False  # Установите True для тестирования

# Настройка SQLAlchemy для PostgreSQL
DATABASE_URL = "postgresql+asyncpg://ubotuser:DBsazer1358@localhost/ubotdb"  # Замени на свой пароль

engine = create_async_engine(DATABASE_URL, echo=False, pool_size=20, max_overflow=0)
SessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

# Определение моделей
class User(Base):
    __tablename__ = 'users'
    user_id = Column(BigInteger, primary_key=True, index=True)
    tariff = Column(String, default='Базовый')
    requests_left = Column(Integer, nullable=True)
    tokens_balance = Column(Integer, default=500)
    model = Column(String)
    has_selected_model = Column(Integer, default=0)

class Payment(Base):
    __tablename__ = 'payments'
    inv_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(BigInteger)
    amount = Column(Float)
    tariff = Column(String)
    status = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

# Создание таблиц в базе данных
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# Инициализация бота и диспетчера с RedisStorage
storage = RedisStorage.from_url("redis://localhost")
bot = Bot(token=API_TOKEN)
router = Router()
dp = Dispatcher(storage=storage)
openai.api_key = OPENAI_API_KEY

# Определение состояний
class UserStates(StatesGroup):
    selecting_tariff = State()
    selecting_model = State()
    purchasing_tariff = State()

# Функция для инициализации/обновления пользователя
async def initialize_user(user_id, tariff):
    tariff_tokens = {'Базовый': 1000, 'Продвинутый': 2000, 'Премиум': 3000}
    tokens_to_add = tariff_tokens.get(tariff, 0)
    logger.info(f"Initializing user {user_id} with tariff {tariff} and tokens {tokens_to_add}")
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        if user:
            user.tariff = tariff
            user.tokens_balance += tokens_to_add
            logger.info(f"Updated user {user_id}: tariff={tariff}, tokens_balance={user.tokens_balance}")
        else:
            new_user = User(user_id=user_id, tariff=tariff, tokens_balance=tokens_to_add)
            session.add(new_user)
            logger.info(f"Added new user {user_id}: tariff={tariff}, tokens_balance={tokens_to_add}")
        await session.commit()

# Проверка доступа пользователя
async def check_user_access(user_id, required_tariff="Базовый"):
    tariff_order = {"Базовый": 1, "Продвинутый": 2, "Премиум": 3}
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        if not user:
            return False, "У вас нет активного тарифа. Пожалуйста, выберите тариф, нажав на кнопку 'Подписка'."
        if user.tokens_balance <= 0:
            return False, "Ваш пакет токенов закончился."
        if tariff_order.get(user.tariff, 0) < tariff_order.get(required_tariff, 0):
            return False, "Ваш тариф не поддерживает эту функцию."
        return True, None

# Обновление баланса токенов
async def update_tokens_balance(user_id, tokens_used):
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        if user:
            if user.tokens_balance - tokens_used < 0:
                logger.warning(f"User {user_id} attempted to use {tokens_used} tokens but has only {user.tokens_balance}")
                user.tokens_balance = 0
            else:
                user.tokens_balance -= tokens_used
            await session.commit()

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

# Функция для очистки завершённых платежей (удалена дублирующаяся версия)
async def clear_old_payments():
    try:
        current_time = datetime.utcnow()
        logger.info(f"Current UTC time: {current_time}")
        async with SessionLocal() as session:
            expiration_query = text("""
                DELETE FROM payments 
                WHERE status = 'pending' 
                AND created_at < (NOW() - INTERVAL '1 hour')
            """)
            result = await session.execute(expiration_query)
            await session.commit()
            logger.info(f"Deleted {result.rowcount} old pending payments")
    except Exception as e:
        logger.error(f"Error clearing old pending payments: {e}")
async def generate_robokassa_link(out_sum, description, user_id):
    try:
        user_id_int = int(user_id)
        out_sum_float = float(out_sum)
        out_sum_str = f"{out_sum_float:.2f}"

        logger.info(f"Generating payment for user_id: {user_id_int}, out_sum: {out_sum_float}, description: {description}")

        async with SessionLocal() as session:
            # Помечаем все предыдущие незавершённые платежи как 'expired'
            await session.execute(
                update(Payment)
                .where(Payment.user_id == user_id_int, Payment.status == 'pending')
                .values(status='expired')
            )
            await session.commit()

            # Создаём новый платёж
            new_payment = Payment(user_id=user_id_int, amount=out_sum_float, tariff=description, status='pending')
            session.add(new_payment)
            await session.commit()

            # Обновляем объект платежа, чтобы получить inv_id
            await session.refresh(new_payment)
            inv_id = new_payment.inv_id
            logger.info(f"Inserted payment for user_id: {user_id_int}, inv_id: {inv_id}, amount: {out_sum_float}")

        # Генерация подписи для Robokassa
        signature_string = f"{ROBOKASSA_MERCHANT_LOGIN}:{out_sum_str}:{inv_id}:{ROBOKASSA_PASSWORD1}"
        signature = hashlib.md5(signature_string.encode('utf-8')).hexdigest()

        # Параметры для ссылки Robokassa
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
        logger.info(f"Generated Robokassa link for user_id: {user_id_int}, inv_id: {inv_id}, url: {url}")

        return url
    except Exception as e:
        logger.error(f"Error in generate_robokassa_link for user_id {user_id}: {e}")
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
    try:
        async with SessionLocal() as session:
            user = await session.get(User, message.from_user.id)
            if not user:
                initial_tokens = 500 if TESTING_MODE else 500
                new_user = User(user_id=message.from_user.id, tokens_balance=initial_tokens, tariff='Базовый')
                session.add(new_user)
                await session.commit()
                logger.info(f"Added new user {message.from_user.id} with initial tokens {initial_tokens}")
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
    except Exception as e:
        logger.error(f"Error in cmd_start for user {message.from_user.id}: {e}")
        await message.answer("Произошла ошибка при запуске. Пожалуйста, попробуйте позже.")

# Обработчик выбора тарифа
@router.message(StateFilter(UserStates.selecting_tariff))
async def process_tariff_selection(message: Message, state: FSMContext):
    try:
        tariff = message.text
        logger.info(f"User {message.from_user.id} selected tariff: {tariff}")

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
            await initialize_user(message.from_user.id, tariff_clean)
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
            payment_link = await generate_robokassa_link(out_sum, tariff_clean, message.from_user.id)

            # Отправка ссылки пользователю
            await message.answer(
                f"Для приобретения тарифа **{tariff_clean}** перейдите по [ссылке для оплаты]({payment_link}).",
                parse_mode="Markdown",
                disable_web_page_preview=True,
                reply_markup=keyboard_level1
            )
            await state.clear()
    except Exception as e:
        logger.error(f"Error in process_tariff_selection for user {message.from_user.id}: {e}")
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
        await message.answer("Выберите тариф:", reply_markup=keyboard_tariff_selection)
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
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        if not user or not user.has_selected_model:
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
    user_id = message.from_user.id
    logger.info(f"User {user_id} requested balance.")
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        if user:
            logger.info(f"User {user_id} has tariff {user.tariff} and tokens {user.tokens_balance}")
            await message.answer(
                f"📦 **Тариф:** {user.tariff}\n🔢 **Токены:** {user.tokens_balance}",
                parse_mode="Markdown",
                reply_markup=keyboard_level1
            )
        else:
            logger.error(f"User {user_id} not found in database.")
            await message.answer("Не удалось получить информацию об остатке.", reply_markup=keyboard_level1)

async def handle_access_error(message: Message, state: FSMContext, error_message: str):
    if error_message == "Ваш пакет токенов закончился.":
        await message.answer("Ваш пакет токенов закончился. Выберите новый тариф:", reply_markup=keyboard_tariff_selection)
        await state.set_state(UserStates.selecting_tariff)
    else:
        await message.answer(error_message)

async def handle_model_selection(message: Message, state: FSMContext, model_name: str):
    user_id = message.from_user.id
    logger.info(f"User {user_id} is selecting model: {model_name}")
    if model_name == "🔙 Назад":
        await message.answer("Вернулись в главное меню.", reply_markup=keyboard_level1)
        await state.clear()
        logger.info(f"User {user_id} returned to main menu.")
        return
    models = {"🧠 GPT-4o": "gpt-4o", "🧠 GPT-4o-mini": "gpt-4o-mini", "🧠 o1-mini": "o1-mini"}
    if model_name not in models:
        await message.answer("Выберите доступную модель.", reply_markup=keyboard_model_selection)
        logger.warning(f"User {user_id} selected an invalid model: {model_name}")
        return
    model_id = models[model_name]
    if model_id == "o1-mini":
        access, error = await check_user_access(user_id, "Премиум")
        if not access:
            await message.answer("Модель 'o1-mini' доступна только в тарифе 'Премиум'.", reply_markup=keyboard_tariff_selection)
            await state.set_state(UserStates.selecting_tariff)
            logger.warning(f"User {user_id} tried to select 'o1-mini' without Premium tariff.")
            return
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        if user:
            user.model = model_id
            user.has_selected_model = 1
            await session.commit()
            logger.info(f"User {user_id} selected model {model_id}.")
    await message.answer(f"Вы выбрали модель {model_name}.", reply_markup=keyboard_level1)
    await state.clear()

async def handle_image_generation(message: Message, state: FSMContext, prompt: str):
    user_id = message.from_user.id
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        model_id = user.model if user else "o1-mini"
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
        logger.info(f"Generated image for user {user_id}: {image_url}")
    except Exception as e:
        logger.error(f"Ошибка генерации изображения: {e}")
        await msg.delete()
        await message.answer("Ошибка при генерации изображения.", reply_markup=keyboard_level1)

async def handle_chat_response(message: Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text
    data = await state.get_data()
    logger.info(f"User {user_id} sent message: {text}")
    conversation = data.get('conversation', []) + [{"role": "user", "content": text}]
    conversation_with_system = [{"role": "system", "content": "Отвечай всегда на русском языке."}] + conversation
    msg = await message.answer("Генерация ответа")
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        if user:
            model_id = user.model
            logger.info(f"User {user_id} is using model {model_id}")
        else:
            model_id = "o1-mini"
            logger.warning(f"User {user_id} not found, using default model 'o1-mini'")
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
        logger.info(f"Responded to user {user_id} with: {reply}")
    except Exception as e:
        logger.error(f"Ошибка генерации ответа для user {user_id}: {e}")
        await msg.delete()
        await message.answer("Ошибка при обработке запроса.", reply_markup=keyboard_level1)

# Обработчик голосовых сообщений
@router.message(F.voice)
async def handle_voice_message(message: Message, state: FSMContext):
    user_id = message.from_user.id
    logger.info(f"User {user_id} sent a voice message.")

    access, error = await check_user_access(user_id, "Премиум")
    if not access:
        await message.answer(
            "🔒 Распознавание голосовых сообщений доступно только для 'Премиум'.",
            reply_markup=keyboard_tariff_selection
        )
        await state.set_state(UserStates.selecting_tariff)
        return

    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        tokens = user.tokens_balance if user else 0
    if tokens <= 0:
        await message.answer("❌ Токены закончились. Выберите тариф:", reply_markup=keyboard_tariff_selection)
        await state.set_state(UserStates.selecting_tariff)
        return

    msg = await message.answer("[распознавание аудио]")
    try:
        # Получение файла голосового сообщения
        file_info = await bot.get_file(message.voice.file_id)
        file_path = file_info.file_path
        logger.info(f"Voice file path: {file_path}")

        # Скачивание файла
        voice_file = await bot.download_file(file_path)
        # voice_file is a BytesIO object
        voice_file.seek(0)
        voice_bytes = voice_file.read()
        logger.info(f"Downloaded voice message from user {user_id}")

        # Конвертация в WAV
        audio = AudioSegment.from_file(BytesIO(voice_bytes), format="ogg")
        wav_io = BytesIO()
        audio.export(wav_io, format="wav")
        wav_io.seek(0)
        wav_io.name = "temp_audio.wav"
        logger.info(f"Converted voice message to WAV for user {user_id}")

        # Распознавание речи
        transcript = await asyncio.to_thread(openai.Audio.transcribe, "whisper-1", wav_io)
        recognized_text = transcript["text"]
        logger.info(f"Transcribed voice message from user {user_id}: {recognized_text}")

        # Генерация ответа
        await show_loading_animation(msg, "Генерация ответа")
        data = await state.get_data()
        conversation = data.get('conversation', []) + [{"role": "user", "content": recognized_text}]
        conversation_with_system = [{"role": "system", "content": "Отвечай всегда на русском языке."}] + conversation

        async with SessionLocal() as session:
            user = await session.get(User, user_id)
            model_id = user.model if user and user.model else "o1-mini"
            logger.info(f"User {user_id} is using model {model_id}")

        response = await asyncio.to_thread(
            openai.ChatCompletion.create,
            model=model_id,
            messages=conversation_with_system
        )
        reply = response['choices'][0]['message']['content']
        conversation.append({"role": "assistant", "content": reply})
        await state.update_data(conversation=conversation)
        await msg.delete()
        await message.answer(reply, reply_markup=keyboard_level1)
        await update_tokens_balance(user_id, response['usage']['total_tokens'])
        logger.info(f"Responded to user {user_id} with: {reply}")

    except Exception as e:
        logger.error(f"Ошибка обработки голосового сообщения от пользователя {user_id}: {e}", exc_info=True)
        await msg.delete()
        await message.answer("Ошибка при обработке голосового сообщения.", reply_markup=keyboard_level1)

async def main():
    await init_db()
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен!")
