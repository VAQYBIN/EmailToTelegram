import asyncio
import logging
import os
import sys
from typing import Dict, Set
import json  # Восстанавливаем - нужен для работы с JSON ответами и отладки
from pathlib import Path  # Восстанавливаем - улучшает работу с путями файлов
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, html
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from email_monitor import EmailMonitor, EmailConfig
from config_manager import ConfigManager

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

load_dotenv()

# Константы
CONFIG_FILE = "user_configs.json"

# Расширенные состояния FSM для работы с множественными конфигурациями


class ConfigStates(StatesGroup):
    """
    Расширенные состояния для управления множественными конфигурациями.

    Новые состояния позволяют пользователю создавать, редактировать и
    управлять несколькими почтовыми ящиками в рамках одного аккаунта Telegram.
    """
    # Состояния выбора действий с конфигурациями
    choosing_config_action = State()    # Выбор: создать новую, редактировать, удалить
    choosing_config_to_edit = State()   # Выбор конфигурации для редактирования
    choosing_config_to_delete = State()  # Выбор конфигурации для удаления

    # Состояния создания новой конфигурации
    waiting_for_config_name = State()   # Ожидаем имя новой конфигурации
    waiting_for_host = State()
    waiting_for_port = State()
    waiting_for_email = State()
    waiting_for_password = State()
    waiting_for_folder = State()
    waiting_for_interval = State()
    waiting_for_target_chat = State()


