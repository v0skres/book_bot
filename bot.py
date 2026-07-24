import logging
import os
import asyncio
import threading
import base64
import requests
from flask import Flask, request, jsonify
from telegram import Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID"))

# Unisender настройки
UNISENDER_API_KEY = os.getenv("UNISENDER_API_KEY")
UNISENDER_FROM_EMAIL = os.getenv("UNISENDER_FROM_EMAIL")
UNISENDER_FROM_NAME = os.getenv("UNISENDER_FROM_NAME", "Future Mission")

BOOK_FILE_PATH = os.getenv("BOOK_FILE_PATH", "book.txt")

logging.basicConfig(level=logging.INFO)

processed_orders = set()
bot = Bot(token=TOKEN)

# --- Функция отправки email через Unisender API (с вложением) ---
def send_email_with_attachment(to_email, subject, body, attachment_path=None):
    if not UNISENDER_API_KEY or not UNISENDER_FROM_EMAIL:
        logging.error("Unisender не настроен: отсутствуют API_KEY или FROM_EMAIL")
        return False

    try:
        # Базовые параметры
        params = {
            "api_key": UNISENDER_API_KEY,
            "format": "json",
            "sender_name": UNISENDER_FROM_NAME,
            "sender_email": UNISENDER_FROM_EMAIL,
            "subject": subject,
            "body": body,
            "email": to_email,
        }

        # Добавляем вложение, если файл существует
        if attachment_path and os.path.exists(attachment_path):
            with open(attachment_path, "rb") as f:
                file_data = f.read()
                encoded = base64.b64encode(file_data).decode()
                filename = os.path.basename(attachment_path)
                # Параметр attachments должен быть массивом
                # Ключ — имя файла, значение — содержимое в base64
                params["attachments"] = {
                    filename: encoded
                }

        response = requests.post(
            "https://api.unisender.com/ru/api/sendEmail",
            data=params
        )

        result = response.json()
        logging.info(f"Unisender ответ: {result}")

        # Проверяем успешность отправки
        if result.get("result") and result["result"].get("status") == "ok":
            logging.info(f"Письмо отправлено на {to_email} через Unisender")
            return True
        else:
            logging.error(f"Ошибка Unisender: {result}")
            return False

    except Exception as e:
        logging.error(f"Ошибка отправки email через Unisender: {e}")
        return False

# --- Функция для отправки сообщений в Telegram (синхронно) ---
def send_telegram_message(text):
    async def send():
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=text)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(send())
    finally:
        loop.close()

# --- Обработчики команд бота ---
async def start(update, context):
    await update.message.reply_text(
        "👋 Привет! Я бот для выдачи книги «О чём зудят твои таланты?».\n\n"
        "Доступные команды:\n"
        "/electronic — получить электронную книгу\n"
        "/printed — заказать печатную книгу (запрос адреса)\n"
        "/both — получить электронную и заказать печатную\n"
        "/cancel — отменить запрос адреса"
    )

async def order_electronic(update, context):
    user_id = update.effective_user.id
    try:
        with open(BOOK_FILE_PATH, 'rb') as f:
            await context.bot.send_document(
                chat_id=user_id,
                document=f,
                caption="📖 Вот ваша электронная книга «О чём зудят твои таланты?». Приятного чтения!"
            )
        logging.info(f"Книга отправлена пользователю {user_id}")
    except FileNotFoundError:
        await context.bot.send_message(
            chat_id=user_id,
            text="⚠️ К сожалению, файл книги временно недоступен."
        )

async def order_printed(update, context):
    user_id = update.effective_user.id
    context.user_data['waiting_for_address'] = True
    await context.bot.send_message(
        chat_id=user_id,
        text="📦 Для отправки печатной версии книги, пожалуйста, напишите адрес ближайшего пункта выдачи заказов (ПВЗ)."
    )

async def order_both(update, context):
    user_id = update.effective_user.id
    try:
        with open(BOOK_FILE_PATH, 'rb') as f:
            await context.bot.send_document(
                chat_id=user_id,
                document=f,
                caption="📖 Вот ваша электронная книга."
            )
        logging.info(f"Книга отправлена пользователю {user_id}")
    except FileNotFoundError:
        await context.bot.send_message(chat_id=user_id, text="⚠️ Файл книги временно недоступен.")
    context.user_data['waiting_for_address'] = True
    await context.bot.send_message(
        chat_id=user_id,
        text="📦 Теперь для печатной версии книги, пожалуйста, напишите адрес ближайшего ПВЗ."
    )

async def cancel(update, context):
    user_id = update.effective_user.id
    context.user_data['waiting_for_address'] = False
    await update.message.reply_text("❌ Операция отменена.")

async def handle_address(update, context):
    user_id = update.effective_user.id
    address = update.message.text
    if context.user_data.get('waiting_for_address', False):
        logging.info(f"Адрес получен от {user_id}: {address}")
        send_telegram_message(f"📍 Печатная книга для {user_id}, адрес: {address}")
        await update.message.reply_text("✅ Спасибо! Ваш адрес передан.")
        context.user_data['waiting_for_address'] = False

