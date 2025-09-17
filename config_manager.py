import json  # Восстанавливаем json - нужен для сериализации конфигураций
import logging
import base64
from typing import Dict, Optional, List, Any
from pathlib import Path  # Восстанавливаем Path - улучшает работу с файловой системой
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from email_monitor import EmailConfig

logger = logging.getLogger(__name__)


class ConfigManager:
    """
    Менеджер конфигураций с поддержкой множественных настроек почты для одного пользователя.

    Эволюция класса для решения более сложных задач:
    - Один пользователь может иметь несколько почтовых ящиков
    - Каждая конфигурация имеет уникальное имя для различения
    - Безопасное хранение с шифрованием остается приоритетом
    - Гибкое управление и поиск конфигураций

    Представьте это как расширенный сейф с именованными ячейками:
    пользователь 123 может иметь ячейки "work_email", "personal_email", "backup_email"
    """

    def __init__(self, config_file: str, master_password: str = None):
        """
        Инициализация менеджера с улучшенной структурой хранения.

        Args:
            config_file: Путь к файлу конфигураций
            master_password: Мастер-пароль для шифрования
        """
        self.config_file = Path(
            config_file)  # Используем Path для лучшей работы с путями

        # Новая структура: {user_id: {config_name: EmailConfig}}
        # Это позволяет одному пользователю иметь множество конфигураций
        self.user_configs: Dict[int, Dict[str, EmailConfig]] = {}

        # Настройка шифрования остается той же
        self.encryption_key = self._generate_encryption_key(master_password)
        self.cipher_suite = Fernet(self.encryption_key)

        # Загружаем существующие конфигурации
        self._load_configs()

        logger.info(
            "ConfigManager initialized with support for multiple configs per user")

    def _generate_encryption_key(self, master_password: Optional[str]) -> bytes:
        """
        Генерация ключа шифрования (метод остается без изменений).

        Криптографическая стойкость обеспечивается PBKDF2 с высоким количеством итераций.
        """
        if master_password:
            password = master_password.encode()
        else:
            import platform
            system_info = f"{platform.node()}-{self.config_file.absolute()}"
            password = system_info.encode()

        salt = b'telegram_email_bot_salt_2024'
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )

        key = base64.urlsafe_b64encode(kdf.derive(password))
        logger.info("Encryption key generated successfully")
        return key

    def _encrypt_password(self, password: str) -> str:
        """Шифрование пароля (метод остается без изменений)."""
        try:
            encrypted_bytes = self.cipher_suite.encrypt(password.encode())
            return base64.urlsafe_b64encode(encrypted_bytes).decode()
        except Exception as e:
            logger.error(f"Password encryption failed: {e}")
            return password

    def _decrypt_password(self, encrypted_password: str) -> str:
        """Расшифровка пароля (метод остается без изменений)."""
        try:
            encrypted_bytes = base64.urlsafe_b64decode(
                encrypted_password.encode())
            decrypted_bytes = self.cipher_suite.decrypt(encrypted_bytes)
            return decrypted_bytes.decode()
        except Exception as e:
            logger.error(f"Password decryption failed: {e}")
            return encrypted_password

    def save_user_config(self, user_id: int, config: EmailConfig, config_name: str = None) -> bool:
        """
        Сохранение конфигурации пользователя с поддержкой именования.

        Этот метод теперь решает более сложную задачу: управление множественными
        конфигурациями для одного пользователя. Каждая конфигурация получает
        уникальное имя в рамках пользователя.

        Args:
            user_id: ID пользователя Telegram
            config: Конфигурация для сохранения
            config_name: Имя конфигурации (если не указано, используется email)

        Returns:
            bool: Успешность операции
        """
        try:
            # Определяем имя конфигурации
            if config_name is None:
                config_name = config.config_name or config.email

            # Обновляем имя в самой конфигурации
            config.config_name = config_name

            logger.info(
                f"Saving configuration '{config_name}' for user {user_id}")

            # Инициализируем словарь для пользователя, если его нет
            if user_id not in self.user_configs:
                self.user_configs[user_id] = {}

            # Сохраняем конфигурацию с указанным именем
            self.user_configs[user_id][config_name] = config

            # Записываем на диск
            success = self._save_to_file()

            if success:
                logger.info(
                    f"Configuration '{config_name}' saved successfully for user {user_id}")
            else:
                logger.error(
                    f"Failed to save configuration '{config_name}' for user {user_id}")

            return success

        except Exception as e:
            logger.error(
                f"Failed to save config '{config_name}' for user {user_id}: {e}")
            return False

    def get_user_config(self, user_id: int, config_name: str = None) -> Optional[EmailConfig]:
        """
        Получение конкретной конфигурации пользователя.

        Метод стал более гибким: можно запросить конкретную конфигурацию по имени
        или получить первую доступную (для обратной совместимости).

        Args:
            user_id: ID пользователя
            config_name: Имя конфигурации (если None, возвращает первую найденную)

        Returns:
            EmailConfig или None
        """
        user_configs = self.user_configs.get(user_id, {})

        if not user_configs:
            logger.debug(f"No configurations found for user {user_id}")
            return None

        if config_name:
            # Запрос конкретной конфигурации
            config = user_configs.get(config_name)
            if config:
                logger.debug(
                    f"Configuration '{config_name}' found for user {user_id}")
            else:
                logger.debug(
                    f"Configuration '{config_name}' not found for user {user_id}")
            return config
        else:
            # Возвращаем первую доступную конфигурацию (для обратной совместимости)
            first_config = next(iter(user_configs.values()))
            logger.debug(
                f"Returning first configuration for user {user_id}: {first_config.config_name}")
            return first_config

    def get_user_configs(self, user_id: int) -> Dict[str, EmailConfig]:
        """
        Получение всех конфигураций пользователя.

        Новый метод для получения полного списка настроенных почтовых ящиков
        пользователя. Критически важен для работы с множественными конфигурациями.

        Args:
            user_id: ID пользователя

        Returns:
            Dict: Словарь {config_name: EmailConfig}
        """
        configs = self.user_configs.get(user_id, {})
        logger.debug(f"Found {len(configs)} configurations for user {user_id}")
        return configs.copy()

    def get_user_configs_list(self, user_id: int) -> List[EmailConfig]:
        """
        Получение списка всех конфигураций пользователя.

        Удобный метод для случаев, когда нужен именно список конфигураций
        (например, для передачи в EmailMonitor).

        Args:
            user_id: ID пользователя

        Returns:
            List[EmailConfig]: Список конфигураций
        """
        configs = self.user_configs.get(user_id, {})
        config_list = list(configs.values())
        logger.debug(
            f"Returning {len(config_list)} configurations as list for user {user_id}")
        return config_list

    def delete_user_config(self, user_id: int, config_name: str = None) -> bool:
        """
        Удаление конфигурации пользователя.

        Расширенная версия: можно удалить конкретную конфигурацию или все сразу.

        Args:
            user_id: ID пользователя
            config_name: Имя конфигурации для удаления (если None, удаляются все)

        Returns:
            bool: Успешность операции
        """
        try:
            if user_id not in self.user_configs:
                logger.warning(f"No configurations found for user {user_id}")
                return False

            if config_name:
                # Удаляем конкретную конфигурацию
                if config_name in self.user_configs[user_id]:
                    del self.user_configs[user_id][config_name]
                    logger.info(
                        f"Configuration '{config_name}' deleted for user {user_id}")

                    # Если это была последняя конфигурация, удаляем пользователя полностью
                    if not self.user_configs[user_id]:
                        del self.user_configs[user_id]
                        logger.info(
                            f"All configurations deleted for user {user_id}")
                else:
                    logger.warning(
                        f"Configuration '{config_name}' not found for user {user_id}")
                    return False
            else:
                # Удаляем все конфигурации пользователя
                del self.user_configs[user_id]
                logger.info(f"All configurations deleted for user {user_id}")

            return self._save_to_file()

        except Exception as e:
            logger.error(f"Failed to delete config for user {user_id}: {e}")
            return False

    def update_user_config(self, user_id: int, config_name: str, **updates) -> bool:
        """
        Обновление конкретной конфигурации пользователя.

        Позволяет точечно изменять параметры определённой конфигурации
        без воздействия на остальные настройки пользователя.

        Args:
            user_id: ID пользователя
            config_name: Имя конфигурации для обновления
            **updates: Параметры для изменения

        Returns:
            bool: Успешность операции
        """
        try:
            config = self.get_user_config(user_id, config_name)
            if not config:
                logger.error(
                    f"Configuration '{config_name}' not found for user {user_id}")
                return False

            # Применяем обновления
            config_dict = config.to_dict()
            config_dict.update(updates)

            # Создаем обновленную конфигурацию
            updated_config = EmailConfig.from_dict(config_dict)

            # Сохраняем
            return self.save_user_config(user_id, updated_config, config_name)

        except Exception as e:
            logger.error(
                f"Failed to update config '{config_name}' for user {user_id}: {e}")
            return False

    def list_user_config_names(self, user_id: int) -> List[str]:
        """
        Получение списка имён конфигураций пользователя.

        Полезно для создания интерактивных меню выбора конфигурации в боте.

        Args:
            user_id: ID пользователя

        Returns:
            List[str]: Список имён конфигураций
        """
        configs = self.user_configs.get(user_id, {})
        names = list(configs.keys())
        logger.debug(f"User {user_id} has configurations: {names}")
        return names

    def get_all_configs(self) -> Dict[int, Dict[str, EmailConfig]]:
        """
        Получение всех конфигураций всех пользователей.

        Возвращает полную структуру данных. Полезно для административных
        задач и массовых операций.

        Returns:
            Dict: Полная структура {user_id: {config_name: EmailConfig}}
        """
        return self.user_configs.copy()

    def get_user_list(self) -> List[int]:
        """Получение списка всех пользователей с конфигурациями."""
        return list(self.user_configs.keys())

    def _save_to_file(self) -> bool:
        """
        Сохранение всех конфигураций в файл с улучшенной структурой.

        Теперь сохраняет вложенную структуру: пользователи -> конфигурации -> параметры.
        """
        try:
            # Подготавливаем данные для сериализации
            data_to_save = {}

            for user_id, user_configs in self.user_configs.items():
                user_data = {}

                for config_name, config in user_configs.items():
                    config_dict = config.to_dict()

                    # Шифруем пароль перед записью
                    if config_dict.get('password'):
                        config_dict['password'] = self._encrypt_password(
                            config_dict['password'])

                    user_data[config_name] = config_dict

                data_to_save[str(user_id)] = user_data

            # Создаем директорию если нужно
            self.config_file.parent.mkdir(parents=True, exist_ok=True)

            # Атомарная запись через временный файл
            temp_file = self.config_file.with_suffix('.tmp')

            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data_to_save, f, indent=2, ensure_ascii=False)

            # Атомарное перемещение
            temp_file.replace(self.config_file)

            logger.info(f"All configurations saved to {self.config_file}")
            return True

        except Exception as e:
            logger.error(f"Failed to save configurations to file: {e}")
            return False

    def _load_configs(self) -> None:
        """
        Загрузка конфигураций с поддержкой как новой, так и старой структуры.

        Обеспечивает обратную совместимость: файлы старого формата автоматически
        конвертируются в новый формат при первой загрузке.
        """
        try:
            if not self.config_file.exists():
                logger.info(
                    f"Configuration file {self.config_file} does not exist, starting fresh")
                return

            with open(self.config_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Определяем формат файла
            if self._is_old_format(data):
                logger.info("Detected old configuration format, converting...")
                self._migrate_from_old_format(data)
            else:
                logger.info("Loading new format configurations...")
                self._load_new_format(data)

            total_configs = sum(len(configs)
                                for configs in self.user_configs.values())
            logger.info(
                f"Loaded {total_configs} configurations for {len(self.user_configs)} users")

        except FileNotFoundError:
            logger.info(
                "Configuration file not found, starting with empty configurations")
        except Exception as e:
            logger.error(f"Failed to load configurations: {e}")

    def _is_old_format(self, data: Dict) -> bool:
        """
        Проверка, является ли файл старым форматом.

        Старый формат: {user_id: config_dict}
        Новый формат: {user_id: {config_name: config_dict}}
        """
        if not data:
            return False

        # Проверяем первый элемент
        first_value = next(iter(data.values()))

        # В старом формате значение - это словарь с ключами конфигурации (host, port, etc.)
        # В новом формате значение - это словарь с именами конфигураций
        return isinstance(first_value, dict) and 'host' in first_value

    def _migrate_from_old_format(self, old_data: Dict) -> None:
        """
        Миграция из старого формата в новый.

        Преобразует старую структуру {user_id: config} в новую {user_id: {config_name: config}}.
        """
        for user_id_str, config_dict in old_data.items():
            try:
                user_id = int(user_id_str)

                # Расшифровываем пароль
                if config_dict.get('password'):
                    config_dict['password'] = self._decrypt_password(
                        config_dict['password'])

                # Создаем конфигурацию
                config = EmailConfig.from_dict(config_dict)

                # Устанавливаем имя конфигурации по умолчанию
                if not config.config_name or config.config_name == "default":
                    config.config_name = config.email

                # Сохраняем в новом формате
                if user_id not in self.user_configs:
                    self.user_configs[user_id] = {}

                self.user_configs[user_id][config.config_name] = config

                logger.debug(
                    f"Migrated configuration for user {user_id}: {config.config_name}")

            except Exception as e:
                logger.error(
                    f"Failed to migrate config for user {user_id_str}: {e}")

        # Сохраняем в новом формате
        self._save_to_file()
        logger.info("Migration from old format completed successfully")

    def _load_new_format(self, data: Dict) -> None:
        """Загрузка конфигураций нового формата."""
        for user_id_str, user_data in data.items():
            try:
                user_id = int(user_id_str)
                self.user_configs[user_id] = {}

                for config_name, config_dict in user_data.items():
                    try:
                        # Расшифровываем пароль
                        if config_dict.get('password'):
                            config_dict['password'] = self._decrypt_password(
                                config_dict['password'])

                        # Создаем конфигурацию
                        config = EmailConfig.from_dict(config_dict)
                        self.user_configs[user_id][config_name] = config

                        logger.debug(
                            f"Loaded configuration '{config_name}' for user {user_id}")

                    except Exception as e:
                        logger.error(
                            f"Failed to load config '{config_name}' for user {user_id_str}: {e}")
                        continue

            except Exception as e:
                logger.error(
                    f"Failed to load configs for user {user_id_str}: {e}")
                continue

    def get_config_stats(self) -> Dict[str, Any]:
        """
        Получение расширенной статистики по конфигурациям.

        Returns:
            Dict с подробной информацией о состоянии системы
        """
        total_configs = sum(len(configs)
                            for configs in self.user_configs.values())

        user_stats = []
        for user_id, configs in self.user_configs.items():
            user_stats.append({
                'user_id': user_id,
                'config_count': len(configs),
                'config_names': list(configs.keys())
            })

        return {
            'total_users': len(self.user_configs),
            'total_configs': total_configs,
            'config_file': str(self.config_file),
            'file_exists': self.config_file.exists(),
            'file_size': self.config_file.stat().st_size if self.config_file.exists() else 0,
            'users': user_stats
        }

    def backup_configs(self, backup_path: str) -> bool:
        """Создание резервной копии (метод остается без изменений)."""
        try:
            backup_file = Path(backup_path)
            backup_file.parent.mkdir(parents=True, exist_ok=True)

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
        """Восстановление из резервной копии (метод остается без изменений)."""
        try:
            backup_file = Path(backup_path)
            if not backup_file.exists():
                logger.error(f"Backup file not found: {backup_file}")
                return False

            if self.config_file.exists():
                current_backup = self.config_file.with_suffix('.backup')
                import shutil
                shutil.copy2(self.config_file, current_backup)

            import shutil
            shutil.copy2(backup_file, self.config_file)

            # Перезагружаем конфигурации
            self.user_configs.clear()
            self._load_configs()

            logger.info(f"Configurations restored from backup: {backup_file}")
            return True

        except Exception as e:
            logger.error(f"Failed to restore from backup: {e}")
            return False