class EmailBot:
    """
    Расширенный Telegram-бот с поддержкой множественных почтовых конфигураций.

    Теперь бот умеет:
    - Управлять несколькими почтовыми ящиками на пользователя
    - Отслеживать только новые сообщения с момента запуска мониторинга
    - Показывать источник каждого письма в уведомлениях
    - Предоставлять удобный интерфейс для управления конфигурациями
    """

    def __init__(self, bot_token: str, config_file: str):
        """
        Инициализация бота с расширенными возможностями.

        Args:
            bot_token: Токен бота от BotFather
            config_file: Путь к файлу конфигураций
        """
        self.bot = Bot(
            token=bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        self.dp = Dispatcher(storage=MemoryStorage())

        # Менеджер конфигураций теперь поддерживает множественные настройки
        self.config_manager = ConfigManager(config_file)

        # Словарь активных мониторов для каждого пользователя
        # Ключ - user_id, значение - EmailMonitor со всеми конфигурациями пользователя
        self.active_monitors: Dict[int, EmailMonitor] = {}

        # Множество пользователей с активным мониторингом
        self.running_users: Set[int] = set()

        # Регистрируем обработчики
        self._register_handlers()

    def _register_handlers(self):
        """Регистрация всех обработчиков команд и колбэков."""
        # Основные команды
        self.dp.message(CommandStart())(self.start_handler)
        self.dp.message(Command("setup"))(self.setup_handler)
        self.dp.message(Command("configs"))(self.configs_handler)
        self.dp.message(Command("start_monitoring"))(
            self.start_monitoring_handler)
        self.dp.message(Command("stop_monitoring"))(
            self.stop_monitoring_handler)
        self.dp.message(Command("status"))(self.status_handler)
        self.dp.message(Command("help"))(self.help_handler)

        # Колбэки для inline кнопок
        self.dp.callback_query(lambda c: c.data.startswith(
            "config_"))(self.handle_config_callback)
        self.dp.callback_query(lambda c: c.data.startswith(
            "confirm_delete_"))(self.handle_delete_confirmation)
        self.dp.callback_query(lambda c: c.data == "cancel_delete")(
            self.handle_cancel_deletion)
        self.dp.callback_query(lambda c: c.data == "back_to_configs")(
            self.handle_back_to_configs)

        # Обработчики состояний FSM
        self.dp.message(ConfigStates.choosing_config_action)(
            self.process_config_action)
        self.dp.message(ConfigStates.choosing_config_to_edit)(
            self.process_config_to_edit)
        self.dp.message(ConfigStates.choosing_config_to_delete)(
            self.process_config_to_delete)
        self.dp.message(ConfigStates.waiting_for_config_name)(
            self.process_config_name)
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
        """Обработчик команды /start с обновленным приветствием."""
        welcome_text = f"""
🤖 <b>Добро пожаловать в Advanced Email Monitor Bot!</b>

Привет, {html.bold(message.from_user.full_name)}!

Этот бот поможет отслеживать входящие письма с множества почтовых ящиков
и пересылать их содержимое прямо в Telegram.

<b>Новые возможности:</b>
• Поддержка множественных почтовых ящиков для одного пользователя
• Мониторинг только новых писем (с момента запуска)
• Отображение источника каждого письма
• Удобное управление конфигурациями

<b>Основные команды:</b>
/setup - Настройка нового почтового ящика
/configs - Управление существующими конфигурациями
/start_monitoring - Запуск мониторинга всех настроенных почт
/status - Просмотр статуса всех конфигураций
/help - Подробная справка

Готовы начать? Используйте /setup для настройки первого почтового ящика!
        """
        await message.answer(welcome_text)

    async def configs_handler(self, message: Message) -> None:
        """
        Обработчик команды /configs для управления множественными конфигурациями.

        Предоставляет пользователю удобный интерфейс для просмотра и управления
        всеми настроенными почтовыми ящиками.
        """
        user_id = message.from_user.id
        user_configs = self.config_manager.get_user_configs(user_id)

        if not user_configs:
            await message.answer("""
📋 <b>Управление конфигурациями</b>

У вас пока нет настроенных почтовых ящиков.

Используйте /setup для создания первой конфигурации.
            """)
            return

        # Формируем список конфигураций с кнопками управления
        config_text = f"<b>Ваши почтовые конфигурации ({len(user_configs)}):</b>\n\n"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[])

        for config_name, config in user_configs.items():
            config_text += f"📧 <b>{config_name}</b>\n"
            config_text += f"   └ {config.email} ({config.host})\n"
            config_text += f"   └ Папка: {config.folder}, Интервал: {config.check_interval}с\n\n"

            # Добавляем кнопки для каждой конфигурации
            keyboard.inline_keyboard.extend([
                [
                    InlineKeyboardButton(
                        text=f"✏️ Редактировать {config_name}",
                        callback_data=f"config_edit_{config_name}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=f"🗑 Удалить {config_name}",
                        callback_data=f"config_delete_{config_name}"
                    )
                ]
            ])

        # Добавляем кнопку создания новой конфигурации
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text="➕ Добавить новую конфигурацию",
                callback_data="config_add_new"
            )
        ])

        await message.answer(config_text, reply_markup=keyboard)

    async def handle_config_callback(self, callback: CallbackQuery) -> None:
        """Обработчик колбэков для управления конфигурациями."""
        data = callback.data
        # user_id = callback.from_user.id

        if data == "config_add_new":
            await self._start_new_config_creation(callback)
        elif data.startswith("config_edit_"):
            config_name = data.replace("config_edit_", "")
            await self._start_config_editing(callback, config_name)
        elif data.startswith("config_delete_"):
            config_name = data.replace("config_delete_", "")
            await self._confirm_config_deletion(callback, config_name)

        await callback.answer()

    async def handle_back_to_configs(self, callback: CallbackQuery) -> None:
        """Обработка возврата к списку конфигураций."""
        user_id = callback.from_user.id
        user_configs = self.config_manager.get_user_configs(user_id)

        if not user_configs:
            await callback.message.edit_text("""
📋 <b>Управление конфигурациями</b>

У вас нет настроенных конфигураций.

Используйте /setup для создания первой конфигурации.
            """)
            await callback.answer()
            return

        # Воссоздаем интерфейс управления конфигурациями
        config_text = f"<b>Ваши почтовые конфигурации ({len(user_configs)}):</b>\n\n"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[])

        for config_name, config in user_configs.items():
            config_text += f"📧 <b>{config_name}</b>\n"
            config_text += f"   └ {config.email} ({config.host})\n"
            config_text += f"   └ Папка: {config.folder}, Интервал: {config.check_interval}с\n\n"

            keyboard.inline_keyboard.extend([
                [
                    InlineKeyboardButton(
                        text=f"✏️ Редактировать {config_name}",
                        callback_data=f"config_edit_{config_name}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=f"🗑 Удалить {config_name}",
                        callback_data=f"config_delete_{config_name}"
                    )
                ]
            ])

        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text="➕ Добавить новую конфигурацию",
                callback_data="config_add_new"
            )
        ])

        await callback.message.edit_text(config_text, reply_markup=keyboard)
        await callback.answer()

    async def _start_new_config_creation(self, callback: CallbackQuery) -> None:
        """Начало создания новой конфигурации."""
        # Создаем контекст состояния
        state = FSMContext(
            storage=self.dp.storage,
            key=f"{callback.from_user.id}:{callback.message.chat.id}"
        )

        await state.set_state(ConfigStates.waiting_for_config_name)

        await callback.message.edit_text("""
🆕 <b>Создание новой конфигурации</b>

Давайте создадим новую конфигурацию для мониторинга почты.

<b>Шаг 1: Имя конфигурации</b>

Придумайте уникальное имя для этой конфигурации.
Это поможет различать разные почтовые ящики.

<b>Примеры имён:</b>
• work_email
• personal_gmail
• support_mail
• backup_email

<b>Введите имя конфигурации:</b>
        """)

    async def process_config_name(self, message: Message, state: FSMContext) -> None:
        """Обработка имени новой конфигурации."""
        config_name = message.text.strip()
        user_id = message.from_user.id

        # Проверяем уникальность имени
        existing_configs = self.config_manager.get_user_configs(user_id)
        if config_name in existing_configs:
            await message.answer(f"❌ Конфигурация с именем '{config_name}' уже существует. Выберите другое имя:")
            return

        # Сохраняем имя и переходим к настройке параметров
        await state.update_data(config_name=config_name)
        await state.set_state(ConfigStates.waiting_for_host)

        await message.answer(f"""
✅ Имя конфигурации: <code>{config_name}</code>

<b>Шаг 2: IMAP сервер</b>

<b>Популярные IMAP серверы:</b>
• Gmail: imap.gmail.com
• Mail.ru: imap.mail.ru
• Yandex: imap.yandex.ru
• ProtonMail: 127.0.0.1 (требует ProtonMail Bridge)
• Outlook: outlook.office365.com

<b>Введите адрес IMAP сервера:</b>
        """)

    async def setup_handler(self, message: Message, state: FSMContext) -> None:
        """
        Упрощенный обработчик /setup для быстрого создания конфигурации.

        Теперь автоматически генерирует имя конфигурации и сразу переходит
        к настройке параметров подключения.
        """
        user_id = message.from_user.id
        existing_configs = self.config_manager.get_user_configs(user_id)

        # Генерируем имя конфигурации автоматически
        config_name = f"config_{len(existing_configs) + 1}"

        await state.update_data(config_name=config_name)
        await state.set_state(ConfigStates.waiting_for_host)

        setup_text = f"""
🔧 <b>Быстрая настройка почтового мониторинга</b>

Создаём конфигурацию: <code>{config_name}</code>

<b>Популярные IMAP серверы:</b>
• Gmail: imap.gmail.com
• Mail.ru: imap.mail.ru
• Yandex: imap.yandex.ru
• ProtonMail: 127.0.0.1 (требует ProtonMail Bridge)
• Outlook: outlook.office365.com

<b>Введите адрес IMAP сервера:</b>
        """
        await message.answer(setup_text)

    # Остальные обработчики состояний остаются похожими, но теперь работают с config_name
    async def process_host(self, message: Message, state: FSMContext) -> None:
        """Обработка IMAP сервера."""
        host = message.text.strip()
        await state.update_data(host=host)
        await state.set_state(ConfigStates.waiting_for_port)

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
        """Обработка порта."""
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
        """)

    async def process_email(self, message: Message, state: FSMContext) -> None:
        """Обработка email адреса."""
        email = message.text.strip()

        if "@" not in email or "." not in email:
            await message.answer("❌ Некорректный формат email. Попробуйте еще раз:")
            return

        await state.update_data(email=email)
        await state.set_state(ConfigStates.waiting_for_password)

        password_text = f"""
