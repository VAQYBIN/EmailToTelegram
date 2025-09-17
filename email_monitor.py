import logging
import email
import ssl
from typing import Optional, Set, Dict, Any, List  # Восстанавливаем List
from dataclasses import dataclass
# Восстанавливаем datetime и добавляем timezone
from datetime import datetime, timezone
import re

import aioimaplib
from aiogram import Bot

logger = logging.getLogger(__name__)


@dataclass
class EmailConfig:
    """
    Конфигурация для подключения к почтовому серверу.

    Эта структура данных содержит все параметры, необходимые для
    подключения к IMAP серверу и настройки мониторинга.
    """
    host: str                    # IMAP сервер (например, imap.gmail.com)
    port: int                    # Порт подключения (обычно 993 для SSL)
    email: str                   # Email адрес для подключения
    password: str                # Пароль или токен приложения
    folder: str = "INBOX"        # Папка для мониторинга
    check_interval: int = 300    # Интервал проверки в секундах
    target_chat_id: int = 0      # ID чата для отправки уведомлений
    user_id: int = 0             # ID пользователя Telegram
    config_name: str = "default"  # Имя конфигурации для различения множественных настроек

    # Время последней проверки (критически важно для парсинга только новых сообщений)
    last_check_time: Optional[datetime] = None

    # Настройки фильтрации писем
    filter_sender: Optional[str] = None        # Фильтр по отправителю
    filter_subject: Optional[str] = None       # Фильтр по теме
    filter_has_attachments: Optional[bool] = None  # Фильтр по наличию вложений

    def to_dict(self) -> Dict[str, Any]:
        """Преобразование в словарь для сериализации"""
        return {
            'host': self.host,
            'port': self.port,
            'email': self.email,
            'password': self.password,  # В реальном проекте следует шифровать
            'folder': self.folder,
            'check_interval': self.check_interval,
            'target_chat_id': self.target_chat_id,
            'user_id': self.user_id,
            'config_name': self.config_name,
            'last_check_time': self.last_check_time.isoformat() if self.last_check_time else None,
            'filter_sender': self.filter_sender,
            'filter_subject': self.filter_subject,
            'filter_has_attachments': self.filter_has_attachments
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EmailConfig':
        """Создание объекта из словаря"""
        # Восстанавливаем datetime из строки
        if data.get('last_check_time'):
            data['last_check_time'] = datetime.fromisoformat(
                data['last_check_time'])

        return cls(**data)


class EmailMonitor:
    """
    Основной класс для мониторинга электронной почты.

    Теперь поддерживает:
    - Множественные конфигурации для одного пользователя
    - Парсинг только новых сообщений с момента последней проверки
    - Отображение источника почты в уведомлениях
    """

    def __init__(self, configs: List[EmailConfig], bot: Bot):
        """
        Инициализация монитора почты с поддержкой множественных конфигураций.

        Args:
            configs: Список конфигураций для мониторинга (может быть несколько почт)
            bot: Экземпляр Telegram бота для отправки сообщений
        """
        self.configs = configs
        self.bot = bot

        # Словарь для отслеживания уже обработанных писем по каждой почте
        # Ключ - email адрес, значение - множество ID сообщений
        self.processed_messages: Dict[str, Set[str]] = {}

        # Словарь IMAP клиентов для каждой почты
        self.imap_clients: Dict[str, Optional[aioimaplib.IMAP4_SSL]] = {}

        # Счетчики ошибок для каждой почты
        self.error_counts: Dict[str, int] = {}
        self.max_errors = 5

        # Инициализируем структуры данных для каждой конфигурации
        for config in self.configs:
            self.processed_messages[config.email] = set()
            self.imap_clients[config.email] = None
            self.error_counts[config.email] = 0

            # Если время последней проверки не установлено, устанавливаем текущее время
            # Это гарантирует, что мы будем парсить только сообщения после запуска мониторинга
            if config.last_check_time is None:
                config.last_check_time = datetime.now(timezone.utc)
                logger.info(
                    f"Set initial check time for {config.email}: {config.last_check_time}")

        logger.info(
            f"Email monitor initialized for {len(configs)} configurations")

    async def test_connection(self, config: EmailConfig) -> bool:
        """
        Тестирование подключения к конкретному почтовому серверу.

        Args:
            config: Конфигурация для тестирования

        Returns:
            bool: True если подключение успешно
        """
        try:
            logger.info(
                f"Testing connection to {config.host}:{config.port} for {config.email}")

            ssl_context = ssl.create_default_context()
            test_client = aioimaplib.IMAP4_SSL(
                host=config.host,
                port=config.port,
                ssl_context=ssl_context,
                timeout=30
            )

            await test_client.wait_hello_from_server()

            login_response = await test_client.login(config.email, config.password)
            if login_response.result != 'OK':
                logger.error(
                    f"Login failed for {config.email}: {login_response}")
                return False

            select_response = await test_client.select(config.folder)
            if select_response.result != 'OK':
                logger.error(
                    f"Folder selection failed for {config.email}: {select_response}")
                return False

            await test_client.logout()
            logger.info(f"Connection test successful for {config.email}")
            return True

        except Exception as e:
            logger.error(f"Connection test failed for {config.email}: {e}")
            return False

    async def test_all_connections(self) -> Dict[str, bool]:
        """
        Тестирование подключения ко всем настроенным почтовым серверам.

        Returns:
            Dict: Результаты тестирования для каждой почты
        """
        results = {}
        for config in self.configs:
            results[config.email] = await self.test_connection(config)
        return results

    async def _connect(self, config: EmailConfig) -> bool:
        """
        Установка соединения с конкретным IMAP сервером.

        Args:
            config: Конфигурация для подключения

        Returns:
            bool: True если подключение установлено успешно
        """
        try:
            email_addr = config.email

            if self.imap_clients[email_addr]:
                # Проверяем существующее соединение
                try:
                    await self.imap_clients[email_addr].noop()
                    return True
                except Exception:
                    self.imap_clients[email_addr] = None

            logger.info(
                f"Connecting to {config.host}:{config.port} for {email_addr}")

            ssl_context = ssl.create_default_context()
            self.imap_clients[email_addr] = aioimaplib.IMAP4_SSL(
                host=config.host,
                port=config.port,
                ssl_context=ssl_context,
                timeout=30
            )

            await self.imap_clients[email_addr].wait_hello_from_server()

            login_response = await self.imap_clients[email_addr].login(
                config.email, config.password
            )
            if login_response.result != 'OK':
                logger.error(
                    f"Login failed for {email_addr}: {login_response}")
                return False

            select_response = await self.imap_clients[email_addr].select(config.folder)
            if select_response.result != 'OK':
                logger.error(
                    f"Failed to select folder {config.folder} for {email_addr}: {select_response}")
                return False

            logger.info(f"Successfully connected to {email_addr}")
            self.error_counts[email_addr] = 0
            return True

        except Exception as e:
            logger.error(f"Connection failed for {config.email}: {e}")
            self.imap_clients[config.email] = None
            self.error_counts[config.email] += 1
            return False

    async def check_new_emails(self) -> None:
        """
        Основной метод для проверки новых писем во всех настроенных почтах.

        Проверяет каждую почту отдельно и обрабатывает только сообщения,
        полученные после последней проверки.
        """
        for config in self.configs:
            try:
                await self._check_emails_for_config(config)
            except Exception as e:
                logger.error(f"Error checking emails for {config.email}: {e}")
                self.error_counts[config.email] += 1

    async def _check_emails_for_config(self, config: EmailConfig) -> None:
        """
        Проверка новых писем для конкретной конфигурации.

        Args:
            config: Конфигурация почты для проверки
        """
        email_addr = config.email

        # Проверяем лимит ошибок
        if self.error_counts[email_addr] >= self.max_errors:
            logger.error(f"Too many errors for {email_addr}, skipping")
            return

        # Подключаемся к серверу
        if not await self._connect(config):
            logger.error(f"Failed to connect to {email_addr}")
            return

        try:
            # Формируем поисковый запрос для новых сообщений
            search_criteria = self._build_search_criteria(config)

            logger.debug(
                f"Searching with criteria: {search_criteria} for {email_addr}")
            search_response = await self.imap_clients[email_addr].search(search_criteria)

            if search_response.result != 'OK':
                logger.error(
                    f"Search failed for {email_addr}: {search_response}")
                return

            # Получаем список ID новых писем
            message_ids = search_response.lines[0].split(
            ) if search_response.lines[0] else []

            if not message_ids:
                logger.debug(f"No new messages found for {email_addr}")
            else:
                logger.info(
                    f"Found {len(message_ids)} new messages for {email_addr}")

                # Обрабатываем каждое новое письмо
                for msg_id in message_ids:
                    await self._process_message(msg_id.decode(), config)

            # Обновляем время последней проверки
            config.last_check_time = datetime.now(timezone.utc)
            logger.debug(
                f"Updated last check time for {email_addr}: {config.last_check_time}")

        except Exception as e:
            logger.error(f"Error checking emails for {email_addr}: {e}")
            self.error_counts[email_addr] += 1
            # Сброс соединения при ошибке
            self.imap_clients[email_addr] = None

    def _build_search_criteria(self, config: EmailConfig) -> str:
        """
        Построение критериев поиска для IMAP.

        Создаёт поисковый запрос, который ищет только сообщения,
        полученные после последней проверки.

        Args:
            config: Конфигурация с временем последней проверки

        Returns:
            str: IMAP поисковый критерий
        """
        criteria_parts = ['UNSEEN']  # Начинаем с непрочитанных

        # Добавляем фильтр по времени, если есть время последней проверки
        if config.last_check_time:
            # Форматируем дату для IMAP (RFC2822 format)
            # IMAP использует формат "DD-MMM-YYYY" для дат
            since_date = config.last_check_time.strftime("%d-%b-%Y")
            criteria_parts.append(f'SINCE {since_date}')

            logger.debug(
                f"Searching for emails since {since_date} for {config.email}")

        return ' '.join(criteria_parts)

    async def _process_message(self, message_id: str, config: EmailConfig) -> None:
        """
        Обработка конкретного письма с указанием источника.

        Args:
            message_id: ID сообщения
            config: Конфигурация почты (для определения источника)
        """
        try:
            email_addr = config.email

            # Проверяем, не обработано ли уже это письмо для данной почты
            if message_id in self.processed_messages[email_addr]:
                return

            logger.info(f"Processing message {message_id} from {email_addr}")

            # Получаем содержимое письма
            fetch_response = await self.imap_clients[email_addr].fetch(message_id, '(RFC822)')

            if fetch_response.result != 'OK':
                logger.error(
                    f"Failed to fetch message {message_id} from {email_addr}: {fetch_response}")
                return

            # Парсим письмо
            raw_email = fetch_response.lines[1]
            email_message = email.message_from_bytes(raw_email)

            # Извлекаем информацию с указанием источника
            email_info = self._extract_email_info(email_message, config)

            # Применяем фильтры
            if not self._should_process_email(email_info, config):
                logger.debug(
                    f"Email {message_id} from {email_addr} filtered out")
                self.processed_messages[email_addr].add(message_id)
                return

            # Отправляем уведомление
            await self._send_notification(email_info, config)

            # Добавляем в список обработанных для данной почты
            self.processed_messages[email_addr].add(message_id)

            logger.info(
                f"Message {message_id} from {email_addr} processed successfully")

        except Exception as e:
            logger.error(
                f"Error processing message {message_id} from {config.email}: {e}")

    def _extract_email_info(self, email_message: email.message.Message, config: EmailConfig) -> Dict[str, Any]:
        """
        Извлечение информации из письма с добавлением источника.

        Args:
            email_message: Объект письма
            config: Конфигурация (для определения источника)

        Returns:
            Dict с информацией о письме включая источник
        """
        info = {
            'subject': email_message.get('Subject', 'No Subject'),
            'sender': email_message.get('From', 'Unknown Sender'),
            'date': email_message.get('Date', 'Unknown Date'),
            'to': email_message.get('To', ''),
            'body': '',
            'has_attachments': False,
            'attachments_count': 0,
            'source_email': config.email,  # Добавляем источник письма
            'config_name': config.config_name  # Добавляем имя конфигурации
        }

        # Декодируем тему письма
        if info['subject']:
            decoded_subject = email.header.decode_header(info['subject'])
            info['subject'] = ''.join([
                part.decode(encoding or 'utf-8') if isinstance(part,
                                                               bytes) else part
                for part, encoding in decoded_subject
            ])

        # Декодируем отправителя
        if info['sender']:
            decoded_sender = email.header.decode_header(info['sender'])
            info['sender'] = ''.join([
                part.decode(encoding or 'utf-8') if isinstance(part,
                                                               bytes) else part
                for part, encoding in decoded_sender
            ])

        # Извлекаем содержимое
        info['body'] = self._extract_body(email_message)

        # Подсчитываем вложения
        for part in email_message.walk():
            if part.get_content_disposition() == 'attachment':
                info['has_attachments'] = True
                info['attachments_count'] += 1

        return info

    def _extract_body(self, email_message: email.message.Message) -> str:
        """
        Извлечение текста письма.
        (Метод остается без изменений)
        """
        body = ""

        if not email_message.is_multipart():
            content_type = email_message.get_content_type()
            charset = email_message.get_content_charset() or 'utf-8'

            if content_type == 'text/plain':
                body = email_message.get_payload(
                    decode=True).decode(charset, errors='ignore')
            elif content_type == 'text/html':
                html_content = email_message.get_payload(
                    decode=True).decode(charset, errors='ignore')
                body = self._strip_html_tags(html_content)
        else:
            for part in email_message.walk():
                content_type = part.get_content_type()
                content_disposition = part.get_content_disposition()

                if content_disposition == 'attachment':
                    continue

                if content_type == 'text/plain':
                    charset = part.get_content_charset() or 'utf-8'
                    part_content = part.get_payload(
                        decode=True).decode(charset, errors='ignore')
                    body += part_content + '\n'
                elif content_type == 'text/html' and not body:
                    charset = part.get_content_charset() or 'utf-8'
                    html_content = part.get_payload(
                        decode=True).decode(charset, errors='ignore')
                    body = self._strip_html_tags(html_content)

        return body[:2000] + '...' if len(body) > 2000 else body

    def _strip_html_tags(self, html_content: str) -> str:
        """Удаление HTML тегов (метод остается без изменений)"""
        clean_text = re.sub(r'<[^>]+>', '', html_content)
        clean_text = clean_text.replace('&nbsp;', ' ')
        clean_text = clean_text.replace('&amp;', '&')
        clean_text = clean_text.replace('&lt;', '<')
        clean_text = clean_text.replace('&gt;', '>')
        clean_text = clean_text.replace('&quot;', '"')
        clean_text = re.sub(r'\s+', ' ', clean_text)
        return clean_text.strip()

    def _should_process_email(self, email_info: Dict[str, Any], config: EmailConfig) -> bool:
        """
        Проверка соответствия письма фильтрам конкретной конфигурации.

        Args:
            email_info: Информация о письме
            config: Конфигурация с фильтрами

        Returns:
            bool: True если письмо прошло все фильтры
        """
        # Фильтр по отправителю
        if config.filter_sender:
            sender_filter = config.filter_sender.lower()
            sender = email_info['sender'].lower()
            if sender_filter not in sender:
                logger.debug(
                    f"Email filtered out by sender: {email_info['sender']}")
                return False

        # Фильтр по теме
        if config.filter_subject:
            subject_filter = config.filter_subject.lower()
            subject = email_info['subject'].lower()
            if subject_filter not in subject:
                logger.debug(
                    f"Email filtered out by subject: {email_info['subject']}")
                return False

        # Фильтр по наличию вложений
        if config.filter_has_attachments is not None:
            if email_info['has_attachments'] != config.filter_has_attachments:
                logger.debug(
                    f"Email filtered out by attachments: {email_info['has_attachments']}")
                return False

        return True

    async def _send_notification(self, email_info: Dict[str, Any], config: EmailConfig) -> None:
        """
        Отправка уведомления в Telegram с указанием источника.

        Args:
            email_info: Информация о письме
            config: Конфигурация (для определения чата назначения)
        """
        try:
            message_text = self._format_notification(email_info)

            await self.bot.send_message(
                chat_id=config.target_chat_id,
                text=message_text,
                parse_mode='HTML'
            )

            logger.info(
                f"Notification sent to chat {config.target_chat_id} for email from {config.email}")

        except Exception as e:
            logger.error(
                f"Failed to send notification for {config.email}: {e}")

    def _format_notification(self, email_info: Dict[str, Any]) -> str:
        """
        Форматирование уведомления с указанием источника почты.

        Args:
            email_info: Информация о письме (включая source_email)

        Returns:
            str: Отформатированное сообщение
        """
        attachment_icon = "📎" if email_info['has_attachments'] else ""
        source_email = email_info.get('source_email', 'Unknown')
        config_name = email_info.get('config_name', 'default')

        message = f"""
📧 <b>Новое письмо!</b> {attachment_icon}

📮 <b>Получено на:</b> <code>{self._escape_html(source_email)}</code>
📋 <b>Конфигурация:</b> <code>{self._escape_html(config_name)}</code>
<b>От:</b> {self._escape_html(email_info['sender'])}
<b>Тема:</b> {self._escape_html(email_info['subject'])}
<b>Дата:</b> {email_info['date']}
        """

        if email_info['has_attachments']:
            message += f"\n<b>Вложений:</b> {email_info['attachments_count']}"

        if email_info['body']:
            preview = email_info['body'][:300] + \
                '...' if len(email_info['body']) > 300 else email_info['body']
            message += f"\n\n<b>Содержимое:</b>\n{self._escape_html(preview)}"

        message += "\n" + "─" * 30

        return message

    def _escape_html(self, text: str) -> str:
        """Экранирование HTML символов (метод остается без изменений)"""
        if not text:
            return ""
        return (text
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;'))

    async def cleanup(self) -> None:
        """Корректная очистка ресурсов для всех подключений."""
        try:
            for email_addr, client in self.imap_clients.items():
                if client:
                    await client.logout()
                    self.imap_clients[email_addr] = None

            logger.info(
                "Email monitor cleanup completed for all configurations")

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """
        Получение статистики работы для всех конфигураций.

        Returns:
            Dict с подробной статистикой
        """
        stats = {
            'total_configs': len(self.configs),
            'configs': []
        }

        for config in self.configs:
            email_addr = config.email
            config_stats = {
                'email': email_addr,
                'config_name': config.config_name,
                'folder': config.folder,
                'processed_messages': len(self.processed_messages.get(email_addr, set())),
                'error_count': self.error_counts.get(email_addr, 0),
                'connected': self.imap_clients.get(email_addr) is not None,
                'check_interval': config.check_interval,
                'last_check_time': config.last_check_time.isoformat() if config.last_check_time else None
            }
            stats['configs'].append(config_stats)

        return stats
