import logging
import os
import asyncio
import threading
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from flask import Flask, request, jsonify
from telegram import Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID"))

# SMTP настройки
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", 465))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM = os.getenv("SMTP_FROM")

# Путь к файлу книги (можно использовать book.pdf, но пока оставим book.txt)
BOOK_FILE_PATH = os.getenv("BOOK_FILE_PATH", "book.txt")

logging.basicConfig(level=logging.INFO)

# Хранилище обработанных заказов (защита от дублей)
processed_orders = set()

# Создаём отдельный экземпляр Bot для отправки сообщений из вебхука
bot = Bot(token=TOKEN)

# --- Функция отправки email с вложением ---
def send_email_with_attachment(to_email, subject, body, attachment_path=None):
    """Отправляет письмо с вложением (файл книги) на указанный email."""
    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_FROM
        msg['To'] = to_email
        msg['Subject'] = subject

        msg.attach(MIMEText(body, 'plain'))

        if attachment_path and os.path.exists(attachment_path):
            with open(attachment_path, 'rb') as f:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header(
                    'Content-Disposition',
                    f'attachment; filename="{os.path.basename(attachment_path)}"'
                )
                msg.attach(part)
        else:
            logging.warning(f"Файл вложения не найден: {attachment_path}")

        # Подключаемся к SMTP-серверу и отправляем
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, to_email, msg.as_string())
        logging.info(f"Письмо отправлено на {to_email}")
        return True
    except Exception as e:
        logging.error(f"Ошибка отправки email: {e}")
        return False

# --- Обработчики команд для бота (polling) ---
# (оставляем без изменений)
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
    await send_electronic_book(update, context, user_id)

async def order_printed(update, context):
    user_id = update.effective_user.id
    context.user_data['waiting_for_address'] = True
    await context.bot.send_message(
        chat_id=user_id,
        text="📦 Для отправки печатной версии книги, пожалуйста, напишите адрес ближайшего пункта выдачи заказов (ПВЗ).\n\n"
             "Чтобы отменить, отправьте /cancel"
    )

async def order_both(update, context):
    user_id = update.effective_user.id
    await send_electronic_book(update, context, user_id)
    context.user_data['waiting_for_address'] = True
    await context.bot.send_message(
        chat_id=user_id,
        text="📦 Теперь для печатной версии книги, пожалуйста, напишите адрес ближайшего пункта выдачи заказов (ПВЗ).\n\n"
             "Чтобы отменить, отправьте /cancel"
    )

async def cancel(update, context):
    user_id = update.effective_user.id
    context.user_data['waiting_for_address'] = False
    await update.message.reply_text("❌ Операция отменена.")

async def send_electronic_book(update, context, user_id):
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
            text="⚠️ К сожалению, файл книги временно недоступен. Мы уже работаем над этим!"
        )

async def handle_address(update, context):
    user_id = update.effective_user.id
    address = update.message.text
    if context.user_data.get('waiting_for_address', False):
        logging.info(f"Адрес получен от {user_id}: {address}")
        send_telegram_message(
            f"📍 Новый заказ печатной книги!\nПользователь: {user_id}\nАдрес ПВЗ: {address}"
        )
        await update.message.reply_text("✅ Спасибо! Ваш адрес передан. В ближайшее время с вами свяжутся для подтверждения заказа.")
        context.user_data['waiting_for_address'] = False

# --- Создаём приложение для бота (polling) ---
app_bot = Application.builder().token(TOKEN).build()
app_bot.add_handler(CommandHandler("start", start))
app_bot.add_handler(CommandHandler("electronic", order_electronic))
app_bot.add_handler(CommandHandler("printed", order_printed))
app_bot.add_handler(CommandHandler("both", order_both))
app_bot.add_handler(CommandHandler("cancel", cancel))
app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_address))

# --- Flask приложение для вебхуков от Tilda ---
flask_app = Flask(__name__)