✅ Email: <code>{email}</code>

<b>Введите пароль:</b>

⚠️ <b>Важно для Gmail:</b>
Используйте "Пароли приложений" вместо основного пароля.

<i>Пароль будет зашифрован при сохранении.</i>
        """
        await message.answer(password_text)

    async def process_password(self, message: Message, state: FSMContext) -> None:
        """Обработка пароля."""
        password = message.text.strip()
        await state.update_data(password=password)

        # Удаляем сообщение с паролем
        try:
            await message.delete()
        except Exception:
            pass

        await state.set_state(ConfigStates.waiting_for_folder)

        await message.answer("""
✅ Пароль сохранен! (сообщение удалено)

<b>Папка для мониторинга:</b>

<b>Стандартные папки:</b>
• INBOX - Входящие (по умолчанию)
• Sent - Отправленные
• Drafts - Черновики

Введите название папки или оставьте пустым для INBOX:
        """)

    async def process_folder(self, message: Message, state: FSMContext) -> None:
        """Обработка папки."""
        folder = message.text.strip() or "INBOX"
        await state.update_data(folder=folder)
        await state.set_state(ConfigStates.waiting_for_interval)

        await message.answer(f"""
✅ Папка: <code>{folder}</code>

<b>Интервал проверки (в секундах):</b>

