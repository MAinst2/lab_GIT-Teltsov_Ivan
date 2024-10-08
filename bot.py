import logging
import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, filters, CommandHandler, CallbackQueryHandler
from fpdf import FPDF
from pydub import AudioSegment
from datetime import datetime
import os
import replicate
from openai import OpenAI
from dotenv import load_dotenv
import threading
import queue
import asyncio
import requests
import uuid  # Для генерации уникальных имен файлов

# Загрузка переменных окружения из файла .env
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Получаем токены и версии из переменных окружения
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")
WHISPER_VERSION = os.getenv("WHISPER_VERSION")
CHATGPT_API_KEY = os.getenv("CHATGPT_API_KEY")

# Настройка клиента Replicate и OpenAI
client = replicate.Client(api_token=REPLICATE_API_TOKEN)
openai_client = OpenAI(api_key=CHATGPT_API_KEY)

# Словарь для хранения текста транскрипции по user_id
transcription_data = {}

# Словарь для хранения состояния обработки для каждого пользователя
user_processing_status = {}

# Переменная для отслеживания пользователя, чей файл сейчас обрабатывается
current_processing_user = None

# Создание очереди для обработки аудиофайлов
task_queue = queue.Queue()
task_lock = threading.Lock()

# Функция для создания PDF с правильной кодировкой
def create_pdf(text, filename):
    pdf = FPDF()
    pdf.add_page()
    pdf.add_font('FreeSerif', '', 'FreeSerif.ttf', uni=True)
    pdf.set_font('FreeSerif', '', 14)
    pdf.multi_cell(0, 10, text)
    pdf.output(filename)

# Функция для создания краткого или подробного конспекта
async def generate_summary(text, detailed=False):
    prompt = "Сделай краткий конспект следующего текста:\n" if not detailed else "Сделай подробный конспект следующего текста:\n"
    prompt += text
    try:
        response = openai_client.chat.completions.create(
            messages=[
                {"role": "user", "content": prompt}
            ],
            model="gpt-3.5-turbo",  # Или используйте "gpt-4", если необходимо
            max_tokens=500,
            temperature=0.7
        )
        return response['choices'][0]['message']['content'].strip()
    except Exception as e:
        logger.error(f"Ошибка при работе с ChatGPT: {str(e)}")
        return "Произошла ошибка при создании конспекта."

# Обработчик команды /start
async def start(update, context):
    await update.message.reply_text(
        "Привет, я транскрибирую аудио-файлы или голосовые сообщения (до 3 часов) и могу создать по ним краткий или подробный конспект!"
    )

# Функция для расчета времени ожидания
def calculate_wait_time_for_user(user_index, user_file_duration, current_file_duration, queue_durations):
    """
    Расчет времени ожидания для пользователя:
    :param user_index: индекс пользователя в очереди
    :param user_file_duration: продолжительность аудиофайла пользователя
    :param current_file_duration: продолжительность обрабатываемого в данный момент файла
    :param queue_durations: список продолжительности файлов пользователей перед данным пользователем
    :return: рассчитанное время ожидания в минутах
    """
    # Суммируем продолжительность всех файлов перед пользователем в очереди, а также текущего обрабатываемого файла
    wait_time_seconds = sum(queue_durations[:user_index]) + current_file_duration + user_file_duration
    wait_time_minutes = (wait_time_seconds / 60) * 0.3  # Применяем множитель для расчета
    return round(wait_time_minutes, 1)

