import asyncio
import logging
import os
import sys
from typing import Dict, Set
# import json
# from pathlib import Path
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, html
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from email_monitor import EmailMonitor, EmailConfig
from config_manager import ConfigManager

# Настройка логирования для понимания процессов в боте
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

load_dotenv()

# Константы для конфигурации
CONFIG_FILE = "user_configs.json"

# Состояния FSM (Finite State Machine) для диалога настройки


class ConfigStates(StatesGroup):
    """
    Состояния для пошаговой настройки почтового мониторинга.
    Подумайте об этом как о диалоге с пользователем, где каждое состояние
    представляет определенный шаг в процессе конфигурации.
    """
    waiting_for_host = State()        # Ожидаем IMAP сервер
    waiting_for_port = State()        # Ожидаем порт
    waiting_for_email = State()       # Ожидаем email адрес
    waiting_for_password = State()    # Ожидаем пароль
    waiting_for_folder = State()      # Ожидаем папку для мониторинга
    waiting_for_interval = State()    # Ожидаем интервал проверки
    waiting_for_target_chat = State()  # Ожидаем ID чата для уведомлений


class EmailBot:
    """
    Основной класс Telegram-бота для мониторинга электронной почты.

    Этот класс координирует все компоненты системы:
    - Обработку команд от пользователей
    - Управление конфигурациями
    - Запуск и остановку мониторинга почты
    - Отправку уведомлений в Telegram
    """

    def __init__(self, bot_token: str, config_file: str):
        """
        Инициализация бота с необходимыми компонентами.

        Args:
            bot_token: Токен бота от BotFather
            config_file: Путь к файлу с конфигурациями пользователей
        """
        # Создаем основные компоненты бота
        self.bot = Bot(
            token=bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        self.dp = Dispatcher()

        # Менеджер конфигураций отвечает за сохранение настроек пользователей
        self.config_manager = ConfigManager(config_file)

        # Словарь активных мониторов почты для каждого пользователя
        self.active_monitors: Dict[int, EmailMonitor] = {}

        # Множество пользователей, у которых уже запущен мониторинг
        self.running_users: Set[int] = set()

        # Регистрируем обработчики команд
        self._register_handlers()

    def _register_handlers(self):
        """
        Регистрация всех обработчиков команд бота.
        Это похоже на настройку меню в ресторане - каждая команда
        соответствует определенному действию.
        """
        self.dp.message(CommandStart())(self.start_handler)
        self.dp.message(Command("setup"))(self.setup_handler)
        self.dp.message(Command("start_monitoring"))(
            self.start_monitoring_handler)
        self.dp.message(Command("stop_monitoring"))(
            self.stop_monitoring_handler)
        self.dp.message(Command("status"))(self.status_handler)
        self.dp.message(Command("help"))(self.help_handler)

        # Обработчики для состояний FSM (процесс настройки)
        self.dp.message(ConfigStates.waiting_for_host)(self.process_host)
        self.dp.message(ConfigStates.waiting_for_port)(self.process_port)
        self.dp.message(ConfigStates.waiting_for_email)(self.process_email)
        self.dp.message(ConfigStates.waiting_for_password)(
            self.process_password)
        self.dp.message(ConfigStates.waiting_for_folder)(self.process_folder)
        self.dp.message(ConfigStates.waiting_for_interval)(
            self.process_interval)
        self.dp.message(ConfigStates.waiting_for_target_chat)(
            self.process_target_chat)

    async def start_handler(self, message: Message) -> None:
        """Обработчик команды /start - первое знакомство с ботом"""
        welcome_text = f"""
🤖 <b>Добро пожаловать в Email Monitor Bot!</b>

Привет, {html.bold(message.from_user.full_name)}!

Этот бот поможет вам отслеживать входящие письма на вашем почтовом ящике
и пересылать их содержимое прямо в Telegram.

<b>Основные возможности:</b>
• Подключение к любым IMAP-серверам (Gmail, Mail.ru, ProtonMail и др.)
• Автоматическая проверка новых писем
• Фильтрация по отправителю, теме и наличию вложений
• Гибкая настройка интервала проверки

<b>Начальные команды:</b>
/setup - Настройка подключения к почте
/help - Помощь по командам

Готовы начать? Используйте /setup для настройки первого почтового ящика!
        """
        await message.answer(welcome_text)

    async def setup_handler(self, message: Message, state: FSMContext) -> None:
        """
        Начало процесса настройки почтового мониторинга.
        Это начальная точка диалога с пользователем для сбора всех необходимых данных.
        """
        await state.set_state(ConfigStates.waiting_for_host)
        setup_text = """
🔧 <b>Настройка мониторинга почты</b>

Давайте настроим подключение к вашему почтовому ящику.

<b>Популярные IMAP серверы:</b>
• Gmail: imap.gmail.com
• Mail.ru: imap.mail.ru
• Yandex: imap.yandex.ru
• ProtonMail: 127.0.0.1 (требует ProtonMail Bridge)
• Outlook: outlook.office365.com

<b>Введите адрес IMAP сервера:</b>
(например: imap.gmail.com)
        """
        await message.answer(setup_text)

    async def process_host(self, message: Message, state: FSMContext) -> None:
        """Обработка введенного IMAP сервера"""
        host = message.text.strip()
        await state.update_data(host=host)
        await state.set_state(ConfigStates.waiting_for_port)

        # Предлагаем стандартные порты для удобства
        port_text = f"""
✅ IMAP сервер: <code>{host}</code>

<b>Теперь укажите порт:</b>

<b>Стандартные порты:</b>
• 993 - IMAP с SSL (рекомендуется)
• 143 - IMAP без шифрования

<b>Введите номер порта:</b>
        """
        await message.answer(port_text)

    async def process_port(self, message: Message, state: FSMContext) -> None:
        """Обработка порта с проверкой корректности"""
        try:
            port = int(message.text.strip())
            if not (1 <= port <= 65535):
                raise ValueError("Порт вне диапазона")
        except ValueError:
            await message.answer("❌ Некорректный порт. Введите число от 1 до 65535:")
            return

        await state.update_data(port=port)
        await state.set_state(ConfigStates.waiting_for_email)

        await message.answer("""
✅ Порт сохранен!

<b>Введите ваш email адрес:</b>
(например: user@gmail.com)
        """)

    async def process_email(self, message: Message, state: FSMContext) -> None:
        """Обработка email адреса с базовой валидацией"""
        email = message.text.strip()

        # Простая проверка формата email
        if "@" not in email or "." not in email:
            await message.answer("❌ Некорректный формат email. Попробуйте еще раз:")
            return

        await state.update_data(email=email)
        await state.set_state(ConfigStates.waiting_for_password)

        password_text = f"""
✅ Email: <code>{email}</code>

<b>Введите пароль:</b>

⚠️ <b>Важно для Gmail пользователей:</b>
Для Gmail необходимо использовать "Пароли приложений" вместо основного пароля.
Включите двухфакторную аутентификацию и создайте пароль приложения в настройках безопасности.

<i>Пароль будет сохранен в зашифрованном виде.</i>
        """
        await message.answer(password_text)

    async def process_password(self, message: Message, state: FSMContext) -> None:
        """Обработка пароля с немедленным удалением из чата"""
        password = message.text.strip()
        await state.update_data(password=password)

        # Удаляем сообщение с паролем из чата для безопасности
        try:
            await message.delete()
        except Exception:
            pass  # Если не удалось удалить, продолжаем

        await state.set_state(ConfigStates.waiting_for_folder)

        await message.answer("""
✅ Пароль сохранен! (сообщение удалено для безопасности)

<b>Укажите папку для мониторинга:</b>

<b>Стандартные папки:</b>
• INBOX - Входящие (по умолчанию)
• Sent - Отправленные
• Drafts - Черновики

Введите название папки или оставьте пустым для использования INBOX:
        """)

    async def process_folder(self, message: Message, state: FSMContext) -> None:
        """Обработка папки для мониторинга"""
        folder = message.text.strip()
        if not folder:
            folder = "INBOX"  # Папка по умолчанию

        await state.update_data(folder=folder)
        await state.set_state(ConfigStates.waiting_for_interval)

        interval_text = f"""
✅ Папка: <code>{folder}</code>

<b>Интервал проверки почты:</b>

Как часто бот должен проверять новые письма?

<b>Рекомендуемые значения:</b>
• 60 - каждую минуту
• 300 - каждые 5 минут
• 600 - каждые 10 минут
• 1800 - каждые 30 минут

<b>Введите интервал в секундах:</b>
        """
        await message.answer(interval_text)

    async def process_interval(self, message: Message, state: FSMContext) -> None:
        """Обработка интервала проверки с валидацией"""
        try:
            interval = int(message.text.strip())
            if interval < 30:  # Минимум 30 секунд чтобы не перегружать сервер
                await message.answer("❌ Минимальный интервал - 30 секунд. Попробуйте еще раз:")
                return
        except ValueError:
            await message.answer("❌ Введите число (интервал в секундах):")
            return

        await state.update_data(interval=interval)
        await state.set_state(ConfigStates.waiting_for_target_chat)

        # Получаем ID текущего чата для предложения по умолчанию
        current_chat_id = message.chat.id

        await message.answer(f"""
✅ Интервал проверки: <code>{interval} секунд</code>

<b>Последний шаг!</b>

<b>Куда отправлять уведомления о новых письмах?</b>

Текущий чат: <code>{current_chat_id}</code>

Вы можете:
• Отправить <code>{current_chat_id}</code> для отправки в этот чат
• Указать другой chat_id
• Добавить бота в группу и использовать ID группы

<b>Введите chat_id для уведомлений:</b>
        """)

    async def process_target_chat(self, message: Message, state: FSMContext) -> None:
        """
        Завершение настройки и сохранение конфигурации.
        Здесь собираются все данные и создается конфигурация пользователя.
        """
        try:
            target_chat_id = int(message.text.strip())
        except ValueError:
            await message.answer("❌ Введите числовой ID чата:")
            return

        # Получаем все собранные данные из состояния FSM
        data = await state.get_data()
        user_id = message.from_user.id

        # Создаем конфигурацию email мониторинга
        config = EmailConfig(
            host=data['host'],
            port=data['port'],
            email=data['email'],
            password=data['password'],
            folder=data['folder'],
            check_interval=data['interval'],
            target_chat_id=target_chat_id,
            user_id=user_id
        )

        # Сохраняем конфигурацию
        self.config_manager.save_user_config(user_id, config)

        # Очищаем состояние FSM
        await state.clear()

        # Формируем итоговое сообщение с конфигурацией
        success_text = f"""
🎉 <b>Настройка завершена!</b>

<b>Конфигурация сохранена:</b>
• Сервер: <code>{config.host}:{config.port}</code>
• Email: <code>{config.email}</code>
• Папка: <code>{config.folder}</code>
• Интервал: <code>{config.check_interval} сек</code>
• Чат для уведомлений: <code>{config.target_chat_id}</code>

<b>Что дальше?</b>
• /start_monitoring - Запустить мониторинг
• /status - Проверить статус
• /help - Все доступные команды

<i>Мониторинг начнется автоматически через несколько секунд после запуска.</i>
        """

        await message.answer(success_text)
        logger.info(f"Configuration saved for user {user_id}")

    async def start_monitoring_handler(self, message: Message) -> None:
        """Запуск мониторинга почты для пользователя"""
        user_id = message.from_user.id

        # Проверяем, не запущен ли уже мониторинг
        if user_id in self.running_users:
            await message.answer("⚡ Мониторинг уже активен!")
            return

        # Загружаем конфигурацию пользователя
        config = self.config_manager.get_user_config(user_id)
        if not config:
            await message.answer("""
❌ <b>Конфигурация не найдена!</b>

Сначала настройте подключение к почте командой /setup
            """)
            return

        try:
            # Создаем и запускаем монитор почты
            monitor = EmailMonitor(config, self.bot)

            # Тестируем подключение перед запуском
            connection_ok = await monitor.test_connection()
            if not connection_ok:
                await message.answer("""
❌ <b>Не удалось подключиться к почтовому серверу</b>

Проверьте настройки и попробуйте еще раз.
Возможно, потребуется настроить пароль приложения для Gmail.
                """)
                return

            # Запускаем мониторинг в фоновом режиме
            self.active_monitors[user_id] = monitor
            self.running_users.add(user_id)

            # Создаем фоновую задачу для мониторинга
            asyncio.create_task(self._run_user_monitoring(user_id))

            await message.answer(f"""
✅ <b>Мониторинг запущен!</b>

📧 Отслеживается: <code>{config.email}</code>
📁 Папка: <code>{config.folder}</code>
⏱ Интервал: <code>{config.check_interval} сек</code>
🎯 Уведомления в чат: <code>{config.target_chat_id}</code>

Бот начнет отправлять уведомления о новых письмах.
            """)

            logger.info(f"Email monitoring started for user {user_id}")

        except Exception as e:
            logger.error(f"Failed to start monitoring for user {user_id}: {e}")
            await message.answer(f"❌ Ошибка при запуске мониторинга: {str(e)}")

    async def _run_user_monitoring(self, user_id: int):
        """
        Фоновая задача для мониторинга почты конкретного пользователя.
        Работает в бесконечном цикле до остановки пользователем.
        """
        monitor = self.active_monitors.get(user_id)
        if not monitor:
            return

        try:
            while user_id in self.running_users:
                await monitor.check_new_emails()
                await asyncio.sleep(monitor.config.check_interval)

        except Exception as e:
            logger.error(f"Monitoring error for user {user_id}: {e}")
            # Отправляем уведомление об ошибке пользователю
            try:
                await self.bot.send_message(
                    monitor.config.target_chat_id,
                    f"⚠️ Ошибка мониторинга почты: {str(e)}\n\nИспользуйте /start_monitoring для перезапуска."
                )
            except Exception:
                pass  # Если не удалось отправить уведомление об ошибке
        finally:
            # Очистка при завершении мониторинга
            if user_id in self.running_users:
                self.running_users.remove(user_id)
            if user_id in self.active_monitors:
                del self.active_monitors[user_id]

    async def stop_monitoring_handler(self, message: Message) -> None:
        """Остановка мониторинга почты"""
        user_id = message.from_user.id

        if user_id not in self.running_users:
            await message.answer("❌ Мониторинг не активен")
            return

        # Останавливаем мониторинг
        self.running_users.remove(user_id)
        if user_id in self.active_monitors:
            del self.active_monitors[user_id]

        await message.answer("🔴 Мониторинг остановлен")
        logger.info(f"Email monitoring stopped for user {user_id}")

    async def status_handler(self, message: Message) -> None:
        """Показать статус мониторинга для пользователя"""
        user_id = message.from_user.id
        config = self.config_manager.get_user_config(user_id)

        if not config:
            await message.answer("""
📊 <b>Статус мониторинга</b>

❌ Конфигурация не настроена
Используйте /setup для настройки
            """)
            return

        is_running = user_id in self.running_users
        status_icon = "✅" if is_running else "❌"
        status_text = "Активен" if is_running else "Остановлен"

        status_message = f"""
📊 <b>Статус мониторинга</b>

{status_icon} Статус: <b>{status_text}</b>
📧 Email: <code>{config.email}</code>
🖥 Сервер: <code>{config.host}:{config.port}</code>
📁 Папка: <code>{config.folder}</code>
⏱ Интервал: <code>{config.check_interval} сек</code>
🎯 Чат уведомлений: <code>{config.target_chat_id}</code>

<b>Доступные команды:</b>
{'• /stop_monitoring - Остановить' if is_running else '• /start_monitoring - Запустить'}
        """

        await message.answer(status_message)

    async def help_handler(self, message: Message) -> None:
        """Справка по всем доступным командам"""
        help_text = """
🆘 <b>Справка по командам</b>

<b>Основные команды:</b>
/start - Приветствие и знакомство с ботом
/setup - Настройка подключения к почте
/start_monitoring - Запуск мониторинга
/stop_monitoring - Остановка мониторинга
/status - Текущий статус и настройки
/help - Эта справка

<b>Процесс работы:</b>
1️⃣ Используйте /setup для настройки подключения
2️⃣ Запустите мониторинг через /start_monitoring
3️⃣ Получайте уведомления о новых письмах в Telegram

<b>Поддерживаемые почтовые сервисы:</b>
• Gmail (через пароли приложений)
• Mail.ru, Yandex
• ProtonMail (с ProtonMail Bridge)
• Любые IMAP серверы

<b>Безопасность:</b>
• Пароли шифруются при хранении
• Сообщения с паролями автоматически удаляются
• Настройки хранятся локально

Возникли вопросы? Обратитесь к разработчику!
        """

        await message.answer(help_text)

    async def start_polling(self):
        """Запуск бота в режиме long-polling"""
        logger.info("Starting Telegram Email Monitor Bot...")

        try:
            # Загружаем существующие конфигурации при запуске
            await self._load_existing_configs()

            # Запускаем polling
            await self.dp.start_polling(self.bot)

        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
        except Exception as e:
            logger.error(f"Bot error: {e}")
        finally:
            # Остановка всех мониторингов при завершении бота
            await self._cleanup()

    async def _load_existing_configs(self):
        """
        Загрузка существующих конфигураций пользователей при запуске бота.
        Полезно для автоматического возобновления мониторинга после перезапуска.
        """
        configs = self.config_manager.get_all_configs()
        logger.info(f"Found {len(configs)} existing configurations")

        # Здесь можно добавить логику автоматического запуска мониторинга
        # для пользователей, которые имели активный мониторинг до перезапуска

    async def _cleanup(self):
        """Корректная остановка всех активных мониторингов"""
        logger.info("Cleaning up active monitors...")
        self.running_users.clear()
        self.active_monitors.clear()
        await self.bot.session.close()


def main():
    """
    Точка входа в приложение.
    Проверяем наличие токена и запускаем бота.
    """
    if os.getenv("BOT_TOKEN") == "YOUR_BOT_TOKEN_HERE":
        print("❌ Пожалуйста, укажите токен бота в переменной BOT_TOKEN")
        print("Получить токен можно у @BotFather в Telegram")
        sys.exit(1)

    # Создаем и запускаем бота
    bot = EmailBot(os.getenv("BOT_TOKEN"), CONFIG_FILE)

    try:
        asyncio.run(bot.start_polling())
    except KeyboardInterrupt:
        logger.info("Shutdown requested by user")
    except Exception as e:
        logger.error(f"Critical error: {e}")


if __name__ == "__main__":
    main()