<b>Рекомендуемые значения:</b>
• 60 - каждую минуту
• 300 - каждые 5 минут
• 600 - каждые 10 минут
• 1800 - каждые 30 минут

<b>Введите интервал:</b>
        """)

    async def process_interval(self, message: Message, state: FSMContext) -> None:
        """Обработка интервала проверки."""
        try:
            interval = int(message.text.strip())
            if interval < 30:
                await message.answer("❌ Минимальный интервал - 30 секунд:")
                return
        except ValueError:
            await message.answer("❌ Введите число (интервал в секундах):")
            return

        await state.update_data(interval=interval)
        await state.set_state(ConfigStates.waiting_for_target_chat)

        current_chat_id = message.chat.id
        await message.answer(f"""
✅ Интервал: <code>{interval} секунд</code>

<b>Последний шаг!</b>

<b>ID чата для уведомлений:</b>

Текущий чат: <code>{current_chat_id}</code>

<b>Введите chat_id:</b>
        """)

    async def process_target_chat(self, message: Message, state: FSMContext) -> None:
        """Завершение создания конфигурации."""
        try:
            target_chat_id = int(message.text.strip())
        except ValueError:
            await message.answer("❌ Введите числовой ID чата:")
            return

        # Получаем все данные
        data = await state.get_data()
        user_id = message.from_user.id
        config_name = data['config_name']

        # Создаем конфигурацию
        config = EmailConfig(
            host=data['host'],
            port=data['port'],
            email=data['email'],
            password=data['password'],
            folder=data['folder'],
            check_interval=data['interval'],
            target_chat_id=target_chat_id,
            user_id=user_id,
            config_name=config_name
        )

        # Сохраняем
        success = self.config_manager.save_user_config(
            user_id, config, config_name)
        await state.clear()

        if success:
            success_text = f"""
🎉 <b>Конфигурация '{config_name}' создана!</b>

<b>Параметры:</b>
• Сервер: <code>{config.host}:{config.port}</code>
• Email: <code>{config.email}</code>
• Папка: <code>{config.folder}</code>
• Интервал: <code>{config.check_interval} сек</code>
• Чат: <code>{config.target_chat_id}</code>

<b>Что дальше?</b>
• /start_monitoring - Запустить мониторинг всех конфигураций
• /configs - Управление конфигурациями
• /status - Проверить статус

<i>При запуске мониторинга будут отслеживаться только новые письма!</i>
            """
            await message.answer(success_text)
        else:
            await message.answer("❌ Ошибка при сохранении конфигурации. Попробуйте еще раз.")

    async def start_monitoring_handler(self, message: Message) -> None:
        """
        Запуск мониторинга для всех конфигураций пользователя.

        Теперь создаёт единый монитор, который отслеживает все настроенные
        почтовые ящики пользователя одновременно.
        """
        user_id = message.from_user.id

        if user_id in self.running_users:
            await message.answer("⚡ Мониторинг уже активен!")
            return

        # Получаем все конфигурации пользователя
        user_configs = self.config_manager.get_user_configs_list(user_id)

        if not user_configs:
            await message.answer("""