# Функция для обработки задач из очереди
def process_queue():
    global current_processing_user
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    while True:
        user_id, file_path, duration_in_seconds, context = task_queue.get()
        current_processing_user = user_id  # Обновляем текущего пользователя
        try:
            logger.info(f"Обработка аудиофайла для пользователя {user_id}")
            with open(file_path, "rb") as audio_file:
                output = client.run(
                    f"openai/whisper:{WHISPER_VERSION}",
                    input={
                        "audio": audio_file,
                        "model": "large-v3",
                        "language": "auto",
                        "translate": False
                    }
                )

            logger.info(f"Ответ от Replicate для пользователя {user_id}: {output}")

            segments = output.get("segments", [])
            text = " ".join([segment["text"] for segment in segments]) if segments else "Текст не найден."

            pdf_filename = f"Перевод голосового сообщения - {datetime.now().strftime('%H:%M %d %B %Y')}.pdf"
            create_pdf(text, pdf_filename)

            loop.run_until_complete(context.bot.send_document(chat_id=user_id, document=open(pdf_filename, "rb")))

            # Сохраняем текст транскрипции для пользователя
            transcription_data[user_id] = text

            # Создаем кнопки
            keyboard = [
                [InlineKeyboardButton("Создать краткий конспект", callback_data=f"summary_short:{user_id}")],
                [InlineKeyboardButton("Создать подробный конспект", callback_data=f"summary_detailed:{user_id}")],
                [InlineKeyboardButton("Отправить следующий файл", callback_data=f"next_file:{user_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            loop.run_until_complete(context.bot.send_message(chat_id=user_id, text="Выберите действие:", reply_markup=reply_markup))

        except replicate.exceptions.ReplicateError as e:
            logger.error(f"Ошибка при работе с Replicate для пользователя {user_id}: {str(e)}")
            loop.run_until_complete(context.bot.send_message(chat_id=user_id, text="Ошибка при обработке аудиофайла."))
        except Exception as e:
            logger.error(f"Непредвиденная ошибка для пользователя {user_id}: {str(e)}")
            loop.run_until_complete(context.bot.send_message(chat_id=user_id, text="Произошла непредвиденная ошибка."))
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)
            if os.path.exists(pdf_filename):
                os.remove(pdf_filename)
            user_processing_status[user_id] = False  # Снимаем блокировку после завершения обработки
            current_processing_user = None  # Освобождаем текущего пользователя
        task_queue.task_done()

# Функция для загрузки файла по ссылке
def download_file_from_url(url):
    local_filename = url.split('/')[-1]
    with requests.get(url, stream=True) as response:
        response.raise_for_status()
        with open(local_filename, 'wb') as out_file:
            for chunk in response.iter_content(chunk_size=8192):
                out_file.write(chunk)
    return local_filename

# Обработчик сообщений с аудиофайлами, голосовыми сообщениями или ссылками
async def handle_audio_or_link(update, context):
    logger.info("Получено сообщение")

    # Проверка на аудиофайл или голосовое сообщение
    if update.message.audio or update.message.voice:
        await handle_audio(update, context)
        return

    # Проверка на ссылку
    if update.message.text and ("drive.google.com" in update.message.text or "yadi.sk" in update.message.text):
        url = update.message.text.strip()
        user_id = update.message.from_user.id

        try:
            # Загрузка файла по ссылке
            file_path = download_file_from_url(url)
            audio = AudioSegment.from_file(file_path)
            duration_in_seconds = len(audio) / 1000

            # Проверяем, обрабатывается ли уже файл для данного пользователя
            if user_processing_status.get(user_id, False):
                await update.message.reply_text("Вы уже отправили файл на обработку. Пожалуйста, дождитесь завершения обработки прежде, чем отправлять новый файл.")
                return

            # Отмечаем, что обработка начата
            user_processing_status[user_id] = True

            # Добавляем задачу в очередь
            with task_lock:
                task_queue.put((user_id, file_path, duration_in_seconds, context))
                queue_size = task_queue.qsize()  # Размер очереди
                if current_processing_user:
                    queue_size += 1  # Добавляем текущего пользователя, если файл уже обрабатывается

                queue_durations = [task[2] for task in list(task_queue.queue)[:-1]]
                current_file_duration = 0
                if current_processing_user:
                    current_file_duration = task_queue.queue[0][2]  # Длительность файла текущего пользователя

                if queue_size == 1:
                    wait_time = calculate_wait_time_for_user(0, duration_in_seconds, current_file_duration, queue_durations)
                    await update.message.reply_text(f"Вы не в очереди! Ваш файл будет обработан через {wait_time} минут(ы).")
                else:
                    for i in range(queue_size):
                        wait_time = calculate_wait_time_for_user(i, duration_in_seconds, current_file_duration, queue_durations)
                        await update.message.reply_text(f"Вы в очереди под номером {i}. Ваш файл будет обработан через {wait_time} минут(ы).")

        except Exception as e:
            logger.error(f"Ошибка при обработке ссылки: {str(e)}")
            await update.message.reply_text("Произошла ошибка при загрузке файла по ссылке. Проверьте правильность ссылки и попробуйте снова.")

# Обработчик аудиофайлов и голосовых сообщений
async def handle_audio(update, context):
    logger.info("Получено аудиосообщение")
    user_id = update.message.from_user.id

    # Проверяем, обрабатывается ли уже файл для данного пользователя
    if user_processing_status.get(user_id, False):
        await update.message.reply_text("Вы уже отправили файл на обработку. Пожалуйста, дождитесь завершения обработки прежде, чем отправлять новый файл.")
        return

    # Если обработка не ведется, отмечаем, что она начата
    user_processing_status[user_id] = True

    # Генерируем уникальное имя для файла
    file_path = f"{user_id}_{uuid.uuid4()}.ogg"  # Генерация уникального имени файла для каждого пользователя

    # Получаем файл и проверяем его размер
    if update.message.audio:
        if update.message.audio.file_size > 20 * 1024 * 1024:
            await update.message.reply_text(
                "Файл слишком большой. Максимальный размер файла - 20 МБ. Если хотите обработать файл большего объема, то загрузите его на Google или Яндекс диск, и отправьте мне ссылку на этот файл!"
            )
            user_processing_status[user_id] = False  # Снимаем блокировку при ошибке
            return
        file = await update.message.audio.get_file()
    elif update.message.voice:
        if update.message.voice.file_size > 20 * 1024 * 1024:
            await update.message.reply_text(
                "Файл слишком большой. Максимальный размер файла - 20 МБ. Если хотите обработать файл большего объема, то загрузите его на Google или Яндекс диск, и отправьте мне ссылку на этот файл!"
            )
            user_processing_status[user_id] = False  # Снимаем блокировку при ошибке
            return
        file = await update.message.voice.get_file()
    else:
        user_processing_status[user_id] = False  # Снимаем блокировку при ошибке
        return

    await file.download_to_drive(file_path)

    audio = AudioSegment.from_file(file_path)
    duration_in_seconds = len(audio) / 1000

    # Добавляем задачу в очередь
    with task_lock:
        task_queue.put((user_id, file_path, duration_in_seconds, context))
        queue_size = task_queue.qsize()  # Размер очереди
        if current_processing_user:
            queue_size += 1  # Учитываем, что есть пользователь, файл которого уже обрабатывается

        queue_durations = [task[2] for task in list(task_queue.queue)[:-1]]
        current_file_duration = 0
        if current_processing_user:
            current_file_duration = task_queue.queue[0][2]  # Длительность файла текущего пользователя

        if queue_size == 1:
            wait_time = calculate_wait_time_for_user(0, duration_in_seconds, current_file_duration, queue_durations)
            await update.message.reply_text(f"Вы не в очереди! Ваш файл будет обработан через {wait_time} минут(ы).")
        else:
            for i in range(queue_size):
                wait_time = calculate_wait_time_for_user(i, duration_in_seconds, current_file_duration, queue_durations)
                if i == queue_size - 1:
                    await update.message.reply_text(f"Вы в очереди под номером {i}. Ваш файл будет обработан через {wait_time} минут(ы).")

# Обработчик выбора кнопок
async def button(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    # Извлекаем команду и user_id
    data = query.data
    command, received_user_id = data.split(":", 1)

    if int(received_user_id) != user_id:
        await query.edit_message_text(text="Произошла ошибка, попробуйте снова.")
        return

    # Если пользователь выбрал краткий или подробный конспект
    if command == "summary_short":
        text = transcription_data.get(user_id, "")
        summary = await generate_summary(text, detailed=False)
        await query.edit_message_text(text=f"Краткий конспект:\n{summary}")
    elif command == "summary_detailed":
        text = transcription_data.get(user_id, "")
        summary = await generate_summary(text, detailed=True)
        await query.edit_message_text(text=f"Подробный конспект:\n{summary}")
    elif command == "next_file":
        await query.edit_message_text(text="Отправьте следующий аудиофайл для транскрибирования.")
        transcription_data.pop(user_id, None)

# Основная функция
def main():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    # Используем универсальный обработчик для аудио и ссылок
    application.add_handler(MessageHandler(filters.AUDIO | filters.VOICE | filters.TEXT, handle_audio_or_link))
    application.add_handler(CallbackQueryHandler(button))

    # Запускаем поток для обработки очереди
    threading.Thread(target=process_queue, daemon=True).start()

    application.run_polling()

if __name__ == "__main__":
    main()




#                                   [main()]
#                                   /      \
#                         [Load .env]    [Setup Bot & Client]
#                              /                  \
#                  [Load Variables]       [Command Handlers]
#                      /                          /        \
#      [Setup Replicate Client]     [Start Command]  [Handle Audio/Link]
#              /             \                                   \
#    [Setup OpenAI Client] [Setup Task Queue]             [Check File Type]
#                           /           \                      /       \
#                  [Create PDF]    [Process Queue]  [Download Audio]  [Handle Voice]
#                                   /        \                               \
#                    [Generate Summary]    [Wait Time Calc]              [Transcribe Audio]
#                                            /           \                         \
#                                [Short Summary] [Detailed Summary]          [Create PDF for User]
#                                                                       /           \
#                                                              [Send PDF]  [Send Action Options]
#