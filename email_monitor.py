import logging
import email
import ssl
from typing import Optional, Set, Dict, Any  # , List
from dataclasses import dataclass
# from datetime import datetime
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
            'filter_sender': self.filter_sender,
            'filter_subject': self.filter_subject,
            'filter_has_attachments': self.filter_has_attachments
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EmailConfig':
        """Создание объекта из словаря"""
        return cls(**data)


class EmailMonitor:
    """
    Основной класс для мониторинга электронной почты.

    Представьте этот класс как специального агента, который:
    1. Подключается к вашему почтовому ящику через IMAP
    2. Периодически проверяет новые письма
    3. Анализирует их содержимое
    4. Отправляет уведомления в Telegram

    Класс использует асинхронное программирование для эффективной работы
    с сетевыми запросами и не блокирует выполнение других задач.
    """

    def __init__(self, config: EmailConfig, bot: Bot):
        """
        Инициализация монитора почты.

        Args:
            config: Конфигурация подключения к почте
            bot: Экземпляр Telegram бота для отправки сообщений
        """
        self.config = config
        self.bot = bot

        # Множество для отслеживания уже обработанных писем
        # Это предотвращает дублирование уведомлений
        self.processed_messages: Set[str] = set()

        # Клиент IMAP будет создан при подключении
        self.imap_client: Optional[aioimaplib.IMAP4_SSL] = None

        # Счетчик ошибок для реализации политики повторных попыток
        self.error_count = 0
        self.max_errors = 5

        logger.info(f"Email monitor initialized for {config.email}")

    async def test_connection(self) -> bool:
        """
        Тестирование подключения к почтовому серверу.

        Эта функция выполняет пробное подключение для проверки
        корректности настроек перед запуском мониторинга.

        Returns:
            bool: True если подключение успешно, False в случае ошибки
        """
        try:
            logger.info(
                f"Testing connection to {self.config.host}:{self.config.port}")

            # Создаем SSL контекст для безопасного подключения
            ssl_context = ssl.create_default_context()

            # Создаем IMAP клиент с SSL
            test_client = aioimaplib.IMAP4_SSL(
                host=self.config.host,
                port=self.config.port,
                ssl_context=ssl_context,
                timeout=30  # 30 секунд на подключение
            )

            # Ждем приветствия от сервера
            await test_client.wait_hello_from_server()

            # Пытаемся авторизоваться
            login_response = await test_client.login(
                self.config.email,
                self.config.password
            )

            if login_response.result != 'OK':
                logger.error(f"Login failed: {login_response}")
                return False

            # Пытаемся выбрать указанную папку
            select_response = await test_client.select(self.config.folder)

            if select_response.result != 'OK':
                logger.error(f"Folder selection failed: {select_response}")
                return False

            # Корректно закрываем соединение
            await test_client.logout()

            logger.info("Connection test successful")
            return True

        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False

    async def _connect(self) -> bool:
        """
        Установка соединения с IMAP сервером.

        Эта внутренняя функция создает и настраивает подключение
        к почтовому серверу с обработкой различных типов ошибок.

        Returns:
            bool: True если подключение установлено успешно
        """
        try:
            if self.imap_client:
                # Если уже подключены, проверяем состояние соединения
                try:
                    await self.imap_client.noop()  # "No operation" для проверки связи
                    return True
                except Exception:
                    # Соединение неактивно, создаем новое
                    self.imap_client = None

            logger.info(f"Connecting to {self.config.host}:{self.config.port}")

            # Создаем SSL контекст
            ssl_context = ssl.create_default_context()

            # Создаем новый IMAP клиент
            self.imap_client = aioimaplib.IMAP4_SSL(
                host=self.config.host,
                port=self.config.port,
                ssl_context=ssl_context,
                timeout=30
            )

            # Ждем приветствие от сервера
            await self.imap_client.wait_hello_from_server()

            # Авторизация
            login_response = await self.imap_client.login(
                self.config.email,
                self.config.password
            )

            if login_response.result != 'OK':
                logger.error(f"Login failed: {login_response}")
                return False

            # Выбираем папку для мониторинга
            select_response = await self.imap_client.select(self.config.folder)

            if select_response.result != 'OK':
                logger.error(
                    f"Failed to select folder {self.config.folder}: {select_response}")
                return False

            logger.info(f"Successfully connected to {self.config.email}")
            self.error_count = 0  # Сбрасываем счетчик ошибок при успешном подключении
            return True

        except Exception as e:
            logger.error(f"Connection failed: {e}")
            self.imap_client = None
            self.error_count += 1
            return False

    async def check_new_emails(self) -> None:
        """
        Основной метод для проверки новых писем.

        Этот метод выполняется периодически и представляет собой
        главный цикл мониторинга. Он:
        1. Подключается к серверу (если не подключен)
        2. Ищет новые письма
        3. Обрабатывает каждое новое письмо
        4. Отправляет уведомления в Telegram
        """
        try:
            # Проверяем лимит ошибок
            if self.error_count >= self.max_errors:
                logger.error(
                    f"Too many errors ({self.error_count}), stopping monitoring")
                return

            # Подключаемся к серверу
            if not await self._connect():
                logger.error("Failed to connect to email server")
                return

            # Ищем новые письма в указанной папке
            # UNSEEN означает непрочитанные письма
            search_response = await self.imap_client.search('UNSEEN')

            if search_response.result != 'OK':
                logger.error(f"Search failed: {search_response}")
                return

            # Получаем список ID новых писем
            message_ids = search_response.lines[0].split(
            ) if search_response.lines[0] else []

            if not message_ids:
                logger.debug("No new messages found")
                return

            logger.info(f"Found {len(message_ids)} new messages")

            # Обрабатываем каждое новое письмо
            for msg_id in message_ids:
                await self._process_message(msg_id.decode())

        except Exception as e:
            logger.error(f"Error checking emails: {e}")
            self.error_count += 1
            # Сброс соединения при ошибке
            self.imap_client = None

    async def _process_message(self, message_id: str) -> None:
        """
        Обработка конкретного письма.

        Эта функция извлекает содержимое письма, применяет фильтры
        и отправляет уведомление в Telegram если письмо соответствует критериям.

        Args:
            message_id: Уникальный идентификатор письма на сервере
        """
        try:
            # Проверяем, не обработано ли уже это письмо
            if message_id in self.processed_messages:
                return

            logger.info(f"Processing message {message_id}")

            # Получаем заголовки и содержимое письма
            # RFC822 означает полный формат письма
            fetch_response = await self.imap_client.fetch(message_id, '(RFC822)')

            if fetch_response.result != 'OK':
                logger.error(
                    f"Failed to fetch message {message_id}: {fetch_response}")
                return

            # Парсим сырое содержимое письма
            raw_email = fetch_response.lines[1]
            email_message = email.message_from_bytes(raw_email)

            # Извлекаем основную информацию о письме
            email_info = self._extract_email_info(email_message)

            # Применяем фильтры
            if not self._should_process_email(email_info):
                logger.debug(f"Email {message_id} filtered out")
                self.processed_messages.add(message_id)
                return

            # Отправляем уведомление в Telegram
            await self._send_notification(email_info)

            # Добавляем в список обработанных
            self.processed_messages.add(message_id)

            logger.info(f"Message {message_id} processed successfully")

        except Exception as e:
            logger.error(f"Error processing message {message_id}: {e}")

    def _extract_email_info(self, email_message: email.message.Message) -> Dict[str, Any]:
        """
        Извлечение информации из письма.

        Эта функция анализирует структуру письма и извлекает
        все важные данные: отправителя, тему, содержимое, вложения.

        Args:
            email_message: Объект письма после парсинга

        Returns:
            Dict со всей извлеченной информацией
        """
        info = {
            'subject': email_message.get('Subject', 'No Subject'),
            'sender': email_message.get('From', 'Unknown Sender'),
            'date': email_message.get('Date', 'Unknown Date'),
            'to': email_message.get('To', ''),
            'body': '',
            'has_attachments': False,
            'attachments_count': 0
        }

        # Декодируем тему письма если она закодирована
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

        # Извлекаем содержимое письма
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

        Письма могут содержать различные форматы: обычный текст, HTML,
        многокомпонентные сообщения. Эта функция обрабатывает все случаи.

        Args:
            email_message: Объект письма

        Returns:
            str: Текстовое содержимое письма
        """
        body = ""

        # Если письмо простое (не multipart)
        if not email_message.is_multipart():
            content_type = email_message.get_content_type()
            charset = email_message.get_content_charset() or 'utf-8'

            if content_type == 'text/plain':
                body = email_message.get_payload(
                    decode=True).decode(charset, errors='ignore')
            elif content_type == 'text/html':
                # Для HTML извлекаем только текст (убираем теги)
                html_content = email_message.get_payload(
                    decode=True).decode(charset, errors='ignore')
                body = self._strip_html_tags(html_content)
        else:
            # Обрабатываем multipart сообщения
            for part in email_message.walk():
                content_type = part.get_content_type()
                content_disposition = part.get_content_disposition()

                # Пропускаем вложения
                if content_disposition == 'attachment':
                    continue

                if content_type == 'text/plain':
                    charset = part.get_content_charset() or 'utf-8'
                    part_content = part.get_payload(
                        decode=True).decode(charset, errors='ignore')
                    body += part_content + '\n'
                elif content_type == 'text/html' and not body:
                    # Используем HTML только если нет plain text версии
                    charset = part.get_content_charset() or 'utf-8'
                    html_content = part.get_payload(
                        decode=True).decode(charset, errors='ignore')
                    body = self._strip_html_tags(html_content)

        # Ограничиваем длину для Telegram (максимум 4096 символов в сообщении)
        return body[:2000] + '...' if len(body) > 2000 else body

    def _strip_html_tags(self, html_content: str) -> str:
        """
        Удаление HTML тегов из текста.

        Простая функция для преобразования HTML в обычный текст.
        В продакшене лучше использовать специализированные библиотеки
        как BeautifulSoup или html2text.

        Args:
            html_content: HTML содержимое

        Returns:
            str: Очищенный текст
        """
        # Удаляем HTML теги с помощью регулярных выражений
        clean_text = re.sub(r'<[^>]+>', '', html_content)

        # Заменяем HTML entities
        clean_text = clean_text.replace('&nbsp;', ' ')
        clean_text = clean_text.replace('&amp;', '&')
        clean_text = clean_text.replace('&lt;', '<')
        clean_text = clean_text.replace('&gt;', '>')
        clean_text = clean_text.replace('&quot;', '"')

        # Убираем лишние пробелы и переводы строк
        clean_text = re.sub(r'\s+', ' ', clean_text)

        return clean_text.strip()

    def _should_process_email(self, email_info: Dict[str, Any]) -> bool:
        """
        Проверка соответствия письма фильтрам.

        Эта функция применяет различные фильтры к письму:
        - По отправителю
        - По теме
        - По наличию вложений

        Args:
            email_info: Информация о письме

        Returns:
            bool: True если письмо прошло все фильтры
        """
        # Фильтр по отправителю
        if self.config.filter_sender:
            sender_filter = self.config.filter_sender.lower()
            sender = email_info['sender'].lower()
            if sender_filter not in sender:
                logger.debug(
                    f"Email filtered out by sender: {email_info['sender']}")
                return False

        # Фильтр по теме
        if self.config.filter_subject:
            subject_filter = self.config.filter_subject.lower()
            subject = email_info['subject'].lower()
            if subject_filter not in subject:
                logger.debug(
                    f"Email filtered out by subject: {email_info['subject']}")
                return False

        # Фильтр по наличию вложений
        if self.config.filter_has_attachments is not None:
            if email_info['has_attachments'] != self.config.filter_has_attachments:
                logger.debug(
                    f"Email filtered out by attachments: {email_info['has_attachments']}")
                return False

        return True

    async def _send_notification(self, email_info: Dict[str, Any]) -> None:
        """
        Отправка уведомления в Telegram.

        Форматирует информацию о письме и отправляет красивое
        уведомление в указанный чат Telegram.

        Args:
            email_info: Информация о письме для отправки
        """
        try:
            # Формируем красивое сообщение для Telegram
            message_text = self._format_notification(email_info)

            # Отправляем уведомление
            await self.bot.send_message(
                chat_id=self.config.target_chat_id,
                text=message_text,
                parse_mode='HTML'
            )

            logger.info(
                f"Notification sent to chat {self.config.target_chat_id}")

        except Exception as e:
            logger.error(f"Failed to send notification: {e}")

    def _format_notification(self, email_info: Dict[str, Any]) -> str:
        """
        Форматирование уведомления для Telegram.

        Создает красиво оформленное сообщение с эмодзи и HTML форматированием.

        Args:
            email_info: Информация о письме

        Returns:
            str: Отформатированное сообщение для Telegram
        """
        # Эмодзи для визуального оформления
        attachment_icon = "📎" if email_info['has_attachments'] else ""

        message = f"""