❌ <b>Конфигурации не найдены!</b>

Сначала настройте хотя бы один почтовый ящик:
• /setup - Быстрая настройка
• /configs - Управление конфигурациями
            """)
            return

        try:
            # Создаем монитор для всех конфигураций пользователя
            monitor = EmailMonitor(user_configs, self.bot)

            # Тестируем подключения ко всем почтовым серверам
            connection_results = await monitor.test_all_connections()

            failed_connections = [
                email for email, success in connection_results.items() if not success]

            if failed_connections:
                failed_list = '\n'.join(
                    [f"• {email}" for email in failed_connections])
                await message.answer(f"""
⚠️ <b>Не удалось подключиться к некоторым почтовым серверам:</b>

{failed_list}

Проверьте настройки и попробуйте еще раз.
Возможно, потребуется настроить пароли приложений.
                """)
                return

            # Запускаем мониторинг
            self.active_monitors[user_id] = monitor
            self.running_users.add(user_id)

            # Создаем фоновую задачу
            asyncio.create_task(self._run_user_monitoring(user_id))

            # Формируем отчёт о запуске
            config_info = []
            for config in user_configs:
                config_info.append(
                    f"📧 <b>{config.config_name}</b>: {config.email}")

            config_list = '\n'.join(config_info)

            await message.answer(f"""
✅ <b>Мониторинг запущен для {len(user_configs)} конфигураций!</b>

{config_list}

⏱ <b>Интервалы проверки:</b> индивидуальные для каждой конфигурации
🎯 <b>Уведомления:</b> будут приходить в настроенные чаты

<b>Важно:</b> Отслеживаются только письма, полученные после запуска мониторинга!

Используйте /stop_monitoring для остановки.
            """)

            logger.info(
                f"Monitoring started for user {user_id} with {len(user_configs)} configurations")

        except Exception as e:
            logger.error(f"Failed to start monitoring for user {user_id}: {e}")
            await message.answer(f"❌ Ошибка при запуске мониторинга: {str(e)}")

    async def _run_user_monitoring(self, user_id: int):
        """
        Фоновая задача мониторинга для пользователя.

        Теперь использует улучшенный EmailMonitor, который сам управляет
        множественными конфигурациями и отслеживает только новые сообщения.
        """
        monitor = self.active_monitors.get(user_id)
        if not monitor:
            return

        try:
            while user_id in self.running_users:
                # Монитор сам определит интервалы для каждой конфигурации
                await monitor.check_new_emails()

                # Используем минимальный интервал из всех конфигураций
                min_interval = min(
                    config.check_interval for config in monitor.configs)
                await asyncio.sleep(min_interval)

        except Exception as e:
            logger.error(f"Monitoring error for user {user_id}: {e}")

            # Уведомляем пользователя об ошибке в первой доступной конфигурации
            if monitor.configs:
                try:
                    await self.bot.send_message(
                        monitor.configs[0].target_chat_id,
                        f"⚠️ Ошибка мониторинга: {str(e)}\n\nИспользуйте /start_monitoring для перезапуска."
                    )
                except Exception:
                    pass
        finally:
            # Очистка при завершении
            if user_id in self.running_users:
                self.running_users.remove(user_id)
            if user_id in self.active_monitors:
                await self.active_monitors[user_id].cleanup()
                del self.active_monitors[user_id]

    async def stop_monitoring_handler(self, message: Message) -> None:
        """Остановка мониторинга."""
        user_id = message.from_user.id

        if user_id not in self.running_users:
            await message.answer("❌ Мониторинг не активен")
            return

        # Останавливаем мониторинг
        self.running_users.remove(user_id)

        if user_id in self.active_monitors:
            await self.active_monitors[user_id].cleanup()
            del self.active_monitors[user_id]

        await message.answer("🔴 Мониторинг остановлен для всех конфигураций")
        logger.info(f"Monitoring stopped for user {user_id}")

    async def status_handler(self, message: Message) -> None:
        """Показать расширенный статус всех конфигураций пользователя."""
        user_id = message.from_user.id
        user_configs = self.config_manager.get_user_configs(user_id)

        if not user_configs:
            await message.answer("""