def send_telegram_message(text):
    """Отправляет сообщение менеджеру (синхронная обёртка)."""
    async def send():
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=text)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(send())
    finally:
        loop.close()

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.form.to_dict()
        logging.info(f"Получены данные из Tilda: {data}")

        # Приводим ключи к нижнему регистру
        data_lower = {k.lower(): v for k, v in data.items()}

        order_id = data_lower.get('payment[orderid]')
        if order_id:
            if order_id in processed_orders:
                logging.info(f"Дубликат заказа {order_id} — игнорируем")
                return jsonify({"status": "duplicate ignored"}), 200
            processed_orders.add(order_id)

        client_name = data_lower.get('name', 'Не указано')
        client_phone = data_lower.get('phone', 'Не указано')
        client_email = data_lower.get('email', 'Не указано')
        client_city = data_lower.get('city', 'Не указано')

        products = []
        i = 0
        while True:
            name_key = f'payment[products][{i}][name]'
            if name_key not in data_lower:
                break
            product = {
                'name': data_lower.get(name_key, ''),
                'quantity': data_lower.get(f'payment[products][{i}][quantity]', '1'),
                'amount': data_lower.get(f'payment[products][{i}][amount]', '0'),
                'price': data_lower.get(f'payment[products][{i}][price]', '0'),
            }
            products.append(product)
            i += 1

        if not products:
            product_name = data_lower.get('product_name') or data_lower.get('product') or data_lower.get('payment[product][name]')
            if product_name:
                products.append({
                    'name': product_name,
                    'quantity': data_lower.get('quantity', '1'),
                    'amount': data_lower.get('amount') or data_lower.get('payment[amount]', '0'),
                    'price': data_lower.get('price') or data_lower.get('payment[amount]', '0'),
                })

        order_types = set()
        total_price = 0
        for p in products:
            amount_str = p.get('amount', '0')
            if amount_str and amount_str.isdigit():
                total_price += int(amount_str)
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

        # --- ОБРАБОТКА ЭЛЕКТРОННОЙ КНИГИ (отправка на email) ---
        email_sent = False
        if order_type in ('electronic', 'both'):
            if client_email and client_email != 'Не указано':
                subject = "Ваша электронная книга «О чём зудят твои таланты?»"
                body = (
                    f"Здравствуйте, {client_name}!\n\n"
                    "Благодарим за покупку электронной книги «О чём зудят твои таланты?».\n"
                    "Файл книги прикреплён к этому письму.\n\n"
                    "Приятного чтения!\n\n"
                    "С уважением,\n"
                    "Команда Future Mission"
                )
                email_sent = send_email_with_attachment(
                    to_email=client_email,
                    subject=subject,
                    body=body,
                    attachment_path=BOOK_FILE_PATH
                )
            else:
                logging.warning("Email клиента не указан, отправка книги невозможна.")

        # --- УВЕДОМЛЕНИЕ МЕНЕДЖЕРА ---
        messages = []

        main_message = (
            f"📦 **Новый заказ книги!**\n"
            f"Клиент: {client_name}\n"
            f"Телефон: {client_phone}\n"
            f"Email: {client_email}\n"
            f"Город: {client_city}\n"
            f"Товары:\n"
        )
        for p in products:
            main_message += f"  - {p['name']} x{p['quantity']} = {p['amount']} руб.\n"
        main_message += f"Итого: {total_price} руб.\n"
        main_message += f"Тип заказа: {order_type}"
        messages.append(main_message)

        if order_type == 'electronic':
            if email_sent:
                messages.append("📧 **Электронная книга автоматически отправлена на email клиента.**")
            else:
                messages.append("⚠️ **Не удалось отправить книгу на email. Отправьте файл вручную.**")
        elif order_type == 'printed':
            messages.append("📌 **Печатная книга.** Свяжитесь с клиентом для уточнения адреса ПВЗ.")
        elif order_type == 'both':
            if email_sent:
                messages.append("📧 **Электронная книга автоматически отправлена на email клиента.**")
            else:
                messages.append("⚠️ **Не удалось отправить книгу на email. Отправьте файл вручную.**")
            messages.append("📌 **Печатная книга.** Свяжитесь с клиентом для уточнения адреса ПВЗ.")
        else:
            messages.append("⚠️ Не удалось определить тип заказа. Проверьте вручную.")

        # Отправляем все сообщения одной асинхронной функцией
        async def send_all_messages():
            for msg in messages:
                await bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(send_all_messages())
        finally:
            loop.close()

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logging.error(f"Ошибка в вебхуке: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@flask_app.route('/')
def index():
    return "✅ Бот Future Mission Book Bot работает!", 200

# --- Запуск бота (polling) и Flask-сервера в разных потоках ---
def run_bot():
    logging.info("Запуск бота (polling)...")
    app_bot.run_polling()

def run_flask():
    logging.info("Запуск Flask-сервера...")
    flask_app.run(host='0.0.0.0', port=10000)

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot)
    flask_thread = threading.Thread(target=run_flask)
    bot_thread.start()
    flask_thread.start()
    bot_thread.join()
    flask_thread.join()