📧 <b>Новое письмо!</b> {attachment_icon}

<b>От:</b> {self._escape_html(email_info['sender'])}
<b>Тема:</b> {self._escape_html(email_info['subject'])}
<b>Дата:</b> {email_info['date']}
        """

        # Добавляем информацию о вложениях
        if email_info['has_attachments']:
            message += f"\n<b>Вложений:</b> {email_info['attachments_count']}"

        # Добавляем превью содержимого если есть
        if email_info['body']:
            preview = email_info['body'][:300] + \
                '...' if len(email_info['body']) > 300 else email_info['body']
            message += f"\n\n<b>Содержимое:</b>\n{self._escape_html(preview)}"

        # Добавляем разделитель
        message += "\n" + "─" * 30

        return message

    def _escape_html(self, text: str) -> str:
        """
        Экранирование HTML символов для Telegram.

        Telegram использует HTML форматирование, поэтому нужно
        экранировать специальные символы в тексте.

        Args:
            text: Исходный текст

        Returns:
            str: Экранированный текст
        """
        if not text:
            return ""

        return (text
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;'))

    async def cleanup(self) -> None:
        """
        Корректная очистка ресурсов при остановке мониторинга.

        Закрывает IMAP соединение и освобождает ресурсы.
        """
        try:
            if self.imap_client:
                await self.imap_client.logout()
                self.imap_client = None

            logger.info("Email monitor cleanup completed")

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """
        Получение статистики работы монитора.

        Returns:
            Dict с информацией о состоянии монитора
        """
        return {
            'email': self.config.email,
            'folder': self.config.folder,
            'processed_messages': len(self.processed_messages),
            'error_count': self.error_count,
            'connected': self.imap_client is not None,
            'check_interval': self.config.check_interval
        }