📊 <b>Статус мониторинга</b>

❌ Конфигурации не настроены
Используйте /setup или /configs для настройки
            """)
            return

        is_running = user_id in self.running_users
        status_icon = "✅" if is_running else "❌"
        status_text = "Активен" if is_running else "Остановлен"

        # Формируем детальную информацию о каждой конфигурации
        config_details = []
        for name, config in user_configs.items():
            last_check = "Никогда"
            if config.last_check_time:
                last_check = config.last_check_time.strftime(
                    "%Y-%m-%d %H:%M:%S UTC")

            detail = f"""
<b>📧 {name}</b>
• Email: <code>{config.email}</code>
• Сервер: <code>{config.host}:{config.port}</code>
• Папка: <code>{config.folder}</code>
• Интервал: <code>{config.check_interval} сек</code>
• Последняя проверка: <code>{last_check}</code>
• Чат: <code>{config.target_chat_id}</code>
            """
            config_details.append(detail)

        configs_text = '\n'.join(config_details)

        # Статистика монитора, если активен
        stats_text = ""
        if is_running and user_id in self.active_monitors:
            stats = self.active_monitors[user_id].get_stats()
            stats_text = f"""

<b>📈 Статистика мониторинга:</b>
• Всего конфигураций: {stats['total_configs']}
• Обработано писем: {sum(len(processed) for processed in self.active_monitors[user_id].processed_messages.values())}
            """

        status_message = f"""
📊 <b>Статус мониторинга</b>

{status_icon} <b>Общий статус:</b> {status_text}
📮 <b>Конфигураций:</b> {len(user_configs)}

{configs_text}{stats_text}

<b>Доступные команды:</b>
{'• /stop_monitoring - Остановить' if is_running else '• /start_monitoring - Запустить'}
• /configs - Управление конфигурациями
        """

        await message.answer(status_message)

    async def help_handler(self, message: Message) -> None:
        """Расширенная справка с новыми командами."""
        help_text = """
🆘 <b>Справка по командам</b>

<b>Основные команды:</b>
/start - Приветствие и знакомство с ботом
/setup - Быстрая настройка нового почтового ящика
/configs - Управление множественными конфигурациями
/start_monitoring - Запуск мониторинга всех настроенных почт
/stop_monitoring - Остановка мониторинга
/status - Детальный статус всех конфигураций
/help - Эта справка

<b>Работа с множественными конфигурациями:</b>
1️⃣ Создайте несколько конфигураций через /setup или /configs
2️⃣ Каждая конфигурация получает уникальное имя для различения
3️⃣ Запустите мониторинг - бот будет следить за всеми почтами
4️⃣ В уведомлениях указывается источник каждого письма

<b>Особенности мониторинга:</b>
• Отслеживаются только письма, полученные после запуска
• Каждая конфигурация работает независимо
• Разные интервалы проверки для разных почт
• Уведомления приходят в настроенные для каждой почты чаты

<b>Поддерживаемые сервисы:</b>
• Gmail (через пароли приложений)
• Mail.ru, Yandex
• ProtonMail (с ProtonMail Bridge)
• Любые IMAP серверы

<b>Безопасность:</b>
• Пароли шифруются при хранении
• Автоматическое удаление сообщений с паролями
• Локальное хранение настроек

Нужна помощь? Обратитесь к разработчику!
        """
        await message.answer(help_text)

    async def process_config_action(self, message: Message, state: FSMContext) -> None:
        """
        Обработка выбора действия с конфигурациями.

        Эта функция была зарегистрирована в обработчиках, но в текущей реализации
        используется inline-клавиатура вместо текстового ввода.
        """
        await message.answer("""
ℹ️ <b>Управление конфигурациями</b>

Для управления конфигурациями используйте команду /configs
с интерактивными кнопками.
        """)
        await state.clear()

    async def process_config_to_edit(self, message: Message, state: FSMContext) -> None:
        """
        Обработка выбора конфигурации для редактирования.

        В текущей версии редактирование через inline-кнопки.
        """
        await message.answer("""
