import json
import logging
import base64
from typing import Dict, Optional, List, Any
from pathlib import Path
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from email_monitor import EmailConfig

logger = logging.getLogger(__name__)


class ConfigManager:
    """
    Менеджер конфигураций для безопасного хранения настроек пользователей.

    Этот класс решает важную задачу: как безопасно сохранить чувствительные
    данные пользователей (пароли от почты) локально, чтобы бот мог работать
    автономно, но при этом данные были защищены от несанкционированного доступа.

    Представьте его как сейф с индивидуальными ячейками для каждого пользователя,
    где каждая ячейка защищена своим уникальным ключом шифрования.
    """

    def __init__(self, config_file: str, master_password: str = None):
        """
        Инициализация менеджера конфигураций.

        Args:
            config_file: Путь к файлу для хранения конфигураций
            master_password: Мастер-пароль для шифрования (если не указан, используется системный)
        """
        self.config_file = Path(config_file)
        self.configs: Dict[int, EmailConfig] = {}

        # Создаем ключ шифрования на основе мастер-пароля или системных данных
        self.encryption_key = self._generate_encryption_key(master_password)
        self.cipher_suite = Fernet(self.encryption_key)

        # Загружаем существующие конфигурации при инициализации
        self._load_configs()

        logger.info(f"ConfigManager initialized with file: {self.config_file}")

    def _generate_encryption_key(self, master_password: Optional[str]) -> bytes:
        """
        Генерация ключа шифрования.

        Эта функция создает стойкий криптографический ключ на основе:
        1. Мастер-пароля (если предоставлен)
        2. Системных параметров (имя машины, путь к файлу)
        3. Статической соли для консистентности

        Подход PBKDF2 (Password-Based Key Derivation Function) используется
        для создания стойкого ключа из относительно слабого пароля.

        Args:
            master_password: Опциональный мастер-пароль

        Returns:
            bytes: 32-байтный ключ для симметричного шифрования
        """
        # Создаем базовую строку для ключа
        if master_password:
            password = master_password.encode()
        else:
            # Если мастер-пароль не задан, используем системные данные
            import platform
            system_info = f"{platform.node()}-{self.config_file.absolute()}"
            password = system_info.encode()

        # Создаем статическую соль (в продакшене лучше хранить отдельно)
        salt = b'telegram_email_bot_salt_2024'

        # Используем PBKDF2 для создания стойкого ключа
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,  # 256 бит для AES
            salt=salt,
            iterations=100000,  # Достаточно итераций для замедления атак
        )

        key = base64.urlsafe_b64encode(kdf.derive(password))
        logger.info("Encryption key generated successfully")
        return key

    def _encrypt_password(self, password: str) -> str:
        """
        Шифрование пароля для безопасного хранения.

        Преобразует пароль в зашифрованную строку, которая безопасна
        для хранения в файле. Даже если злоумышленник получит доступ
        к файлу конфигурации, он не сможет извлечь реальные пароли.

        Args:
            password: Открытый пароль

        Returns:
            str: Зашифрованный пароль в base64 формате
        """
        try:
            encrypted_bytes = self.cipher_suite.encrypt(password.encode())
            return base64.urlsafe_b64encode(encrypted_bytes).decode()
        except Exception as e:
            logger.error(f"Password encryption failed: {e}")
            # В крайнем случае возвращаем исходный пароль (не рекомендуется для продакшена)
            return password

    def _decrypt_password(self, encrypted_password: str) -> str:
        """
        Расшифровка пароля.

        Восстанавливает исходный пароль из зашифрованной строки.

        Args:
            encrypted_password: Зашифрованный пароль

        Returns:
            str: Расшифрованный пароль
        """
        try:
            encrypted_bytes = base64.urlsafe_b64decode(
                encrypted_password.encode())
            decrypted_bytes = self.cipher_suite.decrypt(encrypted_bytes)
            return decrypted_bytes.decode()
        except Exception as e:
            logger.error(f"Password decryption failed: {e}")
            # Если не удалось расшифровать, возможно пароль не был зашифрован
            return encrypted_password

    def save_user_config(self, user_id: int, config: EmailConfig) -> bool:
        """
        Сохранение конфигурации пользователя.

        Эта функция выполняет несколько важных операций:
        1. Шифрует чувствительные данные (пароль)
        2. Сохраняет конфигурацию в память
        3. Записывает все конфигурации на диск
        4. Обрабатывает ошибки записи

        Args:
            user_id: Уникальный ID пользователя Telegram
            config: Конфигурация для сохранения

        Returns:
            bool: True если сохранение прошло успешно
        """
        try:
            logger.info(f"Saving configuration for user {user_id}")

            # Создаем копию конфигурации для шифрования
            config_dict = config.to_dict()

            # Шифруем пароль перед сохранением
            if config_dict.get('password'):
                config_dict['password'] = self._encrypt_password(
                    config_dict['password'])

            # Создаем новый объект конфигурации с зашифрованным паролем
            # encrypted_config = EmailConfig.from_dict(config_dict)

            # Сохраняем в памяти (здесь храним оригинальную конфигурацию с открытым паролем)
            self.configs[user_id] = config

            # Сохраняем на диск
            return self._save_to_file()

        except Exception as e:
            logger.error(f"Failed to save config for user {user_id}: {e}")
            return False

    def get_user_config(self, user_id: int) -> Optional[EmailConfig]:
        """
        Получение конфигурации пользователя.

        Возвращает конфигурацию с расшифрованными паролями,
        готовую для использования в мониторинге.

        Args:
            user_id: ID пользователя

        Returns:
            EmailConfig или None если конфигурация не найдена
        """
        config = self.configs.get(user_id)
        if config:
            logger.debug(f"Configuration found for user {user_id}")
        else:
            logger.debug(f"No configuration found for user {user_id}")
        return config

    def delete_user_config(self, user_id: int) -> bool:
        """
        Удаление конфигурации пользователя.

        Полностью удаляет все данные пользователя из системы.
        Полезно для соблюдения требований приватности.

        Args:
            user_id: ID пользователя для удаления

        Returns:
            bool: True если удаление прошло успешно
        """
        try:
            if user_id in self.configs:
                del self.configs[user_id]
                logger.info(f"Configuration deleted for user {user_id}")
                return self._save_to_file()
            else:
                logger.warning(
                    f"Attempted to delete non-existent config for user {user_id}")
                return False
        except Exception as e:
            logger.error(f"Failed to delete config for user {user_id}: {e}")
            return False

    def get_all_configs(self) -> Dict[int, EmailConfig]:
        """
        Получение всех конфигураций.

        Полезно для административных задач и запуска мониторинга
        для всех пользователей при старте бота.

        Returns:
            Dict: Словарь всех конфигураций {user_id: config}
        """
        return self.configs.copy()

    def get_user_list(self) -> List[int]:
        """
        Получение списка всех пользователей с настроенными конфигурациями.

        Returns:
            List[int]: Список ID пользователей
        """
        return list(self.configs.keys())

    def _save_to_file(self) -> bool:
        """
        Сохранение всех конфигураций в файл.

        Эта внутренняя функция отвечает за персистентность данных.
        Она сериализует все конфигурации в JSON и записывает на диск
        с соответствующими проверками безопасности.

        Returns:
            bool: True если сохранение прошло успешно
        """
        try:
            # Подготавливаем данные для сериализации
            data_to_save = {}

            for user_id, config in self.configs.items():
                config_dict = config.to_dict()

                # Шифруем пароль перед записью на диск
                if config_dict.get('password'):
                    config_dict['password'] = self._encrypt_password(
                        config_dict['password'])

                data_to_save[str(user_id)] = config_dict

            # Создаем директорию если её нет
            self.config_file.parent.mkdir(parents=True, exist_ok=True)

            # Записываем во временный файл сначала (атомарная операция)
            temp_file = self.config_file.with_suffix('.tmp')

            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data_to_save, f, indent=2, ensure_ascii=False)

            # Перемещаем временный файл на место основного (атомарная операция в большинстве ОС)
            temp_file.replace(self.config_file)

            logger.info(f"Configurations saved to {self.config_file}")
            return True

        except Exception as e:
            logger.error(f"Failed to save configurations to file: {e}")
            return False

    def _load_configs(self) -> None:
        """
        Загрузка конфигураций из файла при инициализации.

        Читает файл конфигурации, расшифровывает пароли и
        загружает все настройки пользователей в память.
        """
        try:
            if not self.config_file.exists():
                logger.info(
                    f"Configuration file {self.config_file} does not exist, starting fresh")
                return

            with open(self.config_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Загружаем каждую конфигурацию
            for user_id_str, config_dict in data.items():
                try:
                    user_id = int(user_id_str)

                    # Расшифровываем пароль
                    if config_dict.get('password'):
                        config_dict['password'] = self._decrypt_password(
                            config_dict['password'])

                    # Создаем объект конфигурации
                    config = EmailConfig.from_dict(config_dict)
                    self.configs[user_id] = config

                    logger.debug(f"Loaded configuration for user {user_id}")

                except Exception as e:
                    logger.error(
                        f"Failed to load config for user {user_id_str}: {e}")
                    continue

            logger.info(f"Loaded {len(self.configs)} configurations from file")

        except FileNotFoundError:
            logger.info(
                "Configuration file not found, starting with empty configurations")
        except Exception as e:
            logger.error(f"Failed to load configurations: {e}")

    def update_user_config(self, user_id: int, **updates) -> bool:
        """
        Обновление отдельных параметров конфигурации пользователя.

        Позволяет изменить определенные настройки без пересоздания
        всей конфигурации. Полезно для команд настройки.

        Args:
            user_id: ID пользователя
            **updates: Параметры для обновления

        Returns:
            bool: True если обновление прошло успешно
        """
        try:
            config = self.get_user_config(user_id)
            if not config:
                logger.error(f"No configuration found for user {user_id}")
                return False

            # Применяем обновления
            config_dict = config.to_dict()
            config_dict.update(updates)

            # Создаем обновленную конфигурацию
            updated_config = EmailConfig.from_dict(config_dict)

            # Сохраняем
            return self.save_user_config(user_id, updated_config)

        except Exception as e:
            logger.error(f"Failed to update config for user {user_id}: {e}")
            return False

    def backup_configs(self, backup_path: str) -> bool:
        """
        Создание резервной копии всех конфигураций.

        Создает зашифрованную резервную копию всех настроек
        для восстановления в случае потери данных.

        Args:
            backup_path: Путь для сохранения резервной копии

        Returns:
            bool: True если создание резервной копии прошло успешно
        """
        try:
            backup_file = Path(backup_path)
            backup_file.parent.mkdir(parents=True, exist_ok=True)

            # Копируем текущий файл конфигурации
            if self.config_file.exists():
                import shutil
                shutil.copy2(self.config_file, backup_file)
                logger.info(f"Configuration backup created: {backup_file}")
                return True
            else:
                logger.warning("No configuration file to backup")
                return False

        except Exception as e:
            logger.error(f"Failed to create backup: {e}")
            return False

    def restore_configs(self, backup_path: str) -> bool:
        """
        Восстановление конфигураций из резервной копии.

        Args:
            backup_path: Путь к файлу резервной копии

        Returns:
            bool: True если восстановление прошло успешно
        """
        try:
            backup_file = Path(backup_path)
            if not backup_file.exists():
                logger.error(f"Backup file not found: {backup_file}")
                return False

            # Создаем резервную копию текущего файла
            if self.config_file.exists():
                current_backup = self.config_file.with_suffix('.backup')
                import shutil
                shutil.copy2(self.config_file, current_backup)

            # Восстанавливаем из резервной копии
            import shutil
            shutil.copy2(backup_file, self.config_file)

            # Перезагружаем конфигурации
            self.configs.clear()
            self._load_configs()

            logger.info(f"Configurations restored from backup: {backup_file}")
            return True

        except Exception as e:
            logger.error(f"Failed to restore from backup: {e}")
            return False

    def get_config_stats(self) -> Dict[str, Any]:
        """
        Получение статистики по конфигурациям.

        Returns:
            Dict с информацией о состоянии системы конфигураций
        """
        return {
            'total_users': len(self.configs),
            'config_file': str(self.config_file),
            'file_exists': self.config_file.exists(),
            'file_size': self.config_file.stat().st_size if self.config_file.exists() else 0,
            'users': list(self.configs.keys())
        }