# --- Создаём приложение ---
app_bot = Application.builder().token(TOKEN).build()
app_bot.add_handler(CommandHandler("start", start))
app_bot.add_handler(CommandHandler("electronic", order_electronic))
app_bot.add_handler(CommandHandler("printed", order_printed))
app_bot.add_handler(CommandHandler("both", order_both))
app_bot.add_handler(CommandHandler("cancel", cancel))
app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_address))

# --- Flask ---
flask_app = Flask(__name__)

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.form.to_dict()
        logging.info(f"Получены данные из Tilda: {data}")

        order_id = data.get('payment[orderid]')
        if order_id and order_id in processed_orders:
            logging.info(f"Дубликат заказа {order_id} — игнорируем")
            return jsonify({"status": "duplicate ignored"}), 200
        if order_id:
            processed_orders.add(order_id)

        client_name = data.get('name', 'Не указано')
        client_phone = data.get('Phone', 'Не указано')
        client_email = data.get('Email', 'Не указано')
        client_city = data.get('city', 'Не указано')

        products = []
        i = 0
        while True:
            name_key = f'payment[products][{i}][name]'
            if name_key not in data:
                break
            products.append({
                'name': data.get(name_key, ''),
                'quantity': data.get(f'payment[products][{i}][quantity]', '1'),
                'amount': data.get(f'payment[products][{i}][amount]', '0'),
                'price': data.get(f'payment[products][{i}][price]', '0'),
            })
            i += 1

        if not products:
            product_name = data.get('product_name') or data.get('product')
            if product_name:
                products.append({
                    'name': product_name,
                    'quantity': data.get('quantity', '1'),
                    'amount': data.get('payment[amount]', '0'),
                    'price': data.get('payment[amount]', '0'),
                })

        order_types = set()
        total_price = 0
        for p in products:
            total_price += int(p['amount']) if p['amount'].isdigit() else 0
            name_lower = p['name'].lower()
            if 'электрон' in name_lower:
                order_types.add('electronic')
            if 'печатн' in name_lower:
                order_types.add('printed')

        if len(order_types) == 2:
            order_type = 'both'
        elif 'electronic' in order_types:
            order_type = 'electronic'
        elif 'printed' in order_types:
            order_type = 'printed'
        else:
            order_type = 'unknown'

        # --- Отправка email через Unisender ---
        email_sent = False
        if order_type in ('electronic', 'both'):
            if client_email and client_email != 'Не указано' and client_email != '':
                subject = "Ваша электронная книга «О чём зудят твои таланты?»"
                body = (
                    f"Здравствуйте, {client_name}!\n\n"
                    "Благодарим за покупку электронной книги «О чём зудят твои таланты?».\n"
                    "Файл книги прикреплён к этому письму.\n\n"
                    "Приятного чтения!\n\n"
                    "С уважением,\n"
                    f"{UNISENDER_FROM_NAME}"
                )
                email_sent = send_email_with_attachment(
                    to_email=client_email,
                    subject=subject,
                    body=body,
                    attachment_path=BOOK_FILE_PATH
                )
            else:
                logging.warning("Email клиента не указан, книга не отправлена")

        # --- Формируем сообщения для менеджера ---
        messages = []
        main_msg = (
            f"📦 **Новый заказ!**\n"
            f"Клиент: {client_name}\n"
            f"Телефон: {client_phone}\n"
            f"Email: {client_email}\n"
            f"Город: {client_city}\n"
            f"Товары:\n"
        )
        for p in products:
            main_msg += f"  - {p['name']} x{p['quantity']} = {p['amount']} руб.\n"
        main_msg += f"Итого: {total_price} руб.\nТип: {order_type}"
        messages.append(main_msg)

        if order_type == 'electronic':
            if email_sent:
                messages.append("✅ Электронная книга автоматически отправлена на email клиента.")
            else:
                messages.append("⚠️ Электронная книга НЕ отправлена. Отправьте вручную.")
        elif order_type == 'printed':
            messages.append("📌 Печатная книга. Свяжитесь с клиентом для уточнения адреса ПВЗ.")
        elif order_type == 'both':
            if email_sent:
                messages.append("✅ Электронная книга автоматически отправлена на email.")
            else:
                messages.append("⚠️ Электронная книга НЕ отправлена. Отправьте вручную.")
            messages.append("📌 Печатная книга. Свяжитесь с клиентом для уточнения адреса.")
        else:
            messages.append("⚠️ Тип заказа не определён, проверьте вручную.")

        # Отправляем все сообщения
        for msg in messages:
            send_telegram_message(msg)

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logging.error(f"Ошибка в вебхуке: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@flask_app.route('/')
def index():
    return "✅ Бот работает", 200

# --- Запуск ---
def run_flask():
    flask_app.run(host='0.0.0.0', port=10000)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logging.info("Запуск бота (polling)...")
    app_bot.run_polling()