ℹ️ <b>Редактирование конфигураций</b>

Для редактирования используйте команду /configs
и выберите конфигурацию с помощью кнопок.
        """)
        await state.clear()

    async def process_config_to_delete(self, message: Message, state: FSMContext) -> None:
        """
        Обработка выбора конфигурации для удаления.

        В текущей версии удаление через inline-кнопки.
        """
        await message.answer("""
ℹ️ <b>Удаление конфигураций</b>

Для удаления используйте команду /configs
и выберите конфигурацию с помощью кнопок.
        """)
        await state.clear()

    async def handle_delete_confirmation(self, callback: CallbackQuery) -> None:
        """Обработка подтверждения удаления конфигурации."""
        config_name = callback.data.replace("confirm_delete_", "")
        user_id = callback.from_user.id

        try:
            # Проверяем, есть ли такая конфигурация
            config = self.config_manager.get_user_config(user_id, config_name)
            if not config:
                await callback.message.edit_text(f"❌ Конфигурация '{config_name}' не найдена.")
                await callback.answer()
                return

            # Останавливаем мониторинг если он активен
            if user_id in self.running_users:
                await self.stop_monitoring_for_user(user_id)

            # Удаляем конфигурацию
            success = self.config_manager.delete_user_config(
                user_id, config_name)

            if success:
                await callback.message.edit_text(f"""
✅ <b>Конфигурация удалена</b>

Конфигурация '<b>{config_name}</b>' ({config.email}) успешно удалена.

Используйте /configs для управления оставшимися конфигурациями
или /setup для создания новой.
                """)
                logger.info(
                    f"Configuration '{config_name}' deleted for user {user_id}")
            else:
                await callback.message.edit_text(f"""
❌ <b>Ошибка удаления</b>

Не удалось удалить конфигурацию '{config_name}'.
Попробуйте еще раз или обратитесь к администратору.
                """)

        except Exception as e:
            logger.error(
                f"Error deleting configuration '{config_name}' for user {user_id}: {e}")
            await callback.message.edit_text(f"""
❌ <b>Ошибка удаления</b>

Произошла ошибка при удалении конфигурации: {str(e)}
            """)

        await callback.answer()

    async def handle_cancel_deletion(self, callback: CallbackQuery) -> None:
        """Обработка отмены удаления конфигурации."""
        await callback.message.edit_text("""
✅ <b>Удаление отменено</b>

Конфигурация не была удалена.

Используйте /configs для возврата к управлению конфигурациями.
        """)
        await callback.answer()

    async def stop_monitoring_for_user(self, user_id: int) -> None:
        """
        Вспомогательный метод для остановки мониторинга конкретного пользователя.

        Используется при удалении конфигураций для корректной очистки ресурсов.
        """
        try:
            if user_id in self.running_users:
                self.running_users.remove(user_id)

            if user_id in self.active_monitors:
                await self.active_monitors[user_id].cleanup()
                del self.active_monitors[user_id]

            logger.info(
                f"Monitoring stopped for user {user_id} during configuration management")

        except Exception as e:
            logger.error(f"Error stopping monitoring for user {user_id}: {e}")

    async def start_polling(self):
        """Запуск бота с логированием конфигураций."""
        logger.info("Starting Advanced Telegram Email Monitor Bot...")

        try:
            # Показываем статистику загруженных конфигураций
            stats = self.config_manager.get_config_stats()
            logger.info(
                f"Loaded configurations: {json.dumps(stats, indent=2, ensure_ascii=False)}")

            await self.dp.start_polling(self.bot)

        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
        except Exception as e:
            logger.error(f"Bot error: {e}")
        finally:
            await self._cleanup()

    async def _cleanup(self):
        """Корректная остановка всех мониторингов."""
        logger.info("Cleaning up active monitors...")

        for user_id, monitor in self.active_monitors.items():
            try:
                await monitor.cleanup()
            except Exception as e:
                logger.error(
                    f"Error cleaning up monitor for user {user_id}: {e}")

        self.running_users.clear()
        self.active_monitors.clear()
        await self.bot.session.close()

    # Заглушки для методов, которые нужно будет реализовать для редактирования и удаления
    async def _start_config_editing(self, callback: CallbackQuery, config_name: str) -> None:
        """
        Отображение информации о конфигурации с возможностью пересоздания.

        В будущих версиях здесь будет полноценное редактирование отдельных параметров.
        """
        user_id = callback.from_user.id
        config = self.config_manager.get_user_config(user_id, config_name)

        if not config:
            await callback.message.edit_text(f"❌ Конфигурация '{config_name}' не найдена.")
            return

        # Маскируем пароль для безопасности
        masked_password = "*" * \
            min(len(config.password), 8) if config.password else "Не установлен"

        # Форматируем время последней проверки
        last_check = "Никогда"
        if config.last_check_time:
            last_check = config.last_check_time.strftime(
                "%Y-%m-%d %H:%M:%S UTC")

        config_info = f"""
