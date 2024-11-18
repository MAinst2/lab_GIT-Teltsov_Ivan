import random
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Настройки для подключения к Google Sheets
SHEET_URL = "https://docs.google.com/spreadsheets/d/1bXAB_S6Nyb8tVwkSGjYAJ_Ly6l6Pl7ysNDSprFHiFTg/edit?usp=sharing"
TOKEN = '7483388745:AAFFmpu-dbRLCOfnzqWCwXEEwOa2DG0k76o'

# Подключение к Google Sheets
scope = ["https://spreadsheets.google.com/feeds", 'https://www.googleapis.com/auth/spreadsheets',
         "https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(
    "integral-linker-431012-t7-1e523a49ec9e.json", scope)
client = gspread.authorize(creds)
sheet = client.open_by_url(SHEET_URL).worksheet("Словарь")

# Загрузка данных из таблицы
hieroglyphs = sheet.col_values(2)[3:5000]
pinyin = sheet.col_values(4)[3:5000]
translations = sheet.col_values(5)[3:5000]

# Функция для старта


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(
            "Тренировать перевод иероглифов на русский", callback_data='to_russian')],
        [InlineKeyboardButton(
            "Тренировать перевод русского на иероглифы", callback_data='to_hieroglyph')],
        [InlineKeyboardButton(
            "Тренировать перевод иероглифов на пиньин", callback_data='to_pinyin')],
        [InlineKeyboardButton("Тренировать все вместе", callback_data='mixed')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите тип тренировки:", reply_markup=reply_markup)

# Обработка выбора тренировки


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data['training_type'] = query.data
    await send_question(query.message, context)

# Функция для отправки вопросов


async def send_question(message, context):
    training_type = context.user_data['training_type']

    if training_type == 'mixed':
        types = ['to_russian', 'to_hieroglyph', 'to_pinyin']
        selected_type = random.choice(types)
    else:
        selected_type = training_type

    if selected_type == 'to_russian':
        index = random.randint(0, len(hieroglyphs) - 1)
        question = f"Как переводится иероглиф '{hieroglyphs[index]}' на русский?"
        # Список допустимых ответов
        answer = [a.strip().lower() for a in translations[index].split(',')]

    elif selected_type == 'to_hieroglyph':
        index = random.randint(0, len(translations) - 1)
        question = f"Какой иероглиф соответствует переводу '{translations[index]}'?"
        answer = [hieroglyphs[index].lower()]

    elif selected_type == 'to_pinyin':
        index = random.randint(0, len(hieroglyphs) - 1)
        question = f"Какой пиньин у иероглифа '{hieroglyphs[index]}'?"
        answer = [pinyin[index].lower()]

    context.user_data['answer'] = answer
    await message.reply_text(f"{question}\n\nВведите ответ:")

# Обработка ответов пользователя


async def answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_answer = update.message.text.strip().lower()  # приведение к нижнему регистру
    correct_answers = context.user_data.get('answer')

    if user_answer in correct_answers:
        response = "Верно! Поздравляю 🎉"
        await update.message.reply_text(response)
        await send_question(update.message, context)  # Следующий вопрос
    else:
        response = f"Неправильно. Попробуйте еще раз."
        keyboard = [[InlineKeyboardButton(
            "Посмотреть ответ", callback_data='show_answer')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(response, reply_markup=reply_markup)

# Обработка запроса на показ правильного ответа


async def show_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    correct_answer = ", ".join(context.user_data['answer'])
    await update.callback_query.message.reply_text(f"Правильный ответ: {correct_answer}")
    # Следующий вопрос
    await send_question(update.callback_query.message, context)

# Команда для завершения тренировки


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(
            "Тренировать перевод иероглифов на русский", callback_data='to_russian')],
        [InlineKeyboardButton(
            "Тренировать перевод русского на иероглифы", callback_data='to_hieroglyph')],
        [InlineKeyboardButton(
            "Тренировать перевод иероглифов на пиньин", callback_data='to_pinyin')],
        [InlineKeyboardButton("Тренировать все вместе", callback_data='mixed')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Тренировка окончена. Если хотите потренироваться еще, выберите тип тренировки ниже:", reply_markup=reply_markup)
    context.user_data.clear()  # Очистка данных тренировки

# Запуск бота


def main():
    app = Application.builder().token(TOKEN).build()

    # Команды и обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CallbackQueryHandler(
        button_handler, pattern='^(to_russian|to_hieroglyph|to_pinyin|mixed)$'))
    app.add_handler(CallbackQueryHandler(show_answer, pattern='^show_answer$'))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, answer_handler))

    logger.info("Бот запущен...")
    app.run_polling()


if __name__ == '__main__':
    main()


#                        [main()]
#                            |
#                      [Application Setup]
#                            |
#                 [Command and Query Handlers]
#                 /         |                            \          \
#         [CommandHandler] [CommandHandler] [CallbackQueryHandler] [MessageHandler]
#           ("start", start) ("stop", stop)    (button_handler)     (answer_handler)
#                                 |                 |
#                          [start()]        [button_handler()]
#                             |                     |
#             [Display Training Options]  [Send Questions based on selection]
#                            /                      |          \
#              [send_question()]           [answer_handler()] [show_answer()]
#                              /                       |       \
#                [Select Question Type] [    Check Answer]  [Provide Correct Answer]
#                       /        \                \              /
#   [Translate Hieroglyph to Russian] [Translate Russian to Hieroglyph]
#                       /
#       [Translate Hieroglyph to Pinyin]
#
# [Google Sheets Setup]
#          |
#  [Authorization & Data Loading]
#         /           |              \
# [Load Hieroglyphs] [Load Pinyin] [Load Translations]
#
#   [Logging Setup]
#
#