🔧 <b>Конфигурация: {config_name}</b>

<b>📧 Параметры подключения:</b>
• Сервер: <code>{config.host}:{config.port}</code>
• Email: <code>{config.email}</code>
• Пароль: <code>{masked_password}</code>
• Папка: <code>{config.folder}</code>

<b>⚙️ Настройки мониторинга:</b>
• Интервал проверки: <code>{config.check_interval} сек</code>
• Чат для уведомлений: <code>{config.target_chat_id}</code>
• Последняя проверка: <code>{last_check}</code>

<b>🔍 Фильтры:</b>
• По отправителю: <code>{config.filter_sender or 'Не установлен'}</code>
• По теме: <code>{config.filter_subject or 'Не установлен'}</code>
• По вложениям: <code>{config.filter_has_attachments if config.filter_has_attachments is not None else 'Не установлен'}</code>

<b>📝 Доступные действия:</b>

<i>В текущей версии полное редактирование пока не реализовано.</i>
<i>Вы можете пересоздать конфигурацию с новыми параметрами:</i>

1. Удалите текущую конфигурацию через /configs
2. Создайте новую с тем же именем через /setup
3. Или создайте новую конфигурацию с другим именем

<b>🔄 Планируемые функции редактирования:</b>
• Изменение интервала проверки
• Настройка фильтров
• Смена чата для уведомлений
• Обновление пароля
        """

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Удалить конфигурацию",
                    callback_data=f"config_delete_{config_name}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к списку",
                    callback_data="back_to_configs"
                )
            ]
        ])

        await callback.message.edit_text(config_info, reply_markup=keyboard)

    async def _confirm_config_deletion(self, callback: CallbackQuery, config_name: str) -> None:
        """Подтверждение удаления конфигурации."""
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, удалить",
                    callback_data=f"confirm_delete_{config_name}"
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="cancel_delete"
                )
            ]
        ])

        await callback.message.edit_text(
            f"🗑 <b>Подтверждение удаления</b>\n\n"
            f"Вы уверены, что хотите удалить конфигурацию '<b>{config_name}</b>'?\n\n"
            f"Это действие нельзя отменить.",
            reply_markup=keyboard
        )

        # Здесь нужно добавить обработчик для confirm_delete и cancel_delete колбэков


def main():
    """Точка входа с улучшенной проверкой окружения."""
    # Используем Path для более надёжной работы с файлами
    env_file = Path(".env")

    if not env_file.exists():
        logger.warning(
            ".env file not found, relying on system environment variables")

    bot_token = os.getenv("BOT_TOKEN")

    if not bot_token or bot_token == "YOUR_BOT_TOKEN_HERE":
        print("❌ Пожалуйста, укажите токен бота в переменной BOT_TOKEN")
        print("Получить токен можно у @BotFather в Telegram")
        sys.exit(1)

    # Проверяем доступность файла конфигурации
    config_path = Path(CONFIG_FILE)
    logger.info(f"Configuration file path: {config_path.absolute()}")

    # Создаем и запускаем бота
    bot = EmailBot(bot_token, CONFIG_FILE)

    try:
        asyncio.run(bot.start_polling())
    except KeyboardInterrupt:
        logger.info("Shutdown requested by user")
    except Exception as e:
        logger.error(f"Critical error: {e}")


if __name__ == "__main__":
    main()
